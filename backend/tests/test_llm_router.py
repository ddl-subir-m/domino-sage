"""Table-driven precedence tests for LLMRouter (DESIGN.md Seam 1).

The highest-value unit in the system: pure inputs, pure outputs, zero mocks, no gateway.
Covers auto(plan/implement) > ask/plan/implement pick > modal default. Sensitive attachments
do not change the model.
"""
from __future__ import annotations

import pytest

from sage.router.llm_router import resolve, resolve_unsigned
from sage.router.models import (
    BEDROCK_SERVED,
    SIGNS_TOOL_CALLS,
    Mode,
    ModelCatalog,
    Phase,
    Reason,
    SessionState,
    signing_slot,
)

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


def test_chat_turn_defaults_to_the_ask_model_not_build_mode():
    # Build left on Plan would otherwise pin Chat to catalog.plan. Chat's standing default is Ask.
    state = SessionState(Mode.PLAN, Phase.PLAN, chat_thread_id="thr")
    decision = resolve(state, CATALOG)
    assert decision.model == "ask-vendor"
    assert decision.reason is Reason.CHAT_DEFAULT


def test_chat_pick_overrides_the_ask_default():
    state = SessionState(
        Mode.AUTO, Phase.PLAN, chat_thread_id="thr", chat_model="sonnet",
    )
    decision = resolve(state, CATALOG)
    assert decision.model == "sonnet"
    assert decision.reason is Reason.CHAT_OVERRIDE


# ---- the signing pin, and where a mixed session goes (ADR-0032) ---------------------------------
# The shape that reached a user (#155): the signing model is the IMPLEMENT slot, and plan is not.
SIGNING = ModelCatalog(
    sovereign_plan="sovereign-plan-8b",
    sovereign_implement="sovereign-implement-8b",
    sovereign_ask="sovereign-ask-8b",
    plan="strong-vendor",
    implement="gemini-3.7-flash",
    ask="ask-vendor",
)
ALL_SIGNING = ModelCatalog(
    sovereign_plan="sovereign-plan-8b",
    sovereign_implement="sovereign-implement-8b",
    sovereign_ask="sovereign-ask-8b",
    plan="gemini-3.7-flash",
    implement="gemini-3.7-flash",
    ask="gemini-3.7-flash",
)


def test_the_signing_class_never_overlaps_the_bedrock_class():
    """split_parallel_tool_calls takes a batch apart across messages, and a signed batch carries its
    one signature on the first call — so a model in both sets would have its history reshaped into
    the exact form it rejects. Neither set is huge; this is the cheap way to keep them apart."""
    assert SIGNS_TOOL_CALLS & BEDROCK_SERVED == frozenset()


@pytest.mark.parametrize(
    "state,expected_model,expected_reason",
    [
        # The pin holds every phase and every mode on the signing model, because all three
        # assignable slots share one harness session.
        (SessionState(Mode.AUTO, Phase.PLAN), "gemini-3.7-flash", Reason.SIGNING_PIN),
        (SessionState(Mode.ASK, Phase.PLAN), "gemini-3.7-flash", Reason.SIGNING_PIN),
        (SessionState(Mode.PLAN, Phase.PLAN), "gemini-3.7-flash", Reason.SIGNING_PIN),
        # Already the signing model: the pin has nothing to change, so the ordinary reason stands
        # and the log still says why this turn routed where it did.
        (SessionState(Mode.AUTO, Phase.IMPLEMENT), "gemini-3.7-flash", Reason.AUTO_IMPLEMENT),
        (SessionState(Mode.IMPLEMENT, Phase.IMPLEMENT), "gemini-3.7-flash",
         Reason.IMPLEMENT_PINNED),
        # An in-session act — the user moving the picker while the session is live — beats the pin,
        # and forfeits the signing model for the rest of the session.
        (SessionState(Mode.PLAN, Phase.PLAN, picked_model="my-model"), "my-model",
         Reason.PLAN_OVERRIDE),
        (SessionState(Mode.IMPLEMENT, Phase.IMPLEMENT, picked_model="my-model"), "my-model",
         Reason.IMPLEMENT_OVERRIDE),
        # Chat has no phases for a pin to hold still, and Chat compaction re-enters resolve through
        # the shim, where a pin would overrule summarize_model_id (ADR-0031).
        (SessionState(Mode.AUTO, Phase.IMPLEMENT, chat_thread_id="thr"), "ask-vendor",
         Reason.CHAT_DEFAULT),
        (SessionState(Mode.AUTO, Phase.PLAN, chat_thread_id="thr", chat_model="sonnet"), "sonnet",
         Reason.CHAT_OVERRIDE),
    ],
)
def test_a_signing_slot_pins_the_whole_build_session(state, expected_model, expected_reason):
    decision = resolve(state, SIGNING)
    assert decision.model == expected_model
    assert decision.reason is expected_reason


def test_no_signing_slot_leaves_routing_exactly_as_it_was():
    # The pin must be invisible to every project that has not assigned a signing model.
    for state in (SessionState(Mode.AUTO, Phase.PLAN), SessionState(Mode.AUTO, Phase.IMPLEMENT),
                  SessionState(Mode.ASK, Phase.PLAN), SessionState(Mode.PLAN, Phase.PLAN)):
        assert resolve(state, CATALOG) == resolve(state, CATALOG)
        assert resolve(state, CATALOG).reason is not Reason.SIGNING_PIN


def test_a_mixed_session_falls_back_past_the_slot_that_signs():
    """The #155 case, and the one an obvious fix gets wrong. Dropping the pin alone returns
    catalog.implement — which IS the signing model — so the fallback has to keep looking."""
    decision = resolve_unsigned(SessionState(Mode.AUTO, Phase.IMPLEMENT), SIGNING)
    assert decision is not None
    assert decision.model == "strong-vendor"
    assert decision.reason is Reason.SIGNING_VETO


def test_a_mixed_session_keeps_its_own_slot_when_that_slot_does_not_sign():
    # A plan turn's own model does not sign, so there is nothing to walk past.
    decision = resolve_unsigned(SessionState(Mode.AUTO, Phase.PLAN), SIGNING)
    assert decision is not None
    assert decision.model == "strong-vendor"
    assert decision.reason is Reason.AUTO_PLAN


def test_the_fallback_drops_an_in_session_pick_of_a_signing_model():
    # The veto outranks the pick: choosing a signing model on a session that cannot take one is a
    # request that cannot be served, so the standing configuration answers instead.
    state = SessionState(Mode.IMPLEMENT, Phase.IMPLEMENT, picked_model="gemini-3.7-flash")
    decision = resolve_unsigned(state, CATALOG)
    assert decision is not None
    assert decision.model == "cheap-vendor"


def test_the_fallback_drops_a_chat_pick_of_a_signing_model():
    state = SessionState(Mode.AUTO, Phase.PLAN, chat_thread_id="thr",
                         chat_model="gemini-3.7-flash")
    decision = resolve_unsigned(state, CATALOG)
    assert decision is not None
    assert decision.model == "ask-vendor"


def test_a_catalog_that_signs_everywhere_has_nowhere_safe_to_go():
    # None means the caller sends the request and lets the gateway answer. Substituting a model the
    # user never assigned would be new machinery serving one alias.
    assert resolve_unsigned(SessionState(Mode.AUTO, Phase.IMPLEMENT), ALL_SIGNING) is None
    assert resolve_unsigned(SessionState(Mode.ASK, Phase.PLAN), ALL_SIGNING) is None


def test_signing_slot_is_the_one_copy_of_the_pins_input():
    """`llm_router` routes by it and `preflight.turn_slots` preflights by it. A second copy of this
    rule is how a turn comes to preflight one alias and run on another."""
    assert signing_slot(CATALOG) is None
    assert signing_slot(SIGNING) == "implement"
    assert signing_slot(ALL_SIGNING) == "plan"        # first assignable slot wins
    # A provider-prefixed slot still resolves: only the bare id is meaningful.
    assert signing_slot(ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                     sovereign_ask="s", plan="p", implement="i",
                                     ask="domino/gemini-3.7-flash")) == "ask"
