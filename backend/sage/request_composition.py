"""Privacy-safe accounting for one final model request.

The result contains only fixed keys and integers. It never copies a value from the
request. Known content carriers contribute their serialized UTF-8 byte length; JSON
keys, delimiters, and unknown carriers remain in ``unclassifiedBytes``.
"""
from __future__ import annotations

import json

MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_NODES = 100_000
MAX_DEPTH = 64
_CATEGORIES = (
    "instructionsBytes", "toolSchemasBytes", "ordinaryTextBytes", "toolCallsBytes",
    "toolResultsBytes", "mediaBytes", "opaqueStateBytes",
)
_ROLES = ("system", "developer", "user", "assistant", "tool", "unknown")
_REWRITES = (
    "redactedCalls", "localExecutionReceipts", "markerEchoCorrections",
    "externalImageReceipts", "withheldImageReceipts",
)


class _Limit(Exception):
    def __init__(self, reason: str):
        self.reason = reason


def wire_bytes(value) -> int:
    """Use the same compact UTF-8 JSON settings as the native route."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8"))


def _shape(value, depth=0, seen=None) -> None:
    """Bound the measurement traversal without retaining request content."""
    if depth > MAX_DEPTH:
        raise _Limit("depth")
    if seen is None:
        seen = [0]
    seen[0] += 1
    if seen[0] > MAX_NODES:
        raise _Limit("nodes")
    if isinstance(value, dict):
        for child in value.values():
            _shape(child, depth + 1, seen)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _shape(child, depth + 1, seen)


def _blank(total: int, rewrites: dict | None, *, status="complete", reason=None) -> dict:
    def count(key):
        value = (rewrites or {}).get(key, 0)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    result = {
        "version": 1,
        "boundary": "final_forwarded_json",
        "status": status,
        "totalBytes": total,
        "categories": {**{key: 0 for key in _CATEGORIES}, "unclassifiedBytes": total},
        "messagesByRole": {role: {"count": 0, "bytes": 0} for role in _ROLES},
        "toolSchemaCount": 0,
        "toolCallCount": 0,
        "toolArgumentsBytes": 0,
        "rewrites": {key: count(key) for key in _REWRITES},
        "limitReason": reason,
    }
    window = (rewrites or {}).get("toolResultWindow")
    if isinstance(window, dict):
        keys = (
            "policyVersion", "perResultLimitBytes", "aggregateLimitBytes", "resultCount",
            "originalModelFacingBytes", "forwardedModelFacingBytes", "perResultShortenedCount",
            "aggregateCompactedCount", "emptyFallbackCount",
        )
        result["toolResultWindow"] = {
            key: (window[key] if isinstance(window.get(key), int)
                  and not isinstance(window.get(key), bool) and window[key] >= 0 else 0)
            for key in keys
        }
    return result


def _role(value) -> str:
    return value if value in _ROLES[:-1] else "unknown"


def measure(payload: dict, total_bytes: int, rewrites: dict | None = None) -> dict:
    """Measure known final-wire carriers and reconcile them to ``total_bytes``."""
    result = _blank(total_bytes, rewrites)
    if total_bytes > MAX_PAYLOAD_BYTES:
        return _blank(total_bytes, rewrites, status="limited", reason="payload_bytes")
    try:
        _shape(payload)
        categories = result["categories"]
        roles = result["messagesByRole"]

        tools = payload.get("tools")
        if isinstance(tools, list):
            categories["toolSchemasBytes"] += wire_bytes(tools)
            result["toolSchemaCount"] = len(tools)

        if "instructions" in payload:
            categories["instructionsBytes"] += _instruction_bytes(payload["instructions"])
            roles["system"]["count"] += 1
            roles["system"]["bytes"] += wire_bytes(payload["instructions"])
        if "system" in payload:
            categories["instructionsBytes"] += _instruction_bytes(payload["system"])
            roles["system"]["count"] += 1
            roles["system"]["bytes"] += wire_bytes(payload["system"])

        rows = payload.get("messages")
        if not isinstance(rows, list):
            rows = payload.get("input")
        if isinstance(rows, str):
            categories["ordinaryTextBytes"] += wire_bytes(rows)
            roles["user"]["count"] += 1
            roles["user"]["bytes"] += wire_bytes(rows)
        elif isinstance(rows, list):
            for row in rows:
                _measure_row(row, categories, roles, result)

        classified = sum(categories[key] for key in _CATEGORIES)
        # A future/unknown carrier can overlap a known container only if this classifier is wrong.
        # Fall closed to a limited record rather than publish negative or irreconcilable accounting.
        if classified > total_bytes:
            return _blank(total_bytes, rewrites, status="limited", reason="classification_overlap")
        categories["unclassifiedBytes"] = total_bytes - classified
        return result
    except _Limit as error:
        return _blank(total_bytes, rewrites, status="limited", reason=error.reason)
    except Exception:
        return _blank(total_bytes, rewrites, status="unavailable", reason="measurement_error")


def _measure_row(row, categories, roles, result) -> None:
    if not isinstance(row, dict):
        return
    kind = row.get("type")
    role = row.get("role")
    if kind == "reasoning":
        categories["opaqueStateBytes"] += wire_bytes(row)
        role = "assistant"
    elif kind in ("function_call", "tool_use"):
        categories["toolCallsBytes"] += wire_bytes(row)
        result["toolCallCount"] += 1
        result["toolArgumentsBytes"] += wire_bytes(
            row.get("arguments", row.get("input", {})))
        role = "assistant"
    elif kind in ("function_call_output", "tool_result"):
        categories["toolResultsBytes"] += wire_bytes(row)
        role = "tool"
    else:
        content = row.get("content")
        if role in ("system", "developer"):
            categories["instructionsBytes"] += _instruction_bytes(content)
        elif role == "tool":
            categories["toolResultsBytes"] += wire_bytes(content)
        else:
            _measure_content(content, categories, result)
        calls = row.get("tool_calls")
        if isinstance(calls, list):
            categories["toolCallsBytes"] += wire_bytes(calls)
            result["toolCallCount"] += len(calls)
            for call in calls:
                if isinstance(call, dict):
                    function = call.get("function") or {}
                    result["toolArgumentsBytes"] += wire_bytes(function.get("arguments", {}))

    bucket = roles[_role(role)]
    bucket["count"] += 1
    bucket["bytes"] += wire_bytes(row)


def _measure_content(content, categories, result) -> None:
    if isinstance(content, str):
        categories["ordinaryTextBytes"] += wire_bytes(content)
        return
    if not isinstance(content, list):
        return
    for part in content:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in ("text", "input_text", "output_text"):
            categories["ordinaryTextBytes"] += wire_bytes(part.get("text", ""))
        elif kind in ("image", "image_url", "input_image"):
            categories["mediaBytes"] += wire_bytes(part)
        elif kind in ("thinking", "redacted_thinking", "reasoning"):
            categories["opaqueStateBytes"] += wire_bytes(part)
        elif kind in ("tool_use", "function_call"):
            categories["toolCallsBytes"] += wire_bytes(part)
            result["toolCallCount"] += 1
            result["toolArgumentsBytes"] += wire_bytes(
                part.get("input", part.get("arguments", {})))
        elif kind in ("tool_result", "function_call_output"):
            categories["toolResultsBytes"] += wire_bytes(part)


def _instruction_bytes(content) -> int:
    if isinstance(content, str):
        return wire_bytes(content)
    if not isinstance(content, list):
        return 0
    return sum(wire_bytes(part.get("text", "")) for part in content
               if isinstance(part, dict)
               and part.get("type") in ("text", "input_text", "output_text"))
