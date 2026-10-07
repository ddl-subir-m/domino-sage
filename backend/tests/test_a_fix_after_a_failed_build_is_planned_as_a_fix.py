"""After a failed Build, a small fix is planned against a fix contract, not a first-build one (#677).

A failure-gated turn used to be held to the first-build contract, so a good three-step fix plan was
refused for having no Screens, and a planner that answered in prose was reported as a list of
missing headings. The first-build gate keeps the full contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sage.assets.provider import FakeAssetProvider
from sage.orchestrator.plan_steps import FIX_SECTIONS, validate_execution_contract
from sage.orchestrator.service import Orchestrator

from .fake_opencode import Turn
from .test_plan_reference_persistence import _orchestrator

FIX_PLAN = """# Push Fix

Stop the push from failing on the missing remote.

## Done when
- A push from the app succeeds.

## Plan
### 1. Remote check
- Files — src/App.tsx
- Do — Check the remote before pushing.
- Done when — The push no longer errors.
"""

PROSE = ("Signal Room can't call @haiku or @sonnet yet. Attach those models to the app, "
         "then ask again and I'll plan the change.")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _after_a_failed_build(tmp_path: Path, turns: list[Turn]):
    orch, client = _orchestrator(tmp_path, FakeAssetProvider(), turns)
    app = orch.project(start_preview=False).app_for_turn()
    app.mark_built()
    app.set_last_turn_failed(True)
    return orch, client


def test_a_fix_plan_needs_no_screens_but_a_first_build_plan_does():
    assert not validate_execution_contract(FIX_PLAN).valid
    assert "screens" in validate_execution_contract(FIX_PLAN).missing_sections
    assert validate_execution_contract(FIX_PLAN, required=FIX_SECTIONS).valid


def test_a_failure_gated_fix_plan_with_no_screens_reaches_approval_and_builds(tmp_path: Path):
    orch, _ = _after_a_failed_build(tmp_path, [
        Turn(text=FIX_PLAN),
        Turn(text="Fixed.", writes={"src/App.tsx": "export default function App() { return 1 }\n"}),
    ])

    events = list(orch.build_stream("resolve this push error"))

    assert not any(e.get("decision") == "invalid execution plan" for e in events)
    proposed = next(e for e in events if e.get("type") == "plan-proposed")
    assert next(e for e in events if e.get("type") == "done")["decision"] == "awaiting approval"

    approved = list(orch.approve_stream(plan_id=proposed["planId"]))

    done = [e for e in approved if e.get("type") == "done"]
    assert [e["decision"] for e in done] == ["typecheck clean"]
    assert done[0]["ok"] is True


def test_a_failure_gated_prose_answer_is_shown_not_reported_as_an_invalid_plan(tmp_path: Path):
    orch, client = _after_a_failed_build(tmp_path, [Turn(text=PROSE)])

    events = list(orch.build_stream("Make Write deal brief use @haiku."))

    assert not any(e.get("decision") == "invalid execution plan" for e in events)
    assert not any(e.get("type") == "plan-proposed" for e in events)
    assert any(e.get("type") == "agent" and e.get("kind") == "text" and PROSE in e.get("text", "")
               for e in events)
    assert next(e for e in events if e.get("type") == "done")["decision"] == "answered"
    assert len(client.prompts) == 1


def test_the_first_build_gate_still_rejects_a_plan_with_no_screens(tmp_path: Path):
    orch, _ = _orchestrator(tmp_path, FakeAssetProvider(), [Turn(text=FIX_PLAN), Turn(text=FIX_PLAN)])

    events = list(orch.build_stream("Build a push tool."))

    assert not any(e.get("type") == "plan-proposed" for e in events)
    assert any(e.get("decision") == "invalid execution plan" for e in events)
