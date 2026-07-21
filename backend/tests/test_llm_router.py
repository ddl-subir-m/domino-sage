"""Table-driven precedence tests for LLMRouter (DESIGN.md Seam 1).

The highest-value unit in the system: pure inputs, pure outputs, zero mocks, no gateway.
Covers the full precedence matrix: sensitivity > auto(plan/implement) > ask/plan/implement pick > modal default.
"""
from __future__ import annotations

import pytest

from sage.router.llm_router import resolve
from sage.router.models import Mode, ModelCatalog, Phase, Reason, SessionState

CATALOG = ModelCatalog(
    sovereign_plan="sovereign-plan-8b",
    sovereign_implement="sovereign-implement-8b",
    sovereign_ask="sovereign-ask-8b",
    plan="strong-vendor",
    implement="cheap-vendor",
    ask="ask-vendor",
)


@pytest.mark.parametrize(
    "state,expected_model,expected_reason,expected_locked",
    [
        # 1. Sensitivity lock overrides everything except an explicit pick of a sovereign model.
        # A stale/vendor pick from before the lock triggered is ignored -> falls back to sovereign_ask.
        (SessionState(True, Mode.PLAN, Phase.PLAN, picked_model="strong-vendor"), "sovereign-ask-8b", Reason.SENSITIVITY, True),
        (SessionState(True, Mode.AUTO, Phase.IMPLEMENT), "sovereign-implement-8b", Reason.SENSITIVITY, True),
        # Auto mode ignores any picked_model entirely, even a valid sovereign one.
        (SessionState(True, Mode.AUTO, Phase.PLAN, picked_model="sovereign-implement-8b"), "sovereign-plan-8b", Reason.SENSITIVITY, True),
        # Plan/Implement modes honor an explicit sovereign pick, sticky regardless of current phase.
        (SessionState(True, Mode.PLAN, Phase.IMPLEMENT, picked_model="sovereign-plan-8b"), "sovereign-plan-8b", Reason.SENSITIVITY, True),
        (SessionState(True, Mode.IMPLEMENT, Phase.PLAN, picked_model="sovereign-implement-8b"), "sovereign-implement-8b", Reason.SENSITIVITY, True),
        # Ask mode never surfaces an override, even when locked with a stray picked_model.
        (SessionState(True, Mode.ASK, Phase.PLAN, picked_model="sovereign-plan-8b"), "sovereign-ask-8b", Reason.SENSITIVITY, True),
        # 2. Auto mode picks by phase.
        (SessionState(False, Mode.AUTO, Phase.PLAN), "strong-vendor", Reason.AUTO_PLAN, False),
        (SessionState(False, Mode.AUTO, Phase.IMPLEMENT), "cheap-vendor", Reason.AUTO_IMPLEMENT, False),
        # 3. Ask mode is always pinned to the ask model, no override.
        (SessionState(False, Mode.ASK, Phase.PLAN, picked_model="my-model"), "ask-vendor", Reason.ASK_PINNED, False),
        (SessionState(False, Mode.ASK, Phase.PLAN), "ask-vendor", Reason.ASK_PINNED, False),
        # 4. Plan mode: pinned to catalog.plan, overridable by an explicit pick.
        (SessionState(False, Mode.PLAN, Phase.PLAN), "strong-vendor", Reason.PLAN_PINNED, False),
        (SessionState(False, Mode.PLAN, Phase.PLAN, picked_model="my-model"), "my-model", Reason.PLAN_OVERRIDE, False),
        # 5. Implement mode: pinned to catalog.implement, overridable by an explicit pick.
        (SessionState(False, Mode.IMPLEMENT, Phase.IMPLEMENT), "cheap-vendor", Reason.IMPLEMENT_PINNED, False),
        (SessionState(False, Mode.IMPLEMENT, Phase.IMPLEMENT, picked_model="my-model"), "my-model", Reason.IMPLEMENT_OVERRIDE, False),
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
        SessionState(False, Mode.ASK, Phase.PLAN),
        SessionState(False, Mode.PLAN, Phase.PLAN, picked_model="x"),
        SessionState(False, Mode.IMPLEMENT, Phase.IMPLEMENT, picked_model="x"),
    ]:
        assert resolve(state, CATALOG).locked is False
    assert resolve(SessionState(True, Mode.AUTO, Phase.PLAN), CATALOG).locked is True
