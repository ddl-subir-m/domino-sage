"""Types for the model-policy seam (DESIGN.md Seam 1).

Pure data. No I/O, no OpenCode concepts, no HTTP. If a harness-specific field ever
appears in SessionState, that is a design bug (see DESIGN.md leak rules).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

ModelId = str


class Mode(str, Enum):
    ASK = "ask"
    PLAN = "plan"
    IMPLEMENT = "implement"
    AUTO = "auto"


class Phase(str, Enum):
    PLAN = "plan"
    IMPLEMENT = "implement"


class Reason(str, Enum):
    SENSITIVITY = "sensitivity"
    AUTO_PLAN = "auto-plan"
    AUTO_IMPLEMENT = "auto-implement"
    ASK_PINNED = "ask-pinned"
    PLAN_PINNED = "plan-pinned"
    PLAN_OVERRIDE = "plan-override"
    IMPLEMENT_PINNED = "implement-pinned"
    IMPLEMENT_OVERRIDE = "implement-override"


# Which gateway models accept OpenAI image_url content parts. Empirical, not advertised: verified
# by sending a test image through the live Domino gateway on 2026-07-30 — sonnet/gpt-5.4/opus
# described it, bedrock-qwen3-coder returned HTTP 400 ("This model doesn't support the image content
# block that you provided"), qwen-2-5 returned 502. An unknown model is treated as NOT vision-capable
# on purpose: guessing wrong costs a hard 400 that kills the whole build turn, guessing conservatively
# only costs the agent one image.
VISION_CAPABLE = frozenset({"sonnet", "gpt-5.4", "opus"})


def supports_vision(model: ModelId) -> bool:
    """A model id may arrive provider-prefixed (`domino/sonnet`); only the bare id is meaningful."""
    return model.rsplit("/", 1)[-1] in VISION_CAPABLE


@dataclass(frozen=True)
class ModelCatalog:
    """The model ids the router chooses between. Confirmed by gateway-questions Q8."""

    sovereign_plan: ModelId        # sovereign model for the plan phase
    sovereign_implement: ModelId   # sovereign model for the implement phase
    sovereign_ask: ModelId         # sovereign model for ask mode, and the lock fallback
    plan: ModelId             # stronger model for the plan phase
    implement: ModelId        # cheaper model for the implement phase
    ask: ModelId               # read-only ask mode model


@dataclass(frozen=True)
class SessionState:
    """Everything the router needs. Snapshot taken by the shim per request."""

    sensitivity_locked: bool
    mode: Mode
    phase: Phase
    picked_model: ModelId | None = None
    # This turn must not touch the filesystem. Ask mode implies it, but a gated plan turn does too
    # while `mode` is still auto/plan — the gate is a per-turn decision the mode can't express, so
    # the orchestrator sets it explicitly and the shim strips write/shell tools on that basis.
    read_only_turn: bool = False


@dataclass(frozen=True)
class ModelDecision:
    model: ModelId
    reason: Reason
    locked: bool  # when True, the shim must override the request model and not let it change
