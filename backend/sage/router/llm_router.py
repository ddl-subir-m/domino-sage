"""LLMRouter — the pure model-policy function (DESIGN.md Seam 1).

resolve(state, catalog) -> ModelDecision

Precedence (highest first), per SPEC.md Component 3:
    1. auto mode         -> plan model in plan phase, implement model in implement phase
    2. ask mode          -> catalog.ask (never overridable; read-only is enforced by the shim)
    3. plan mode         -> user's picked model if set, else catalog.plan
    4. implement mode    -> user's picked model if set, else catalog.implement

A Chat turn (chat_thread_id set) is separate: the Chat pick, else catalog.ask.
Build Auto/Ask/Plan/Implement does not apply.

Over ALL of that sits the sensitivity lock (ADR-0043). When a Dataset in scope is declared
sensitive, `state.approved_models` holds the aliases an administrator approved for sensitive work,
and no branch below may leave that set. It is applied once, outside the Chat/Build fork, so the two
harnesses cannot disagree and a third one added later inherits it without being asked to.

Under the lock sits the signing pin (ADR-0032). A model that signs its tool calls cannot be
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
        return _lock_sensitivity(_resolve_chat(state, catalog), state, catalog)
    return _lock_sensitivity(_pin_signing(_resolve_build(state, catalog), catalog), state, catalog)


def _lock_sensitivity(
    decision: ModelDecision, state: SessionState, catalog: ModelCatalog
) -> ModelDecision:
    """Hold this turn on a model approved for sensitive work (ADR-0043).

    Outranks everything below it, the signing pin included. The pin keeps a session on one model to
    avoid a hard 400; the lock keeps declared rows away from an unapproved vendor. A 400 is an
    outage and a leak is not recoverable, so when the two disagree the lock wins and the session may
    have to be started again on an approved model.

    Raises on an EMPTY approved set rather than returning the model it was asked to refuse. That set
    is the orchestrator's refusal to make before the turn starts (`no-approved-model-access`), and a
    router that quietly handed back a vendor model here would fail open at the one point the whole
    decision exists to fail closed.
    """
    approved = state.approved_models
    if approved is None:
        return decision
    if decision.model in approved:
        return replace(decision, locked=True)
    return ModelDecision(
        model=_nearest_approved(state, catalog, approved), reason=Reason.SENSITIVITY, locked=True
    )


def nearest_approved(state: SessionState, catalog: ModelCatalog) -> str:
    """Where the lock moves a turn whose model is barred (ADR-0043). Public so a label can say it.

    NOT the same question as `resolve`. `resolve` answers "what runs", which for an approved pick is
    the pick itself — so a caller that wanted the fallback and asked `resolve` gets the pick back and
    remembers it, and then names that stale model the moment the pick changes to a barred one. This
    reads no pick at all, which is why it is safe to hold across one.

    NOT the same question as `locked_runs_on` either, and #285 is what telling them apart costs:
    this one is the MOVE, and it never applies the signing pin, so a caller that wanted "what will
    this turn run" and asked here named the sovereign slot on a session the pin held on one model.
    Both callers are legitimate — a label consulted only once the turn's own model is already barred
    wants the move, and one drawn per slot with no turn in hand wants what runs.

    The mode and the phase it DOES read: `_lock_preferences` prefers the sovereign slot for them.

    Raises on an empty or absent approved set, for the reason `_lock_sensitivity` does — asking where
    a lock moves a turn to, when nothing is approved, has no answer that is not a refusal.
    """
    return _nearest_approved(state, catalog, state.approved_models or frozenset())


def locked_runs_on(state: SessionState, catalog: ModelCatalog) -> str:
    """What a turn under the lock RUNS, for a label with no turn in hand (ADR-0043, #285).

    `resolve`'s own chain with the pick dropped out of it, which is the whole difference between
    this and its two neighbours. `nearest_approved` answers where the lock MOVES a barred turn and
    never applies the signing pin — right for a chip consulted only once the turn's own model is
    already barred, wrong for the model panel, which draws all three slots with no turn in hand and
    has to say what each one will run. Under a held pin that is the signing model, on every row
    (ADR-0032), and the panel named the sovereign slot instead.

    Not `resolve` itself, for the reason `nearest_approved` records: `resolve` reads the pick, and
    the browser holds this answer across pick changes, so an answer that could BE the pick goes
    stale the moment somebody picks a barred model. Dropped HERE rather than by each caller, so
    there is one copy of what the label is allowed to read.

    Dropping the pick also drops the one rule that outranks the pin below the lock: an in-session
    act (`_pin_signing` returns a PLAN_OVERRIDE or IMPLEMENT_OVERRIDE untouched). So while a pick is
    live and barred, a turn goes where the lock MOVES it and this still names the pin's model. That
    is the same trade the pick-free answer has always made, one rule further down; closing it would
    put the pick in the browser's re-read list, which `store.js` keeps out on purpose.

    Raises on an empty or absent approved set, exactly as `nearest_approved` does.
    """
    # An absent approved set becomes an empty one, so the refusal below is the one
    # `nearest_approved` makes: `_lock_sensitivity` passes an unlocked state straight through, and a
    # label asking what a lock runs when there is no lock is a caller bug, not a model.
    state = replace(state, picked_model=None, chat_model=None,
                    approved_models=state.approved_models or frozenset())
    # `resolve`'s own fork, mirrored rather than flattened: the pin is a Build rule, because Chat has
    # no phases to hold still. Flattened, Chat's row would name a signing model it never runs.
    if state.chat_thread_id:
        decision = _resolve_chat(state, catalog)
    else:
        decision = _pin_signing(_resolve_build(state, catalog), catalog)
    # And the lock itself, not its two lines restated here — which would be this very defect told
    # about the rule one layer up.
    return _lock_sensitivity(decision, state, catalog).model


def _nearest_approved(
    state: SessionState, catalog: ModelCatalog, approved: frozenset[str]
) -> str:
    """The approved alias closest to what this turn asked for.

    The sovereign slots are the preference, not the authority: they are already assignable and
    preflighted, so an administrator who set them gets the model they meant, while the approved set
    from the gateway group is still what decides.

    When no sovereign slot is approved, the administrator's own ordering of the group decides
    (`state.approved_order`, straight off /api/alias-groups). It comes second and not first because
    a sovereign slot is a deployment-level assignment made for this Sage, while the group ordering
    is a preference expressed about a list of models — and it comes at all because the alternative
    below is alphabetical order pretending to be a decision.

    `min(approved)` is the last resort, for a deployment whose gateway offers no group listing to
    order. Any approved alias beats refusing a turn a person is waiting on, and sorting makes that
    pick the same one every time rather than whatever the gateway happened to list first.

    The empty set still raises: it is the orchestrator's refusal, not a choice to make here.
    """
    for candidate in _lock_preferences(state, catalog):
        if candidate in approved:
            return candidate
    for candidate in state.approved_order:
        if candidate in approved:
            return candidate
    if not approved:
        raise ValueError("sensitivity lock reached the router with an empty approved set")
    return min(approved)


def _lock_preferences(state: SessionState, catalog: ModelCatalog) -> tuple[str, ...]:
    if state.chat_thread_id or state.mode is Mode.ASK:
        return (catalog.sovereign_ask,)
    if state.mode is Mode.IMPLEMENT or (state.mode is Mode.AUTO and state.phase is Phase.IMPLEMENT):
        return (catalog.sovereign_implement, catalog.sovereign_ask)
    return (catalog.sovereign_plan, catalog.sovereign_ask)


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
