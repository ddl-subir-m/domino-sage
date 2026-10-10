"""An unavailable plan review keeps the app and says which check is missing."""
from pathlib import Path

import pytest

from .fake_opencode import Turn
from .test_a_dead_alias_stops_the_turn_before_it_starts import _no_waiting  # noqa: F401
from .test_a_plan_steps_verify_is_held_to_the_page import _approve
from .test_a_planned_build_is_reviewed_against_its_done_when import ReviewGateway, _done
from .test_build_conversation_return import run as render
from .test_build_diagnostic_export import _finished
from .test_phased_build import PHASED_PLAN, _build, _writes


@pytest.mark.parametrize("answer", ["Looks complete!", RuntimeError("review unavailable"),
                                   TimeoutError("review timed out")],
                         ids=["malformed", "error", "timeout"])
def test_an_unavailable_review_is_unverified_after_the_page_passes(tmp_path, monkeypatch, answer):
    gateway = ReviewGateway(answer)
    events, oc = _approve(tmp_path, monkeypatch, gateway)
    done = _done(events)
    assert done["ok"] is True
    assert done["verification"]["stages"]["runtime"] == "passed"
    assert done["verification"]["overall"] == "unverified"
    assert done["verification"]["stages"]["plan"] == "unverified"
    assert "Plan review was not completed" in done["verification"]["reason"]
    assert any(root.joinpath("static/components/ProductInsights.js").exists()
               for root in [oc.workspace, *(Path(s["directory"]) for s in oc.sessions)])
    assert len(oc.prompts) == 2


def test_live_and_saved_status_name_the_unfinished_plan_review():
    stages = {"runtime": "passed", "data": "not_applicable", "plan": "unverified"}
    live = render({"pageValidation": "current", "verificationStages": stages})["status"][-1]
    saved = render({"savedVerification": "unverified", "verificationStages": stages})[-1]
    for row in (live, saved):
        assert row["value"] == "App built. Plan review was not completed."
        assert row["ok"] is None and row["warn"] is True


def test_diagnostics_keep_the_unfinished_plan_review(tmp_path):
    record = _finished(tmp_path, {"type": "done", "ok": True, "verification": {
        "overall": "unverified", "stages": {"runtime": "passed", "plan": "unverified"},
    }})
    assert record["buildOutcome"]["verification"]["stages"]["plan"] == "unverified"


def test_a_phased_build_keeps_the_unfinished_review_in_its_final_result(tmp_path):
    orch, _oc, project = _build(tmp_path, [Turn(text=PHASED_PLAN), _writes("src/data.ts"),
                                        _writes("src/Table.tsx"), _writes("src/Filter.tsx")])
    project.shim._gateway = ReviewGateway(RuntimeError("review unavailable"))
    list(orch.build_stream("Build a trades dashboard.", conversation="c1"))
    done = _done(list(orch.approve_stream(conversation="c1")))
    assert done["ok"] is True
    assert done["verification"]["overall"] == "unverified"
    assert done["verification"]["stages"]["plan"] == "unverified"
    assert project.workspace.has_built()
