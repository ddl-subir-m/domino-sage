"""A repair reply that writes nothing does not turn a writing turn into a no-edit one (#694).

Live (workspace 6ac6b6dbe5bb883af93acbae): pass 1 made the edit and typechecked clean, a repair
nudge followed, and the model answered that the change was already in place. The write witness is
per dispatch, so that reply read as "wrote nothing" and the no-edit ladder ran to the end:
"Stopped — the model replied but didn't change any files", with pass 1's work never saved. The
ladder is for a turn that never wrote. A repair that writes nothing ends the way a repair that
didn't fix the problem ends.
"""
from __future__ import annotations

from pathlib import Path

from sage.router.models import Mode

from .fake_opencode import Turn
from .test_a_dead_alias_stops_the_turn_before_it_starts import (  # noqa: F401
    _done,
    _no_waiting,
    _orch,
    _skip_planning,
)
from .test_phased_build import PHASED_PLAN, _writes
from .test_phased_build import _build as _phased

NO_EDIT = "didn't change any files"


def _implement(tmp_path: Path, turns: list[Turn]):
    orch, oc = _orch(tmp_path, turns=turns)
    _skip_planning(orch)
    orch.project(start_preview=False).control.set_mode(Mode.IMPLEMENT)
    return orch, oc


def test_a_repair_answered_without_an_edit_ends_on_the_repair_not_the_no_edit_ladder(
        tmp_path: Path, monkeypatch):
    orch, oc = _implement(tmp_path, [
        Turn(text="Built it.", writes={"src/App.tsx": "export default () => 'sales'\n"}),
        Turn(text="That is already in place."),
    ])
    scans = iter([[("sales.csv", ["src/sales.csv"])]])
    monkeypatch.setattr(orch, "_detect_leaks", lambda *_: next(scans, []))

    events = list(orch.build_stream("show sales"))

    reasons = [e["reason"] for e in events if e["type"] == "iterate"]
    assert reasons == ["copied attached data into source — moving it back to data/"]
    assert len(oc.prompts) == 2
    done = _done(events)
    assert NO_EDIT not in done["decision"]
    assert done["ok"] is True
    history = orch.project(start_preview=False).app_for_turn().read_history()
    assert "app_change" in [r["type"] for r in history]


def test_a_phase_that_never_wrote_still_runs_the_no_edit_ladder_and_stops(tmp_path: Path):
    """A phase is its own unit: phase 1 writing does not excuse phase 2 writing nothing. (A plain
    build turn that never writes is ended by the pre-edit guard before this ladder.)"""
    turns = [Turn(text=PHASED_PLAN), _writes("src/data.ts"),
             Turn(text="I looked around."), Turn(text="Still stuck.")]
    orch, _oc, _project = _phased(tmp_path, turns, no_edit_nudge_limit=1)
    list(orch.build_stream("build me a trades dashboard"))

    events = list(orch.approve_stream())

    reasons = [e["reason"] for e in events if e["type"] == "iterate"]
    # Once per attempt: a failed phase gets one retry of its own.
    assert reasons == ["wrote no code — retrying with the strong model"] * 2
    done = _done(events)
    assert done["ok"] is False
    assert "phase 2 of 3" in done["decision"]
