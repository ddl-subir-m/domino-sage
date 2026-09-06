"""LLMRouter — the pure model-policy function (DESIGN.md Seam 1).

resolve(state, catalog) -> ModelDecision

Precedence (highest first), per SPEC.md Component 3 minus the old sensitivity lock:
    1. auto mode         -> plan model in plan phase, implement model in implement phase
    2. ask mode          -> catalog.ask (never overridable; read-only is enforced by the shim)
    3. plan mode         -> user's picked model if set, else catalog.plan
    4. implement mode    -> user's picked model if set, else catalog.implement

A Chat turn (chat_thread_id set) is separate: the Chat pick, else catalog.ask.
Build Auto/Ask/Plan/Implement does not apply. Sensitive attachments do not change the model —
the caller uses any LLM alias they can reach on the gateway.

Over all of that sits the signing pin (ADR-0032). A model that signs its tool calls cannot be
mixed with one that does not inside a single harness session, so if any assignable slot signs, a
Build turn resolves to it whatever the phase or mode says. The pin loses to an in-session act (the
user moving the picker mid-session) and does not apply to Chat, which has no phases to hold still.

This is the single highest-value unit under test: pure inputs, pure output, zero mocks.
"""
from __future__ import annotations

from dataclasses import replace

from .models import (
    ASSIGNABLE_SLOTS,
    Mode,
    ModelCatalog,
    ModelDecision,
    Phase,
    Reason,
    SessionState,
    signing_slot,
    signs,
)


def resolve(state: SessionState, catalog: ModelCatalog) -> ModelDecision:
    # Chat is a Workbench mode, not a ModelControl mode. A Chat turn still has to pick a real
    # gateway alias; the standing Build Auto/Ask/Plan/Implement choice must not leak into it.
    if state.chat_thread_id:
        return _resolve_chat(state, catalog)
    return _pin_signing(_resolve_build(state, catalog), catalog)


def resolve_unsigned(state: SessionState, catalog: ModelCatalog) -> ModelDecision | None:
    """The best NON-signing model for a session that can no longer take a signing one (ADR-0032).

    Called when the history already holds unsigned tool calls, which makes a signing model a hard
    400 on the whole request. Two candidates, in order: the model this turn would have had if no
    signing model were assigned at all, and then any other assignable slot that does not sign. The
    second is not a nicety — in the shape that reached a user (#155) the signing model IS the
    implement slot, so the first candidate is the very model being refused.

    The user's own in-session pick is dropped along with the pin, because the veto outranks both:
    picking a signing model on a session that cannot take one is a request that cannot be served.

    None when every assignable slot signs. There is then nowhere safe to go, and the caller's least
    bad option is to send the request and let the gateway say so.
    """
    if state.chat_thread_id:
        base = _resolve_chat(replace(state, chat_model=None), catalog)
    else:
        base = _resolve_build(replace(state, picked_model=None), catalog)
    if not signs(base.model):
        return base
    for slot in ASSIGNABLE_SLOTS:
        model = getattr(catalog, slot)
        if not signs(model):
            return ModelDecision(model=model, reason=Reason.SIGNING_VETO, locked=False)
    return None


def _pin_signing(decision: ModelDecision, catalog: ModelCatalog) -> ModelDecision:
    """Hold a Build session on the signing model, if any slot it could reach names one (ADR-0032).

    The slot set is every ASSIGNABLE_SLOT, not just the phase's own: all three share one harness
    session, an Ask turn makes tool calls too (read tools survive READ_ONLY_DENIED), and the user
    may switch mode between any two turns. So one signing assignment in any slot makes the whole
    Build session single-model. That is the price of the invariant, and it is deliberate — an
    explicit assignment outranks Auto's automatic phase switch.
    """
    # An in-session act beats the pin: the OVERRIDE reasons are exactly the user moving the picker
    # while the session is live, and honouring it forfeits the signing model for the rest of the
    # session (the shim's veto then keeps the aftermath correct, rather than 400ing).
    if decision.reason in (Reason.PLAN_OVERRIDE, Reason.IMPLEMENT_OVERRIDE):
        return decision
    slot = signing_slot(catalog)
    if slot is None:
        return decision
    model = getattr(catalog, slot)
    if model == decision.model:
        return decision
    return ModelDecision(model=model, reason=Reason.SIGNING_PIN, locked=False)


def _resolve_build(state: SessionState, catalog: ModelCatalog) -> ModelDecision:
    # 1. Auto mode: the pipeline drives model choice by phase.
    if state.mode is Mode.AUTO:
        if state.phase is Phase.PLAN:
            return ModelDecision(model=catalog.plan, reason=Reason.AUTO_PLAN, locked=False)
        return ModelDecision(model=catalog.implement, reason=Reason.AUTO_IMPLEMENT, locked=False)

    # 2. Ask mode: pinned to the ask model. Read-only is enforced by the shim, not routing.
    if state.mode is Mode.ASK:
        return ModelDecision(model=catalog.ask, reason=Reason.ASK_PINNED, locked=False)

    # 3. Plan mode: pinned to the plan model, overridable by an explicit pick.
    if state.mode is Mode.PLAN:
        if state.picked_model is not None:
            return ModelDecision(model=state.picked_model, reason=Reason.PLAN_OVERRIDE, locked=False)
        return ModelDecision(model=catalog.plan, reason=Reason.PLAN_PINNED, locked=False)

    # 4. Implement mode: pinned to the implement model, overridable by an explicit pick.
    if state.picked_model is not None:
        return ModelDecision(model=state.picked_model, reason=Reason.IMPLEMENT_OVERRIDE, locked=False)
    return ModelDecision(model=catalog.implement, reason=Reason.IMPLEMENT_PINNED, locked=False)


def _resolve_chat(state: SessionState, catalog: ModelCatalog) -> ModelDecision:
    if state.chat_model:
        return ModelDecision(model=state.chat_model, reason=Reason.CHAT_OVERRIDE, locked=False)
    return ModelDecision(model=catalog.ask, reason=Reason.CHAT_DEFAULT, locked=False)
