"""A Project skill the person named reaches the Build of the plan they approved (#751).

Live (Signal Room, #714): Chat, told to follow @revops-conventions, answered "open pipeline this
quarter and next" with the skill's pinned query, $31.99M across 103 deals. The app built from
prompt 7, which also said "Follow @revops-conventions", showed $172.0M across 356 deals: its own
`open_deals` query, no quarter, deals 799 days in stage. Chat had the skill's SKILL.md in the turn
(#737); the Build never did. An approved plan builds from a fixed control prompt, and the turn's
@-named skills were read from that prompt, so the person's own sentence — carried in the Build
intent's source requests — named nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator import brand, plan_resources
from sage.router.models import Mode

from .fake_opencode import Turn
from .test_an_approved_plan_runs_as_implement import PLAN
from .test_an_at_named_skill_reaches_the_model_and_the_turn_says_so import (
    RULE,
    _ShimOpenCode,
    _skill,
    _user_text,
)
from .test_no_edit_recovery_uses_the_active_stack import (
    _no_waiting,  # noqa: F401  (autouse)
)
from .test_phased_build import PHASED_PLAN
from .test_turn_path import _build

ROOT = Path(__file__).resolve().parents[2]


def _writes(rel: str) -> Turn:
    return Turn(writes={rel: f"// {rel}\nexport const x = 1;\n"})


def _approved(tmp_path: Path, request: str, *, phased: bool):
    plan, builds = ((Turn(text=PHASED_PLAN), [_writes("src/data.ts"), _writes("src/Table.tsx"),
                                              _writes("src/Filter.tsx")])
                    if phased else (PLAN, [_writes("src/App.tsx")]))
    orch, oc, _ = _build(tmp_path, [plan, *builds], fake=_ShimOpenCode)
    orch.create_app(stack="react-vite")
    project = orch.project(start_preview=False)
    if phased:
        project.record.write_settings({"phased_build": True})
    orch.add_extension(_skill("alpha"))
    orch.add_extension(_skill("beta", "Beta rule."))
    oc.shim = project.shim
    project.control.set_mode(Mode.AUTO)
    list(orch.build_stream(request))
    planned = len(oc.outgoing)
    events = list(orch.approve_stream())
    return project, oc.outgoing[planned:], events


@pytest.mark.parametrize("phased", [False, True], ids=["whole", "phased"])
def test_an_approved_plan_builds_with_the_skill_its_request_named(tmp_path, phased):
    project, outgoing, events = _approved(
        tmp_path, "Build me a pipeline dashboard. Follow @alpha.", phased=phased)
    assert outgoing, "the approval built nothing"
    assert all(RULE in _user_text(r) for r in outgoing)
    assert not any("Beta rule." in _user_text(r) for r in outgoing)
    [done] = [e for e in events if e.get("type") == "done"]
    assert [(s["name"], s["sent"]) for s in done.get("skills") or []] == [("alpha", True)]
    assert project.control.skills_sent() == []


def test_an_approved_plan_whose_request_named_no_skill_gives_none(tmp_path):
    _, outgoing, events = _approved(tmp_path, "Build me a pipeline dashboard.", phased=False)
    assert outgoing and not any("<project_skill" in _user_text(r) for r in outgoing)
    [done] = [e for e in events if e.get("type") == "done"]
    assert "skills" not in done


# ---- what the Build and the planner are told ---------------------------------------------------

@pytest.mark.parametrize("stack", ["fastapi-antd", "react-vite"])
def test_the_build_prompt_says_a_skills_measure_is_binding(stack):
    text = " ".join((ROOT / "template" / stack / "AGENTS.md").read_text().split())
    common = text.split("sage:build-profile:v1:common:end")[0]
    assert "A measure, query or default scope that a {project} skill defines is binding" in common
    assert "uses the skill's pinned query or definition" in common
    assert "opens on the skill's default scope" in common


def test_the_planner_is_told_a_skills_measure_is_binding():
    note = plan_resources.planner_note(plan_resources.Resources(skills=("revops-conventions",)))
    assert brand.apply_voice("- {project} skill `revops-conventions`") in note
    assert "defines is binding: a step that shows one names the skill" in note
    alone = plan_resources.planner_note(plan_resources.Resources(secrets=("KEY",)))
    assert "binding" not in alone
    assert plan_resources.planner_note(plan_resources.Resources()) == ""


def test_the_planner_lists_only_the_skills_switched_on_for_the_app(tmp_path):
    orch, _oc, _ = _build(tmp_path, [])
    orch.create_app(stack="react-vite")
    project = orch.project(start_preview=False)
    orch.add_extension(_skill("alpha"))
    orch.add_extension(_skill("beta", "Beta rule."))
    assert orch._plan_resources(project).skills == ("alpha", "beta")
    orch.set_extension_enabled("skill:beta", False, app=project.app_for_turn().app_id)
    assert orch._plan_resources(project).skills == ("alpha",)
