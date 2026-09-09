"""Table-driven precedence tests for LLMRouter (DESIGN.md Seam 1).

The highest-value unit in the system: pure inputs, pure outputs, zero mocks, no gateway.
Covers auto(plan/implement) > ask/plan/implement pick > modal default, and the sensitivity lock
(ADR-0043) that sits over all of it.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from sage.router.llm_router import nearest_approved, resolve, resolve_unsigned
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


# --- The sensitivity lock (ADR-0043) -------------------------------------------------------------
#
# `approved_models` is the whole input: None is no lock, a frozenset is the set of aliases an
# administrator approved for sensitive work. These assert the one property the promise rests on —
# no branch of the router can leave that set — rather than re-testing precedence under a lock.

APPROVED = frozenset({"sovereign-plan-8b", "sovereign-implement-8b", "sovereign-ask-8b"})


@pytest.mark.parametrize(
    "state,expected_model",
    [
        # Auto follows the phase, within the approved set.
        (SessionState(Mode.AUTO, Phase.PLAN, approved_models=APPROVED), "sovereign-plan-8b"),
        (SessionState(Mode.AUTO, Phase.IMPLEMENT, approved_models=APPROVED), "sovereign-implement-8b"),
        # Ask has one sovereign slot, and the user's pick never reached it anyway.
        (SessionState(Mode.ASK, Phase.PLAN, approved_models=APPROVED), "sovereign-ask-8b"),
        # An explicit pick loses to the lock — this is the case the promise is made of.
        (SessionState(Mode.PLAN, Phase.PLAN, picked_model="strong-vendor", approved_models=APPROVED),
         "sovereign-plan-8b"),
        (SessionState(Mode.IMPLEMENT, Phase.IMPLEMENT, picked_model="cheap-vendor",
                      approved_models=APPROVED), "sovereign-implement-8b"),
        # Chat is not a second door: the lock is applied outside the fork, so it lands here too.
        (SessionState(Mode.AUTO, Phase.PLAN, chat_thread_id="t1", chat_model="strong-vendor",
                      approved_models=APPROVED), "sovereign-ask-8b"),
        (SessionState(Mode.AUTO, Phase.PLAN, chat_thread_id="t1", approved_models=APPROVED),
         "sovereign-ask-8b"),
    ],
)
def test_the_lock_never_leaves_the_approved_set(state, expected_model):
    decision = resolve(state, CATALOG)
    assert decision.model == expected_model
    assert decision.model in APPROVED
    assert decision.reason is Reason.SENSITIVITY
    assert decision.locked is True


def test_an_already_approved_model_is_kept_and_marked_locked():
    """The lock narrows; it does not reshuffle. A turn already on an approved model stays there."""
    state = SessionState(Mode.PLAN, Phase.PLAN, picked_model="sovereign-ask-8b",
                         approved_models=APPROVED)
    decision = resolve(state, CATALOG)
    assert decision.model == "sovereign-ask-8b"
    assert decision.reason is Reason.PLAN_OVERRIDE  # the original reason survives
    assert decision.locked is True


def test_no_lock_leaves_every_decision_untouched():
    """approved_models=None is the default, and must be a no-op for a deployment that never opted in."""
    for state in (
        SessionState(Mode.AUTO, Phase.PLAN),
        SessionState(Mode.IMPLEMENT, Phase.IMPLEMENT, picked_model="cheap-vendor"),
        SessionState(Mode.AUTO, Phase.PLAN, chat_thread_id="t1", chat_model="strong-vendor"),
    ):
        assert resolve(state, CATALOG).locked is False


def test_the_lock_outranks_the_signing_pin():
    """A 400 is an outage; a leak is not recoverable. When they disagree, the lock wins (ADR-0043)."""
    signing = next(iter(SIGNS_TOOL_CALLS))
    catalog = ModelCatalog(
        sovereign_plan="sovereign-plan-8b", sovereign_implement="sovereign-implement-8b",
        sovereign_ask="sovereign-ask-8b", plan=signing, implement="cheap-vendor", ask="ask-vendor",
    )
    assert signing_slot(catalog) == "plan"  # the pin would otherwise take this session
    decision = resolve(SessionState(Mode.AUTO, Phase.IMPLEMENT, approved_models=APPROVED), catalog)
    assert decision.model == "sovereign-implement-8b"
    assert decision.reason is Reason.SENSITIVITY


def test_an_unapproved_sovereign_slot_falls_back_deterministically():
    """No sovereign slot approved and no ordering to read: any approved alias beats refusing, and
    the pick must not wobble."""
    approved = frozenset({"zeta-approved", "alpha-approved"})
    state = SessionState(Mode.AUTO, Phase.PLAN, approved_models=approved)
    assert resolve(state, CATALOG).model == "alpha-approved"
    assert resolve(state, CATALOG).model == "alpha-approved"


def test_the_administrators_ordering_decides_before_the_alphabet():
    """The group is a list somebody wrote in an order. `min()` would answer with the alphabet and
    call it a decision; the ordering is the only expression of preference in the input."""
    order = ("zeta-approved", "alpha-approved")
    state = SessionState(Mode.AUTO, Phase.PLAN,
                         approved_models=frozenset(order), approved_order=order)
    assert resolve(state, CATALOG).model == "zeta-approved"


def test_a_sovereign_slot_still_outranks_the_administrators_ordering():
    """The ordering is a preference about a list of models; a sovereign slot is an assignment made
    for THIS Sage, already preflighted. The narrower statement wins."""
    order = ("zeta-approved", "sovereign-plan-8b")
    state = SessionState(Mode.AUTO, Phase.PLAN,
                         approved_models=frozenset(order), approved_order=order)
    assert resolve(state, CATALOG).model == "sovereign-plan-8b"


def test_an_ordering_that_names_an_unapproved_model_is_skipped():
    """The order is a preference, never the authority — the set is what decides."""
    state = SessionState(Mode.AUTO, Phase.PLAN,
                         approved_models=frozenset({"zeta-approved"}),
                         approved_order=("gone-from-the-group", "zeta-approved"))
    assert resolve(state, CATALOG).model == "zeta-approved"


def test_nearest_approved_answers_where_a_barred_turn_moves_and_reads_no_pick():
    """The public half, for a label that has to name the fallback (ADR-0043). Deliberately NOT
    `resolve`: that returns an approved pick unchanged, so a caller holding its answer would go on
    naming the old pick after somebody picked a barred model. This reads no pick at all."""
    order = ("zeta-approved", "alpha-approved")
    base = SessionState(Mode.AUTO, Phase.PLAN,
                        approved_models=frozenset(order), approved_order=order)
    assert nearest_approved(base, CATALOG) == "zeta-approved"
    for pick in ("strong-vendor", "alpha-approved", None):
        assert nearest_approved(replace(base, picked_model=pick), CATALOG) == "zeta-approved"


def test_nearest_approved_follows_the_mode_the_way_the_lock_does():
    """It is the same `_lock_preferences`, so an Ask turn and a Chat turn prefer the sovereign Ask
    slot. That is why the state carries an answer for Build and one for Chat."""
    assert nearest_approved(SessionState(Mode.AUTO, Phase.IMPLEMENT, approved_models=APPROVED),
                            CATALOG) == "sovereign-implement-8b"
    assert nearest_approved(SessionState(Mode.AUTO, Phase.IMPLEMENT, chat_thread_id="t1",
                                         approved_models=APPROVED), CATALOG) == "sovereign-ask-8b"


def test_nearest_approved_raises_with_nothing_approved():
    """Asking where a lock moves a turn to, when nothing is approved, has no answer that is not a
    refusal — the same edge `_lock_sensitivity` keeps."""
    for state in (SessionState(Mode.AUTO, Phase.PLAN, approved_models=frozenset()),
                  SessionState(Mode.AUTO, Phase.PLAN)):
        with pytest.raises(ValueError, match="empty approved set"):
            nearest_approved(state, CATALOG)


def test_an_empty_approved_set_raises_rather_than_failing_open():
    """The orchestrator refuses this turn before it starts. If one ever arrives, crash — do not
    hand back the vendor model the lock exists to refuse."""
    state = SessionState(Mode.AUTO, Phase.PLAN, approved_models=frozenset())
    with pytest.raises(ValueError, match="empty approved set"):
        resolve(state, CATALOG)
