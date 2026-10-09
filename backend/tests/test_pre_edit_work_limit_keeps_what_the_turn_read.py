"""#723: the pre-edit work limit nudges the session that did the reading, not a clean one.

The instance (#720, prompt 8): the implementer read files, the schema and git history, found the
lost queries, and hit the work limit before its first write. The clean retry started from zero,
re-read the same things and hit the same limit. The model-call trip is that instance; the
tool-result trip is its sibling shape. The request-size trip keeps the clean retry, because the
session it would nudge is the one that is already too large to send.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.build_policy import BuildPolicy
from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.tool_result_window import CompletedToolResult

from .fake_opencode import Turn
from .test_fresh_approved_session import CONVERSATION, DRAFT, RecordingOpenCode, _build, _done, _plan

READ = ["src/queries.ts", ".sage/queries.json", "docs/handoff.md"]
TERMINAL = ("Sage stopped before changing the app because the clean retry also reached the "
            "pre-edit work limit.")

# trigger -> (policy, decide_request arguments that cross only that limit)
TRIPS = {
    "model_calls": (BuildPolicy(pre_edit_model_call_limit=0), ((), 1)),
    "tool_result_bytes": (BuildPolicy(pre_edit_original_tool_result_max_bytes=10),
                          ((CompletedToolResult("native-1", 11),), 1)),
    "request_bytes": (BuildPolicy(pre_edit_request_non_media_max_bytes=10), ((), 11)),
}


class TrippingOpenCode(RecordingOpenCode):
    """Cross the guard's limit while the scripted turn at each listed prompt index runs."""

    def __init__(self, workspace: Path, turns: list[Turn]) -> None:
        super().__init__(workspace, turns)
        self.trip_at: set[int] = set()
        self.trip_args: tuple = ((), 1)

    def send_prompt(self, session_id, text, model=None, agent=None, attachments=None,
                    chat=False, tail="") -> None:
        index = len(self.prompts)
        super().send_prompt(session_id, text, model, agent, attachments, chat)
        project = self.orch.project(start_preview=False)
        if index in self.trip_at:
            with project.pre_edit_tree_lock:
                project.pre_edit_guard.decide_request(*self.trip_args)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def _reading_turn() -> Turn:
    return Turn(text="Perfect! Now I have the previous queries.",
                calls=[("read", {"filePath": path}, "contents", "completed") for path in READ])


def _run(tmp_path: Path, trigger: str, turns: list[Turn], trip_at: set[int]):
    policy, args = TRIPS[trigger]
    orch, opencode = _build(tmp_path, [Turn(text=DRAFT), *turns],
                            build_policy=policy, opencode_type=TrippingOpenCode)
    opencode.trip_at, opencode.trip_args = trip_at, args
    _plan(orch)
    sessions = len(opencode.sessions)
    events = list(orch.approve_stream(conversation=CONVERSATION))
    return orch, opencode, events, sessions


def _said(events: list[dict]) -> str:
    return "\n".join(str(event.get("message") or "") for event in events)


@pytest.mark.parametrize("trigger", ["model_calls", "tool_result_bytes"])
def test_the_work_limit_nudges_the_same_session_and_the_turn_edits_the_app(tmp_path, trigger):
    edit = Turn(writes={"src/App.tsx": "// queries restored\n"})
    orch, opencode, events, sessions = _run(tmp_path, trigger, [_reading_turn(), edit], {1})

    implement, nudge = opencode.prompts[1:]
    assert nudge["session"] == implement["session"]
    assert len(opencode.sessions) == sessions
    assert all(path in nudge["text"] for path in READ)
    assert "make the first app edit now" in nudge["text"]
    assert "only clean recovery" not in nudge["text"]
    assert [event["type"] for event in events].count("build-recovery") == 1
    assert "Starting over" not in _said(events)
    assert _done(events)["ok"] is True
    assert (orch.project(start_preview=False).workspace.path / "src" / "App.tsx").read_text() == (
        "// queries restored\n")


@pytest.mark.parametrize("trigger", ["model_calls", "tool_result_bytes"])
def test_a_nudge_that_still_writes_nothing_ends_the_turn_as_before(tmp_path, trigger):
    still_reading = Turn(text="Let me check one more file.")
    _orch, opencode, events, _sessions = _run(
        tmp_path, trigger, [_reading_turn(), still_reading], {1, 2})

    assert opencode.prompts[2]["session"] == opencode.prompts[1]["session"]
    assert len(opencode.prompts) == 3
    limit = [event for event in events if event["type"] == "build-pre-edit-limit"]
    assert [event["message"] for event in limit] == [TERMINAL]
    done = _done(events)
    assert (done["ok"], done["decision"]) == (False, "pre_edit_limit")


@pytest.mark.parametrize("cause", ["request_bytes", "malformed_call"])
def test_a_session_that_cannot_carry_a_nudge_keeps_the_clean_retry(tmp_path, cause):
    """Too large to send again, or holding a malformed call: the nudge would inherit either."""
    reading = _reading_turn()
    reading.broken_write = cause == "malformed_call"
    trigger = "request_bytes" if cause == "request_bytes" else "model_calls"
    edit = Turn(writes={"src/App.tsx": "// rebuilt in a clean session\n"})
    _orch, opencode, events, sessions = _run(tmp_path, trigger, [reading, edit], {1})

    implement, recovery = opencode.prompts[1:]
    assert recovery["session"] != implement["session"]
    assert len(opencode.sessions) == sessions + 1
    assert "only clean recovery" in recovery["text"]
    assert "No changes yet. Starting over once." in _said(events)
    assert _done(events)["ok"] is True
