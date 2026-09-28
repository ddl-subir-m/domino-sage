"""Notice the first tool call one model response repeats with byte-identical arguments (#595)."""
from __future__ import annotations

from ..gateway.protocol import Protocol
from .tool_json import _event_of


class RepeatedToolCall:
    """Read the frames about to reach OpenCode and say when one completes a repeated call.

    One instance is one model response. A call counts where OpenCode would execute it: Responses'
    `output_item.done`, Messages' `content_block_stop`, and Chat's finish frame, which closes every
    call at once because that lane has no per-call stop. The arguments compared are exactly the
    bytes forwarded. Only the pair's identity is held, and it is never logged or exported.
    """

    def __init__(self, protocol: Protocol) -> None:
        self.protocol = protocol
        self._made: set[tuple[str, str]] = set()
        self._names: dict[object, str] = {}
        self._args: dict[object, list[str]] = {}

    def repeats(self, frames: list[bytes]) -> bool:
        for frame in frames:
            event = _event_of(frame)
            if isinstance(event, dict) and any(self._repeated(call) for call in self._closed(event)):
                return True
        return False

    def _repeated(self, call: tuple[str, str]) -> bool:
        if call in self._made:
            return True
        self._made.add(call)
        return False

    def _closed(self, event: dict) -> list[tuple[str, str]]:
        if self.protocol is Protocol.RESPONSES:
            item = event.get("item")
            if (event.get("type") == "response.output_item.done" and isinstance(item, dict)
                    and item.get("type") == "function_call"):
                return [(str(item.get("name")), str(item.get("arguments")))]
            return []
        if self.protocol is Protocol.MESSAGES:
            kind = event.get("type")
            index = event.get("index")
            block = event.get("content_block")
            delta = event.get("delta")
            if (kind == "content_block_start" and isinstance(block, dict)
                    and block.get("type") == "tool_use"):
                self._names[index] = str(block.get("name"))
                self._args[index] = []
            elif (kind == "content_block_delta" and isinstance(delta, dict)
                    and delta.get("type") == "input_json_delta" and index in self._args):
                self._args[index].append(str(delta.get("partial_json") or ""))
            elif kind == "content_block_stop" and index in self._names:
                return [(self._names.pop(index), "".join(self._args.pop(index)))]
            return []
        closed: list[tuple[str, str]] = []
        choices = event.get("choices")
        for choice in choices if isinstance(choices, list) else []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            tools = delta.get("tool_calls")
            for tool in tools if isinstance(tools, list) else []:
                if not isinstance(tool, dict):
                    continue
                index = tool.get("index", 0)
                function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
                if function.get("name"):
                    self._names[index] = str(function["name"])
                if isinstance(function.get("arguments"), str):
                    self._args.setdefault(index, []).append(function["arguments"])
            if choice.get("finish_reason"):
                closed += [(self._names.get(index, ""), "".join(self._args.get(index, [])))
                           for index in {**self._names, **self._args}]
                self._names.clear()
                self._args.clear()
        return closed
