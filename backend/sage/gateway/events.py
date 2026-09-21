"""Incremental SSE accounting. Retain metadata, never reasoning or tool arguments."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .protocol import Protocol

_BOUNDARY = re.compile(br"\r?\n\r?\n")
MAX_EVENT_BYTES = 2 * 1024 * 1024


@dataclass
class StreamEvents:
    protocol: Protocol
    terminal: str | None = None
    error: str | None = None
    refused: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    tool_names: set[str] = field(default_factory=set)
    tool_ids: set[str] = field(default_factory=set)
    events: int = 0
    response_contract: dict | None = None
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

        cached = (usage.get("cache_read_input_tokens") if self.protocol is Protocol.MESSAGES else
                  (usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}).get("cached_tokens"))
        if isinstance(cached, int):
            self.cached_tokens = cached
        if self.protocol is Protocol.MESSAGES and isinstance(usage.get("input_tokens"), int):
            self.input_tokens = sum(usage.get(key, 0) or 0 for key in
                                    ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))

    def _tool(self, item: dict, *, identity: str = "id") -> None:
        if item.get(identity) is not None:
            self.tool_ids.add(str(item[identity]))
        name = item.get("name") or (item.get("function") or {}).get("name")
        if isinstance(name, str) and name:
            self.tool_names.add(name)

    def _event(self, event: dict) -> None:
        kind = event.get("type")
        error = event.get("error")
        if error or kind == "error":
            # Error messages can echo a prompt or a signature. Record only the class.
            code = error.get("code") or error.get("type") if isinstance(error, dict) else None
            self.error = code if code in {"overloaded_error", "rate_limit_error", "invalid_request_error"} else "upstream_error"
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
                for tool in delta.get("tool_calls") or []:
                    self._tool(tool, identity="index")
        elif self.protocol is Protocol.MESSAGES:
            self._usage((event.get("message") or {}).get("usage") or event.get("usage") or {})
            block = event.get("content_block") or {}
            if kind == "content_block_start" and block.get("type") == "tool_use":
                self._tool(block)
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
                self._tool(item, identity="call_id")
            if kind in ("response.refusal.delta", "response.refusal.done"):
                self.refused = True
            if kind in ("response.completed", "response.incomplete", "response.failed"):
                self.terminal = kind
                if kind != "response.completed":
                    reason = (response.get("incomplete_details") or {}).get("reason")
                    self.error = reason if reason in {"max_output_tokens", "content_filter"} else kind
