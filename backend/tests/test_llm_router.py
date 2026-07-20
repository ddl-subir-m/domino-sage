"""Table-driven precedence tests for LLMRouter (DESIGN.md Seam 1).

The highest-value unit in the system: pure inputs, pure outputs, zero mocks, no gateway.
Covers the full precedence matrix: sensitivity > auto(plan/implement) > manual pick > modal default.
"""
from __future__ import annotations

import pytest

from sage.router.llm_router import resolve
from sage.router.models import Mode, ModelCatalog, Phase, Reason, SessionState

CATALOG = ModelCatalog(sovereign="sovereign-8b", plan="strong-vendor", implement="cheap-vendor", default="default-vendor")


@pytest.mark.parametrize(
    "state,expected_model,expected_reason,expected_locked",
    [
        # 1. Sensitivity lock overrides everything, including an explicit pick.
        (SessionState(True, Mode.MANUAL, Phase.PLAN, picked_model="strong-vendor"), "sovereign-8b", Reason.SENSITIVITY, True),
        (SessionState(True, Mode.AUTO, Phase.IMPLEMENT), "sovereign-8b", Reason.SENSITIVITY, True),
        # 2. Auto mode picks by phase.
        (SessionState(False, Mode.AUTO, Phase.PLAN), "strong-vendor", Reason.AUTO_PLAN, False),
        (SessionState(False, Mode.AUTO, Phase.IMPLEMENT), "cheap-vendor", Reason.AUTO_IMPLEMENT, False),
        # 3. Manual mode honors an explicit pick.
        (SessionState(False, Mode.MANUAL, Phase.PLAN, picked_model="my-model"), "my-model", Reason.MANUAL, False),
        # 4. Manual with no pick -> modal default.
        (SessionState(False, Mode.MANUAL, Phase.PLAN), "default-vendor", Reason.MODAL_DEFAULT, False),
        (SessionState(False, Mode.MANUAL, Phase.IMPLEMENT), "default-vendor", Reason.MODAL_DEFAULT, False),
    ],
)
def test_resolve(state, expected_model, expected_reason, expected_locked):
    decision = resolve(state, CATALOG)
    assert decision.model == expected_model
    assert decision.reason is expected_reason
    assert decision.locked is expected_locked


def test_sensitivity_is_only_locked_decision():
    """Only the sensitivity path is non-overridable."""
    for state in [
        SessionState(False, Mode.AUTO, Phase.PLAN),
        SessionState(False, Mode.MANUAL, Phase.PLAN, picked_model="x"),
    ]:
        assert resolve(state, CATALOG).locked is False
    assert resolve(SessionState(True, Mode.AUTO, Phase.PLAN), CATALOG).locked is True
