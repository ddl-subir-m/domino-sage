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
    AUTO_PLAN = "auto-plan"
    AUTO_IMPLEMENT = "auto-implement"
    ASK_PINNED = "ask-pinned"
    PLAN_PINNED = "plan-pinned"
    PLAN_OVERRIDE = "plan-override"
    IMPLEMENT_PINNED = "implement-pinned"
    IMPLEMENT_OVERRIDE = "implement-override"
    CHAT_DEFAULT = "chat-default"
    CHAT_OVERRIDE = "chat-override"
    SIGNING_PIN = "signing-pin"
    SIGNING_VETO = "signing-veto"


# Which gateway models accept OpenAI image_url content parts. Empirical, not advertised: verified by
# sending a test image through the live Domino gateway on 2026-07-30 — sonnet/gpt-5.4/opus/
# etan-opus-4.6 described it, bedrock-qwen3-coder returned HTTP 400 ("This model doesn't support the
# image content block that you provided"), qwen-2-5 returned 502. Re-run on 2026-09-03 against the
# aliases the gateway has added since: gemini-3.7-flash answered "Red" to an 8x8 red PNG (HTTP 200),
# so it is listed; domino-gcp/claude-sonnet-5 is offered but 404s upstream from GCP ("Publisher
# model ... was not found or your project does not have access to it"), so it stays off the list —
# it was never shown an image. That is every model the gateway lists today. An unknown model is
# treated as NOT vision-capable on purpose: guessing wrong costs a hard 400 that kills the whole
# build turn, guessing conservatively only costs the agent one image.
VISION_CAPABLE = frozenset({"sonnet", "gpt-5.4", "opus", "etan-opus-4.6", "gemini-3.7-flash"})


def supports_vision(model: ModelId) -> bool:
    """A model id may arrive provider-prefixed (`domino/sonnet`); only the bare id is meaningful."""
    return model.rsplit("/", 1)[-1] in VISION_CAPABLE


# Gateway aliases served by AWS Bedrock (MODELS.md). These need the parallel-tool-call workaround in
# the shim — see split_parallel_tool_calls. Listed rather than prefix-matched because `nova` carries
# no `bedrock-` prefix, and a wrong guess here silently reshapes history for a model that didn't need
# it. TEMPORARY: delete this and its use once the gateway's Bedrock adapter groups tool results.
BEDROCK_SERVED = frozenset({"bedrock-qwen3-coder", "nova"})

# OpenAI-style reasoning_effort values. Used when the gateway alias record does not list them.
_REASONING_EFFORTS = ("low", "medium", "high")


def is_bedrock(model: ModelId) -> bool:
    return model.rsplit("/", 1)[-1] in BEDROCK_SERVED


# Gateway aliases that attach a `thought_signature` to their tool calls and reject any later request
# that does not hand it back (ADR-0031). Signing is a property of the MODEL, but the transcript is a
# property of the harness session, and OpenCode replays the whole transcript every request — so one
# session must never mix a signing model with a non-signing one. See llm_router's pin, which keeps a
# session single-model, and resolve_unsigned, which is where a session that already mixed them goes
# (ADR-0032).
#
# Must stay DISJOINT from BEDROCK_SERVED: split_parallel_tool_calls takes a parallel batch apart
# across messages, and a signed batch carries its one signature on the FIRST call, so splitting one
# would manufacture the very shape Gemini rejects. A test holds the two sets apart.
SIGNS_TOOL_CALLS = frozenset({"gemini-3.7-flash"})


def signs(model: ModelId) -> bool:
    return model.rsplit("/", 1)[-1] in SIGNS_TOOL_CALLS


def signing_slot(catalog: "ModelCatalog") -> str | None:
    """The first assignable slot holding a signing model, or None (ADR-0032).

    THE one copy of the pin's input. `llm_router._pin_signing` routes by it and
    `preflight.turn_slots` preflights by it, because a turn that preflights one alias and runs on
    another is worse than no preflight: it refuses builds that were going to succeed.
    """
    for slot in ASSIGNABLE_SLOTS:
        if signs(getattr(catalog, slot)):
            return slot
    return None


def reasoning_efforts_for(model: ModelId) -> tuple[str, ...]:
    """Heuristic: GPT-5 and the o-series accept `reasoning_effort` on chat/completions.

    Alias metadata (`inference_params`) is the authority when present; this is the fallback so a
    picker can still offer Low/Medium/High for gpt-5.4 when the gateway omits the enum.
    """
    bare = model.rsplit("/", 1)[-1].lower()
    if "gpt-5" in bare or bare.startswith(("o1", "o3", "o4")):
        return _REASONING_EFFORTS
    return ()


@dataclass(frozen=True)
class ModelCatalog:
    """The model ids the router chooses between. Confirmed by gateway-questions Q8."""

    sovereign_plan: ModelId        # sovereign model for the plan phase
    sovereign_implement: ModelId   # sovereign model for the implement phase
    sovereign_ask: ModelId         # sovereign model for ask mode, and the lock fallback
    plan: ModelId             # stronger model for the plan phase
    implement: ModelId        # cheaper model for the implement phase
    ask: ModelId               # read-only ask mode model


# The slots a person may assign in the model panel (ADR-0017). The panel lays out its own rows,
# because the label and the sentence under each one are its to write; this is the set, not the order. A subset
# of `preflight.SLOTS`, and deliberately so: the three sovereign slots are persisted and preflighted
# but the router reads none of them — they belong to a sensitivity lock that no longer routes — and
# a row that changes nothing is worse than no row.
#
# `ask` is one row for two consumers. `_resolve_chat` returns `catalog.ask` as CHAT_DEFAULT, so this
# slot has always driven Chat's default model as well; the panel labels it for both rather than
# repointing Chat silently.
ASSIGNABLE_SLOTS: tuple[str, ...] = ("plan", "implement", "ask")


@dataclass(frozen=True)
class SessionState:
    """Everything the router needs. Snapshot taken by the shim per request."""

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
    # A Chat turn (docs/workbench/chat.md). When set, the shim keeps write/bash tools (Chat writes
    # Artifacts) and only allows writes under that Thread's examples/ and .sage/threads/ dirs.
    chat_thread_id: str | None = None
    # Standing Chat pick. Ignored on Build turns. None means catalog.ask.
    chat_model: ModelId | None = None
    # OpenAI-style reasoning_effort for Chat, when the picked alias supports it. None omits the field.
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class ModelDecision:
    model: ModelId
    reason: Reason
    locked: bool  # leftover from the old sensitivity lock; always False. The shim still overwrites model.
