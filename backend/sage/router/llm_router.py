"""LLMRouter — the pure model-policy function (DESIGN.md Seam 1).

resolve(state, catalog) -> ModelDecision

Precedence (highest first), per SPEC.md Component 3 minus the old sensitivity lock:
    1. auto mode         -> plan model in plan phase, implement model in implement phase
    2. ask mode          -> catalog.ask (never overridable; read-only is enforced by the shim)
    3. plan mode         -> user's picked model if set, else catalog.plan
    4. implement mode    -> user's picked model if set, else catalog.implement

A Chat turn (chat_thread_id set) is separate: the Chat pick, else catalog.plan.
Build Auto/Ask/Plan/Implement does not apply. Sensitive attachments do not change the model —
the caller uses any LLM alias they can reach on the gateway.

This is the single highest-value unit under test: pure inputs, pure output, zero mocks.
"""
from __future__ import annotations

from .models import Mode, ModelCatalog, ModelDecision, Phase, Reason, SessionState


def resolve(state: SessionState, catalog: ModelCatalog) -> ModelDecision:
    # Chat is a Workbench mode, not a ModelControl mode. A Chat turn still has to pick a real
    # gateway alias; the standing Build Auto/Ask/Plan/Implement choice must not leak into it.
    if state.chat_thread_id:
        return _resolve_chat(state, catalog)

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
    return ModelDecision(model=catalog.plan, reason=Reason.CHAT_AUTO, locked=False)
