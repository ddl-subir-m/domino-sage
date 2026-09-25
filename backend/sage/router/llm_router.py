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
    EffortSource,
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
    return _lock_sensitivity(
        _pin_signing(_resolve_build(state, catalog), state, catalog), state, catalog)


def _slot_effort(
    state: SessionState, catalog: ModelCatalog, slot: str, *, stage_default: bool
) -> tuple[str | None, EffortSource]:
    """Return the level chosen for one slot, else the Build stage default (#545).

    An UNSET level is not a choice to run unlimited. It used to be: a saved row carrying null said
    "this slot exists and names no level", and this read that presence as the person having asked
    for the provider's own default — so every assigned model ran with no limit, and the stage
    default reached only a slot nobody had ever saved. Measured on #545: GLM 5.3 OR assigned to
    Implement at that unset level reasoned for 120 s twice and ended the turn with no edit.

    So row presence no longer decides the source; only the VALUE does. A level the person picked is
    theirs and is sent as-is. No level, on a Build turn whose caller allows a stage default, is the
    stage's (`plan_reasoning_effort` / `implement_reasoning_effort`, applied in `enforcement.py`).
    `stage_default=False` — Ask and Chat — keeps the provider default it always had.
    """
    effort = getattr(catalog, f"{slot}_effort")
    if slot in state.saved_effort_slots and effort is not None:
        return effort, EffortSource.USER
    if stage_default and state.effort_rows_armed:
        return None, EffortSource.STAGE_DEFAULT
    return effort, EffortSource.USER if effort is not None else EffortSource.PROVIDER_DEFAULT


def _picked_effort(state: SessionState) -> tuple[str | None, EffortSource]:
    """The in-session pick's own level, else the stage default — the same rule as a saved row.

    A pick made with no level is unset in exactly the sense `_slot_effort` now reads, and every
    caller is a Build plan or implement override. Left on the provider default, the one act that
    replaces a model would also be the one way back to the unlimited thinking #545 removes.
    """
    if state.picked_effort is not None:
        return state.picked_effort, EffortSource.USER
    if state.effort_rows_armed:
        return None, EffortSource.STAGE_DEFAULT
    return None, EffortSource.PROVIDER_DEFAULT


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
    final_model = _nearest_approved(state, catalog, approved)
    # An armed Build turn keeps the configured decision and validates it against the model that
    # actually receives the request. This preserves an explicit Model default and lets an automatic
    # stage apply its default to the final approved alias (#532). Other callers keep the established
    # Chat/Ask behavior: the lock chose no assignment, so it carries no effort.
    if state.effort_rows_armed:
        return replace(decision, model=final_model, reason=Reason.SENSITIVITY, locked=True)
    return ModelDecision(model=final_model, reason=Reason.SENSITIVITY, locked=True)


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
    """What a turn in this SLOT runs under the lock, for a label with no turn in hand (ADR-0043).

    Three names for three questions, which is a guardrail this router keeps needing and #285 is what
    losing it costs. `nearest_approved` is where the lock MOVES a barred turn: right for a chip
    consulted only once the turn's own model is already barred, wrong here, because it never applies
    the signing pin and so named the sovereign slot on a session the pin held on one model
    (ADR-0032). `resolve` is what THIS turn runs, with the mode and phase the session armed. This
    one asks `resolve`'s question of a slot rather than a turn — the caller forces the mode — and
    normalises an absent approved set to an empty one so that the lock below refuses rather than
    passing an unlocked state through to a label that only makes sense under a lock.

    It reads the pick, and since #286 that is the whole of the difference from the answer this used
    to give. An in-session act outranks the signing pin one layer below the lock (`_pin_signing`
    returns a PLAN_OVERRIDE or an IMPLEMENT_OVERRIDE untouched), so a pick-free answer named the
    pin's model while the pick was the thing deciding the turn. Wrong in all three pick cases, not
    only the barred one it was reported for: no pick runs the pin's model, a barred pick runs where
    the lock moves it, and an approved pick runs itself.

    The browser may hold this answer across a pick change, and what makes that safe is the drawer
    rather than anything here. The panel's rows are the only reader; it re-reads on open, its mask
    puts the picker out of reach for as long as it is open, and `set_catalog` clears the Build pick
    on every save (the Chat pick it leaves standing, and no save can move one either). That is a UI
    invariant standing in for a data one, and the weaker of the two — it breaks the moment anyone
    passes `mask: false`, which is why that prop is now passed explicitly and asserted.

    A mask bounds one pair of hands, not every client, so it leaves two windows rather than one:
    #294, where the orchestrator moves the pick itself on a build escalation with no human act to
    block; and a second Workbench open on the same Project, whose picker this tab's mask cannot
    reach. Both are the same shape and #294 carries the decided fix.

    A NAME over `resolve`, and since #286 nothing else. The body used to mirror `resolve`'s chain
    with the pick dropped out of it; with the pick back, the copy had no difference left to carry and
    was only a second place for the chain to drift from — which is precisely what #285 was, a rule
    (`_pin_signing`) added on one side of a duplicated fork and not the other. So it delegates, and
    the guardrail is the three names rather than three bodies.

    Raises on an empty or absent approved set, exactly as `nearest_approved` does.
    """
    # The one thing this adds. An absent approved set becomes an empty one, so the refusal below is
    # the one `nearest_approved` makes: `_lock_sensitivity` passes an unlocked state straight
    # through, and a label asking what a lock runs when there is no lock is a caller bug, not a
    # model. Here rather than at the caller, because it is about the ANSWER this name promises —
    # "what a slot runs under the lock" has no meaning without one — where the caller's own
    # preconditions (the forced mode, the forced `chat_thread_id`, a pick the standing mode will
    # honour) are about the QUESTION and stay with it. #298's seam rule, said the way this function
    # needs it: the caller knows what it is asking, and only the answer's own meaning lives here.
    state = replace(state, approved_models=state.approved_models or frozenset())
    return resolve(state, catalog).model


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
            effort, source = _slot_effort(
                state, catalog, slot,
                stage_default=not state.chat_thread_id and state.mode is not Mode.ASK,
            )
            return ModelDecision(model=model, reason=Reason.SIGNING_VETO, locked=False,
                                 effort=effort, effort_source=source)
    return None


def _pin_signing(
    decision: ModelDecision, state: SessionState, catalog: ModelCatalog
) -> ModelDecision:
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
    # The pin's own slot supplies the effort, because the pin is what chose the model: a plan turn
    # pinned to implement's model runs implement's effort (ADR-0049). Anything else sends plan's
    # level to a model that never advertised it, which on gemini — the only signing alias — is a 400.
    #
    # Only on THIS return. The early return above is not the same case wearing a different shape:
    # there the pin moved nothing, so the asking slot's own assignment is still the one that chose
    # what runs, and two slots holding the same alias with different efforts is the per-phase split
    # working rather than a coincidence to be normalised away.
    effort, source = _slot_effort(
        state, catalog, slot, stage_default=state.mode is not Mode.ASK)
    return ModelDecision(model=model, reason=Reason.SIGNING_PIN, locked=False,
                         effort=effort, effort_source=source)


def _resolve_build(state: SessionState, catalog: ModelCatalog) -> ModelDecision:
    # 1. Auto mode: the pipeline drives model choice by phase.
    if state.mode is Mode.AUTO:
        if state.phase is Phase.PLAN:
            effort, source = _slot_effort(state, catalog, "plan", stage_default=True)
            return ModelDecision(model=catalog.plan, reason=Reason.AUTO_PLAN, locked=False,
                                 effort=effort, effort_source=source)
        effort, source = _slot_effort(state, catalog, "implement", stage_default=True)
        return ModelDecision(model=catalog.implement, reason=Reason.AUTO_IMPLEMENT, locked=False,
                             effort=effort, effort_source=source)

    # 2. Ask mode: pinned to the ask model. Read-only is enforced by the shim, not routing.
    if state.mode is Mode.ASK:
        effort, source = _slot_effort(state, catalog, "ask", stage_default=False)
        return ModelDecision(model=catalog.ask, reason=Reason.ASK_PINNED, locked=False,
                             effort=effort, effort_source=source)

    # 3. Plan mode: pinned to the plan model, overridable by an explicit pick.
    if state.mode is Mode.PLAN:
        if state.picked_model is not None:
            # An in-session pick replaces the model, so it replaces the effort — with the one the
            # SAME act chose, never the slot's (ADR-0049). Falling back to `catalog.plan_effort` is
            # the version anyone writes first, and it applies a level chosen for a model the person
            # just moved off. `None` when they picked no level, which is every pick made before the
            # menu could carry one (#295) and every pick of an alias that advertises none.
            effort, source = _picked_effort(state)
            return ModelDecision(model=state.picked_model, reason=Reason.PLAN_OVERRIDE, locked=False,
                                 effort=effort, effort_source=source)
        effort, source = _slot_effort(state, catalog, "plan", stage_default=True)
        return ModelDecision(model=catalog.plan, reason=Reason.PLAN_PINNED, locked=False,
                             effort=effort, effort_source=source)

    # 4. Implement mode: pinned to the implement model, overridable by an explicit pick.
    if state.picked_model is not None:
        effort, source = _picked_effort(state)
        return ModelDecision(model=state.picked_model, reason=Reason.IMPLEMENT_OVERRIDE,
                             locked=False, effort=effort,
                             effort_source=source)   # the pick's own, as above
    effort, source = _slot_effort(state, catalog, "implement", stage_default=True)
    return ModelDecision(model=catalog.implement, reason=Reason.IMPLEMENT_PINNED, locked=False,
                         effort=effort, effort_source=source)


def _resolve_chat(state: SessionState, catalog: ModelCatalog) -> ModelDecision:
    if state.chat_model:
        # Chat's picker carries an effort beside the model, and since #295 Build's does too — so
        # both overrides now supply one, from their own field. `reasoning_effort` is this pick's,
        # `picked_effort` is Build's, and they stay two fields because they are two standing
        # choices on two models; see `SessionState` and `_resolve_build`.
        return ModelDecision(model=state.chat_model, reason=Reason.CHAT_OVERRIDE, locked=False,
                             effort=state.reasoning_effort,
                             effort_source=(EffortSource.USER if state.reasoning_effort is not None
                                            else EffortSource.PROVIDER_DEFAULT))
    effort, source = _slot_effort(state, catalog, "ask", stage_default=False)
    return ModelDecision(model=catalog.ask, reason=Reason.CHAT_DEFAULT, locked=False,
                         effort=effort, effort_source=source)
