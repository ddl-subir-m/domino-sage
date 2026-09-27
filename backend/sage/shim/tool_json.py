"""Mechanical repair of tool-call arguments, and redaction of the invalid-tool result.

OpenCode executes the arguments the model sent. A finished JSON object is repaired only when
the edit is a raw newline, tab, or carriage return inside a string, or a comma whose next
non-space character is ``}`` or ``]``. An unescaped quote stays as the model wrote it: a
guessed quote can be legal JSON and the wrong SQL, and that call would run. A call that does
not end in ``}``, or that the provider closed with an output cap, is forwarded unchanged.
Already-valid JSON is forwarded byte for byte. The repaired text is the edited source, not a
re-serialized object, so key order and every other escape stay as the model wrote them.

Responses executes ``response.output_item.done`` ``item.arguments`` and ignores the argument
deltas, so that one field is rewritten. Chat and Messages concatenate fragments, so a
non-empty fragment is held until the call closes; releasing it earlier would glue a repaired
string onto the broken prefix. The result has to be a JSON object.

The invalid tool's result quotes the raw arguments (``Text: ...``). The next request replaces
that text with a closed sentence. The message stays: dropping a tool result while its tool
call remains is an HTTP 400. A repaired call never produces that result.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from ..gateway.protocol import Protocol

log = logging.getLogger("sage.shim")

_BOUNDARY = re.compile(br"\r?\n\r?\n")
_CUT_FINISH = frozenset({"length", "max_tokens", "content_filter"})
_HEALTHY_FINISH = frozenset({"stop", "tool_calls"})
_MESSAGE_CUT = frozenset({"max_tokens", "pause_turn"})
_WS = frozenset(" \t\r\n")
_STRING_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}

# OpenCode's invalid tool prefixes its output with this and then the parser error, which
# embeds the raw arguments. The replacement does not, so a second pass leaves it alone.
INVALID_TOOL_PREFIX = "The arguments provided to the tool are invalid:"
INVALID_TOOL_CLOSED = (
    "The arguments provided to the tool are invalid. "
    "Send the call again as one valid JSON object with every required field."
)


class _Done:
    pass


_DONE = _Done()


def repair_json_object(text: str) -> str | None:
    """The edited source when one mechanical pass produces a JSON object, else None.

    None means the caller forwards the original bytes: the text is already valid, it is not
    a finished object, the edit changed nothing, or the edit still does not parse as an object.
    """
    if not isinstance(text, str) or not text.rstrip().endswith("}"):
        return None
    try:
        json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        return None
    edited = _mechanical(text)
    if edited == text:
        return None
    try:
        parsed = json.loads(edited)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return edited


def _mechanical(text: str) -> str:
    out: list[str] = []
    in_string = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            if escape:
                out.append(ch)
                escape = False
            elif ch == "\\":
                out.append(ch)
                escape = True
            elif ch == '"':
                out.append(ch)
                in_string = False
            elif ch in _STRING_ESCAPES:
                out.append(_STRING_ESCAPES[ch])
            else:
                out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in _WS:
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _argument_class(text: str, fixed: str | None, *, cut: bool) -> str | None:
    if cut or not text.rstrip().endswith("}"):
        return "cutoff"
    if fixed is not None:
        return "repaired"
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return "unparsed"
    return None


def redact_invalid_tool_results(messages: list) -> list:
    """Replace an invalid-tool result's text. Leave every other message alone."""
    if not isinstance(messages, list):
        return messages
    out = []
    changed = False
    for message in messages:
        new = _redact_message(message)
        if new is not message:
            changed = True
        out.append(new)
    return out if changed else messages


def _redact_message(message: object) -> object:
    if not isinstance(message, dict) or message.get("role") != "tool":
        return message
    content = message.get("content")
    new = _redact_content(content)
    if new is content:
        return message
    return {**message, "content": new}


def _redact_content(content: object) -> object:
    if isinstance(content, str):
        if content.startswith(INVALID_TOOL_PREFIX):
            return INVALID_TOOL_CLOSED
        return content
    if isinstance(content, list):
        for part in content:
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(text, str) and text.startswith(INVALID_TOOL_PREFIX):
                return INVALID_TOOL_CLOSED
    return content


def _event_of(frame: bytes) -> dict | _Done | None:
    data = b"\n".join(
        line[5:].lstrip(b" ") for line in frame.splitlines() if line.startswith(b"data:")
    )
    if not data:
        return None
    if data == b"[DONE]":
        return _DONE
    try:
        event = json.loads(data)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(event, dict):
        return None
    return event


def _rewrite_data(frame: bytes, event: dict) -> bytes:
    payload = json.dumps(event).encode()
    lines = []
    replaced = False
    for line in frame.splitlines(keepends=True):
        raw = line.rstrip(b"\r\n")
        if raw.startswith(b"data:"):
            if not replaced:
                lines.append(b"data: " + payload + line[len(raw):])
                replaced = True
            continue
        lines.append(line)
    return b"".join(lines) if replaced else frame


@dataclass
class _Lane:
    parts: list[str] = field(default_factory=list)


class ArgumentRepair:
    """Forward tool-argument SSE, repairing a finished object once the call closes."""

    def __init__(self, protocol: Protocol):
        self.protocol = protocol
        self._pending = b""
        self._cut = False
        self._held: list[bytes] = []
        self._lanes: dict[object, _Lane] = {}

    def push_chunk(self, chunk: bytes) -> list[bytes]:
        """Legacy relays hand over arbitrary byte splits. Frames are split on a blank line."""
        if not chunk:
            return []
        self._pending += chunk
        out: list[bytes] = []
        while match := _BOUNDARY.search(self._pending):
            frame = self._pending[:match.end()]
            self._pending = self._pending[match.end():]
            out.extend(self._accept(frame))
        return out

    def push_frames(self, frames: list[bytes]) -> list[bytes]:
        """Native routes already have the frames ``StreamEvents.feed`` returned."""
        out: list[bytes] = []
        for frame in frames:
            out.extend(self._accept(frame))
        return out

    def finish(self) -> list[bytes]:
        """The stream ended. Anything still held was cut off; forward it unchanged."""
        out: list[bytes] = []
        if self._pending:
            out.append(self._pending)
            self._pending = b""
        out.extend(self._flush(repair=False))
        return out

    def _accept(self, frame: bytes) -> list[bytes]:
        if self.protocol is Protocol.RESPONSES:
            return self._responses(frame)
        if self.protocol is Protocol.MESSAGES:
            return self._messages(frame)
        return self._chat(frame)

    def _responses(self, frame: bytes) -> list[bytes]:
        event = _event_of(frame)
        if not isinstance(event, dict):
            return [frame]
        kind = event.get("type")
        if kind in ("response.incomplete", "response.failed"):
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            details = response.get("incomplete_details")
            if not isinstance(details, dict):
                details = {}
            reason = details.get("reason")
            if kind == "response.failed" or reason in ("max_output_tokens", "content_filter"):
                self._cut = True
            return [frame]
        if self._cut:
            return [frame]
        if kind == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                return self._repair_field(frame, event, item)
        if kind == "response.function_call_arguments.done":
            return self._repair_field(frame, event, event)
        return [frame]

    def _repair_field(self, frame: bytes, event: dict, carrier: dict) -> list[bytes]:
        text = carrier.get("arguments")
        if not isinstance(text, str) or not text.strip():
            return [frame]
        fixed = repair_json_object(text)
        self._log(text, fixed, cut=False)
        if fixed is None:
            return [frame]
        carrier["arguments"] = fixed
        return [_rewrite_data(frame, event)]

    def _chat(self, frame: bytes) -> list[bytes]:
        event = _event_of(frame)
        if event is _DONE:
            return self._flush(repair=False) + [frame]
        if not isinstance(event, dict):
            if self._held:
                self._held.append(frame)
                return []
            return [frame]
        finish, has_args = self._read_chat(event)
        if isinstance(finish, str) and finish in _CUT_FINISH:
            self._cut = True
        if self._held or has_args:
            self._held.append(frame)
            if finish:
                healthy = isinstance(finish, str) and finish in _HEALTHY_FINISH and not self._cut
                return self._flush(repair=healthy)
            return []
        return [frame]

    def _read_chat(self, event: dict) -> tuple[object, bool]:
        finish = None
        has_args = False
        choices = event.get("choices")
        if not isinstance(choices, list):
            return None, False
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            tools = delta.get("tool_calls")
            if not isinstance(tools, list):
                continue
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
                arguments = function.get("arguments")
                if isinstance(arguments, str) and arguments:
                    index = tool.get("index", 0)
                    self._lanes.setdefault(index, _Lane()).parts.append(arguments)
                    has_args = True
        return finish, has_args

    def _messages(self, frame: bytes) -> list[bytes]:
        event = _event_of(frame)
        if event is _DONE:
            return self._flush(repair=False) + [frame]
        if not isinstance(event, dict):
            if self._held:
                self._held.append(frame)
                return []
            return [frame]
        kind = event.get("type")
        delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
        if delta.get("stop_reason") in _MESSAGE_CUT:
            self._cut = True
        if kind == "content_block_start" and not self._held:
            block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
            if block.get("type") == "tool_use":
                return [frame]
        if kind == "content_block_delta" and delta.get("type") == "input_json_delta":
            part = delta.get("partial_json")
            if isinstance(part, str) and part:
                self._lanes.setdefault(event.get("index", 0), _Lane()).parts.append(part)
                self._held.append(frame)
                return []
        if kind == "content_block_stop" and self._held:
            self._held.append(frame)
            return self._flush(repair=not self._cut)
        if self._held:
            self._held.append(frame)
            return []
        return [frame]

    def _flush(self, *, repair: bool) -> list[bytes]:
        if not self._held:
            return []
        replacements = self._replacements(repair=repair)
        frames = self._held
        self._held = []
        self._lanes = {}
        if not replacements:
            return frames
        if self.protocol is Protocol.MESSAGES:
            return _rewrite_partials(frames, replacements)
        return _rewrite_chat(frames, replacements)

    def _replacements(self, *, repair: bool) -> dict[object, str]:
        found: dict[object, str] = {}
        for index, lane in self._lanes.items():
            joined = "".join(lane.parts)
            if not joined:
                continue
            fixed = repair_json_object(joined) if repair else None
            self._log(joined, fixed, cut=not repair)
            if fixed is not None:
                found[index] = fixed
        return found

    def _log(self, text: str, fixed: str | None, *, cut: bool) -> None:
        kind = _argument_class(text, fixed, cut=cut)
        if kind is not None:
            log.info("tool arguments %s: len=%d", kind, len(text))


def _rewrite_partials(frames: list[bytes], replacements: dict[object, str]) -> list[bytes]:
    written: set[object] = set()
    out = []
    for frame in frames:
        event = _event_of(frame)
        delta = event.get("delta") if isinstance(event, dict) else None
        if not isinstance(delta, dict) or delta.get("type") != "input_json_delta":
            out.append(frame)
            continue
        index = event.get("index", 0)
        if index not in replacements:
            out.append(frame)
            continue
        if index in written:
            delta["partial_json"] = ""
        else:
            delta["partial_json"] = replacements[index]
            written.add(index)
        out.append(_rewrite_data(frame, event))
    return out


def _rewrite_chat(frames: list[bytes], replacements: dict[object, str]) -> list[bytes]:
    written: set[object] = set()
    out = []
    for frame in frames:
        event = _event_of(frame)
        if not isinstance(event, dict):
            out.append(frame)
            continue
        changed = False
        choices = event.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                tools = delta.get("tool_calls")
                if not isinstance(tools, list):
                    continue
                for tool in tools:
                    if not isinstance(tool, dict):
                        continue
                    function = tool.get("function") if isinstance(tool.get("function"), dict) else None
                    if function is None or "arguments" not in function:
                        continue
                    index = tool.get("index", 0)
                    if index not in replacements:
                        continue
                    if index in written:
                        function["arguments"] = ""
                    else:
                        function["arguments"] = replacements[index]
                        written.add(index)
                    changed = True
        out.append(_rewrite_data(frame, event) if changed else frame)
    return out
