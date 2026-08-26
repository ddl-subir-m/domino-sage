"""When a Chat OpenCode session should be compacted, and which model to ask OpenCode to use.

Sage `history.jsonl` is the UI replay and is never rewritten. Compaction only shrinks what the
model sees on the next Chat turn (docs/workbench/chat.md).
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

# Matches opencode.json `provider.sage-gateway.models.*.limit.context`. Unknown aliases get the
# conservative default so we compact before a 32k window overflows, not after a 200k one.
CONTEXT_LIMITS: dict[str, int] = {
    "deepseek-v4-pro": 128_000,
    "qwen-2-5": 32_768,
    "local-domino-llm": 32_768,
    "bedrock-qwen3-coder": 128_000,
    "gpt-5.4": 200_000,
    "sonnet": 200_000,
    "haiku": 200_000,
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
