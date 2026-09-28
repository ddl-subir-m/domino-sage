"""Issue #595: before the first edit, one model response may not repeat a tool call.

MiMo streamed 200+ greps in ONE response; from 19:05:45 on it was the same nine calls with
byte-identical arguments, over and over, until the person pressed Stop. The model sees no tool
result until its response ends, so nothing inside that response could break the cycle.

The first exact repeat (same tool, byte-identical arguments) inside one response ends it and hands
the pre-edit guard's one clean recovery the cause. The repeated call never reaches OpenCode.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from sage import build_diagnostics, timing
from sage.build_policy import BuildPolicy
from sage.gateway.protocol import Protocol
from sage.pre_edit_guard import (
    PreEditAction,
    PreEditDecision,
    PreEditGuard,
    PreEditState,
    PreEditTrigger,
)

from .fake_opencode import Turn
from .test_fresh_approved_session import (
    CONVERSATION,
    DRAFT,
    EDITED,
    RecordingOpenCode,
    _build,
    _done,
    _no_waiting,  # noqa: F401
    _plan,
)
from .test_native_model_controls import RecordingGateway, active, dispatch
from .test_native_model_controls import running as _running  # noqa: F401

LANES = [("Opus-4.8", Protocol.MESSAGES), ("gpt-5.4", Protocol.RESPONSES),
         ("GLM 5.3 OR", Protocol.CHAT)]
REPEAT = ("grep", '{"pattern":"window\\\\.|sage\\\\.|exports|module"}')


def _guard(tree: list[str] | None = None) -> PreEditGuard:
    current = tree if tree is not None else ["base"]
    return PreEditGuard(BuildPolicy(), "base", lambda: current[0])


# --- the guard ----------------------------------------------------------------------------------

def test_a_repeat_recovers_once_then_stops_the_recovery():
    guard = _guard()
    first = guard.repeated_tool_call()
    assert first == PreEditDecision(PreEditAction.RECOVER, PreEditTrigger.REPEATED_TOOL_CALL)
    assert guard.consume_pending() == first
    assert guard.begin_recovery().action is PreEditAction.RECOVER
    assert guard.start_recovery()

    second = guard.repeated_tool_call()
    assert second == PreEditDecision(PreEditAction.STOP, PreEditTrigger.REPEATED_TOOL_CALL)
    assert guard.consume_pending() == second
    assert guard.state is PreEditState.TERMINAL


def test_a_repeat_after_the_first_edit_is_not_the_guards():
    tree = ["base"]
    guard = _guard(tree)
    tree[0] = "edited"
    assert guard.repeated_tool_call().action is PreEditAction.DISARM
    assert guard.consume_pending() is None


def test_the_trigger_is_recorded_content_free_in_both_diagnostics():
    record = timing.start_turn("build", turn_id="turn_595")
    try:
        guard = _guard()
        guard.repeated_tool_call()
        published = timing.as_dict(record)["preEditGuard"]
    finally:
        timing.finish_turn()
    assert published["trigger"] == "repeated_tool_call"
    assert published["action"] == "recover"
    assert build_diagnostics._pre_edit_guard(published)["trigger"] == "repeated_tool_call"


# --- the native routes --------------------------------------------------------------------------

class CallsGateway(RecordingGateway):
    """Stream one response of tool calls, in each protocol's own shape."""

    def __init__(self, calls, *, between=None):
        super().__init__()
        self.calls = calls
        self.between = between or (lambda _index: None)

    def route(self, request, labels, *, protocol=Protocol.CHAT, cancel=None):
        self.seen.append((request, labels))
        for event in self._events(request, protocol):
            if callable(event):
                event()
                continue
            yield (b"data: [DONE]\n\n" if event == "[DONE]"
                   else b"data: " + json.dumps(event).encode() + b"\n\n")

    def _events(self, request, protocol):
        if protocol is Protocol.RESPONSES:
            response = {"store": False, "metadata": request["metadata"],
                        "reasoning": request.get("reasoning", {})}
            yield {"type": "response.created", "response": response}
            for i, (name, args) in enumerate(self.calls):
                item = {"type": "function_call", "id": f"fc_{i}", "call_id": f"call_{i}",
                        "name": name, "arguments": ""}
                yield {"type": "response.output_item.added", "output_index": i, "item": item}
                yield {"type": "response.function_call_arguments.delta", "item_id": f"fc_{i}",
                       "output_index": i, "delta": args}
                yield {"type": "response.function_call_arguments.done", "item_id": f"fc_{i}",
                       "output_index": i, "arguments": args}
                yield {"type": "response.output_item.done", "output_index": i,
                       "item": {**item, "arguments": args}}
                yield lambda i=i: self.between(i)
            yield {"type": "response.completed", "response": response}
        elif protocol is Protocol.MESSAGES:
            yield {"type": "message_start", "message": {"usage": {"input_tokens": 7}}}
            for i, (name, args) in enumerate(self.calls):
                yield {"type": "content_block_start", "index": i, "content_block": {
                    "type": "tool_use", "id": f"toolu_{i}", "name": name, "input": {}}}
                yield {"type": "content_block_delta", "index": i,
                       "delta": {"type": "input_json_delta", "partial_json": args}}
                yield {"type": "content_block_stop", "index": i}
                yield lambda i=i: self.between(i)
            yield {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}
            yield {"type": "message_stop"}
        else:
            for i, (name, args) in enumerate(self.calls):
                yield {"choices": [{"index": 0, "delta": {"tool_calls": [{
                    "index": i, "id": f"call_{i}", "type": "function",
                    "function": {"name": name, "arguments": ""}}]}}]}
                yield {"choices": [{"index": 0, "delta": {"tool_calls": [{
                    "index": i, "function": {"arguments": args}}]}}]}
                yield lambda i=i: self.between(i)
            yield {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
            yield "[DONE]"


def _executed(protocol: Protocol, text: str) -> list[tuple[str, str]]:
    """The calls OpenCode would run from what the route forwarded, read independently."""
    done: list[tuple[str, str]] = []
    names: dict[object, str] = {}
    args: dict[object, str] = {}
    for frame in text.split("\n\n"):
        data = "".join(line[5:].strip() for line in frame.splitlines()
                       if line.startswith("data:"))
        if not data or data == "[DONE]":
            continue
        event = json.loads(data)
        kind = event.get("type")
        if protocol is Protocol.RESPONSES:
            item = event.get("item") or {}
            if kind == "response.output_item.done" and item.get("type") == "function_call":
                done.append((item["name"], item["arguments"]))
        elif protocol is Protocol.MESSAGES:
            block = event.get("content_block") or {}
            delta = event.get("delta") or {}
            if kind == "content_block_start" and block.get("type") == "tool_use":
                names[event["index"]], args[event["index"]] = block["name"], ""
            elif delta.get("type") == "input_json_delta":
                args[event["index"]] += delta["partial_json"]
            elif kind == "content_block_stop" and event["index"] in names:
                done.append((names.pop(event["index"]), args.pop(event["index"])))
        else:
            for choice in event.get("choices") or []:
                for tool in (choice.get("delta") or {}).get("tool_calls") or []:
                    function = tool.get("function") or {}
                    if function.get("name"):
                        names[tool["index"]] = function["name"]
                    args[tool["index"]] = args.get(tool["index"], "") + function.get(
                        "arguments", "")
                if choice.get("finish_reason"):
                    done += [(names[i], args[i]) for i in sorted(names)]
                    names.clear()
                    args.clear()
    return done


def _stream(running, model, protocol, calls, *, guard, between=None, chat=False,
            read_only=""):
    client, orch, _ = running
    project = orch._project
    assert client.post("/api/project/model", json={
        "pick": model, "mode": "implement"}).status_code == 200
    gateway = CallsGateway(calls, between=between)
    project.shim._gateway = gateway
    project.pre_edit_guard = guard
    token = project.control.arm_read_only(read_only) if read_only else None
    try:
        with active(orch, chat=chat) as headers:
            response = dispatch(client, headers, protocol, model)
    finally:
        if token is not None:
            project.control.disarm_read_only(token)
    assert response.status_code == 200, response.text
    return response, project


@pytest.mark.parametrize("model,protocol", LANES)
def test_the_first_exact_repeat_ends_the_response_before_opencode_runs_it(
        _running, model, protocol):  # noqa: F811
    read = ("read", '{"filePath":"src/App.tsx"}')
    calls = [read, REPEAT, REPEAT, ("grep", '{"pattern":"never reached"}')]
    response, project = _stream(_running, model, protocol, calls, guard=_guard())

    assert "sage_gateway_error" in response.text
    executed = _executed(protocol, response.text)
    # CHAT closes every call on its one finish frame, so the repeat withholds the whole batch.
    assert executed == ([] if protocol is Protocol.CHAT else [read, REPEAT])
    assert "never reached" not in response.text
    decision = project.pre_edit_guard.consume_pending()
    assert decision == PreEditDecision(PreEditAction.RECOVER, PreEditTrigger.REPEATED_TOOL_CALL)
    assert project.pre_edit_guard.consume_pending() is None
    assert REPEAT[1] not in project.last_gateway_error["message"]


class WholeChatCallsGateway(CallsGateway):
    """Each Chat call arrives named and complete in one frame, so nothing is forwarded first."""

    def _events(self, request, protocol):
        for i, (name, args) in enumerate(self.calls):
            yield {"choices": [{"index": 0, "delta": {"tool_calls": [{
                "index": i, "id": f"call_{i}", "type": "function",
                "function": {"name": name, "arguments": args}}]}}]}
        yield {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
        yield "[DONE]"


def test_a_repeat_before_any_forwarded_byte_is_a_local_conflict_not_a_502(
        _running):  # noqa: F811
    client, orch, _ = _running
    project = orch._project
    assert client.post("/api/project/model", json={
        "pick": "GLM 5.3 OR", "mode": "implement"}).status_code == 200
    project.shim._gateway = WholeChatCallsGateway([REPEAT, REPEAT])
    project.pre_edit_guard = _guard()
    with active(orch) as headers:
        response = dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR")

    assert response.status_code == 409
    assert REPEAT[1] not in response.text
    assert project.pre_edit_guard.consume_pending().trigger is PreEditTrigger.REPEATED_TOOL_CALL


@pytest.mark.parametrize("model,protocol", LANES)
def test_many_similar_distinct_calls_are_not_a_repeat(_running, model, protocol):  # noqa: F811
    # The shape of the 64 exploration greps before the loop: same tool, widening patterns.
    calls = [("grep", json.dumps({"pattern": "window\\.|sage\\.|" + "|".join(
        f"name{j}" for j in range(i + 1))})) for i in range(64)]
    response, project = _stream(_running, model, protocol, calls, guard=_guard())

    assert "sage_gateway_error" not in response.text
    assert _executed(protocol, response.text) == calls
    assert project.pre_edit_guard.consume_pending() is None
    assert project.pre_edit_guard.state is PreEditState.INITIAL_ARMED


def test_the_same_call_in_two_different_responses_is_not_a_repeat(_running):  # noqa: F811
    guard = _guard()
    for _ in range(2):
        response, _ = _stream(
            _running, "gpt-5.4", Protocol.RESPONSES, [REPEAT], guard=guard)
        assert _executed(Protocol.RESPONSES, response.text) == [REPEAT]
    assert guard.consume_pending() is None
    assert guard.state is PreEditState.INITIAL_ARMED


@pytest.mark.parametrize("model,protocol", LANES)
def test_a_repeat_after_the_first_edit_is_forwarded(_running, model, protocol):  # noqa: F811
    tree = ["base"]
    guard = _guard(tree)

    def edit_lands(index):
        if index == 0:
            tree[0] = "edited"

    response, _ = _stream(_running, model, protocol, [REPEAT, REPEAT],
                          guard=guard, between=edit_lands)

    assert "sage_gateway_error" not in response.text
    assert _executed(protocol, response.text) == [REPEAT, REPEAT]
    assert guard.state is PreEditState.DISARMED
    assert guard.consume_pending() is None


def test_a_disarmed_guard_does_not_watch(_running):  # noqa: F811
    guard = _guard(["edited"])
    response, _ = _stream(_running, "gpt-5.4", Protocol.RESPONSES, [REPEAT, REPEAT],
                          guard=guard)
    assert _executed(Protocol.RESPONSES, response.text) == [REPEAT, REPEAT]
    assert guard.state is PreEditState.DISARMED


def test_a_repeat_in_the_recovery_stops_with_no_third_attempt(_running):  # noqa: F811
    guard = _guard()
    guard.no_edit_completion()
    guard.consume_pending()
    guard.begin_recovery()
    assert guard.start_recovery()

    response, _ = _stream(_running, "gpt-5.4", Protocol.RESPONSES, [REPEAT, REPEAT],
                          guard=guard)

    assert _executed(Protocol.RESPONSES, response.text) == [REPEAT]
    assert guard.consume_pending() == PreEditDecision(
        PreEditAction.STOP, PreEditTrigger.REPEATED_TOOL_CALL)
    assert guard.state is PreEditState.TERMINAL


@pytest.mark.parametrize(("chat", "read_only"), [(True, ""), (False, "plan"), (False, "ask")])
def test_chat_plan_and_ask_turns_are_not_watched(_running, chat, read_only):  # noqa: F811
    guard = _guard()
    response, _ = _stream(_running, "gpt-5.4", Protocol.RESPONSES, [REPEAT, REPEAT],
                          guard=guard, chat=chat, read_only=read_only)
    assert _executed(Protocol.RESPONSES, response.text) == [REPEAT, REPEAT]
    assert guard.consume_pending() is None


# --- the orchestrator ---------------------------------------------------------------------------

class _RepeatingImplementOpenCode(RecordingOpenCode):
    """Stand in for the native route: the implement responses named here repeat a call."""

    def __init__(self, workspace: Path, turns: list[Turn], repeat_on: set[int]) -> None:
        super().__init__(workspace, turns)
        self.repeat_on = repeat_on

    def send_prompt(self, session_id, text, model=None, agent=None, attachments=None,
                    chat=False):
        index = len(self.prompts)
        super().send_prompt(session_id, text, model, agent, attachments, chat)
        if index in self.repeat_on:
            project = self.orch.project(start_preview=False)
            with project.pre_edit_tree_lock:
                project.pre_edit_guard.repeated_tool_call()


def _approve(tmp_path: Path, turns: list[Turn], repeat_on: set[int]):
    orch, opencode = _build(
        tmp_path, turns,
        opencode_type=lambda workspace, turns: _RepeatingImplementOpenCode(
            workspace, turns, repeat_on))
    _plan(orch)
    events = list(orch.approve_stream(conversation=CONVERSATION, plan_edits=EDITED))
    return events, opencode


def test_a_repeat_starts_the_one_recovery_and_tells_the_model_why(tmp_path: Path):
    events, opencode = _approve(tmp_path, [
        Turn(text=DRAFT),
        Turn(text="I searched again."),
        Turn(writes={"src/App.tsx": "// built\n"}),
    ], repeat_on={1})

    assert _done(events)["ok"] is True
    assert len([e for e in events if e["type"] == "build-recovery"]) == 1
    retry = opencode.prompts[2]["text"]
    assert "repeated a tool call it had already made" in retry
    assert "make your edit now" in retry.lower()


def test_a_repeat_in_the_recovery_stops_the_turn_with_a_clear_message(tmp_path: Path):
    events, opencode = _approve(tmp_path, [
        Turn(text=DRAFT),
        Turn(text="I searched again."),
        Turn(text="I searched once more."),
        Turn(writes={"src/App.tsx": "// never reached\n"}),
    ], repeat_on={1, 2})

    assert len(opencode.prompts) == 3
    stopped = [e for e in events if e["type"] == "build-pre-edit-limit"]
    assert len(stopped) == 1
    assert stopped[0]["trigger"] == "repeated_tool_call"
    assert "repeated a tool call it had already made" in stopped[0]["message"]
    assert _done(events)["ok"] is False
    assert _done(events)["decision"] == "pre_edit_limit"


def test_the_repeat_policy_does_not_change_the_recovery_limit():
    policy = replace(BuildPolicy(), pre_edit_clean_recovery_limit=0)
    guard = PreEditGuard(policy, "base", lambda: "base")
    assert guard.repeated_tool_call().action is PreEditAction.STOP
