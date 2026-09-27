"""A finished tool-call object is repaired; a cut-off one is forwarded as the model wrote it.

The repair is mechanical and has one result: a raw newline, tab, or carriage return inside
a string, and a trailing comma. An unescaped quote is left alone — a guessed quote can be
legal JSON and the wrong SQL. StreamEvents still records the gateway's bytes; the repair
runs on the frames after that.
"""
from __future__ import annotations

import json
import logging

import pytest

from sage.gateway.capabilities import RouteCapability
from sage.gateway.client import FakeGatewayClient
from sage.gateway.events import StreamEvents
from sage.gateway.protocol import Protocol
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase
from sage.shim.enforcement import EnforcementShim
from sage.shim.native import prepare_native
from sage.shim.tool_json import (
    INVALID_TOOL_CLOSED,
    INVALID_TOOL_PREFIX,
    ArgumentRepair,
    redact_invalid_tool_results,
    repair_json_object,
)

from .test_enforcement_shim import CATALOG

SECRET = "ZZTOPSECRET"
RAW = '{"sql": "SELECT ' + SECRET + '\n"}'
TRAILING = '{"b": 1, "a": 2,}'
QUOTED = '{"sql": "SELECT "' + SECRET + '""}'


def sse(event: dict) -> bytes:
    return b"data: " + json.dumps(event).encode() + b"\n\n"


def _through(protocol: Protocol, frames: list[bytes]) -> list[bytes]:
    repair = ArgumentRepair(protocol)
    out = repair.push_frames(frames)
    out.extend(repair.finish())
    return out


def _chat_arguments(frames: list[bytes]) -> str:
    parts = []
    for frame in frames:
        payload = frame.split(b"data:", 1)[1].strip()
        if payload == b"[DONE]":
            continue
        event = json.loads(payload)
        for choice in event.get("choices") or []:
            for tool in (choice.get("delta") or {}).get("tool_calls") or []:
                arguments = (tool.get("function") or {}).get("arguments")
                if arguments:
                    parts.append(arguments)
    return "".join(parts)


def _message_arguments(frames: list[bytes]) -> str:
    parts = []
    for frame in frames:
        event = json.loads(frame.split(b"data:", 1)[1])
        delta = event.get("delta") or {}
        if delta.get("type") == "input_json_delta" and delta.get("partial_json"):
            parts.append(delta["partial_json"])
    return "".join(parts)


def test_mechanical_edits_that_produce_one_object():
    fixed = repair_json_object(RAW)
    assert fixed is not None and "\n" not in fixed and json.loads(fixed) == json.loads(
        '{"sql": "SELECT ' + SECRET + '\\n"}')
    assert json.loads(repair_json_object('{"sql": "a\tb"}')) == {"sql": "a\tb"}
    assert json.loads(repair_json_object('{"sql": "a\rb"}')) == {"sql": "a\rb"}
    assert repair_json_object(TRAILING) == '{"b": 1, "a": 2}'
    assert json.loads(repair_json_object('{"a": [1,], "b": 2,}')) == {"a": [1], "b": 2}
    assert json.loads(repair_json_object('{"a": "b\\"",}')) == {"a": 'b"'}
    both = repair_json_object('{"sql": "SELECT 1\n",}')
    assert both is not None and json.loads(both) == {"sql": "SELECT 1\n"}


def test_everything_else_is_left_alone():
    assert repair_json_object('{"sql": "SELECT 1"}') is None
    assert repair_json_object('{"sql": "SELECT 1\\n"}') is None
    assert repair_json_object(QUOTED) is None
    assert repair_json_object('{"sql": "SELECT ' + SECRET) is None
    assert repair_json_object("[1, 2,]") is None
    assert repair_json_object('{"sql": "SELECT "' + SECRET + '"",}') is None


def test_responses_repairs_the_done_event_and_leaves_deltas_alone():
    delta = sse({"type": "response.function_call_arguments.delta", "item_id": "i",
                 "delta": '{"sql": "SELECT '})
    done = sse({"type": "response.output_item.done", "item": {
        "type": "function_call", "call_id": "c", "name": "live_read_query", "arguments": RAW}})
    events = StreamEvents(Protocol.RESPONSES)
    forwarded = events.feed(done)
    assert b"".join(forwarded) == done
    agreed = sse({"type": "response.function_call_arguments.done", "item_id": "i", "arguments": RAW})
    out = _through(Protocol.RESPONSES, [delta, done, agreed])
    assert out[0] == delta
    arguments = json.loads(out[1].split(b"data:", 1)[1])["item"]["arguments"]
    assert json.loads(arguments) == {"sql": "SELECT " + SECRET + "\n"}
    assert json.loads(json.loads(out[2].split(b"data:", 1)[1])["arguments"]) == json.loads(arguments)


def test_responses_forwards_a_cut_off_object_and_an_output_cap():
    cut = sse({"type": "response.output_item.done", "item": {
        "type": "function_call", "arguments": '{"sql": "SELECT ' + SECRET}})
    assert _through(Protocol.RESPONSES, [cut]) == [cut]
    incomplete = sse({"type": "response.incomplete", "response": {
        "incomplete_details": {"reason": "max_output_tokens"}}})
    done = sse({"type": "response.output_item.done", "item": {
        "type": "function_call", "arguments": RAW}})
    assert b"".join(_through(Protocol.RESPONSES, [incomplete, done])) == incomplete + done


def test_a_responses_object_that_closes_before_the_cap_is_repaired():
    done = sse({"type": "response.output_item.done", "item": {
        "type": "function_call", "arguments": RAW}})
    incomplete = sse({"type": "response.incomplete", "response": {
        "incomplete_details": {"reason": "max_output_tokens"}}})
    out = _through(Protocol.RESPONSES, [done, incomplete])
    assert out[1] == incomplete
    assert json.loads(json.loads(out[0].split(b"data:", 1)[1])["item"]["arguments"])["sql"].endswith("\n")


def _chat(arguments: str, *, finish: str | None = None, name: bool = False) -> bytes:
    function: dict = {"arguments": arguments}
    tool: dict = {"index": 0, "function": function}
    if name:
        tool["id"] = "c1"
        tool["type"] = "function"
        function["name"] = "live_read_query"
    return sse({"choices": [{"index": 0, "delta": {"tool_calls": [tool]}, "finish_reason": finish}]})


def test_chat_holds_fragments_until_the_call_closes_and_repairs_once():
    start = _chat("", name=True)
    mid = _chat('{"sql": "SELECT ' + SECRET)
    end = _chat('\n"}')
    finish = sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
    done = b"data: [DONE]\n\n"
    repair = ArgumentRepair(Protocol.CHAT)
    assert repair.push_frames([start]) == [start]
    assert repair.push_frames([mid]) == []
    out = repair.push_frames([end, finish, done])
    assert out[-1] == done
    assert json.loads(_chat_arguments(out)) == {"sql": "SELECT " + SECRET + "\n"}


def test_a_repaired_call_leaves_the_valid_call_beside_it_unchanged():
    frames = [
        sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "a", "type": "function",
             "function": {"name": "live_read_query", "arguments": RAW}},
            {"index": 1, "id": "b", "type": "function",
             "function": {"name": "live_read_table", "arguments": '{"name": "orders"}'}},
        ]}}]}),
        sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
    ]
    event = json.loads(_through(Protocol.CHAT, frames)[0].split(b"data:", 1)[1])
    calls = event["choices"][0]["delta"]["tool_calls"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"sql": "SELECT " + SECRET + "\n"}
    assert calls[1]["function"]["arguments"] == '{"name": "orders"}'
    assert calls[1]["id"] == "b"


def test_chat_reassembles_a_split_chunk_the_same_way():
    frames = [_chat("", name=True), _chat('{"sql": "SELECT ' + SECRET), _chat('\n"}'),
              sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})]
    blob = b"".join(frames)
    repair = ArgumentRepair(Protocol.CHAT)
    out: list[bytes] = []
    for i in range(0, len(blob), 5):
        out.extend(repair.push_chunk(blob[i:i + 5]))
    out.extend(repair.finish())
    assert json.loads(_chat_arguments(out)) == {"sql": "SELECT " + SECRET + "\n"}


def test_valid_chat_arguments_are_the_original_bytes(caplog):
    caplog.set_level(logging.INFO, logger="sage.shim")
    frames = [
        _chat("", name=True),
        _chat('{"path": "src/App.tsx"'),
        _chat(', "text": "hi"}'),
        sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
    ]
    assert b"".join(_through(Protocol.CHAT, frames)) == b"".join(frames)
    done = sse({"type": "response.output_item.done", "item": {
        "type": "function_call", "arguments": '{"sql": "SELECT 1"}'}})
    arguments_done = sse({"type": "response.function_call_arguments.done",
                          "arguments": '{"sql": "SELECT 1"}'})
    assert _through(Protocol.RESPONSES, [done, arguments_done]) == [done, arguments_done]
    block = [
        sse({"type": "content_block_start", "index": 0,
             "content_block": {"type": "tool_use", "id": "t", "name": "live_read_query"}}),
        sse({"type": "content_block_delta", "index": 0,
             "delta": {"type": "input_json_delta", "partial_json": '{"sql": "SEL'}}),
        sse({"type": "content_block_delta", "index": 0,
             "delta": {"type": "input_json_delta", "partial_json": 'ECT 1"}'}}),
        sse({"type": "content_block_stop", "index": 0}),
    ]
    assert b"".join(_through(Protocol.MESSAGES, block)) == b"".join(block)
    assert "tool arguments" not in caplog.text


def test_a_length_finish_and_an_unescaped_quote_are_the_original_bytes(caplog):
    caplog.set_level(logging.INFO, logger="sage.shim")
    length = [_chat(RAW, name=True), sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]})]
    assert b"".join(_through(Protocol.CHAT, length)) == b"".join(length)
    quoted = [_chat(QUOTED, name=True),
              sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})]
    assert b"".join(_through(Protocol.CHAT, quoted)) == b"".join(quoted)
    assert "cutoff" in caplog.text and "unparsed" in caplog.text
    assert SECRET not in caplog.text


def _messages_block() -> list[bytes]:
    return [
        sse({"type": "content_block_start", "index": 0,
             "content_block": {"type": "tool_use", "id": "t", "name": "live_read_query"}}),
        sse({"type": "content_block_delta", "index": 0,
             "delta": {"type": "input_json_delta", "partial_json": '{"sql": "SELECT ' + SECRET}}),
        sse({"type": "content_block_delta", "index": 0,
             "delta": {"type": "input_json_delta", "partial_json": '\n"}'}}),
        sse({"type": "content_block_stop", "index": 0}),
    ]


def test_messages_replaces_the_fragments_before_the_block_stops():
    start, first, second, stop = _messages_block()
    repair = ArgumentRepair(Protocol.MESSAGES)
    assert repair.push_frames([start]) == [start]
    assert repair.push_frames([first]) == []
    out = repair.push_frames([second, stop])
    assert json.loads(_message_arguments(out)) == {"sql": "SELECT " + SECRET + "\n"}
    assert out[-1] == stop


def test_messages_without_a_stop_and_a_max_tokens_stop_forward_the_originals():
    start, first, second, stop = _messages_block()
    repair = ArgumentRepair(Protocol.MESSAGES)
    repair.push_frames([start, first])
    assert repair.finish() == [first]
    capped = [start, first, second,
              sse({"type": "message_delta", "delta": {"stop_reason": "max_tokens"}}), stop]
    assert b"".join(_through(Protocol.MESSAGES, capped)) == b"".join(capped)


def test_the_log_names_the_class_and_the_length_only(caplog):
    caplog.set_level(logging.INFO, logger="sage.shim")
    _through(Protocol.RESPONSES, [sse({"type": "response.output_item.done", "item": {
        "type": "function_call", "arguments": RAW}})])
    assert "repaired" in caplog.text
    assert SECRET not in caplog.text
    assert "len=" in caplog.text


def test_redaction_replaces_the_payload_and_keeps_the_message():
    payload = INVALID_TOOL_PREFIX + ' JSON parsing failed: Text: {"sql": "SELECT ' + SECRET + '"}.'
    messages = [
        {"role": "assistant", "content": payload},
        {"role": "tool", "tool_call_id": "t1", "content": payload},
        {"role": "tool", "tool_call_id": "t2", "content": [{"type": "text", "text": payload}]},
        {"role": "tool", "tool_call_id": "t3", "content": "Invalid input for tool write: " + SECRET},
        {"role": "tool", "tool_call_id": "t4", "content": "Wrote src/App.tsx"},
    ]
    out = redact_invalid_tool_results(messages)
    assert len(out) == len(messages)
    assert out[0] is messages[0]
    assert out[1]["content"] == INVALID_TOOL_CLOSED and out[1]["tool_call_id"] == "t1"
    assert out[2]["content"] == INVALID_TOOL_CLOSED
    assert out[3]["content"].endswith(SECRET)
    assert out[4]["content"] == "Wrote src/App.tsx"
    assert SECRET not in out[1]["content"] and "Text:" not in out[1]["content"]
    assert redact_invalid_tool_results(out) is out
    assert messages[1]["content"] == payload


def test_the_gateway_request_keeps_the_tool_message_and_loses_the_payload():
    payload = INVALID_TOOL_PREFIX + ' JSON parsing failed: Text: {"sql": "SELECT ' + SECRET + '"}.'
    messages = [
        {"role": "user", "content": "run it"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "t1", "type": "function",
             "function": {"name": "live_read_query", "arguments": "{"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": payload},
        {"role": "user", "content": "try again"},
    ]
    gw = FakeGatewayClient()
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    list(EnforcementShim(control, CATALOG, gw).handle(
        {"messages": messages, "tools": []}, project="p"))
    sent = gw.seen[-1][0]["messages"]
    assert len(sent) == len(messages)
    result = next(m for m in sent if m.get("role") == "tool")
    assert result["tool_call_id"] == "t1"
    assert result["content"] == INVALID_TOOL_CLOSED
    assert SECRET not in json.dumps(sent)


def _native_shim(protocol: Protocol) -> EnforcementShim:
    control = ModelControl(mode=Mode.AUTO)
    catalog = ModelCatalog("model", "model", "model", "model", "model", "model")
    shim = EnforcementShim(control, catalog, FakeGatewayClient())
    shim.resolve_capability = lambda _: RouteCapability(
        protocol, True, ("none", "high"), ("none", "high"), "")
    return shim


@pytest.mark.parametrize("protocol", [Protocol.MESSAGES, Protocol.RESPONSES])
def test_prepare_native_strips_the_invalid_tool_payload(protocol: Protocol):
    payload = INVALID_TOOL_PREFIX + ' JSON parsing failed: Text: {"sql": "SELECT ' + SECRET + '"}.'
    tool = {"name": "live_read_query", "description": "q", "parameters": {"type": "object"}}
    if protocol is Protocol.MESSAGES:
        body = {"model": "model", "stream": True, "max_tokens": 64, "messages": [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "call_1", "name": "live_read_query", "input": {}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": payload}]},
        ], "tools": [{"name": tool["name"], "description": "q", "input_schema": {"type": "object"}}]}
    else:
        body = {"model": "model", "stream": True, "input": [
            {"role": "user", "content": "question"},
            {"type": "function_call", "call_id": "call_1", "name": "live_read_query", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": payload},
        ], "tools": [{"type": "function", **tool}]}
    result, *_ = prepare_native(_native_shim(protocol), body, protocol, "p", "ses_test")
    encoded = json.dumps(result)
    assert SECRET not in encoded and "Text:" not in encoded
    assert INVALID_TOOL_CLOSED in encoded and "call_1" in encoded
