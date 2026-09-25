"""Incremental SSE accounting. Retain metadata, never reasoning or tool arguments."""
from __future__ import annotations

import hashlib
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
# Per-call cap on argument lanes (#560). The same number as `tool_invocations`, because a lane is
# one announced call and the two lists are read side by side.
MAX_ARGUMENT_LANES = 40

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
    first_action_kind: str | None = None
    reasoning_only_chunks: int = 0
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
    # Where each tool call's ARGUMENTS crossed this relay, one lane per announced call (#560).
    # Facts only: counts, byte lengths, which terminal event closed the lane, whether the joined
    # deltas and the completion agreed. The join is never held: each fragment updates a digest and
    # is dropped, and only the comparison RESULT leaves this object. Xiaomi's malformed calls kept
    # no deltas, so nothing could say which side of this relay first damaged an argument; this is
    # that missing witness, and it is not an assembler. OpenCode still owns assembly (ADR-0066).
    response_id: str | None = None
    argument_lanes: list[dict] = field(default_factory=list)
    argument_lanes_truncated: bool = False
    # A fragment that named a lane nothing had announced. Ordering evidence, not an argument.
    orphan_argument_deltas: int = 0
    _lane_index: dict[object, dict] = field(default_factory=dict)
    _lane_digest: dict[object, object] = field(default_factory=dict)

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
        if not (isinstance(name, str) and name) and not (isinstance(provider, str) and provider):
            return
        self.first_action_kind = self.first_action_kind or "tool"
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

    def _open_stream(self, key: object, item: dict, *, identity: str = "id",
                     output_index: object = None) -> None:
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
        self._open_lane(key, item, identity=identity, output_index=output_index)

    # --- argument lanes (#560) --------------------------------------------------------------

    def _open_lane(self, key: object, item: dict, *, identity: str, output_index: object) -> None:
        """One lane per ANNOUNCED call. A fragment that names only its index opens nothing."""
        name = item.get("name") or (item.get("function") or {}).get("name")
        # CHAT keys its stream by `index` and names the call by `id`; the other two lanes key
        # and name by the same field.
        provider = item.get("id") if identity == "index" else item.get(identity)
        if key is None or key in self._lane_index:
            return
        if not (isinstance(name, str) and name) and not (isinstance(provider, str) and provider):
            return
        if len(self.argument_lanes) >= MAX_ARGUMENT_LANES:
            self.argument_lanes_truncated = True
            return
        lane = {
            "lane": str(key)[:200],
            "providerId": provider[:200] if isinstance(provider, str) and provider else None,
            "name": name[:80] if isinstance(name, str) and name else None,
            "outputIndex": (output_index if isinstance(output_index, int)
                            and not isinstance(output_index, bool) else None),
            "deltaJoin": {"count": 0, "empty": 0, "bytes": 0},
            "done": {"source": "none", "bytes": None, "jsonValidity": "absent",
                     "agreement": "none"},
            "boundary": "open",
            "terminal": "open",
        }
        self.argument_lanes.append(lane)
        self._lane_index[key] = lane
        self._lane_digest[key] = hashlib.sha256()

    def _lane_delta(self, key: object, fragment: object) -> None:
        if not isinstance(fragment, str):
            return
        lane = self._lane_index.get(key)
        if lane is None:
            self.orphan_argument_deltas += 1
            return
        join = lane["deltaJoin"]
        join["count"] += 1
        if fragment:
            join["bytes"] += len(fragment.encode("utf-8", "surrogatepass"))
            self._lane_digest[key].update(fragment.encode("utf-8", "surrogatepass"))
        else:
            join["empty"] += 1

    def _lane_done(self, key: object, arguments: object, *, source: str) -> None:
        """A completion for this lane: `arguments_done` (Responses' arguments.done event) or
        `item_done` (its output_item.done, which is what the installed codec executes from).
        Only the category of the text survives: its length, whether it parses, and whether it
        equals the joined deltas. `json.loads` here classifies; it never gates or repairs."""
        lane = self._lane_index.get(key)
        if lane is None:
            return
        done = lane["done"]
        text = arguments if isinstance(arguments, str) else None
        digest = (hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
                  if text is not None else None)
        previous = lane.pop("_doneDigest", None)
        sources = lane.setdefault("_doneSources", set())
        sources.add(source)
        done["source"] = "both" if len(sources) == 2 else source
        # A repeated completion (the same event twice) is compared like a second kind would be.
        if done["agreement"] == "none":
            done["agreement"] = "single"
        elif done["agreement"] != "disagree":
            done["agreement"] = "agree" if previous == digest else "disagree"
        lane["_doneDigest"] = digest
        # The codec builds the executed call from `item_done`; report against that one.
        if source == "item_done" or "item_done" not in sources:
            done["bytes"] = len(text.encode("utf-8", "surrogatepass")) if text is not None else None
            done["jsonValidity"] = self._json_validity(text)
            if text is None:
                lane["boundary"] = "no_done"
            elif lane["deltaJoin"]["count"] == 0:
                lane["boundary"] = "done_only"
            else:
                lane["boundary"] = ("match" if self._lane_digest[key].hexdigest() == digest
                                    else "mismatch")

    @staticmethod
    def _json_validity(text: str | None) -> str:
        if text is None:
            return "absent"
        if not text.strip():
            return "empty"
        try:
            value = json.loads(text)
        except ValueError:
            return "invalid"
        return "valid_object" if isinstance(value, dict) else "valid_non_object"

    def _close_lane(self, key: object) -> None:
        lane = self._lane_index.get(key)
        if lane is not None:
            lane["terminal"] = "closed"
            if lane["boundary"] == "open":
                lane["boundary"] = "no_done"

    def argument_boundaries(self) -> dict | None:
        """The bounded lane facts for this call, or None when the stream announced no call."""
        if not self.argument_lanes and not self.orphan_argument_deltas:
            return None
        lanes = []
        for lane in self.argument_lanes:
            copied = {k: (dict(v) if isinstance(v, dict) else v)
                      for k, v in lane.items() if not k.startswith("_")}
            lanes.append(copied)
        return {"responseId": self.response_id, "lanes": lanes,
                "lanesTruncated": self.argument_lanes_truncated,
                "orphanDeltas": self.orphan_argument_deltas}

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
        self._lane_delta(key, fragment)
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
        self._close_lane(key)

    def _event(self, event: dict) -> None:
        before_action = self.first_action_kind
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
                if delta.get("content"):
                    self.first_action_kind = self.first_action_kind or "text"
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
                    self._open_stream(key, tool, identity="index", output_index=key)
                    self._count_lines(key, (tool.get("function") or {}).get("arguments"))
                if choice.get("finish_reason"):
                    # This lane has no per-call stop event, so the choice finishing is the only
                    # boundary there is. Every stream it held is closed at once.
                    for key in list(self._streaming):
                        self._close_lane(key)
                    self._streaming.clear()
                    self._carry.clear()
        elif self.protocol is Protocol.MESSAGES:
            self._usage((event.get("message") or {}).get("usage") or event.get("usage") or {})
            block = event.get("content_block") or {}
            if kind == "content_block_start" and block.get("type") == "tool_use":
                self._announce(event.get("index"), block)
                self._open_stream(event.get("index"), block, output_index=event.get("index"))
            elif kind == "content_block_start" and block.get("type") == "text":
                self.saw_text |= bool(block.get("text"))
                if block.get("text"):
                    self.first_action_kind = self.first_action_kind or "text"
            elif kind == "content_block_delta":
                # The eager fragments themselves. #497 measured these already arriving on this lane
                # and dying inside OpenCode, which never exposes a partly-filled tool input — the
                # transcript goes from `{}` straight to complete. This is the same bytes, counted
                # where they actually are.
                inner = event.get("delta") or {}
                if inner.get("type") == "text_delta":
                    self.saw_text |= bool(inner.get("text"))
                    if inner.get("text"):
                        self.first_action_kind = self.first_action_kind or "text"
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
            if kind == "response.created" and isinstance(response.get("id"), str):
                self.response_id = response["id"][:200]
            item = event.get("item") or {}
            if kind == "response.output_item.added" and item.get("type") == "function_call":
                # Keyed on `item_id`, which is what the argument deltas below carry — NOT on
                # `call_id`, which is what the ledger identifies the call by. The two are different
                # ids on this lane and joining on the wrong one silently counts nothing.
                self._announce(event.get("item_id") or item.get("id") or event.get("output_index"),
                               item, identity="call_id")
                self._open_stream(event.get("item_id") or item.get("id"), item, identity="call_id",
                                  output_index=event.get("output_index"))
            elif kind == "response.function_call_arguments.delta":
                self._count_lines(event.get("item_id"), event.get("delta"))
            elif kind == "response.function_call_arguments.done":
                self._lane_done(event.get("item_id"), event.get("arguments"), source="arguments_done")
                self._close_stream(event.get("item_id"))
            elif kind == "response.output_item.done" and item.get("type") == "function_call":
                # The installed codec builds the executed `tool-call` from THIS item's
                # `arguments` and ignores `arguments.done`, so this is the completion to compare.
                key = event.get("item_id") or item.get("id")
                self._lane_done(key, item.get("arguments"), source="item_done")
                self._close_stream(key)
            if kind == "response.output_text.delta":
                self.saw_text |= bool(event.get("delta"))
                if event.get("delta"):
                    self.first_action_kind = self.first_action_kind or "text"
            if kind in ("response.refusal.delta", "response.refusal.done"):
                self.refused = True
            if kind in ("response.completed", "response.incomplete", "response.failed"):
                self.terminal = kind
                if kind != "response.completed":
                    reason = (response.get("incomplete_details") or {}).get("reason")
                    self.error = reason if reason in {"max_output_tokens", "content_filter"} else kind
        if before_action is None and self.first_action_kind is None and self._is_reasoning(event):
            self.reasoning_only_chunks += 1

    def _is_reasoning(self, event: dict) -> bool:
        """Identify protocol reasoning frames without retaining their content."""
        if self.protocol is Protocol.CHAT:
            return any(bool((choice.get("delta") or {}).get(key))
                       for choice in event.get("choices") or []
                       for key in ("reasoning", "reasoning_content"))
        if self.protocol is Protocol.MESSAGES:
            return (event.get("type") == "content_block_delta"
                    and (event.get("delta") or {}).get("type") == "thinking_delta"
                    and bool((event.get("delta") or {}).get("thinking")))
        return event.get("type") in {
            "response.reasoning_text.delta", "response.reasoning_summary_text.delta",
        } and bool(event.get("delta"))
