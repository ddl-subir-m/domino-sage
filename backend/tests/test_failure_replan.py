"""Failure-triggered replan (case 3): the turn after a failed turn gates on a plan in Auto mode,
instead of retrying blind.

The _build_stream wiring itself isn't exercised here — there is no OpenCode fake in this suite — so
these cover the pure predicate and the persisted signal, which is where all the decisions live.
"""
from __future__ import annotations

from pathlib import Path

from sage.orchestrator.service import _failure_gate_applies
from sage.router.models import Mode
from sage.workspace.manager import Workspace


def _applies(**kw) -> bool:
    base = {"mode": Mode.AUTO, "is_approval": False, "is_question": False,
            "skip_planning": False, "prev_turn_failed": True}
    base.update(kw)
    return _failure_gate_applies(**base)


def test_a_failed_previous_turn_gates_the_next_auto_turn():
    # The whole feature: stop and propose a plan rather than starting another blind build.
    assert _applies() is True


def test_a_clean_previous_turn_changes_nothing():
    # The common case by far. Nothing about dispatch may change when nothing went wrong.
    assert _applies(prev_turn_failed=False) is False


def test_an_explicit_mode_pick_is_never_second_guessed():
    # Mirrors _should_gate / _scope_gate_applies. Implement is where a user retries a failure on
    # purpose; gating it would override the choice they just made.
    for mode in (Mode.PLAN, Mode.IMPLEMENT, Mode.ASK):
        assert _applies(mode=mode) is False


def test_an_approval_is_never_gated_by_a_failure():
    # Re-proposing a plan for a plan the user just approved is the exact loop this exists to break.
    assert _applies(is_approval=True) is False


def test_a_question_is_never_gated_by_a_failure():
    # A question is answered read-only and isn't a build; there is nothing to plan.
    assert _applies(is_question=True) is False


def test_opting_out_of_planning_opts_out_of_this_gate_too():
    # skip_planning is an explicit "don't stop to plan for me". A project that turned automatic
    # gating off must not get one back through a side door.
    assert _applies(skip_planning=True) is False


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(project_id="p", path=tmp_path)


def test_a_fresh_workspace_reports_no_failure(tmp_path: Path):
    # Fail open: no state at all must read exactly like a successful previous turn.
    assert _ws(tmp_path).read_last_turn_failed() is False


def test_the_failure_signal_round_trips_and_clears(tmp_path: Path):
    ws = _ws(tmp_path)
    ws.set_last_turn_failed(True)
    assert ws.read_last_turn_failed() is True
    # Consumption is what keeps the gate one-shot rather than a permanent approval wall.
    ws.set_last_turn_failed(False)
    assert ws.read_last_turn_failed() is False


def test_recording_a_failure_leaves_other_settings_alone(tmp_path: Path):
    # It shares settings.json with `built` and `skip_planning`; clobbering either would re-gate a
    # built project or silently undo the user's opt-out.
    ws = _ws(tmp_path)
    ws.write_settings({"built": True, "skip_planning": True})
    ws.set_last_turn_failed(True)
    assert ws.read_settings() == {"built": True, "skip_planning": True, "last_turn_failed": True}


def test_corrupt_settings_read_as_no_failure(tmp_path: Path):
    # A broken feature must never become a feature that blocks builds.
    ws = _ws(tmp_path)
    ws.settings_path.parent.mkdir(parents=True, exist_ok=True)
    ws.settings_path.write_text("{not json")
    assert ws.read_last_turn_failed() is False
