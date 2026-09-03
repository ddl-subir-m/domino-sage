"""Keeping a Chat conversation small enough to fit in a prompt — its own, and Build's.

Two jobs, one subject. `should_compact` / `compact_model` decide when a Chat OpenCode session has
grown too big and which model to ask OpenCode to shrink it with. `chat_summary` renders the same
conversation down to a few hundred characters so a BUILD turn can be told what was said in Chat
(#53) — the two harness sessions are deliberately not merged, so this is the only way across.

Sage `history.jsonl` is the UI replay and is never rewritten. Compaction only shrinks what the
model sees on the next Chat turn (docs/workbench/chat.md), and `chat_summary` reads that untouched
replay rather than the compacted session, so what Build hears does not depend on whether Chat has
compacted yet.
"""
from __future__ import annotations

from typing import Any

from ..router import llm_router
from ..router.models import ModelCatalog, SessionState

# OpenCode 1.18.4: POST /api/session/{id}/summarize. The only provider Sage configures.
PROVIDER_ID = "sage-gateway"

# Compact between turns once usage is this fraction of the alias's context window, so the next
# Chat prompt (context chips, descriptors, artifacts) still fits. OpenCode's own auto-compact
# waits until ~95% and can fire mid-turn.
TOKEN_RATIO = 0.70

# Used only when OpenCode messages carry no usage. Count is user turns since the last compaction
# in that session (assistant-only lists, as in FakeOpenCode, count the same way).
TURN_FALLBACK = 12

# Matches opencode.json `provider.sage-gateway.models.*.limit.context`, and a test compares the two
# as sets. Unknown aliases get the conservative default so we compact before a 32k window overflows
# rather than after a 1M one.
#
# These are MEASURED, not the models' advertised windows. `/api/aliases` reports
# `inference_params: {}` for every alias — the gateway states no window anywhere — so each number
# below was read off the gateway's own refusal on 2026-09-03: send a prompt over the limit and the
# error names the limit ("prompt is too long: 1050024 tokens > 1000000 maximum"). An over-limit
# request is rejected before it bills, so this costs nothing to redo.
#
# Under-claiming is not the safe direction it looks like. This map decides when a conversation gets
# summarised, so a window smaller than the truth throws away context the model could still hold and
# pays for a summarize call to do it. Compacting earlier than the window is a cost policy and
# belongs in TOKEN_RATIO, not here, where it would read as the model's limit.
CONTEXT_LIMITS: dict[str, int] = {
    # Unmeasurable, left as documented: this alias is not served by the dogfood gateway.
    "deepseek-v4-pro": 128_000,
    "qwen-2-5": 32_768,
    # Unmeasurable: the sovereign endpoint answers 502 to everything, "Say OK" included, so it could
    # not be asked. Mistral-7B-Instruct-v02's documented window, and conservative.
    "local-domino-llm": 32_768,
    "bedrock-qwen3-coder": 262_144,
    "gpt-5.4": 922_000,
    "sonnet": 1_000_000,
    # Unmeasurable: not in this gateway's /v1/models, so it is not offered here and cannot be asked.
    "haiku": 200_000,
    # Keyed bare, like every entry here, because `context_limit` reduces via `bare_model_id` — the
    # gateway offers this one as `domino/gemini-3.7-flash`. Exactly 1Mi, as the refusal spells out:
    # "The input token count exceeds the maximum number of tokens allowed 1048576."
    "gemini-3.7-flash": 1_048_576,
    # Both back onto claude-opus-4-6 and both measured at the same ceiling. Added because the
    # gateway offers them and the picker offers every accessible alias — unlisted, they had no
    # window to compact against. `domino-gcp/claude-sonnet-5` is offered too but stays out: it 404s
    # upstream from GCP, so it cannot be measured and a number here would be invention.
    "opus": 1_000_000,
    "etan-opus-4.6": 1_000_000,
}
DEFAULT_CONTEXT = 128_000


def bare_model_id(model: str) -> str:
    return (model or "").rsplit("/", 1)[-1]


def context_limit(model: str) -> int:
    return CONTEXT_LIMITS.get(bare_model_id(model), DEFAULT_CONTEXT)


def compact_model(state: SessionState, catalog: ModelCatalog) -> tuple[str, str]:
    """providerID, modelID for OpenCode summarize — the same alias this Chat thread routes to."""
    decision = llm_router.resolve(state, catalog)
    return PROVIDER_ID, bare_model_id(decision.model)


# The alias named on a summarize call has to be one OpenCode can RESOLVE, and OpenCode resolves
# only what `provider.sage-gateway.models` lists: an alias missing from that map fails the whole
# request with "UnknownError: Unexpected server error" rather than falling back (verified live
# against opencode 1.18.4 — `opus` unlisted failed, `opus` listed answered, nothing else changed).
# CONTEXT_LIMITS holds exactly those keys and a test compares the two as sets, so asking this map is
# the same question as asking the config.
#
# It needs a fallback because the gateway offers aliases the config does not list — `opus`,
# `etan-opus-4.6` and `domino-gcp/claude-sonnet-5` today — and the picker offers every accessible
# one. Their turns are fine: the shim overwrites `model` on every request and OpenCode stays on its
# configured default, so it is never asked to resolve them. Summarize is the one call that hands
# OpenCode the alias itself, which made compaction the only place the gap showed: it raised,
# `_maybe_compact_chat` swallowed it (fail-open, by design), and the session then grew unbounded
# until the gateway refused the prompt for context, with nothing in the UI to say why.
#
# Naming a different alias here costs nothing, because the shim rewrites this request like any
# other — `_maybe_compact_chat` keeps Chat arming live precisely so the summary routes as the
# Thread's own model. This is a local handle, not a routing decision. Deliberately NOT used for
# `should_compact`, which must weigh the REAL alias: substituting a 200k default there would let a
# 32k session sail past its own window.
COMPACT_FALLBACK = "gpt-5.4"


def summarize_model_id(model: str) -> str:
    """The modelID to name on a summarize call: this alias when OpenCode knows it, else one it does."""
    bare = bare_model_id(model)
    return bare if bare in CONTEXT_LIMITS else COMPACT_FALLBACK


def should_compact(messages: list[dict], model: str) -> bool:
    used = last_usage_tokens(messages)
    if used is not None:
        return used >= int(context_limit(model) * TOKEN_RATIO)
    return turns_since_compact(messages) >= TURN_FALLBACK


def last_usage_tokens(messages: list[dict]) -> int | None:
    """Tokens OpenCode reported on the latest assistant message since the last compact.

    `input` on that message is the prompt that just ran — i.e. current context size — so it is
    the right number to compare to the window. Messages from before a compact still carry their
    old usage; those must not retrigger compact. Missing `tokens` means we cannot read usage.
    """
    for m in reversed(_since_compact(messages)):
        if _role(m) != "assistant":
            continue
        n = _tokens(m)
        if n is not None:
            return n
    return None


def turns_since_compact(messages: list[dict]) -> int:
    after = _since_compact(messages)
    users = [m for m in after if _role(m) == "user" and not _is_compaction(m)]
    if users:
        return len(users)
    return sum(1 for m in after if _role(m) == "assistant" and not _is_compaction(m))


def _since_compact(messages: list[dict]) -> list[dict]:
    after = list(messages or [])
    last = -1
    for i, m in enumerate(after):
        if _is_compaction(m):
            last = i
    if last >= 0:
        return after[last + 1 :]
    return after


def _role(m: dict) -> str:
    if not isinstance(m, dict):
        return ""
    t = m.get("type") or m.get("role")
    if t:
        return str(t)
    info = m.get("info")
    if isinstance(info, dict):
        return str(info.get("role") or info.get("type") or "")
    return ""


def _parts(m: dict) -> list:
    for key in ("content", "parts"):
        v = m.get(key)
        if isinstance(v, list):
            return v
    return []


def _is_compaction(m: dict) -> bool:
    if not isinstance(m, dict):
        return False
    info = m.get("info") if isinstance(m.get("info"), dict) else m
    if info.get("summary") or info.get("mode") == "compaction" or info.get("agent") == "compaction":
        return True
    return any(isinstance(p, dict) and p.get("type") == "compaction" for p in _parts(m))


def _tokens(m: dict) -> int | None:
    blobs: list[Any] = [m.get("tokens")]
    info = m.get("info")
    if isinstance(info, dict):
        blobs.append(info.get("tokens"))
    for tokens in blobs:
        if not isinstance(tokens, dict):
            continue
        if "total" in tokens:
            try:
                return int(tokens["total"] or 0)
            except (TypeError, ValueError):
                continue
        keys = ("input", "output", "reasoning", "prompt_tokens", "completion_tokens")
        if not any(k in tokens for k in keys) and "cache" not in tokens:
            continue
        n = 0
        for k in keys:
            try:
                n += int(tokens.get(k) or 0)
            except (TypeError, ValueError):
                pass
        cache = tokens.get("cache")
        if isinstance(cache, dict):
            try:
                n += int(cache.get("read") or 0)
            except (TypeError, ValueError):
                pass
        return n
    return None


# --- What a Build turn hears of the Chat half of its Conversation (#53) --------------------------
#
# A Conversation has two harness sessions and the Build one cannot see the Chat one. The only
# bridge used to be `.sage/handoff.md`, written once when the person crossed over, so a chart
# discussed a minute after the crossing was not in it and "make that chart bigger" had nothing to
# resolve against. This renders the transcript instead, freshly on every Build turn, which is what
# stops it going stale — and bounded, which is what stops the cost growing with the Conversation.

# Characters of transcript a Build turn carries. Roughly 500 tokens: enough for the last handful of
# exchanges, small enough that turn fifty costs the same as turn five.
SUMMARY_BUDGET = 2000
# One turn's share of it. Long turns are truncated rather than dropped — a 3000-character answer
# that ate the whole budget would push out the exchanges around it, and its first two sentences say
# what it was about anyway.
SUMMARY_TURN_CHARS = 400


def chat_summary(history: list[dict]) -> str:
    """One Thread's Chat transcript, newest turns first into a fixed budget, oldest dropped.

    Newest wins because that is what a follow-up points at: "make that chart bigger" is about the
    chart just discussed, not the opening question. Rendered back in the order it was said, so the
    agent reads a conversation rather than a reversed one.

    Only what was SAID — the person's turns and Sage's prose. Tool calls, artifact cards and turn
    dividers are transcript furniture, and a Build turn can read the files themselves.

    Empty string for a Conversation with no Chat turns, so the caller can leave the section out
    entirely rather than write an empty heading.
    """
    lines: list[str] = []
    used = 0
    for entry in reversed(history or []):
        line = _said(entry)
        if not line:
            continue
        # Counting the newline that will join it keeps `used` equal to the length of the string
        # this returns, so the budget is the real cap and not the cap minus one line's worth.
        cost = len(line) + (1 if lines else 0)
        if used + cost > SUMMARY_BUDGET:
            break
        lines.append(line)
        used += cost
    lines.reverse()
    return "\n".join(lines)


def _said(entry: dict) -> str:
    """One transcript line, or "" for an entry that is not something somebody said."""
    if not isinstance(entry, dict):
        return ""
    if entry.get("type") == "user":
        who = "They said"
    elif entry.get("type") == "agent" and entry.get("kind") == "text":
        who = "You said"
    else:
        return ""
    # Collapsed to one line: a pasted table or a multi-paragraph answer would otherwise spend the
    # budget on its own blank lines, and the bullet list has to stay readable as a list.
    text = " ".join(str(entry.get("text") or "").split())
    if not text:
        return ""
    if len(text) > SUMMARY_TURN_CHARS:
        text = text[: SUMMARY_TURN_CHARS - 1].rstrip() + "…"
    return f"- {who}: {text}"
