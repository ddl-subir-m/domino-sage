"""Chat compaction on the native codec reaches the model, and never answers the next prompt (#760).

Seen on the #714 demo re-run (2026-10-10, native codec on): `_maybe_compact_chat` took the turn lock
directly and named no turn, so the native harness refused the summary request with
`sage_turn_scope_changed` every time. OpenCode still answered `POST /summarize` with `true`, so
nothing was logged, and the compaction stayed pending in the session. OpenCode 1.18.4 runs a pending
compaction before anything else on the next prompt — parented on the person's new message, and with
`auto=false` it then stops — so prompt 6 was answered with a checkpoint of prompt 5.

`NativeOpenCode` stands in for OpenCode at the two points that matter: its `summarize` asks Sage's
native routes for the summary the way the provider does (`/v1/sage/resolve`, then the inference
route), and records a summary that finished or one that failed; its `send_prompt` runs a pending
compaction in place of the prompt, by OpenCode's own rule — a `compaction` part newer than the last
assistant message that finished.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from sage.gateway.protocol import Protocol
from sage.orchestrator import chat_compact
from sage.orchestrator.native_routes import _TURN_SCOPE_CHANGED
from sage.orchestrator.service import Orchestrator

from .fake_opencode import FakeOpenCode, Turn
from .test_native_model_controls import dispatch
from .test_native_model_controls import running as native_running

running = native_running

pytestmark = pytest.mark.usefixtures("ledger")

CHECKPOINT = "Objective / Important Details / Work State / Next Move"
OVER = chat_compact.CHAT_MAX_TOKENS + 1


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    from sage.orchestrator import handoff
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def _opencode_would_run_a_compaction(msgs: list[dict]) -> bool:
    """OpenCode 1.18.4's `latest()`: a compaction part after the last finished assistant is a task."""
    finished = max((i for i, m in enumerate(msgs) if m.get("type") == "assistant" and m.get("finish")),
                   default=-1)
    return any(isinstance(p, dict) and p.get("type") == "compaction"
               for m in msgs[finished + 1:] for p in m.get("content", []))


class NativeOpenCode(FakeOpenCode):
    def __init__(self, workspace: Path, turns: list[Turn], http, orch) -> None:
        super().__init__(workspace, turns)
        self.http = http
        self.orch = orch
        self.summaries: list[dict] = []
        self.during_summary = None

    def summarize(self, session_id: str, provider_id: str, model_id: str, *, auto: bool = False) -> None:
        n = len(self.compacts) + 1
        self.compacts.append({"session": session_id, "providerID": provider_id,
                              "modelID": model_id, "auto": auto})
        msgs = self._by_session.setdefault(session_id, [])
        parent = f"c{n}"
        msgs.append({"id": parent, "type": "user", "content": [{"type": "compaction", "auto": auto}]})
        if self.during_summary is not None:
            self.during_summary()
        headers = {"X-Session-Id": session_id}
        prompt = [{"role": "user", "content": [{"type": "text", "text": "Summarize the conversation."}]}]
        chosen = self.http.post("/v1/sage/resolve", headers=headers, json={"prompt": prompt, "tools": []})
        seen = {"resolve": chosen.status_code, "running": self.orch.turn_state()["running_turn"]}
        if chosen.status_code == 200:
            choice = chosen.json()
            answer = dispatch(self.http, headers, Protocol(choice["protocol"]), choice["model"])
            body = answer.json() if answer.status_code != 200 else {}
            seen.update(model=choice["model"], status=answer.status_code,
                        refusal=(body.get("error") or {}).get("message"),
                        failed=answer.status_code != 200 or b'"error"' in answer.content)
        else:
            seen.update(status=chosen.status_code, failed=True)
        self.summaries.append(seen)
        summary = {"id": f"cs{n}", "type": "assistant", "summary": True, "parentID": parent}
        if seen["failed"]:
            summary.update(content=[], error={"name": "APIError", "data": {
                "message": seen.get("refusal") or "refused"}})
        else:
            summary.update(finish="stop", content=[{"id": f"cs{n}-x", "type": "text", "text": CHECKPOINT}])
        msgs.append(summary)

    def send_prompt(self, session_id: str, text: str, model: dict | None = None,
                    agent: str | None = None, attachments: list[dict] | None = None,
                    chat: bool = False, tail: str = "") -> None:
        msgs = self._by_session.setdefault(session_id, [])
        if not _opencode_would_run_a_compaction(msgs):
            return super().send_prompt(session_id, text, model=model, agent=agent,
                                       attachments=attachments, chat=chat, tail=tail)
        self.prompts.append({"text": text, "agent": agent, "attachments": attachments,
                             "session": session_id, "model": model, "tail": tail})
        self._running[session_id] = True
        n = len(msgs)
        msgs.append({"id": f"h{n}", "type": "assistant", "summary": True, "finish": "stop",
                     "content": [{"id": f"h{n}-x", "type": "text", "text": CHECKPOINT}]})


def _chat(running, turns: list[Turn]):
    http, orch, gateway = running
    project = orch.project(start_preview=False)
    project.control.pick_chat("Opus-4.8")
    oc = NativeOpenCode(Path(project.record.path), turns, http, orch)
    orch._oc_client = oc
    return orch, oc, gateway, orch.create_thread()["id"]


def _answer(events: list[dict]) -> str:
    return "".join(e.get("text", "") for e in events
                   if e.get("type") == "agent" and e.get("kind") == "text")


def test_the_summary_is_admitted_on_the_native_route_as_the_threads_chat_model(running):
    orch, oc, gateway, tid = _chat(running, [Turn(text="five", tokens={"input": OVER, "output": 1})])

    list(orch.chat_stream(tid, "what is in the news"))

    assert len(oc.summaries) == 1
    seen = oc.summaries[0]
    assert seen.get("refusal") != _TURN_SCOPE_CHANGED, seen
    assert seen["status"] == 200 and not seen["failed"], seen
    # Routed as the Thread's Chat pick, not as the handle OpenCode was told to resolve.
    assert oc.compacts[0]["modelID"] == "gpt-5.4"
    assert seen["model"] == "Opus-4.8"
    assert gateway.seen_protocols[-1] is Protocol.MESSAGES
    assert seen["running"]["kind"] == "chat"
    assert seen["running"]["conversation"] == tid
    assert orch.turn_state()["running_turn"] is None
    msgs = oc.messages("fake-session")
    assert chat_compact.unfinished_compaction(msgs) == []
    assert msgs[-1]["summary"] is True and msgs[-1]["finish"] == "stop"


def test_a_refused_summary_is_logged_and_leaves_no_compaction_pending(running, caplog):
    orch, oc, gateway, tid = _chat(running, [Turn(text="five", tokens={"input": OVER, "output": 1})])
    gateway.failure = "before"

    with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
        list(orch.chat_stream(tid, "what is in the news"))

    assert oc.summaries and oc.summaries[0]["failed"], oc.summaries
    assert "the summary did not complete" in caplog.text, caplog.text
    msgs = oc.messages("fake-session")
    assert not _opencode_would_run_a_compaction(msgs), msgs
    assert not any(m.get("summary") for m in msgs), msgs


def test_the_next_prompt_is_answered_after_a_summary_failed(running):
    orch, oc, gateway, tid = _chat(running, [
        Turn(text="five", tokens={"input": OVER, "output": 1}),
        Turn(text="The deal desk approves it."),
    ])
    gateway.failure = "before"
    list(orch.chat_stream(tid, "what is in the news"))
    gateway.failure = None
    orch._cancel_chat_idle_save()

    events = list(orch.chat_stream(tid, "who has to approve it?"))

    assert CHECKPOINT not in _answer(events)
    assert "The deal desk approves it." in _answer(events)


def test_a_compaction_left_pending_earlier_does_not_answer_this_prompt(running, caplog):
    """A session can already carry one: written by a Sage without this fix, or left when the
    summary was interrupted or the delete after it failed. The prompt must still be answered."""
    orch, oc, gateway, tid = _chat(running, [Turn(text="five"), Turn(text="The deal desk approves it.")])
    list(orch.chat_stream(tid, "what is in the news"))
    orch._cancel_chat_idle_save()
    oc._by_session["fake-session"] += [
        {"id": "c-old", "type": "user", "content": [{"type": "compaction", "auto": False}]},
        {"id": "cs-old", "type": "assistant", "summary": True, "parentID": "c-old", "content": [],
         "error": {"name": "APIError", "data": {"message": "refused"}}},
    ]

    with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
        events = list(orch.chat_stream(tid, "who has to approve it?"))

    assert "The deal desk approves it." in _answer(events)
    assert CHECKPOINT not in _answer(events)
    assert "the summary did not complete" in caplog.text, caplog.text
    assert not any(m["id"] in ("c-old", "cs-old") for m in oc.messages("fake-session"))


def test_a_stop_during_compaction_does_not_reach_the_next_turn(running):
    """The summary runs under a named Chat turn, so a Stop can find it — and its flag has to end
    with the compaction, or the next question is stopped before it runs a step."""
    orch, oc, gateway, tid = _chat(running, [
        Turn(text="five", tokens={"input": OVER, "output": 1}),
        Turn(text="The deal desk approves it."),
    ])
    stopped: list[bool] = []
    oc.during_summary = lambda: stopped.append(orch.stop_build(kind="chat", conversation=tid))

    list(orch.chat_stream(tid, "what is in the news"))

    assert stopped == [True]
    assert orch.project(start_preview=False).stop_requested is False
    oc.during_summary = None
    orch._cancel_chat_idle_save()
    events = list(orch.chat_stream(tid, "who has to approve it?"))
    assert "The deal desk approves it." in _answer(events)


@pytest.mark.parametrize("msgs,pending", [
    ([], []),
    # Completed: an assistant after the marker finished with no error.
    ([{"id": "c", "type": "user", "content": [{"type": "compaction"}]},
      {"id": "s", "type": "assistant", "summary": True, "finish": "stop", "parentID": "c"}], []),
    # Refused: OpenCode left no finish, so it is still a task.
    ([{"id": "c", "type": "user", "content": [{"type": "compaction"}]},
      {"id": "s", "type": "assistant", "summary": True, "error": {"name": "APIError"}}], ["c", "s"]),
    # Failed with a finish: OpenCode does not count a summary with an error as the compaction.
    ([{"id": "c", "type": "user", "content": [{"type": "compaction"}]},
      {"id": "s", "type": "assistant", "summary": True, "finish": "error", "error": {"name": "X"}}],
     ["c", "s"]),
    # No summary at all yet: the marker alone is the task.
    ([{"id": "a", "type": "assistant", "finish": "stop"},
      {"id": "c", "type": "user", "content": [{"type": "compaction"}]}], ["c"]),
    # The v1 `{info, parts}` shape reads the same.
    ([{"info": {"id": "c", "role": "user"}, "parts": [{"type": "compaction"}]},
      {"info": {"id": "s", "role": "assistant", "summary": True, "finish": "stop"}, "parts": []}], []),
])
def test_unfinished_compaction_follows_opencodes_own_rule(msgs, pending):
    found = chat_compact.unfinished_compaction(msgs)
    assert [(m.get("info") or m)["id"] for m in found] == pending


def test_the_driver_deletes_one_message(monkeypatch):
    import httpx

    from sage.driver.opencode import OpenCodeClient

    calls: list[str] = []

    def delete(url, **_k):
        calls.append(url)
        return httpx.Response(200, json=True, request=httpx.Request("DELETE", url))

    monkeypatch.setattr(httpx, "delete", delete)
    OpenCodeClient("http://127.0.0.1:1").delete_message("ses_a", "msg_b")
    assert calls == ["http://127.0.0.1:1/session/ses_a/message/msg_b"]
