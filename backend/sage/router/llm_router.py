"""LLMRouter — the pure model-policy function (DESIGN.md Seam 1).

resolve(state, catalog) -> ModelDecision

Precedence (highest first), per SPEC.md Component 3:
    1. sensitivity lock  -> sovereign, locked (non-overridable)
    2. auto mode         -> plan model in plan phase, implement model in implement phase
    3. manual mode       -> user's picked model if set, else the phase model
    4. modal default     -> catalog.default

This is the single highest-value unit under test: pure inputs, pure output, zero mocks.
"""
from __future__ import annotations

from .models import Mode, ModelCatalog, ModelDecision, Phase, Reason, SessionState


def resolve(state: SessionState, catalog: ModelCatalog) -> ModelDecision:
    # 1. Sensitivity lock wins over everything and is non-overridable.
    if state.sensitivity_locked:
        return ModelDecision(model=catalog.sovereign, reason=Reason.SENSITIVITY, locked=True)

    # 2. Auto mode: the pipeline drives model choice by phase.
    if state.mode is Mode.AUTO:
        if state.phase is Phase.PLAN:
            return ModelDecision(model=catalog.plan, reason=Reason.AUTO_PLAN, locked=False)
        return ModelDecision(model=catalog.implement, reason=Reason.AUTO_IMPLEMENT, locked=False)

    # 3. Manual mode: honor the user's explicit pick (from the build-time modal).
    if state.picked_model is not None:
        return ModelDecision(model=state.picked_model, reason=Reason.MANUAL, locked=False)

    # 4. Nothing picked yet: modal-on-build default.
    return ModelDecision(model=catalog.default, reason=Reason.MODAL_DEFAULT, locked=False)
