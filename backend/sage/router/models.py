"""Types for the model-policy seam (DESIGN.md Seam 1).

Pure data. No I/O, no OpenCode concepts, no HTTP. If a harness-specific field ever
appears in SessionState, that is a design bug (see DESIGN.md leak rules).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

ModelId = str


class Mode(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"


class Phase(str, Enum):
    PLAN = "plan"
    IMPLEMENT = "implement"


class Reason(str, Enum):
    SENSITIVITY = "sensitivity"
    AUTO_PLAN = "auto-plan"
    AUTO_IMPLEMENT = "auto-implement"
    MANUAL = "manual"
    MODAL_DEFAULT = "modal-default"


@dataclass(frozen=True)
class ModelCatalog:
    """The model ids the router chooses between. Confirmed by gateway-questions Q8."""

    sovereign: ModelId
    plan: ModelId           # stronger model for the plan phase
    implement: ModelId      # cheaper model for the implement phase
    default: ModelId        # fallback when no modal pick


@dataclass(frozen=True)
class SessionState:
    """Everything the router needs. Snapshot taken by the shim per request."""

    sensitivity_locked: bool
    mode: Mode
    phase: Phase
    picked_model: ModelId | None = None


@dataclass(frozen=True)
class ModelDecision:
    model: ModelId
    reason: Reason
    locked: bool  # when True, the shim must override the request model and not let it change
