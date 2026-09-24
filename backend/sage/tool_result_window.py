"""Bound the privacy-safe tool-result copy sent to a Build model."""
from __future__ import annotations

import json
from typing import Protocol

SHORTENED_RECEIPT = (
    "[Sage shortened this tool result to fit the model context. The beginning and end are shown. "
    "Run a narrower command or bounded read for the omitted section.]"
)
COMPACTED_RECEIPT = (
    "[Sage removed this older tool result from the model context to keep the Build bounded. "
    "Run a bounded read again if it is still needed.]"
)


class ToolResultWindowPolicy(Protocol):
    """The fields this transformation consumes from the central BuildPolicy."""

    tool_result_max_bytes: int
    tool_result_aggregate_max_bytes: int
    tool_result_head_fraction: float


def _json_bytes(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _is_media(part) -> bool:
    if not isinstance(part, dict):
        return False
    kind = str(part.get("type") or "").lower()
    mime = str(part.get("mime") or part.get("mimeType") or "").lower()
    if kind in {"image", "image_url", "input_image", "audio", "input_audio", "video", "input_video"}:
        return True
    if kind == "file" and mime.startswith(("image/", "audio/", "video/")):
        return True
    url = str(part.get("url") or "").lower()
    if kind == "file" and url.startswith(("data:image/", "data:audio/", "data:video/")):
        return True
    source = part.get("source")
    return isinstance(source, dict) and source.get("type") == "base64" and str(
        source.get("media_type") or ""
    ).lower().startswith(("image/", "audio/", "video/"))


def _content_bytes(content) -> int:
    """Count the UTF-8 bytes of non-media content, including text-block framing."""
    if isinstance(content, str):
        if content.lower().startswith(("data:image/", "data:audio/", "data:video/")):
            return 0
        return len(content.encode("utf-8"))
    if isinstance(content, list):
        non_media = [part for part in content if not _is_media(part)]
        return _json_bytes(non_media) if non_media else 0
    if content is None:
        return 0
    return _json_bytes(content)


def _model_content(message: dict, content):
    """Return the non-outer content shape native rendering will send."""
    wire = message.get("_wire") or {}
    original = wire.get("original") or {}
    kind = original.get("type")
    if content == wire.get("content"):
        if kind == "tool_result":
            return original.get("content", "")
        if kind == "function_call_output":
            return original.get("output", "")
    if kind == "tool_result":
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        return content
    if kind == "function_call_output" and isinstance(content, list):
        return [
            {**part, "type": "input_text"}
            if isinstance(part, dict) and part.get("type") == "text" else part
            for part in content
        ]
    return content


def _result_bytes(message: dict, content=None) -> int:
    if content is None:
        content = message.get("content")
    return _content_bytes(_model_content(message, content))


def _plain_text(content) -> str | None:
    if isinstance(content, str):
        if content.lower().startswith(("data:image/", "data:audio/", "data:video/")):
            return None
        return content
    if not isinstance(content, list):
        return None
    parts = []
    for part in content:
        if _is_media(part):
            continue
        if (not isinstance(part, dict) or part.get("type") != "text"
                or not isinstance(part.get("text"), str)):
            return None
        parts.append(part["text"])
    return "".join(parts)


def _replace_non_media(content, text: str):
    """Replace text or structured content while keeping every media block byte-for-byte."""
    if not isinstance(content, list):
        return text
    out = []
    inserted = False
    for part in content:
        if _is_media(part):
            out.append(part)
        elif not inserted:
            if text:
                out.append({"type": "text", "text": text})
            inserted = True
    if text and not inserted:
        out.insert(0, {"type": "text", "text": text})
    return out


def _empty_non_media(message: dict, content):
    original = (message.get("_wire") or {}).get("original") or {}
    if original.get("type") == "tool_result" and not isinstance(content, list):
        # Messages rendering turns a changed string into one text block. An empty list is the
        # protocol-valid representation with zero non-media content and zero text-block framing.
        return []
    return _replace_non_media(content, "")


def _piece_cost(text: str, *, framed: bool) -> int:
    if not framed:
        return len(text.encode("utf-8"))
    # Remove the two JSON quote bytes. Escapes remain because they occupy model-facing framing.
    return _json_bytes(text) - 2


def _prefix_within(text: str, budget: int, *, framed: bool) -> str:
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _piece_cost(text[:middle], framed=framed) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def _suffix_within(text: str, budget: int, *, framed: bool) -> str:
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _piece_cost(text[len(text) - middle:], framed=framed) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[len(text) - low:] if low else ""


def _shorten(message: dict, limit: int, head_fraction: float):
    content = message.get("content")
    plain = _plain_text(content)
    if plain is None:
        replacement = _replace_non_media(content, SHORTENED_RECEIPT)
        return replacement if _result_bytes(message, replacement) <= limit else _empty_non_media(
            message, content
        )
    receipt_only = _replace_non_media(content, SHORTENED_RECEIPT)
    remaining = max(0, limit - _result_bytes(message, receipt_only))
    head_budget = int(remaining * head_fraction)
    tail_budget = remaining - head_budget
    framed = isinstance(_model_content(message, receipt_only), list)
    head = _prefix_within(plain, head_budget, framed=framed)
    tail = _suffix_within(plain, tail_budget, framed=framed)
    shortened = _replace_non_media(content, head + SHORTENED_RECEIPT + tail)
    # A configured limit smaller than the fixed receipt cannot carry the receipt. Empty content is
    # the only protocol-valid result that still honours the hard byte bound.
    if _result_bytes(message, shortened) > limit:
        return _empty_non_media(message, content)
    return shortened


def _metadata(policy: ToolResultWindowPolicy, *, result_count: int, original: int,
              forwarded: int, shortened: int, compacted: int, emptied: int) -> dict:
    return {
        "policyVersion": 1,
        "perResultLimitBytes": policy.tool_result_max_bytes,
        "aggregateLimitBytes": policy.tool_result_aggregate_max_bytes,
        "resultCount": result_count,
        "originalModelFacingBytes": original,
        "forwardedModelFacingBytes": forwarded,
        "perResultShortenedCount": shortened,
        "aggregateCompactedCount": compacted,
        "emptyFallbackCount": emptied,
    }


def apply_tool_result_window(request: dict, policy: ToolResultWindowPolicy) -> tuple[dict, dict]:
    """Return a bounded request copy and content-free measurement metadata.

    Only ``role: tool`` message content is changed. The source request, call messages, result ids,
    status fields, message order, and media blocks remain unchanged.
    """
    source = request.get("messages")
    if not isinstance(source, list):
        return request.copy(), _metadata(
            policy, result_count=0, original=0, forwarded=0,
            shortened=0, compacted=0, emptied=0,
        )

    messages = list(source)
    indices = [index for index, message in enumerate(messages)
               if isinstance(message, dict) and message.get("role") == "tool"]
    original = sum(_result_bytes(messages[index]) for index in indices)
    shortened_count = 0
    for index in indices:
        message = messages[index]
        if _result_bytes(message) <= policy.tool_result_max_bytes:
            continue
        messages[index] = {
            **message,
            "content": _shorten(
                message, policy.tool_result_max_bytes, policy.tool_result_head_fraction
            ),
        }
        shortened_count += 1

    forwarded = sum(_result_bytes(messages[index]) for index in indices)
    compacted = []
    if forwarded > policy.tool_result_aggregate_max_bytes:
        for index in indices:
            message = messages[index]
            content = message.get("content")
            before = _result_bytes(message)
            if before == 0:
                continue
            replacement = _replace_non_media(content, COMPACTED_RECEIPT)
            messages[index] = {**message, "content": replacement}
            forwarded += _result_bytes(messages[index]) - before
            compacted.append(index)
            if forwarded <= policy.tool_result_aggregate_max_bytes:
                break

    emptied = 0
    if forwarded > policy.tool_result_aggregate_max_bytes and compacted:
        # Every textual result was compacted. Keep one aggregate receipt on the newest result and
        # make the earlier results empty. Their messages and ids remain present and paired.
        newest = compacted[-1]
        for index in compacted[:-1]:
            message = messages[index]
            content = message.get("content")
            before = _result_bytes(message)
            replacement = _empty_non_media(message, content)
            messages[index] = {**message, "content": replacement}
            forwarded += _result_bytes(messages[index]) - before
            emptied += 1
        message = messages[newest]
        content = message.get("content")
        before = _result_bytes(message)
        replacement = _replace_non_media(content, COMPACTED_RECEIPT)
        messages[newest] = {**message, "content": replacement}
        forwarded += _result_bytes(messages[newest]) - before

    result = {**request, "messages": messages}
    return result, _metadata(
        policy,
        result_count=len(indices),
        original=original,
        forwarded=forwarded,
        shortened=shortened_count,
        compacted=len(compacted),
        emptied=emptied,
    )
