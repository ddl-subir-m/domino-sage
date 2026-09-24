"""Privacy-safe accounting for one final model request.

The result contains only fixed keys and integers. It never copies a value from the
request. Known content carriers contribute their serialized UTF-8 byte length; JSON
keys, delimiters, and unknown carriers remain in ``unclassifiedBytes``.
"""
from __future__ import annotations

import json

from .tool_result_window import is_media_part

_DIAGNOSTIC_TOOL_NAMES = frozenset({
    "apply_patch", "artifact_write", "bash", "delegated_model_call", "edit", "fetch", "glob",
    "get_file", "grep", "list", "live_read_files", "live_read_query", "live_read_table", "ls",
    "open", "patch", "question", "read", "read_file", "readfile", "replace",
    "sage-delegated_delegated_model_call",
    "sage-live-read_artifact_write", "sage-live-read_live_read_files",
    "sage-live-read_live_read_query", "sage-live-read_live_read_table", "skill", "task", "todo_write",
    "todo_read", "todoread", "todowrite", "view", "web_fetch", "web_search", "webfetch",
    "websearch", "write", "write_file",
})

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
_INSTRUCTION_SOURCES = (
    "topLevelInstructions", "topLevelSystem", "messageSystem", "messageDeveloper",
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
        "systemInstructionsBySource": {
            key: {"count": 0, "bytes": 0} for key in _INSTRUCTION_SOURCES
        },
        "toolSchemasByName": {},
        "exactDuplicateInstructionBlocks": {"count": 0, "bytes": 0},
        "buildBytes": {"fixedBytes": total, "dynamicBuildIntentBytes": 0,
                       "toolResultBytes": 0, "mediaBytes": 0},
        "buildInstructionProfile": _profile(rewrites),
        "implementationAssembly": _assembly(rewrites),
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


def _assembly(rewrites: dict | None) -> dict:
    raw = (rewrites or {}).get("implementationAssembly")
    if not isinstance(raw, dict):
        return {}

    def number(key):
        value = raw.get(key, 0)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    by_name = raw.get("removedToolSchemasByName")
    by_source = raw.get("duplicateInstructionBlocksRemovedBySource")
    removed: dict[str, int] = {}
    for name, value in (by_name.items() if isinstance(by_name, dict) else ()):
        if isinstance(name, str) and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            identifier = diagnostic_tool_name(name)
            removed[identifier] = removed.get(identifier, 0) + value
    return {
        "beforeBytes": number("beforeBytes"),
        "afterBytes": number("afterBytes"),
        "removedBytes": number("removedBytes"),
        "duplicateInstructionBlocksRemoved": number("duplicateInstructionBlocksRemoved"),
        "duplicateInstructionBytesRemoved": number("duplicateInstructionBytesRemoved"),
        "duplicateInstructionBlocksRemovedBySource": {
            source: value for source, value in (
                by_source.items() if isinstance(by_source, dict) else ())
            if source in _INSTRUCTION_SOURCES and isinstance(value, int)
            and not isinstance(value, bool) and value >= 0
        },
        "duplicateToolSchemasRemoved": number("duplicateToolSchemasRemoved"),
        "unreachableToolSchemasRemoved": number("unreachableToolSchemasRemoved"),
        "removedToolSchemasByName": removed,
    }


def _profile(rewrites: dict | None) -> dict:
    raw = (rewrites or {}).get("buildInstructionProfile")
    if not isinstance(raw, dict):
        return {}

    def number(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    profile = raw.get("profile")
    status = raw.get("status")
    if profile not in {"plan", "implement"} or status not in {"valid", "absent"}:
        return {}
    blocks = raw.get("removedStageBlocksById")
    tools = raw.get("removedToolSchemasByName")
    removed_tools: dict[str, int] = {}
    for name, count in (tools.items() if isinstance(tools, dict) else ()):
        identifier = diagnostic_tool_name(name)
        removed_tools[identifier] = removed_tools.get(identifier, 0) + number(count)
    implement = blocks.get("implement") if isinstance(blocks, dict) else None
    return {
        "profile": profile,
        "version": number(raw.get("version")),
        "status": status,
        "instructionBytesBefore": number(raw.get("instructionBytesBefore")),
        "instructionBytesAfter": number(raw.get("instructionBytesAfter")),
        "removedStageBlocksById": {
            "implement": {
                "count": number(implement.get("count")),
                "bytes": number(implement.get("bytes")),
            }
        } if isinstance(implement, dict) else {},
        "removedToolSchemasByName": removed_tools,
    }


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
            for tool in tools:
                name = _tool_name(tool)
                bucket = result["toolSchemasByName"].setdefault(
                    name, {"count": 0, "bytes": 0})
                bucket["count"] += 1
                bucket["bytes"] += wire_bytes(tool)

        if "instructions" in payload:
            categories["instructionsBytes"] += _instruction_bytes(payload["instructions"])
            _attribute_instructions(payload["instructions"], "topLevelInstructions", result)
            roles["system"]["count"] += 1
            roles["system"]["bytes"] += wire_bytes(payload["instructions"])
        if "system" in payload:
            categories["instructionsBytes"] += _instruction_bytes(payload["system"])
            _attribute_instructions(payload["system"], "topLevelSystem", result)
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
        dynamic = (categories["ordinaryTextBytes"] + categories["toolCallsBytes"]
                   + categories["opaqueStateBytes"])
        result["buildBytes"] = {
            "fixedBytes": total_bytes - dynamic - categories["toolResultsBytes"]
                          - categories["mediaBytes"],
            "dynamicBuildIntentBytes": dynamic,
            "toolResultBytes": categories["toolResultsBytes"],
            "mediaBytes": categories["mediaBytes"],
        }
        result.pop("_instructionFingerprints", None)
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
            _attribute_instructions(
                content, "messageSystem" if role == "system" else "messageDeveloper", result)
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


def _tool_name(tool) -> str:
    if not isinstance(tool, dict):
        return "unknown"
    function = tool.get("function")
    value = function.get("name") if isinstance(function, dict) else tool.get("name")
    return diagnostic_tool_name(value)


def diagnostic_tool_name(value: object) -> str:
    """Return a fixed public tool identifier, never a request-supplied unknown name."""
    if not isinstance(value, str):
        return "unknown"
    if value in _DIAGNOSTIC_TOOL_NAMES:
        return value
    for name in ("task", "todoread", "todowrite", "todo_read", "todo_write"):
        if value.endswith(("_" + name, "-" + name, "/" + name)):
            return name
    return "unknown"


def _instruction_blocks(content):
    if isinstance(content, str):
        yield content, wire_bytes(content)
    elif isinstance(content, list):
        for part in content:
            if (isinstance(part, dict)
                    and part.get("type") in ("text", "input_text", "output_text")):
                yield part, wire_bytes(part.get("text", ""))


def _attribute_instructions(content, source: str, result: dict) -> None:
    bucket = result["systemInstructionsBySource"][source]
    seen = result.setdefault("_instructionFingerprints", set())
    duplicates = result["exactDuplicateInstructionBlocks"]
    for block, size in _instruction_blocks(content):
        bucket["count"] += 1
        bucket["bytes"] += size
        fingerprint = json.dumps(block, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False)
        if fingerprint in seen:
            duplicates["count"] += 1
            duplicates["bytes"] += size
        else:
            seen.add(fingerprint)


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
        elif is_media_part(part):
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
