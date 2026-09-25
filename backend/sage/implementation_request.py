"""Canonical assembly for Build's final provider request."""
from __future__ import annotations

import json
import re

# These tools have implementations, but not on a Build implementation turn. The artifact writer
# and delegated model call are scoped to Chat by the enforcement shim. Keep both OpenCode spellings
# here because MCP tools can arrive with their server name prefixed.
UNREACHABLE_TOOLS = frozenset({
    "artifact_write",
    "sage-live-read_artifact_write",
    "delegated_model_call",
    "sage-delegated_delegated_model_call",
})

PROFILE_VERSION = 1
_PROFILE_PREFIX = "<!-- sage:build-profile:"
_MARKER = re.compile(
    r"(?m)^<!-- sage:build-profile:v(?P<version>[0-9]+):"
    r"(?P<block>[a-z][a-z0-9_-]*):(?P<edge>begin|end) -->[ \t]*\n?"
)
# `common` and `implement` are REQUIRED and carry the v1 contract unchanged. The optional ids are
# sections an implement turn only sometimes needs; a template that omits them is still valid, which
# is what keeps every app built before this change working. Apps keep their own `AGENTS.md` and are
# never re-seeded, so the four-marker v1 shape has to stay valid forever, not just for a release.
#
# The set is deliberately CLOSED and ordered. An id outside it still raises, so this widens the
# grammar by exactly two names rather than making the validator permissive. A template can carry
# 4 distinct shapes: the required pair alone (the v1 shape), plus either or both optional ids.
_REQUIRED_BLOCKS = ("common", "implement")
_OPTIONAL_BLOCKS = ("design", "platform")
_BLOCK_ORDER = _REQUIRED_BLOCKS + _OPTIONAL_BLOCKS

#: Every optional section. The shim passes this until a per-turn trigger is chosen, so that the
#: grammar can withhold a section without any turn yet losing one.
IMPLEMENT_SECTIONS = frozenset(_OPTIONAL_BLOCKS)


class BuildInstructionProfileError(ValueError):
    """The Build request contains an invalid stage-profile contract."""


def _content_text(block: object) -> str | None:
    if isinstance(block, str):
        return block
    if (isinstance(block, dict) and block.get("type") in
            {"text", "input_text", "output_text"}):
        value = block.get("text")
        return value if isinstance(value, str) else None
    return None


def _replace_content_text(block: object, text: str) -> object:
    if isinstance(block, str):
        return text
    return {**block, "text": text}


def _marked_blocks(text: str) -> tuple[list, list[tuple[str, str]]]:
    """Return the marker matches and the ordered (id, body) pairs, or raise.

    Rejects unknown ids/versions, duplicate groups, nesting, unpaired edges, out-of-order
    groups, and any prefix the strict expression did not consume.
    """
    matches = list(_MARKER.finditer(text))
    found = [(int(match.group("version")), match.group("block"), match.group("edge"))
             for match in matches]
    if len(found) % 2 or text.count(_PROFILE_PREFIX) != len(matches):
        raise BuildInstructionProfileError("Invalid Build instruction profile markers")

    blocks: list[tuple[str, str]] = []
    for index in range(0, len(found), 2):
        (begin_version, begin_id, begin_edge) = found[index]
        (end_version, end_id, end_edge) = found[index + 1]
        if (begin_version != PROFILE_VERSION or end_version != PROFILE_VERSION
                or begin_edge != "begin" or end_edge != "end" or begin_id != end_id
                or begin_id not in _BLOCK_ORDER
                or any(begin_id == seen for seen, _ in blocks)):
            raise BuildInstructionProfileError("Invalid Build instruction profile markers")
        blocks.append((begin_id, text[matches[index].end():matches[index + 1].start()]))

    ids = [block_id for block_id, _ in blocks]
    if [block_id for block_id in _BLOCK_ORDER if block_id in ids] != ids:
        raise BuildInstructionProfileError("Invalid Build instruction profile markers")
    if any(required not in ids for required in _REQUIRED_BLOCKS):
        raise BuildInstructionProfileError("Invalid Build instruction profile markers")
    return matches, blocks


def _profile_text(text: str, profile: str,
                  sections: frozenset[str]) -> tuple[str, dict[str, int]] | None:
    """Apply one complete marked profile, or leave unrelated instruction text alone."""
    if _PROFILE_PREFIX not in text:
        return None
    matches, blocks = _marked_blocks(text)
    bodies = dict(blocks)
    if not bodies["common"].strip():
        raise BuildInstructionProfileError("The Build common instruction profile is empty")

    kept_ids = {"common"}
    if profile == "implement":
        # An optional section rides the turn only when the turn asked for it. It stays in the
        # workspace `AGENTS.md` either way, so dropping it withholds it from the prompt, not
        # from the model.
        kept_ids.add("implement")
        kept_ids |= {block_id for block_id in _OPTIONAL_BLOCKS if block_id in sections}
    elif profile != "plan":  # Callers use a fixed stage enum; keep this guard local for direct tests.
        raise BuildInstructionProfileError("Unknown Build instruction profile")

    parts: list[str] = []
    removed: dict[str, int] = {}
    cursor = 0
    for index, (block_id, body) in enumerate(blocks):
        parts.append(text[cursor:matches[2 * index].start()])
        if block_id in kept_ids:
            parts.append(body)
        else:
            removed[block_id] = _wire_bytes(body)
        cursor = matches[2 * index + 1].end()
    parts.append(text[cursor:])
    return "".join(parts), removed


def apply_instruction_profile(request: dict, profile: str, *,
                              removed_tools: dict[str, int] | None = None,
                              sections: frozenset[str] = frozenset()) -> tuple[dict, dict]:
    """Select the marked Build instructions without retaining any instruction content."""
    before_bytes = after_bytes = 0
    profiled_blocks = 0
    removed_by_id: dict[str, dict[str, int]] = {}
    result = {**request}
    messages = result.get("messages")
    if isinstance(messages, list):
        updated_messages = []
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in {"system", "developer"}:
                updated_messages.append(message)
                continue
            content = message.get("content")
            blocks = content if isinstance(content, list) else [content]
            updated_blocks = []
            changed = False
            for block in blocks:
                text = _content_text(block)
                if text is None:
                    updated_blocks.append(block)
                    continue
                before_bytes += _wire_bytes(text)
                selected = _profile_text(text, profile, sections)
                if selected is None:
                    updated_blocks.append(block)
                    after_bytes += _wire_bytes(text)
                    continue
                profiled_blocks += 1
                if profiled_blocks > 1:
                    raise BuildInstructionProfileError("Duplicate Build instruction profiles")
                selected_text, removed = selected
                updated_blocks.append(_replace_content_text(block, selected_text))
                after_bytes += _wire_bytes(selected_text)
                changed = True
                for block_id, size in removed.items():
                    row = removed_by_id.setdefault(block_id, {"count": 0, "bytes": 0})
                    row["count"] += 1
                    row["bytes"] += size
            updated_content = updated_blocks if isinstance(content, list) else updated_blocks[0]
            updated_messages.append({**message, "content": updated_content} if changed else message)
        result["messages"] = updated_messages

    status = "valid" if profiled_blocks == 1 else "absent"
    return result, {
        "profile": profile,
        "version": PROFILE_VERSION,
        "status": status,
        "instructionBytesBefore": before_bytes,
        "instructionBytesAfter": after_bytes,
        "removedStageBlocksById": removed_by_id,
        "removedToolSchemasByName": dict(sorted((removed_tools or {}).items())),
    }


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
