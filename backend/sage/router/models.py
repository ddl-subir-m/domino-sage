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
    SENSITIVITY = "sensitivity"


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


def signing_slot(catalog: ModelCatalog) -> str | None:
    """The first assignable slot holding a signing model, or None (ADR-0032).

    THE one copy of the pin's input. `llm_router._pin_signing` routes by it and
    `preflight.turn_slots` preflights by it, because a turn that preflights one alias and runs on
    another is worse than no preflight: it refuses builds that were going to succeed.
    """
    for slot in ASSIGNABLE_SLOTS:
        if signs(getattr(catalog, slot)):
            return slot
    return None


# Which `reasoning_effort` values each gateway alias actually accepts. One alias at a time, by
# sending a NONSENSE value (scripts/reasoning-probe.py): a 400 means the alias validates the field
# and so honours it, a 200 means it discards the field in silence. The two are indistinguishable if
# you only ever send a value that happens to be legal, which is why nobody had noticed. Probed live
# on sage.gcp.cs.domino.tech, 2026-09-12.
#
# Measured per alias because the name cannot tell you, and the name match this replaced was wrong
# in both directions: gemini-3.7-flash honours the field, while sonnet, Opus-4.8, haiku and both
# Gemmas throw it away. The old `gpt-5`/o-series match named none of that, and was right about the
# rest only by accident.
#
# Keys are the alias name EXACTLY as the gateway spells it, not a normalised one — the lookup is
# case-sensitive, like VISION_CAPABLE and SIGNS_TOOL_CALLS above it, and this deployment does serve
# mixed-case names (`Opus-4.8`). A key lowercased out of habit answers `()` forever and looks like
# an alias that simply offers no control.
#
# This is the USABLE set, not the advertised one. Gemini's own refusal names
# 'high','low','max','medium','minimal', but `minimal` then 400s at Vertex ("Thinking level
# unsupported: THINKING_LEVEL_MINIMAL"), so republishing the enum verbatim would offer a level that
# cannot run. An alias absent from this table is offered no effort at all: a missing control costs
# its user one choice, where a wrong guess costs a hard 400 that kills the whole turn.
REASONING_EFFORTS: dict[str, tuple[str, ...]] = {
    # Every level except `none` is refused together with function tools ("Function tools with
    # reasoning_effort are not supported for gpt-5.4 in /v1/chat/completions"). gemini takes any of
    # its levels alongside tools, and is the only alias here that does. The exclusion is a property
    # of the request SHAPE, not of the alias's enum, so it does not narrow these rows — it narrows
    # `EFFORTS_WITH_TOOLS` below, which the send path reads INSTEAD of this table on a request that
    # carries tools.
    #
    # `none` and `xhigh` are in the row because the probe found them, not because anything
    # advertised them: the name match this replaced published low/medium/high for gpt-5.4 and had
    # simply never been checked against the alias.
    "gpt-5.4": ("none", "low", "medium", "high", "xhigh"),
    "gemini-3.7-flash": ("low", "medium", "high", "max"),
}


def reasoning_efforts_for(model: ModelId) -> tuple[str, ...]:
    """The efforts this alias was measured to accept; empty for one nobody has probed.

    NOT the last word, and not a passthrough either. `alias_reasoning_efforts` lets an alias record
    that advertises an enum choose which of these levels to offer, and narrows that enum by this
    row — so a level here can be dropped by the gateway, and a level the gateway advertises but the
    probe proved broken never reaches a picker. Read that function for the whole rule; it is two
    sentences and this one cannot state it alone. Today the gateway publishes `{}` for every alias
    (#284), so in practice this table answers by itself.
    """
    return REASONING_EFFORTS.get(model.rsplit("/", 1)[-1], ())


# The subset of an alias's levels that survives when the request ALSO carries function tools. An
# alias absent here keeps its whole row: a tool-carrying request is unremarkable to most aliases,
# and defaulting to "narrower" is precisely the bug this table ends — the guard it replaced dropped
# the field from every tool-carrying turn, for every alias, which is why a Build turn had never sent
# one (#282, ADR-0049). Same key spelling as the tables above it: the alias as the gateway writes it.
EFFORTS_WITH_TOOLS: dict[str, tuple[str, ...]] = {
    # A hard 400 on the WHOLE request, not a preference: "Function tools with reasoning_effort are
    # not supported for gpt-5.4 in /v1/chat/completions".
    #
    # `none` surviving is measured, not reasoned: it answers 200 beside tools where every other
    # level 400s (scripts/reasoning-probe.py, 2026-09-12). It is kept because it is a LEVEL, not an
    # absence — dropping it would run at the alias's own higher default on the one turn somebody
    # asked for no reasoning at all, which is the cost lever they were reaching for. Dropping the
    # field is the right answer only where nobody chose it; see the send path in enforcement.py.
    "gpt-5.4": ("none",),
}


def reasoning_efforts_with_tools(model: ModelId) -> tuple[str, ...]:
    """`reasoning_efforts_for`, narrowed to what the alias keeps on a tool-carrying request.

    A separate question from the enum, and it has to be one: gemini takes every level it advertises
    alongside tools (200) while gpt-5.4 takes one of its five. Reading the enum alone sends a 400 on
    a Build plan phase, which always carries tools; reading neither is what the old guard did, and
    it cost the field on every Build turn ever run.

    Never wider than the enum — a level here that `reasoning_efforts_for` does not offer would reach
    the wire on exactly the requests the alias refuses it on. The tests hold the two tables to that.
    """
    alias = model.rsplit("/", 1)[-1]
    if alias in EFFORTS_WITH_TOOLS:
        return EFFORTS_WITH_TOOLS[alias]
    return reasoning_efforts_for(model)


@dataclass(frozen=True)
class ModelCatalog:
    """The model ids the router chooses between. Confirmed by gateway-questions Q8."""

    sovereign_plan: ModelId        # sovereign model for the plan phase
    sovereign_implement: ModelId   # sovereign model for the implement phase
    sovereign_ask: ModelId         # sovereign model for ask mode, and the lock fallback
    plan: ModelId             # stronger model for the plan phase
    implement: ModelId        # cheaper model for the implement phase
    ask: ModelId               # read-only ask mode model
    # An effort is half of an assignment, carried beside the model it belongs to (ADR-0049) — never
    # one Build-wide level, which cannot say "think hard while planning, cheaply while implementing"
    # and so gives up the split the slots exist for. `None` means no effort: the alias answers at its
    # own default, which is what every assignment did before this field existed.
    #
    # Only the three ASSIGNABLE_SLOTS carry one. The sovereign slots are persisted and preflighted
    # but the router reads none of them, so an effort there would be a value nothing ever sends —
    # `set_catalog` refuses it rather than saving a setting that visibly does nothing.
    #
    # Named `<slot>_effort` so that `replace(catalog, **fields)` and `getattr(catalog, slot)` both
    # keep working as they are written today; a nested `{model, effort}` on the dataclass would
    # rewrite every reader of a model id to buy nothing.
    plan_effort: str | None = None
    implement_effort: str | None = None
    ask_effort: str | None = None


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
    # Content the gateway's guardrail refuses, which this Conversation has therefore stopped sending
    # (ADR-0022). Keys are `file:<path>` or `text:<fingerprint>` — see shim.chat_paths. Applies to
    # Chat and Build alike: the orchestrator reads the set out of whichever transcript owns the
    # turn, so the shim never has to know which half it is serving. Empty when nothing is armed.
    withheld: frozenset[str] = frozenset()

    # Standing Chat pick. Ignored on Build turns. None means catalog.ask.
    chat_model: ModelId | None = None
    # OpenAI-style reasoning_effort for Chat, when the picked alias supports it.
    #
    # Still the Chat PICK and only that — the in-session-act row of ADR-0049's table, which is the
    # one act that carries an effort of its own. What actually reaches the wire is
    # `ModelDecision.effort`, chosen by whatever chose the model; read that before adding a second
    # reader here.
    reasoning_effort: str | None = None
    # The aliases approved for sensitive work, when this turn is under the lock (ADR-0043).
    # None means no lock: either the deployment never configured SAGE_SENSITIVE_MODEL_GROUP, or no
    # Dataset in scope carries the tag. A frozenset means locked, and the router will not leave it.
    # It is never EMPTY here: an approved set that resolves to nothing is a refusal the orchestrator
    # makes before the turn starts, because a router that returns a model cannot express "no".
    approved_models: frozenset[ModelId] | None = None
    # The same aliases in the order the administrator listed them in the group (ADR-0043). The set
    # above decides what is allowed; this decides which one is PREFERRED when more than one is, and
    # the two are separate fields because they answer separate questions — a set has no order to
    # read, and sorting one would be alphabet dressed up as an administrator's choice.
    # Empty is not a contradiction: a deployment whose gateway offers no group listing has an
    # approved set and no ordering for it, and the router falls back the way it always did.
    approved_order: tuple[ModelId, ...] = ()


@dataclass(frozen=True)
class ModelDecision:
    model: ModelId
    reason: Reason
    # True when the sensitivity lock chose or approved this model (ADR-0043); the picker shows the
    # rest disabled. False everywhere else. The shim still overwrites model on every request.
    locked: bool
    # The reasoning effort that belongs to THIS model, or None for the alias's own default.
    #
    # Carried on the decision rather than read off the catalog by the caller, because the slot that
    # was asked for is not always the slot that answered: the signing pin (ADR-0032), the signing
    # veto and the sensitivity lock all move the model, and an effort is validated by the model that
    # receives it — two aliases 400 on a level they do not list, and `qwen-2-5` 400s on the field
    # itself. So whatever chose the model chose the effort (ADR-0049), and the shim reads one field
    # instead of writing the same comparison once per slot and drifting twice.
    #
    # NOT a promise the model accepts it. A stored effort is validated at save against the model the
    # slot ran then, and the deployment default can move under it afterwards; the send path re-checks
    # against the measured table and drops rather than letting the turn 400.
    effort: str | None = None
