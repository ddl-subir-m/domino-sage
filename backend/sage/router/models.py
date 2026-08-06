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


# Which gateway models accept OpenAI image_url content parts. Empirical, not advertised: verified by
# sending a test image through the live Domino gateway on 2026-07-30 — sonnet/gpt-5.4/opus/
# etan-opus-4.6 described it, bedrock-qwen3-coder returned HTTP 400 ("This model doesn't support the
# image content block that you provided"), qwen-2-5 returned 502. That is every model the gateway
# lists today. An unknown model is treated as NOT vision-capable on purpose: guessing wrong costs a
# hard 400 that kills the whole build turn, guessing conservatively only costs the agent one image.
VISION_CAPABLE = frozenset({"sonnet", "gpt-5.4", "opus", "etan-opus-4.6"})


def supports_vision(model: ModelId) -> bool:
    """A model id may arrive provider-prefixed (`domino/sonnet`); only the bare id is meaningful."""
    return model.rsplit("/", 1)[-1] in VISION_CAPABLE


# Gateway aliases served by AWS Bedrock (MODELS.md). These need the parallel-tool-call workaround in
# the shim — see split_parallel_tool_calls. Listed rather than prefix-matched because `nova` carries
# no `bedrock-` prefix, and a wrong guess here silently reshapes history for a model that didn't need
# it. TEMPORARY: delete this and its use once the gateway's Bedrock adapter groups tool results.
BEDROCK_SERVED = frozenset({"bedrock-qwen3-coder", "nova"})


def is_bedrock(model: ModelId) -> bool:
    return model.rsplit("/", 1)[-1] in BEDROCK_SERVED


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
    # Why this turn is read-only, when it was armed as one: "ask" / "question" (it answers and stops)
    # or "plan" (it proposes a plan). Both withhold write and shell tools, but only an answering turn
    # also withholds the task-list tool — a task list on a turn that answers and returns is a build
    # the user is left waiting for. "" when nothing armed it (including Ask, which is read-only by
    # mode alone); read_only_turn stays the flag to test for the write/shell guarantee.
    read_only_reason: str = ""
    # This turn may reach the public internet (webfetch/websearch). Default-deny: the orchestrator
    # arms it only when the current prompt actually asked for the web (a URL or an intent verb), and
    # the shim strips web tools from every request otherwise. Per-turn, like read_only_turn.
    web_allowed: bool = False


@dataclass(frozen=True)
class ModelDecision:
    model: ModelId
    reason: Reason
    locked: bool  # when True, the shim must override the request model and not let it change
