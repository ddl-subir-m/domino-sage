"""Retry on a failed Chat turn replaces the failed answer rather than adding a second one (#665).

Before, Retry sent the question again as a brand-new turn: a second copy of the question appeared,
and the failed answer kept its half-drawn chart and its red "couldn't finish" line above the new
one. Anyone reading the conversation later saw two answers to one question, one marked failed.

Now the press names the turn it retries. The server records the question once and writes a
`turn-retried` row; the live stream and a reload both read that row and draw the failed answer
collapsed under one line that can open it.

The browser half runs the real store and `SW.Message` in node (`js/chat_retry_harness.mjs`), fed
the rows the server writes. The server half runs a real Chat turn over FakeOpenCode.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .fake_opencode import Turn
from .test_chat_turn import _no_waiting
from .test_chat_turn import _orch as _chat_orch

__all__ = ["_no_waiting"]  # the host's autouse fixture, so a poll never really sleeps here

_HARNESS = Path(__file__).resolve().parent / "js" / "chat_retry_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

QUESTION = "chart revenue by month"
FAILED = [
    {"type": "user", "text": QUESTION, "contextIds": [], "context": []},
    {"type": "artifacts", "items": [{"path": "examples/t1/revenue.png", "kind": "chart",
                                     "title": "Revenue", "role": "answer"}]},
    {"type": "error", "message": "Sage couldn't finish — model call failed. Retry the turn."},
    {"type": "done", "ok": False, "decision": "step failed", "turnId": "turn_failed"},
]
RETRIED = [
    {"type": "turn-retried", "of": "turn_failed"},
    {"type": "agent", "kind": "text", "text": "Revenue rose 4% in March."},
    {"type": "done", "ok": True, "decision": "answered", "turnId": "turn_again"},
]


def _run(history: list[dict], frames: list[dict] | None = None, press: bool = False,
         ask: dict | None = None) -> dict:
    out = subprocess.run(["node", str(_HARNESS)],
                         input=json.dumps({"history": history, "frames": frames or [],
                                           "press": press, "ask": ask}),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@needs_node
def test_retry_names_the_failed_turn_and_does_not_ask_the_question_twice():
    drawn = _run(FAILED, RETRIED, press=True)

    assert drawn["posted"] == [{"prompt": QUESTION, "retryOf": "turn_failed"}]
    assert [m["role"] for m in drawn["after"]] == ["user", "assistant", "assistant"]


@needs_node
def test_the_failed_answer_is_superseded_live():
    drawn = _run(FAILED, RETRIED, press=True)

    assert [m["superseded"] for m in drawn["after"]] == [False, True, False]


@needs_node
def test_a_turn_that_failed_in_this_tab_is_superseded_without_a_reload():
    """The failed answer was streamed here, not read back, so only the live `done` named it."""
    drawn = _run([], RETRIED, press=True, ask={"prompt": QUESTION, "frames": FAILED[1:]})

    assert drawn["posted"][-1] == {"prompt": QUESTION, "retryOf": "turn_failed"}
    assert [(m["role"], m["superseded"]) for m in drawn["after"]] == [
        ("user", False), ("assistant", True), ("assistant", False)]


@needs_node
def test_a_reload_draws_the_retried_thread_the_way_the_live_one_was_drawn():
    """The row the server wrote is what a reload reads. Without the reload half, the failed answer
    comes back the moment the Conversation is reopened — and the retried answer joins it in one
    message, because a reload starts a new answer only at a question."""
    live = _run(FAILED, RETRIED, press=True)["after"]
    reloaded = _run(FAILED + RETRIED)["before"]

    assert [(m["role"], m["superseded"]) for m in reloaded] == [
        (m["role"], m["superseded"]) for m in live]


@needs_node
def test_a_superseded_answer_is_drawn_as_one_line_that_can_open_it():
    failed = _run(FAILED + RETRIED)["before"][1]

    assert failed["blocks"] == 0
    assert "Earlier attempt failed — show" in failed["text"]
    assert failed["retry"] is False


@needs_node
def test_the_retried_answer_still_offers_retry():
    """Retry walks back to the question, and the superseded answer now sits between the two."""
    after = _run(FAILED + RETRIED)["before"]

    assert after[2]["retry"] is True


def test_the_server_records_the_question_once_and_says_which_turn_was_retried(tmp_path: Path):
    orch, _oc = _chat_orch(tmp_path, [Turn(text="Revenue fell."), Turn(text="Revenue rose.")])
    tid = orch.create_thread()["id"]
    first = list(orch.chat_stream(tid, QUESTION))
    failed = next(e for e in first if e.get("type") == "done")["turnId"]

    again = list(orch.chat_stream(tid, QUESTION, retry_of=failed))

    rows = orch.get_thread(tid)["history"]
    assert [e["text"] for e in rows if e.get("type") == "user"] == [QUESTION]
    assert [e["of"] for e in rows if e.get("type") == "turn-retried"] == [failed]
    assert [e["of"] for e in again if e.get("type") == "turn-retried"] == [failed]


def test_the_route_hands_retry_of_to_the_turn(monkeypatch):
    from sage.orchestrator import app as appmod

    seen: list[str] = []

    class Streams:
        def prepare_stream_turn(self, turn_id, **_kwargs):
            return type("Ticket", (), {"id": turn_id, "sequence": 1, "epoch": "e"})(), "running"

        def release_stream_turn(self, _ticket) -> None:
            return None

        def chat_stream(self, *_args, **kwargs):
            seen.append(kwargs.get("retry_of"))
            yield {"type": "done", "ok": True}

    monkeypatch.setattr(appmod, "orchestrator", Streams())
    # Not entered as a context manager: that runs the app's lifespan, which starts OpenCode.
    client = TestClient(appmod.control_app)
    try:
        client.post("/api/threads/thr_a/chat/stream", json={"prompt": "hi"})
        client.post("/api/threads/thr_a/chat/stream", json={"prompt": "hi", "retryOf": "turn_x"})
    finally:
        client.close()

    assert seen == ["", "turn_x"]
