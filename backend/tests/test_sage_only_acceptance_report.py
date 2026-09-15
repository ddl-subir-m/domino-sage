"""#361 acceptance evidence stays tied to the Sage-only release scope."""

import json
from pathlib import Path


REPORT = Path(__file__).resolve().parents[2] / "docs/performance/2026-09-14-sage-only-acceptance.json"


def _report():
    return json.loads(REPORT.read_text())


def test_acceptance_report_keeps_the_sage_only_scope_and_limits():
    data = _report()
    focused = data["focused_test_run"]
    assert data["release_scope"] == "sage-only"
    assert data["gateway_policy_mutation"] == "none"
    assert data["production_rollout"] == "not_performed"
    assert focused["collected"] == focused["passed"] == 178
    assert focused["failures"] == []
    assert set(data["deferred"]) == {"gateway #32", "gateway #33", "gateway #34", "sage #360"}
    assert data["ready_for_landing"] is True
    assert "serving_model_when_absent" in data["unknown_evidence_preserved"]
    assert any("not gateway-internal policy coverage" in limit for limit in data["limits"])


def test_acceptance_report_has_required_controlled_evidence():
    checks = {row["name"]: row for row in _report()["controlled_acceptance"]}
    required = {
        "real_opencode_sales_chat_and_build",
        "real_opencode_message_paths",
        "semantic_coverage",
        "new_built_app_runtime",
        "browser_runtime",
        "new_built_app_queries",
    }
    assert required <= set(checks)
    assert "email column absent from the second gateway request" in checks[
        "real_opencode_sales_chat_and_build"]["evidence"]
    assert any("10,000" in item for item in checks["semantic_coverage"]["evidence"])
    assert any("without rebuild" in item for item in checks["new_built_app_runtime"]["evidence"])


def test_acceptance_report_has_repeated_live_model_probe_for_all_required_models():
    probe = _report()["live_gateway_probe"]
    assert probe["rounds_per_shape_per_model"] == 2
    assert set(probe["models"]) == {"sonnet", "gemini-3.7-flash", "Gemma 4 31B"}
    seen = {(row["shape"], row["model"]) for row in probe["summary"]}
    assert seen == {(shape, model) for shape in probe["shapes"] for model in probe["models"]}
    assert all(row["rounds"] == 2 and row["ok"] is True for row in probe["summary"])
    assert all(row["request_bytes"] > 0 and row["total_s_avg"] >= row["first_output_s_avg"] > 0
               for row in probe["summary"])
