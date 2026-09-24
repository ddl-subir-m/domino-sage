"""Canonical assembly for the implementation route's final policy request."""
from __future__ import annotations

import json

# These tools have implementations, but not on a Build implementation turn. The artifact writer
# and delegated model call are scoped to Chat by the enforcement shim. Keep both OpenCode spellings
# here because MCP tools can arrive with their server name prefixed.
UNREACHABLE_TOOLS = frozenset({
    "artifact_write",
    "sage-live-read_artifact_write",
    "delegated_model_call",
    "sage-delegated_delegated_model_call",
})


def _wire_bytes(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8"))


def _tool_name(tool: object) -> str:
    if not isinstance(tool, dict):
        return ""
    function = tool.get("function")
    value = function.get("name") if isinstance(function, dict) else tool.get("name")
    return value if isinstance(value, str) else ""


def _instruction_key(block: object) -> bytes:
    return json.dumps(block, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _deduplicate_instruction_content(content: object, seen: set[bytes]) -> tuple[object, int, int]:
    """Return content with exact blocks removed, plus removed block count and bytes."""
    if isinstance(content, str):
        key = _instruction_key(content)
        if key in seen:
            return "", 1, _wire_bytes(content)
        seen.add(key)
        return content, 0, 0
    if not isinstance(content, list):
        return content, 0, 0

    kept = []
    removed = removed_bytes = 0
    for block in content:
        # Only text is an instruction block. Preserve unknown/provider blocks byte for byte.
        is_text = isinstance(block, dict) and block.get("type") in {
            "text", "input_text", "output_text",
        }
        if not is_text:
            kept.append(block)
            continue
        key = _instruction_key(block)
        if key in seen:
            removed += 1
            removed_bytes += _wire_bytes(block.get("text", ""))
        else:
            seen.add(key)
            kept.append(block)
    return kept, removed, removed_bytes


def assemble(request: dict) -> tuple[dict, dict]:
    """Build one ordered implementation request without duplicate instructions or tool names.

    The first instruction block and the first schema for a tool name win. The input is not mutated.
    The report contains identifiers and counts only.
    """
    before_bytes = _wire_bytes(request)
    result = {**request}
    seen_instructions: set[bytes] = set()
    duplicate_blocks = duplicate_block_bytes = 0
    duplicate_blocks_by_source: dict[str, int] = {}

    messages = result.get("messages")
    if isinstance(messages, list):
        kept_messages = []
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in {"system", "developer"}:
                kept_messages.append(message)
                continue
            content, count, size = _deduplicate_instruction_content(
                message.get("content"), seen_instructions)
            duplicate_blocks += count
            duplicate_block_bytes += size
            if count:
                source = "messageSystem" if message.get("role") == "system" else "messageDeveloper"
                duplicate_blocks_by_source[source] = duplicate_blocks_by_source.get(source, 0) + count
            if content not in ("", []):
                kept_messages.append({**message, "content": content})
        result["messages"] = kept_messages

    seen_tools: set[str] = set()
    duplicate_tools = unreachable_tools = 0
    removed_by_name: dict[str, int] = {}
    tools = result.get("tools")
    if isinstance(tools, list):
        kept_tools = []
        for tool in tools:
            name = _tool_name(tool)
            if name in UNREACHABLE_TOOLS:
                unreachable_tools += 1
                removed_by_name[name] = removed_by_name.get(name, 0) + 1
                continue
            if name and name in seen_tools:
                duplicate_tools += 1
                removed_by_name[name] = removed_by_name.get(name, 0) + 1
                continue
            if name:
                seen_tools.add(name)
            kept_tools.append(tool)
        result["tools"] = kept_tools

    after_bytes = _wire_bytes(result)
    return result, {
        "beforeBytes": before_bytes,
        "afterBytes": after_bytes,
        "removedBytes": before_bytes - after_bytes,
        "duplicateInstructionBlocksRemoved": duplicate_blocks,
        "duplicateInstructionBytesRemoved": duplicate_block_bytes,
        "duplicateInstructionBlocksRemovedBySource": duplicate_blocks_by_source,
        "duplicateToolSchemasRemoved": duplicate_tools,
        "unreachableToolSchemasRemoved": unreachable_tools,
        "removedToolSchemasByName": removed_by_name,
    }


def assemble_for_route(request: dict, *, mode: str, phase: str,
                       chat_thread_id: str | None) -> tuple[dict, dict]:
    """Apply canonical assembly only to the implementation route."""
    if chat_thread_id is not None or not (
            mode == "implement" or mode == "auto" and phase == "implement"):
        return request, {}
    return assemble(request)
