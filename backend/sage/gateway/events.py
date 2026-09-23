"""Incremental SSE accounting. Retain metadata, never reasoning or tool arguments."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from .protocol import Protocol

_BOUNDARY = re.compile(br"\r?\n\r?\n")
MAX_EVENT_BYTES = 2 * 1024 * 1024
# A refusal body can be as large as the event that carried it, and the diag ring holds 400
# lines. Clip so one refusal cannot push the rest of the turn out of it.
MAX_LOGGED_ERROR_CHARS = 2000

# "sage.*" -> surfaced by /api/diag's log tail and the Workspace Logs panel.
log = logging.getLogger("sage.gateway")


@dataclass
class StreamEvents:
    protocol: Protocol
    terminal: str | None = None
    error: str | None = None
    refused: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    # Provider response metadata. This is evidence reported on the wire, not verified serving
    # identity; the diagnostic exporter validates it as a bounded model name.
    reported_model: str | None = None
    saw_text: bool = False
    saw_tool_argument: bool = False
    tool_invocations: list[dict] = field(default_factory=list)
    tools_truncated: bool = False
    _invocation_lanes: dict[object, dict] = field(default_factory=dict)
    tool_names: set[str] = field(default_factory=set)
    tool_ids: set[str] = field(default_factory=set)
    events: int = 0
    response_contract: dict | None = None
    # How many lines of argument each tool has streamed so far, this call (#497). Metadata derived
    # from the fragments and NOT the fragments: only a count is kept, never a character of the
    # argument, which is what lets this live in a module whose contract is the line at the top of
    # the file. See `_count_lines`.
    #
    # Keyed by tool NAME, which is as precise as the reader can use. The orchestrator matches this
    # against an OpenCode transcript part, and an OpenCode part carries no provider tool-call id to
    # join on — so a block index or a `tool_use` id here would be a key with nothing to unlock. Two
    # `write` calls streaming in one response therefore share a count. The label is a sign of life
    # and not a measurement, so it reads a little high there rather than going blank.
    tool_input_lines: dict[str, int] = field(default_factory=dict)
    # Which tool each open argument stream belongs to, by the id the protocol gives it: a content
    # block index (MESSAGES), a tool_call index (CHAT), an output item id (RESPONSES). Every lane
    # names the tool once, at the start, and then sends fragments that name nothing.
    _streaming: dict[object, str] = field(default_factory=dict)
    # One character of carry per open stream. A fragment can end mid-escape — `"\` here, `n..."`
    # next — and the two halves counted apart are two non-matches. Holding the last character back
    # costs one character of state and makes the count independent of where the boundaries fall.
    _carry: dict[object, str] = field(default_factory=dict)
    _pending: bytes = b""

    def feed(self, chunk: bytes) -> list[bytes]:
        frames = []
        self._pending += chunk
        while boundary := _BOUNDARY.search(self._pending):
            frames.append(self._pending[:boundary.end()])
            frame, self._pending = self._pending[:boundary.start()], self._pending[boundary.end():]
            if len(frame) > MAX_EVENT_BYTES:
                raise ValueError("Gateway stream event exceeds the size limit")
            data = b"\n".join(line[5:].lstrip(b" ") for line in frame.splitlines()
                              if line.startswith(b"data:"))
            if not data:
                continue
            if data == b"[DONE]":
                if self.protocol is Protocol.CHAT:
                    self.terminal = self.terminal or "done"
                continue
            try:
                event = json.loads(data)
            except (ValueError, UnicodeError):
                raise ValueError("Gateway stream contains invalid JSON") from None
            if not isinstance(event, dict):
                raise TypeError("Gateway stream event is not an object")
            self.events += 1
            self._event(event)
        if len(self._pending) > MAX_EVENT_BYTES:
            raise ValueError("Gateway stream event exceeds the size limit")

        return frames

    def finish(self) -> None:
        if self._pending.strip():
            raise ValueError("Gateway stream ended inside an event")
        if self.terminal is None:
            raise ValueError("Gateway stream ended before its terminal event")

    def _usage(self, usage: dict) -> None:
        for key, alternate, attr in (("input_tokens", "prompt_tokens", "input_tokens"),
                                     ("output_tokens", "completion_tokens", "output_tokens")):
            value = usage.get(key, usage.get(alternate))
            if isinstance(value, int):
                setattr(self, attr, value)

        details = usage.get("output_tokens_details") or usage.get("completion_tokens_details") or {}
        reasoning = details.get("reasoning_tokens", usage.get("reasoning_tokens"))
        if isinstance(reasoning, int):
            # A subset of output usage on providers that report it, never an extra total.
            self.reasoning_tokens = reasoning
        cached = (usage.get("cache_read_input_tokens") if self.protocol is Protocol.MESSAGES else
                  (usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}).get("cached_tokens"))
        if isinstance(cached, int):
            self.cached_tokens = cached
        if self.protocol is Protocol.MESSAGES and isinstance(usage.get("input_tokens"), int):
            self.input_tokens = sum(usage.get(key, 0) or 0 for key in
                                    ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))

    def _announce(self, lane: object, item: dict, *, identity: str = "id") -> None:
        """Bounded invocation metadata, distinct from the name-based live progress label.

        A protocol index identifies a lane when its provider ID has not arrived yet. Only an
        actual announcement (name or ID) enters here; argument fragments cannot inflate counts.
        A missing lane AND ID is explicitly unidentifiable, never a fabricated provider identity.
        """
        name = item.get("name") or (item.get("function") or {}).get("name")
        provider = item.get(identity)
        if not isinstance(name, str) and not isinstance(provider, str):
            return
        provider = provider if isinstance(provider, str) and provider else None
        key = lane if lane is not None else (("id", provider) if provider else None)
        previous = next((entry for entry in self.tool_invocations
                         if provider and len(provider) <= 200 and entry["providerId"] == provider), None)
        if previous is None and key is not None:
            previous = self._invocation_lanes.get(key)
        if previous is not None and (not provider or previous["providerId"] in (None, provider)):
            if provider:
                previous["providerId"] = provider[:200]
                previous["identityStatus"] = "provider_id"
            if name:
                previous["name"] = name[:80]
            previous["metadataTruncated"] |= bool(isinstance(name, str) and len(name) > 80)
            return
        if len(self.tool_invocations) >= 40:
            self.tools_truncated = True
            return
        entry = {"providerId": provider[:200] if provider else None,
                 "name": name[:80] if isinstance(name, str) else None,
                 "identityStatus": "provider_id" if provider else
                    ("protocol_index" if lane is not None else "unidentifiable"),
                 "protocolIndex": str(lane)[:200] if lane is not None else None,
                 "metadataTruncated": bool(provider and len(provider) > 200 or
                                           isinstance(name, str) and len(name) > 80 or
                                           lane is not None and len(str(lane)) > 200)}
        self.tool_invocations.append(entry)
        if key is not None:
            self._invocation_lanes[key] = entry

    def _tool(self, item: dict, *, identity: str = "id") -> None:
        if item.get(identity) is not None:
            self.tool_ids.add(str(item[identity]))
        name = item.get("name") or (item.get("function") or {}).get("name")
        if isinstance(name, str) and name:
            self.tool_names.add(name)

    def _open_stream(self, key: object, item: dict, *, identity: str = "id") -> None:
        """Remember which tool an argument stream belongs to, and start its count at zero.

        Zero rather than absent, so the reader can tell "this tool is streaming and has produced no
        line yet" from "this tool is not streaming". The first is a call that has just begun; the
        second is a label that must not be drawn at all.
        """
        self._tool(item, identity=identity)
        name = item.get("name") or (item.get("function") or {}).get("name")
        if isinstance(name, str) and name:
            self._streaming[key] = name
            self.tool_input_lines.setdefault(name, 0)

    def _count_lines(self, key: object, fragment: object) -> None:
        """Add one fragment of a tool argument to its tool's line count, and keep nothing else.

        The fragment is a piece of JSON, so a newline inside a string value arrives as the two
        characters `\\` and `n` rather than as a byte this could count directly. Counting the escape
        is therefore counting lines — and it counts an escaped backslash followed by an `n` too, so
        a file whose own source spells `\\n` reads a line or two high. That is the right direction
        to be wrong in for a sign of life, and cheaper than tracking escape state across fragments.

        Nothing here is retained: the fragment is counted and dropped, and the only state that
        survives is an integer and at most one character of carry.
        """
        if isinstance(fragment, str) and fragment:
            self.saw_tool_argument = True
        name = self._streaming.get(key)
        if name is None or not isinstance(fragment, str) or not fragment:
            return
        text = self._carry.pop(key, "") + fragment
        # Hold back a trailing backslash: it may be the first half of an escape that the next
        # fragment completes. Any other last character cannot begin one.
        if text.endswith("\\"):
            text, self._carry[key] = text[:-1], "\\"
        self.tool_input_lines[name] = self.tool_input_lines.get(name, 0) + text.count("\\n")

    def _close_stream(self, key: object) -> None:
        self._streaming.pop(key, None)
        self._carry.pop(key, None)

    def _event(self, event: dict) -> None:
        kind = event.get("type")
        reported = (event.get("model") if self.protocol is Protocol.CHAT else
                    (event.get("message") or {}).get("model") if self.protocol is Protocol.MESSAGES
                    else (event.get("response") or {}).get("model"))
        if isinstance(reported, str) and reported:
            self.reported_model = reported
        error = event.get("error")
        if error or kind == "error":
            # The STREAM keeps only the class: this event flows back into OpenCode's session
            # history and is sent to the model next turn, so a body that echoes a prompt or an
            # opaque signature must not ride along.
            code = error.get("code") or error.get("type") if isinstance(error, dict) else None
            self.error = code if code in {"overloaded_error", "rate_limit_error", "invalid_request_error"} else "upstream_error"
            # The body itself goes to the log ring instead (#506). The ring is local to the
            # workspace, already readable by the person whose workspace it is, and read by no
            # model — which is the distinction the stream cannot make. Without it the only
            # sentence naming WHY a turn was refused is discarded before anyone can read it.
            # Written here, where the refusal is classified, because the 400 that matters is
            # not retryable and kills the turn on the first refusal; a line written on a
            # retry-exhaustion path would never appear for it. One line per error event.
            log.warning("gateway stream error (%s): %s", self.error,
                        json.dumps(error or event)[:MAX_LOGGED_ERROR_CHARS])
            self.terminal = "error"
        if self.protocol is Protocol.CHAT:
            self._usage(event.get("usage") or {})
            for choice in event.get("choices") or []:
                if choice.get("finish_reason"):
                    self.terminal = choice["finish_reason"]
                    if self.terminal in ("length", "content_filter"):
                        self.error = self.terminal
                delta = choice.get("delta") or {}
                self.refused |= bool(delta.get("refusal"))
                self.saw_text |= bool(delta.get("content"))
                for tool in delta.get("tool_calls") or []:
                    if not isinstance(tool, dict):
                        continue
                    # One shape carries both here: the fragment that NAMES the tool and the
                    # fragments that extend its arguments arrive as the same `tool_calls` entry,
                    # told apart only by which keys are filled. So open on a name and count on
                    # arguments, in that order, and a first fragment carrying both is handled by
                    # the one pass.
                    key = tool.get("index")
                    lane = (choice.get("index", 0), key) if key is not None else None
                    self._announce(lane, tool)
                    self._open_stream(key, tool, identity="index")
                    self._count_lines(key, (tool.get("function") or {}).get("arguments"))
                if choice.get("finish_reason"):
                    # This lane has no per-call stop event, so the choice finishing is the only
                    # boundary there is. Every stream it held is closed at once.
                    self._streaming.clear()
                    self._carry.clear()
        elif self.protocol is Protocol.MESSAGES:
            self._usage((event.get("message") or {}).get("usage") or event.get("usage") or {})
            block = event.get("content_block") or {}
            if kind == "content_block_start" and block.get("type") == "tool_use":
                self._announce(event.get("index"), block)
                self._open_stream(event.get("index"), block)
            elif kind == "content_block_start" and block.get("type") == "text":
                self.saw_text |= bool(block.get("text"))
            elif kind == "content_block_delta":
                # The eager fragments themselves. #497 measured these already arriving on this lane
                # and dying inside OpenCode, which never exposes a partly-filled tool input — the
                # transcript goes from `{}` straight to complete. This is the same bytes, counted
                # where they actually are.
                inner = event.get("delta") or {}
                if inner.get("type") == "text_delta":
                    self.saw_text |= bool(inner.get("text"))
                if inner.get("type") == "input_json_delta":
                    self._count_lines(event.get("index"), inner.get("partial_json"))
            elif kind == "content_block_stop":
                self._close_stream(event.get("index"))
            stop = (event.get("delta") or {}).get("stop_reason")
            if stop == "refusal":
                self.refused = True
            elif stop in ("max_tokens", "pause_turn"):
                self.error = stop
            if kind == "message_stop":
                self.terminal = "message_stop"
        else:
            response = event.get("response") or {}
            if kind in ("response.created", "response.completed") and self.response_contract is not None:
                expected = self.response_contract
                if (response.get("store") is not False
                        or response.get("metadata", {}).get("sage_route_check") != expected["nonce"]
                        or (expected.get("effort") is not None and
                            response.get("reasoning", {}).get("effort") != expected["effort"])):
                    raise ValueError("The gateway did not preserve the requested Responses settings. "
                                     "A translated fallback cannot be used for this request.")
            self._usage(response.get("usage") or {})
            item = event.get("item") or {}
            if kind == "response.output_item.added" and item.get("type") == "function_call":
                # Keyed on `item_id`, which is what the argument deltas below carry — NOT on
                # `call_id`, which is what the ledger identifies the call by. The two are different
                # ids on this lane and joining on the wrong one silently counts nothing.
                self._announce(event.get("item_id") or item.get("id") or event.get("output_index"),
                               item, identity="call_id")
                self._open_stream(event.get("item_id") or item.get("id"), item, identity="call_id")
            elif kind == "response.function_call_arguments.delta":
                self._count_lines(event.get("item_id"), event.get("delta"))
            elif kind == "response.function_call_arguments.done":
                self._close_stream(event.get("item_id"))
            if kind == "response.output_text.delta":
                self.saw_text |= bool(event.get("delta"))
            if kind in ("response.refusal.delta", "response.refusal.done"):
                self.refused = True
            if kind in ("response.completed", "response.incomplete", "response.failed"):
                self.terminal = kind
                if kind != "response.completed":
                    reason = (response.get("incomplete_details") or {}).get("reason")
                    self.error = reason if reason in {"max_output_tokens", "content_filter"} else kind
