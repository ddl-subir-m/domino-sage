"""In Direct, a turn a volume cap paused offers Keep going, and Keep going stays in its session (#585).

Direct is the way of working for people who want the agent to run (ADR-0070). Three caps end a
turn that is still working rather than one that is stuck: Chat's ceiling, Build's shell cap, and
Build's progress budget. Each still ends the turn exactly as it did — the session is interrupted and
the lock goes back — and in Direct it then draws one card whose button sends a fixed sentence into
the SAME OpenCode session, so the model keeps everything it had already read and done.

What must not change is as much the claim as what must: Guided draws no such card, and a loop
(the repeat brake) never offers to continue, because going on with a loop must not be one click.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from sage.orchestrator import service as svc
from sage.orchestrator.service import _KEEP_GOING_PROMPT, Orchestrator

from .fake_opencode import Turn
from .test_a_repeated_call_stops_the_turn import (
    CountingOpenCode,
    LoopingOpenCode,
)
from .test_a_repeated_call_stops_the_turn import _orch as _looping_orch
from .test_a_shape_only_table_artifact_renders_as_a_receipt import _node, needs_node
from .test_a_turn_stopped_at_the_ceiling_keeps_what_it_measured import (
    QUESTION,
    WorksUntilStopped,
    _findings_write,
    _short_ceiling,
)
from .test_a_turn_stopped_at_the_ceiling_keeps_what_it_measured import _orch as _chat_orch
from .test_a_turn_that_stops_changing_anything_is_budgeted import (
    TEST_POLICY,
    ScriptedPartsOpenCode,
    _bash,
    _write,
)
from .test_a_turn_that_stops_changing_anything_is_budgeted import _orch as _budget_orch


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    from sage.orchestrator import handoff
    handoff._health.reset()
    yield
    handoff._health.reset()


def _offers(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("type") == "keep-going"]


# ---- Chat's ceiling -----------------------------------------------------------------------------

def _chat_at_the_ceiling(tmp_path: Path, monkeypatch, works: str):
    _short_ceiling(monkeypatch)
    oc = WorksUntilStopped(tmp_path / "mnt" / "code", [Turn(text="working on it")])
    orch = _chat_orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    oc.turns.append(Turn(writes=_findings_write(tid)))
    return orch, oc, tid, list(orch.chat_stream(tid, QUESTION, how_sage_works=works))


def test_a_direct_chat_turn_at_the_ceiling_offers_keep_going_in_place_of_continue(
        tmp_path: Path, monkeypatch):
    orch, _oc, tid, out = _chat_at_the_ceiling(tmp_path, monkeypatch, "direct")

    offer = _offers(out)
    assert len(offer) == 1
    assert offer[0]["prompt"] == _KEEP_GOING_PROMPT
    assert offer[0]["threadId"] == tid
    # One way back in, not two: Continue replays the question in a turn that starts over.
    assert not [e for e in out if e["type"] == "continue-offer"]
    # The ceiling still ends the turn the way it did, and the card rides under the settled `done`.
    done = next(e for e in out if e["type"] == "done")
    assert (done["ok"], done["decision"]) == (False, "timeout")
    kinds = [e["type"] for e in out]
    assert kinds.index("done") < kinds.index("keep-going")
    said = next(e for e in out if e["type"] == "error")["message"]
    assert "paused it before it had an answer" in said
    assert "number of steps it takes" not in said
    # On the record, so a reload draws the sentence where it was.
    assert any(e["type"] == "keep-going" for e in orch.get_thread(tid)["history"])


def test_a_guided_chat_turn_at_the_ceiling_is_unchanged(tmp_path: Path, monkeypatch):
    _orch, _oc, _tid, out = _chat_at_the_ceiling(tmp_path, monkeypatch, "guided")

    assert _offers(out) == []
    assert [e for e in out if e["type"] == "continue-offer"]


def test_keep_going_in_chat_goes_into_the_session_the_ceiling_paused(tmp_path: Path, monkeypatch):
    """The whole point of the sentence being the only thing sent: the session holds the rest."""
    orch, oc, tid, _out = _chat_at_the_ceiling(tmp_path, monkeypatch, "direct")
    paused = oc.prompts[0]["session"]
    oc.stay_running = False
    oc.turns.append(Turn(text="Four accounts score above 0.8."))

    list(orch.chat_stream(tid, _KEEP_GOING_PROMPT, how_sage_works="direct"))

    resumed = [p for p in oc.prompts if _KEEP_GOING_PROMPT in p["text"]]
    assert len(resumed) == 1
    assert resumed[0]["session"] == paused


# ---- Build's shell cap --------------------------------------------------------------------------

def test_a_direct_build_turn_at_the_shell_cap_offers_keep_going(tmp_path: Path):
    oc = CountingOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _looping_orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart the uploads", how_sage_works="direct"))

    card = next(e for e in events if e.get("type") == "build-stalled")
    # Every call differed, so in Direct this is a pause and not advice to ask another way.
    assert "Ask a different way" not in card["message"]
    assert "paused" in card["message"]
    assert next(e for e in events if e.get("type") == "done")["decision"] == "looped"
    offer = _offers(events)
    assert len(offer) == 1 and offer[0]["prompt"] == _KEEP_GOING_PROMPT
    assert "threadId" not in offer[0]
    history = orch.project(start_preview=False).app_for_turn().read_history(
        orch.project(start_preview=False).build_conversation)
    assert any(e.get("type") == "keep-going" for e in history)


def test_a_guided_build_turn_at_the_shell_cap_is_unchanged(tmp_path: Path):
    oc = CountingOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _looping_orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart the uploads"))

    assert _offers(events) == []
    card = next(e for e in events if e.get("type") == "build-stalled")
    assert "Ask a different way" in card["message"]


def test_keep_going_in_build_goes_into_the_session_the_shell_cap_paused(
        tmp_path: Path, monkeypatch):
    """Build carries the sentence as the turn's canonical request, which the shim puts in front of
    the model; the OpenCode prompt only points at it. So both halves are read: which session the
    turn was sent into, and what it was asked."""
    oc = CountingOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _looping_orch(tmp_path, oc, "BUILD")
    list(orch.build_stream("chart the uploads", how_sage_works="direct"))
    paused = oc.prompts[0]["session"]
    oc.stop_after = oc.calls
    oc.turns.append(Turn(text="built it", writes={"src/App.tsx": "v2\n"}))
    asked: list[str] = []
    for_direct = svc.BuildIntent.for_direct
    monkeypatch.setattr(svc.BuildIntent, "for_direct",
                        classmethod(lambda cls, request: (asked.append(request),
                                                          for_direct(request))[1]))

    events = list(orch.build_stream(_KEEP_GOING_PROMPT, how_sage_works="direct"))

    assert asked == [_KEEP_GOING_PROMPT]
    assert len(oc.prompts) == 2
    assert oc.prompts[1]["session"] == paused
    assert next(e for e in events if e.get("type") == "done")["ok"] is True


# ---- Build's progress budget --------------------------------------------------------------------

def _stopped_by_the_budget(tmp_path: Path, monkeypatch, works: str) -> list[dict]:
    monkeypatch.setattr(svc, "_PROGRESS_CALL_MIN_SECONDS", 0.0)
    oc = ScriptedPartsOpenCode(tmp_path / "mnt" / "code",
                               [[_write(1)]] + [[_bash(n)] for n in range(12)],
                               [Turn(text="built it", writes={"src/App.tsx": "v1\n"})])
    from dataclasses import replace
    policy = replace(TEST_POLICY, progress_stop_seconds=3600.0, progress_notice_seconds=1800.0)
    orch = _budget_orch(tmp_path, oc, policy)
    return list(orch.build_stream("build me a chart", how_sage_works=works))


def test_a_direct_build_turn_the_progress_budget_stopped_offers_keep_going_after_the_check(
        tmp_path: Path, monkeypatch):
    events = _stopped_by_the_budget(tmp_path, monkeypatch, "direct")

    offer = _offers(events)
    assert len(offer) == 1 and offer[0]["prompt"] == _KEEP_GOING_PROMPT
    kinds = [e.get("type") for e in events]
    # Sage checked what it wrote before offering to go on, and the card is the last word.
    assert kinds.index("typecheck-start") < kinds.index("keep-going")
    assert kinds.index("done") < kinds.index("keep-going")
    assert kinds[-1] == "keep-going"


def test_a_guided_build_turn_the_progress_budget_stopped_is_unchanged(tmp_path: Path, monkeypatch):
    assert _offers(_stopped_by_the_budget(tmp_path, monkeypatch, "guided")) == []


# ---- a loop never offers to continue ------------------------------------------------------------

def test_the_repeat_brake_in_direct_offers_no_keep_going(tmp_path: Path):
    oc = LoopingOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _looping_orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart the uploads", how_sage_works="direct"))

    assert next(e for e in events if e.get("type") == "done")["decision"] == "repeat_brake"
    assert _offers(events) == []


# ---- the Workbench card -------------------------------------------------------------------------

A_DIRECT_CEILING = [
    {"type": "user", "text": QUESTION},
    {"type": "error", "message": "This reached the 10-minute limit for one turn, so Sage paused it "
                                 "before it had an answer."},
    {"type": "done", "ok": False, "decision": "timeout"},
    {"type": "keep-going", "prompt": _KEEP_GOING_PROMPT, "threadId": "thr_1",
     "message": "Keep going picks up in the same conversation, with everything Sage has "
                "already read."},
]


@needs_node
def test_the_workbench_draws_keep_going_and_pressing_it_sends_the_sentence_to_that_thread():
    result = _node("chat_continue_offer_harness.mjs",
                   {"history": A_DIRECT_CEILING, "rowType": "keep-going"})

    assert len(result["cards"]) == 1
    assert result["cards"][0]["live"] is False
    assert result["replayedButtons"] == 0
    assert result["buttons"] == ["Keep going"]
    routes = result["routes"]
    assert routes.index("api/threads/thr_1") < next(
        i for i, r in enumerate(routes) if "chat/stream" in r)
    assert result["replay"]["prompt"] == _KEEP_GOING_PROMPT
    assert result["replay"]["howSageWorks"] in {"direct", "guided"}
