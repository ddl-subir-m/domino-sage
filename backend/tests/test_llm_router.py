"""Table-driven precedence tests for LLMRouter (DESIGN.md Seam 1).

The highest-value unit in the system: pure inputs, pure outputs, zero mocks, no gateway.
Covers auto(plan/implement) > ask/plan/implement pick > modal default. Sensitive attachments
do not change the model.
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
    "state,expected_model,expected_reason",
    [
        # 1. Auto mode picks by phase.
        (SessionState(Mode.AUTO, Phase.PLAN), "strong-vendor", Reason.AUTO_PLAN),
        (SessionState(Mode.AUTO, Phase.IMPLEMENT), "cheap-vendor", Reason.AUTO_IMPLEMENT),
        # 2. Ask mode is always pinned to the ask model, no override.
        (SessionState(Mode.ASK, Phase.PLAN, picked_model="my-model"), "ask-vendor", Reason.ASK_PINNED),
        (SessionState(Mode.ASK, Phase.PLAN), "ask-vendor", Reason.ASK_PINNED),
        # 3. Plan mode: pinned to catalog.plan, overridable by an explicit pick.
        (SessionState(Mode.PLAN, Phase.PLAN), "strong-vendor", Reason.PLAN_PINNED),
        (SessionState(Mode.PLAN, Phase.PLAN, picked_model="my-model"), "my-model", Reason.PLAN_OVERRIDE),
        # 4. Implement mode: pinned to catalog.implement, overridable by an explicit pick.
        (SessionState(Mode.IMPLEMENT, Phase.IMPLEMENT), "cheap-vendor", Reason.IMPLEMENT_PINNED),
        (SessionState(Mode.IMPLEMENT, Phase.IMPLEMENT, picked_model="my-model"), "my-model", Reason.IMPLEMENT_OVERRIDE),
    ],
)
def test_resolve(state, expected_model, expected_reason):
    decision = resolve(state, CATALOG)
    assert decision.model == expected_model
    assert decision.reason is expected_reason
    assert decision.locked is False


def test_chat_turn_does_not_inherit_build_mode():
    # Build left on Ask would otherwise pin Chat to catalog.ask.
    state = SessionState(Mode.ASK, Phase.PLAN, chat_thread_id="thr")
    decision = resolve(state, CATALOG)
    assert decision.model == "strong-vendor"
    assert decision.reason is Reason.CHAT_AUTO


def test_chat_pick_overrides_auto():
    state = SessionState(
        Mode.AUTO, Phase.PLAN, chat_thread_id="thr", chat_model="sonnet",
    )
    decision = resolve(state, CATALOG)
    assert decision.model == "sonnet"
    assert decision.reason is Reason.CHAT_OVERRIDE
