"""A Build turn is told what its Conversation said in Chat (#53).

A Conversation has two harness sessions and the Build one cannot see the Chat one. The bridge used
to be `.sage/handoff.md`, written once when the person crossed over — so "make that chart bigger",
about a chart discussed after the crossing, had nothing to resolve against. These tests hold the
replacement to the two things that make it different from that file: it is rebuilt on every turn,
and it is bounded.

The other half is attribution. Two Conversations can drive the same Built App (#73), so a turn's
background has to come from the Conversation that drove THAT turn — not from the app, and not from
whichever Conversation touched the app last.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator import chat_compact
from sage.orchestrator.service import _CHAT_CONTEXT_PREAMBLE, Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """The scope classifier is the only caller on this path; BUILD keeps it out of the way."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """Same two waits test_turn_path strips: the poll sleep and the runtime-error poll, neither of
    which a scripted turn can ever spend usefully."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _orch(tmp: Path, turns: list[Turn]):
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")

    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, turns)
    orch = Orchestrator(
        workspace_dir=ws, template=template, gateway=ScriptedGateway(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False)
    return orch, oc


def _store(orch) -> ThreadStore:
    return ThreadStore(orch.project(start_preview=False).record.path)


def _thread(orch) -> str:
    return orch.create_thread()["id"]


def _said(orch, thread_id: str, who: str, text: str) -> None:
    """One Chat turn on the record, written the way chat_stream writes it."""
    entry = ({"type": "user", "text": text} if who == "user"
             else {"type": "agent", "kind": "text", "text": text})
    _store(orch).append_history(thread_id, entry)


def _build(orch, prompt: str, conversation: str | None = None) -> list[dict]:
    return list(orch.build_stream(prompt, None, None, conversation))


def _sent(oc) -> str:
    return oc.prompts[-1]["text"]


# ---- the summary reaches the turn ---------------------------------------------------------------


def test_a_build_turn_carries_its_conversations_chat(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="1. Make the chart bigger")])
    thread = _thread(orch)
    _said(orch, thread, "user", "which regions sell the most?")
    _said(orch, thread, "agent", "West leads. Here is a bar chart of revenue by region.")

    _build(orch, "make that chart bigger", conversation=thread)

    sent = _sent(oc)
    assert "which regions sell the most?" in sent
    assert "revenue by region" in sent
    # The person's words are what the turn is for; the background sits after them.
    assert sent.index("make that chart bigger") < sent.index(_CHAT_CONTEXT_PREAMBLE)


def test_the_background_is_framed_as_background(tmp_path: Path):
    """The preamble is not decoration. Without it a model reads the questions in a transcript as a
    backlog and starts answering them, on a turn that was asked for one change."""
    orch, oc = _orch(tmp_path, [Turn(text="1. Sort it")])
    thread = _thread(orch)
    _said(orch, thread, "user", "also, can we add a churn forecast?")

    _build(orch, "sort the table by cohort", conversation=thread)

    assert _CHAT_CONTEXT_PREAMBLE in _sent(oc)


def test_it_is_the_compaction_modules_summary(tmp_path: Path):
    """Not a second summariser living in the orchestrator: the block IS chat_compact's output."""
    orch, oc = _orch(tmp_path, [Turn(text="1. Do it")])
    thread = _thread(orch)
    _said(orch, thread, "user", "chart the revenue")
    _said(orch, thread, "agent", "Done — revenue by region.")

    _build(orch, "build me a dashboard", conversation=thread)

    expected = chat_compact.chat_summary(_store(orch).read_history(thread))
    assert f"{_CHAT_CONTEXT_PREAMBLE}\n\n{expected}" in _sent(oc)


# ---- rebuilt, not written once ------------------------------------------------------------------


def test_the_summary_is_rebuilt_on_every_turn(tmp_path: Path):
    """The whole point of the ticket. A digest written at the crossing cannot contain a chart
    discussed after it; a summary rebuilt per turn can."""
    orch, oc = _orch(tmp_path, [
        Turn(text="1. **Table** — Show it."),
        Turn(text="Built it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text="Sorted it.", writes={"src/App.tsx": "// v2\n"}),
    ])
    thread = _thread(orch)
    _said(orch, thread, "user", "chart the revenue")
    _build(orch, "build me a dashboard", conversation=thread)
    list(orch.approve_stream(conversation=thread))

    # Said in Chat AFTER the crossing — invisible to a one-shot digest.
    _said(orch, thread, "agent", "The churn table sorts by cohort.")
    _build(orch, "sort it the other way", conversation=thread)

    assert "churn table sorts by cohort" in _sent(oc)


def test_a_conversation_with_no_chat_turns_adds_no_section(tmp_path: Path):
    """Typed straight into Build. There is nothing to say, so nothing is said — an empty heading
    would be one more thing for the agent to read and account for."""
    orch, oc = _orch(tmp_path, [Turn(text="1. Add a table")])
    thread = _thread(orch)

    _build(orch, "build me a dashboard", conversation=thread)

    sent = _sent(oc)
    assert _CHAT_CONTEXT_PREAMBLE not in sent
    assert sent.strip().endswith("build me a dashboard")


def test_a_turn_with_no_conversation_still_builds(tmp_path: Path):
    """The CLI and the tests pass no conversation. There is no Chat to attribute, and the turn is
    not the place to find that out."""
    orch, oc = _orch(tmp_path, [Turn(text="1. Add a table")])

    _build(orch, "build me a dashboard")

    assert _CHAT_CONTEXT_PREAMBLE not in _sent(oc)


# ---- attribution ---------------------------------------------------------------------------------


def test_two_conversations_driving_one_app_each_get_their_own(tmp_path: Path):
    """Keyed on the Conversation that drove the turn, never on the Built App. Keyed on the app, the
    second Conversation would resolve "that chart" against a chart it had never seen (#73)."""
    orch, oc = _orch(tmp_path, [
        Turn(text="1. **Table** — Show it."),
        Turn(text="Built it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text="Changed it.", writes={"src/App.tsx": "// v2\n"}),
    ])
    first, second = _thread(orch), _thread(orch)
    _said(orch, first, "agent", "The revenue chart is a bar chart by region.")
    _said(orch, second, "agent", "The churn table sorts by cohort.")

    # `first` drives the app: it plans, approves, and is the last Conversation to touch it.
    _build(orch, "build me a dashboard", conversation=first)
    list(orch.approve_stream(conversation=first))

    _build(orch, "make that bigger", conversation=second)

    sent = _sent(oc)
    assert "churn table sorts by cohort" in sent
    assert "revenue chart" not in sent


# ---- bounded --------------------------------------------------------------------------------------


def test_the_summary_does_not_grow_with_the_conversation(tmp_path: Path):
    """Ten times the transcript, the same size prompt. This is what makes the background affordable
    on every turn rather than once at the crossing."""
    orch, oc = _orch(tmp_path, [Turn(text="1. Do it"), Turn(text="1. Do it")])
    long, longer = _thread(orch), _thread(orch)
    for i in range(60):
        _said(orch, long, "user", f"question {i} about the quarterly revenue numbers")
    for i in range(600):
        _said(orch, longer, "user", f"question {i} about the quarterly revenue numbers")

    def block(sent: str) -> str:
        return sent.split(_CHAT_CONTEXT_PREAMBLE, 1)[1].strip()

    _build(orch, "build me a dashboard", conversation=long)
    a = block(_sent(oc))
    _build(orch, "build me a dashboard", conversation=longer)
    b = block(_sent(oc))

    assert len(a) <= chat_compact.SUMMARY_BUDGET
    assert len(b) <= chat_compact.SUMMARY_BUDGET
    assert abs(len(b) - len(a)) < 100  # both full; the difference is one line's rounding


# ---- chat_compact.chat_summary ---------------------------------------------------------------------


def _user(text: str) -> dict:
    return {"type": "user", "text": text}


def _agent(text: str) -> dict:
    return {"type": "agent", "kind": "text", "text": text}


def test_chat_summary_reads_back_in_the_order_it_was_said():
    out = chat_compact.chat_summary([_user("first"), _agent("second"), _user("third")])
    assert out.splitlines() == ["- They said: first", "- You said: second", "- They said: third"]


def test_chat_summary_is_empty_for_a_thread_with_no_turns():
    assert chat_compact.chat_summary([]) == ""
    # Furniture only: a Chat turn that just ran a tool has nothing anybody said in it.
    assert chat_compact.chat_summary([
        {"type": "agent", "kind": "tool", "tool": "read"},
        {"type": "done", "ok": True},
        {"type": "user", "text": "   "},
    ]) == ""


def test_chat_summary_keeps_the_newest_turns_when_the_budget_runs_out():
    """A follow-up points at what was just said, so the tail is what has to survive."""
    turns = [_user(f"{i} " + "x" * 200) for i in range(50)]
    out = chat_compact.chat_summary(turns)

    assert len(out) <= chat_compact.SUMMARY_BUDGET
    assert "- They said: 49 x" in out
    assert "- They said: 0 x" not in out
    assert len(out.splitlines()) < 50


def test_chat_summary_truncates_a_long_turn_rather_than_dropping_it():
    """One 3000-character answer must not evict the exchanges around it."""
    out = chat_compact.chat_summary([_user("what is churn?"), _agent("y" * 3000)])
    lines = out.splitlines()

    assert lines[0] == "- They said: what is churn?"
    assert lines[1].startswith("- You said: yyy")
    assert lines[1].endswith("…")
    assert len(lines[1]) <= chat_compact.SUMMARY_TURN_CHARS + len("- You said: ")


def test_chat_summary_collapses_a_multi_line_turn_onto_one_line():
    out = chat_compact.chat_summary([_agent("Here is the table:\n\n| a | b |\n| 1 | 2 |")])
    assert out == "- You said: Here is the table: | a | b | | 1 | 2 |"
