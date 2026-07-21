"""LLMRouter — the pure model-policy function (DESIGN.md Seam 1).

resolve(state, catalog) -> ModelDecision

Precedence (highest first), per SPEC.md Component 3:
    1. sensitivity lock  -> the sovereign models only, non-overridable by any vendor model:
                            - auto mode:            sovereign plan/implement model by phase, fixed
                            - plan/implement modes:  user's picked sovereign model if set, sticky
                                                     for the session until changed; else
                                                     catalog.sovereign_ask
                            - ask mode:              always catalog.sovereign_ask
    2. auto mode         -> plan model in plan phase, implement model in implement phase
    3. ask mode          -> catalog.ask (never overridable; read-only is enforced by the shim)
    4. plan mode         -> user's picked model if set, else catalog.plan
    5. implement mode    -> user's picked model if set, else catalog.implement

This is the single highest-value unit under test: pure inputs, pure output, zero mocks.
"""
from __future__ import annotations

from .models import Mode, ModelCatalog, ModelDecision, Phase, Reason, SessionState


def resolve(state: SessionState, catalog: ModelCatalog) -> ModelDecision:
    # 1. Sensitivity lock wins over everything and is non-overridable by any vendor model.
    # Only the sovereign models are ever selectable while locked.
    if state.sensitivity_locked:
        sovereign_options = (catalog.sovereign_plan, catalog.sovereign_implement)
        if state.mode in (Mode.PLAN, Mode.IMPLEMENT):
            if state.picked_model in sovereign_options:
                return ModelDecision(model=state.picked_model, reason=Reason.SENSITIVITY, locked=True)
            return ModelDecision(model=catalog.sovereign_ask, reason=Reason.SENSITIVITY, locked=True)
        if state.mode is Mode.AUTO:
            model = catalog.sovereign_plan if state.phase is Phase.PLAN else catalog.sovereign_implement
            return ModelDecision(model=model, reason=Reason.SENSITIVITY, locked=True)
        # Ask mode: no override surfaced, always the sovereign ask model.
        return ModelDecision(model=catalog.sovereign_ask, reason=Reason.SENSITIVITY, locked=True)

    # 2. Auto mode: the pipeline drives model choice by phase.
    if state.mode is Mode.AUTO:
        if state.phase is Phase.PLAN:
            return ModelDecision(model=catalog.plan, reason=Reason.AUTO_PLAN, locked=False)
        return ModelDecision(model=catalog.implement, reason=Reason.AUTO_IMPLEMENT, locked=False)

    # 3. Ask mode: pinned to the ask model. Read-only is enforced by the shim, not routing.
    if state.mode is Mode.ASK:
        return ModelDecision(model=catalog.ask, reason=Reason.ASK_PINNED, locked=False)

    # 4. Plan mode: pinned to the plan model, overridable by an explicit pick.
    if state.mode is Mode.PLAN:
        if state.picked_model is not None:
            return ModelDecision(model=state.picked_model, reason=Reason.PLAN_OVERRIDE, locked=False)
        return ModelDecision(model=catalog.plan, reason=Reason.PLAN_PINNED, locked=False)

    # 5. Implement mode: pinned to the implement model, overridable by an explicit pick.
    if state.picked_model is not None:
        return ModelDecision(model=state.picked_model, reason=Reason.IMPLEMENT_OVERRIDE, locked=False)
    return ModelDecision(model=catalog.implement, reason=Reason.IMPLEMENT_PINNED, locked=False)
