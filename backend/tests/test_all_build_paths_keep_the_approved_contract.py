"""An approved plan keeps its full acceptance criteria through review and phased execution."""
from __future__ import annotations

import json
from pathlib import Path

from .fake_opencode import Turn
from .test_a_dead_alias_stops_the_turn_before_it_starts import _no_waiting  # noqa: F401
from .test_a_planned_build_is_reviewed_against_its_done_when import (
    BUILT,
    PLAN,
    ReviewGateway,
    _approve,
    _done,
    _orch,
    _review_text,
)
from .test_an_at_named_skill_reaches_the_model_and_the_turn_says_so import RULE, _skill
from .test_phased_build import PHASED_PLAN, _build, _writes


def test_review_keeps_plan_wide_requirements_that_no_step_repeats(tmp_path: Path):
    plan = Turn(text=PLAN.text.replace(
        "## Done when\n", "## Not doing\n- No duplicate activity sections.\n\n## Done when\n"
        "- Reference dates end before the current period starts.\n"))
    _, gateway, _ = _approve(tmp_path, '{"unmet": []}', plan=plan)

    text = _review_text(gateway.reviews[0])
    assert "Reference dates end before the current period starts." in text
    assert "No duplicate activity sections." in text


def test_phased_build_reviews_the_whole_app_and_keeps_an_unmet_step_failed(tmp_path: Path):
    turns = [Turn(text=PHASED_PLAN), _writes("src/data.ts"), _writes("src/Table.tsx"),
             _writes("src/Filter.tsx"), Turn(writes={"src/Table.tsx": "export const x = 2;\n"})]
    orch, oc, project = _build(tmp_path, turns)
    unmet = {"unmet": [{"step": 2, "done_when": "The preview shows a sortable table.",
                        "file": "src/Table.tsx", "why": "The table has no sort control."}]}
    gateway = ReviewGateway(json.dumps(unmet))
    project.shim._gateway = gateway
    list(orch.build_stream("build me a trades dashboard"))
    events = list(orch.approve_stream())

    assert len(gateway.reviews) == 2
    assert "src/data.ts" in _review_text(gateway.reviews[0])
    assert "src/Table.tsx" in _review_text(gateway.reviews[0])
    assert "src/Filter.tsx" in _review_text(gateway.reviews[0])
    assert _done(events)["ok"] is False
    assert "step 2" in _done(events)["decision"]
    assert any(e["type"] == "plan-unbuilt" for e in events)
    assert len(oc.prompts) == 5  # Three phases and exactly one repair after the plan.


def test_each_phase_keeps_the_plan_wide_defaults_and_scope(tmp_path: Path):
    plan = PHASED_PLAN.replace(
        "## Done when\n", "## Not doing\n- No duplicate activity sections.\n\n## Done when\n"
        "- Default currency is CAD.\n")
    orch, oc, project = _build(tmp_path, [
        Turn(text=plan), _writes("src/data.ts"), _writes("src/Table.tsx"),
        _writes("src/Filter.tsx")])
    list(orch.build_stream("build me a trades dashboard"))
    intents = []
    send = oc.send_prompt

    def capture(*args, **kwargs):
        intents.append(project.active_build_intent)
        return send(*args, **kwargs)

    oc.send_prompt = capture
    list(orch.approve_stream())

    assert len(intents) == 3
    for intent in intents:
        assert "Default currency is CAD." in intent.authoritative_plan
        assert "No duplicate activity sections." in intent.authoritative_plan
        assert "### 1. Data module" not in intent.authoritative_plan


def test_a_query_only_build_is_reviewed_without_sending_stored_rows(tmp_path: Path):
    plan = Turn(text=PLAN.text.replace("static/UsageDrift.js", ".sage/queries.json"))
    built = Turn(writes={".sage/queries.json": json.dumps([{
        "name": "drift_totals", "binding": "source",
        "sql": "SELECT COUNT(*) AS USERS FROM usage WHERE period = :period",
        "params": [{"name": "period", "type": "string", "default": "ROW-SENTINEL"}],
        "rows": [{"email": "ROW-SENTINEL"}],
    }])})
    gateway = ReviewGateway('{"unmet": []}')
    orch, _ = _orch(tmp_path, gateway, [plan, built])
    orch.project(start_preview=False).workspace.update_bindings(lambda _: [{
        "kind": "data_source", "id": "source", "name": "Warehouse", "display_name": "Warehouse",
        "database": "DWH", "schema": "PUBLIC", "connector_type": "SnowflakeConfig",
    }])
    list(orch.build_stream("Build the usage query.", conversation="c1"))
    list(orch.approve_stream(conversation="c1"))

    assert len(gateway.reviews) == 1
    text = _review_text(gateway.reviews[0])
    assert "SELECT COUNT(*) AS USERS FROM usage" in text
    assert "ROW-SENTINEL" not in text


def test_review_receives_only_the_skills_the_build_was_told_to_use(tmp_path: Path):
    gateway = ReviewGateway('{"unmet": []}')
    orch, _ = _orch(tmp_path, gateway, [PLAN, BUILT])
    orch.add_extension(_skill("alpha"))
    orch.add_extension(_skill("beta", "OTHER-SKILL-SENTINEL"))
    list(orch.build_stream("Build a usage drift tab. Follow @alpha.", conversation="c1"))
    list(orch.approve_stream(conversation="c1"))

    text = _review_text(gateway.reviews[0])
    assert RULE in text
    assert "OTHER-SKILL-SENTINEL" not in text
