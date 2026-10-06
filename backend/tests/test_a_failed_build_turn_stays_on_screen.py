"""A build turn that fails partway stays in the Build transcript, and the composer stops saying "building" (#662).

Live (2026-10-06): an approved plan ran on GLM 5.3 OR for about six minutes, writing the app, and
then OpenCode logged `stream error … The model reply could not be read` (`driver/provider.mjs`).
Nothing on screen said what the six minutes had done, and the composer chip went on reading
"GLM 5.3 OR · building" over a turn that had ended.

Two halves. The transcript keeps the failure AND the receipt for what the turn changed, the receipt
every other turn that changes an app leaves. And Auto's phase goes back to planning when the turn
lets go of its pin, because the shim's classifier starts every Auto turn in PLAN until its first
write (`phase_classifier.classify`) — an IMPLEMENT left standing is a claim about a turn that is over.
"""
from __future__ import annotations

from pathlib import Path

from sage.router.models import Mode

from .fake_opencode import Turn
from .test_a_dead_alias_stops_the_turn_before_it_starts import (
    PLAN,
    _no_waiting,  # noqa: F401
    _orch,
)

# The provider codec's own sentence for a reply it could not parse, as OpenCode stamps it on the
# assistant message.
UNREADABLE = {"name": "UnknownError",
              "data": {"message": "The model reply could not be read. Try again."}}
HALF_BUILT = Turn(writes={"src/App.tsx": "// half of the dashboard\n"}, error=UNREADABLE)


def _failed_approve(tmp_path: Path):
    orch, _ = _orch(tmp_path, turns=[PLAN, HALF_BUILT])
    list(orch.build_stream("build me a consumption dashboard", conversation="c1"))
    events = list(orch.approve_stream(conversation="c1"))
    project = orch.project(start_preview=False)
    return project, events, project.app_for_turn().read_history("c1")


def test_a_build_that_fails_after_writing_keeps_its_error_and_what_it_changed(tmp_path: Path):
    _, _, history = _failed_approve(tmp_path)
    turn = history[[r.get("text") for r in history].index("Approved the plan."):]
    kinds = [r["type"] for r in turn]

    assert any("could not be read" in str(r.get("message", ""))
               for r in turn if r["type"] == "error"), kinds
    assert "app_change" in kinds, kinds
    assert kinds.index("error") < kinds.index("app_change") < kinds.index("done")


def test_a_build_that_fails_before_writing_claims_no_change(tmp_path: Path):
    orch, _ = _orch(tmp_path, turns=[PLAN, Turn(error=UNREADABLE)])
    list(orch.build_stream("build me a consumption dashboard", conversation="c1"))
    list(orch.approve_stream(conversation="c1"))
    history = orch.project(start_preview=False).app_for_turn().read_history("c1")

    assert "app_change" not in [r["type"] for r in history]


def test_auto_says_planning_again_once_a_failed_build_lets_go(tmp_path: Path):
    project, events, _ = _failed_approve(tmp_path)

    assert events[-1]["type"] == "done" and events[-1]["ok"] is False
    model = project.status()["model"]
    assert model["mode"] == "auto"
    assert model["phase"] == "plan"


def test_a_pinned_mode_keeps_its_own_phase_when_the_pin_drops(tmp_path: Path):
    orch, _ = _orch(tmp_path)
    control = orch.project(start_preview=False).control
    control.set_mode(Mode.IMPLEMENT)
    control.disarm_turn_mode(control.arm_turn_mode(Mode.PLAN))

    assert control.snapshot().phase.value == "implement"
