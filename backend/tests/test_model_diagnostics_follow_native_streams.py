"""Metadata through the real native endpoint, parser, pump and JSON readout (#511)."""
import copy
import json
from types import SimpleNamespace

import pytest

from sage import build_diagnostics as diagnostics
from sage import timing
from sage.gateway.protocol import Protocol

from .test_native_model_controls import active, dispatch
from .test_native_model_controls import running as native_running

running = native_running

pytestmark = pytest.mark.usefixtures("ledger")


@pytest.fixture(autouse=True)
def enable_recorder(monkeypatch):
    monkeypatch.setenv("SAGE_TIMING", "1")


LANES = [("GLM 5.3 OR", Protocol.CHAT), ("Opus-4.8", Protocol.MESSAGES),
         ("gpt-5.4", Protocol.RESPONSES)]


def frames(protocol, request):
    """Two reads, each split into three argument events; no input usage on purpose."""
    if protocol is Protocol.CHAT:
        yield {"model": request["model"], "choices": [{"index": 0, "delta": {"content": "private text"}}]}
        for i in range(2):
            yield {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": i, "id": f"provider_{i}", "function": {"name": "read"}}]}}]}
            for fragment in ('{"path":', '"private-row"', '}'):
                yield {"choices": [{"index": 0, "delta": {"tool_calls": [
                    {"index": i, "function": {"arguments": fragment}}]}}]}
        yield {"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
               "usage": {"completion_tokens": 17, "completion_tokens_details": {"reasoning_tokens": 4}}}
    elif protocol is Protocol.MESSAGES:
        yield {"type": "message_start", "message": {"model": request["model"]}}
        yield {"type": "content_block_delta", "index": 2,
               "delta": {"type": "text_delta", "text": "private text"}}
        for i in range(2):
            yield {"type": "content_block_start", "index": i,
                   "content_block": {"type": "tool_use", "id": f"provider_{i}", "name": "read"}}
            for fragment in ('{"path":', '"private-row"', '}'):
                yield {"type": "content_block_delta", "index": i,
                       "delta": {"type": "input_json_delta", "partial_json": fragment}}
            yield {"type": "content_block_stop", "index": i}
        yield {"type": "message_delta", "usage": {"output_tokens": 17}, "delta": {"stop_reason": "tool_use"}}
        yield {"type": "message_stop"}
    else:
        yield {"type": "response.output_text.delta", "delta": "private text"}
        for i in range(2):
            yield {"type": "response.output_item.added", "output_index": i,
                   "item": {"id": f"item_{i}", "type": "function_call", "call_id": f"provider_{i}", "name": "read"}}
            for fragment in ('{"path":', '"private-row"', '}'):
                yield {"type": "response.function_call_arguments.delta", "item_id": f"item_{i}", "delta": fragment}
        yield {"type": "response.completed", "response": {
            "model": request["model"],
            "store": False, "metadata": request["metadata"], "reasoning": request.get("reasoning", {}),
            "usage": {"output_tokens": 17, "output_tokens_details": {"reasoning_tokens": 4}}}}


def scripted(gateway, protocol, transform=lambda items: items):
    def route(request, labels, **kwargs):
        gateway.seen.append((copy.deepcopy(request), labels))
        for event in transform(list(frames(protocol, request))):
            wire = b"data: " + json.dumps(event).encode() + b"\n\n"
            for offset in range(0, len(wire), 19):
                yield wire[offset:offset + 19]
    return route


@pytest.mark.parametrize("model,protocol", LANES)
def test_native_pump_counts_two_reads_and_exposes_metadata(running, monkeypatch, model, protocol):
    client, orch, gateway = running
    monkeypatch.setenv("SAGE_TIMING", "1")
    monkeypatch.setattr(gateway, "route", scripted(gateway, protocol))
    client.post("/api/project/model", json={"pick": model, "mode": "plan"})
    timing.start_turn("build")
    try:
        with active(orch) as headers:
            response = dispatch(client, headers, protocol, model)
        assert response.status_code == 200, response.text
        rec = client.get("/api/diag/timing?format=json&n=1").json()[0]
        call = rec["calls"][0]
        assert call["tools"] == ["read", "read"]
        assert [t["providerId"] for t in call["toolInvocations"]] == ["provider_0", "provider_1"]
        assert call["outTokens"] == 17
        assert call["inTokens"] is None and call["cachedTokens"] is None
        assert call["reasoningTokens"] == (None if protocol is Protocol.MESSAGES else 4)
        assert call["turnId"] == rec["turnId"] and call["callId"]
        assert call["sessionId"] == call["rootSessionId"] == "ses_native"
        assert call["protocol"] == protocol.value and call["model"] == model
        assert call["requestedAlias"] == model
        assert call["responseReportedModel"] == model
        assert call["effortStatus"] == "provider_default" and call["requestedEffort"] is None
        assert call["firstTextMs"] is not None and call["firstToolArgumentMs"] is not None
        assert call["lastChunkMs"] >= call["firstToolArgumentMs"] >= call["ttfbMs"]
        assert call["maxChunkGapMs"] >= 0 and call["outcome"] == "success"
        import httpx
        encoded = httpx.Request("POST", "https://example.test", json=gateway.seen[-1][0]).content
        assert call["forwardedReqBytes"] == len(encoded)
        composition = call["requestComposition"]
        assert composition["status"] == "complete"
        assert composition["boundary"] == "final_forwarded_json"
        assert composition["totalBytes"] == call["forwardedReqBytes"]
        assert sum(composition["categories"].values()) == call["forwardedReqBytes"]
        assert composition["toolSchemaCount"] == 2  # Plan mode removes write.
        assert composition["rewrites"] == {
            "redactedCalls": 0, "localExecutionReceipts": 0,
            "markerEchoCorrections": 0, "externalImageReceipts": 0,
            "withheldImageReceipts": 0,
        }
        if protocol is Protocol.MESSAGES:
            assert "cache_control" in json.dumps(gateway.seen[-1][0])
        if protocol is Protocol.RESPONSES:
            assert gateway.seen[-1][0]["metadata"]["sage_route_check"]
        assert call["reqBytes"] > 0
        assert "private-row" not in json.dumps(call) and "private text" not in json.dumps(call)
    finally:
        timing.finish_turn()


@pytest.mark.parametrize("model,protocol", LANES)
@pytest.mark.parametrize("ending", ["missing", "refusal", "cap", "error"])
def test_stream_outcomes_keep_their_distinct_meaning(running, monkeypatch, model, protocol, ending):
    client, orch, gateway = running
    def change(items):
        if ending == "missing":
            return items[:-1]
        if ending == "error":
            return items[:-1] + [{"type": "error", "error": {"type": "overloaded_error"}}]
        if protocol is Protocol.CHAT:
            items[-1] = {"choices": [{"delta": {"refusal": "private refusal"} if ending == "refusal" else {},
                                      "finish_reason": "stop" if ending == "refusal" else "length"}]}
        elif protocol is Protocol.MESSAGES:
            items[-2] = {"type": "message_delta", "delta": {
                "stop_reason": "refusal" if ending == "refusal" else "max_tokens"}}
        elif ending == "refusal":
            items.insert(-1, {"type": "response.refusal.delta", "delta": "private refusal"})
        else:
            items[-1] = {"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}}}
        return items
    monkeypatch.setattr(gateway, "route", scripted(gateway, protocol, change))
    client.post("/api/project/model", json={"pick": model, "mode": "plan"})
    timing.start_turn("build")
    with active(orch) as headers:
        dispatch(client, headers, protocol, model)
    record = timing.finish_turn()
    call = timing.as_dict(record)["calls"][0]
    assert call["outcome"] == {"missing": "incomplete", "cap": "incomplete", "error": "error", "refusal": "refusal"}[ending]
    assert call["ok"] is False
    assert "private refusal" not in json.dumps(call)


@pytest.mark.parametrize("model,protocol", LANES)
def test_explicit_effort_absent_usage_and_child_session_are_not_guessed(running, monkeypatch, model, protocol):
    client, orch, gateway = running
    def no_usage(items):
        for item in items:
            item.pop("usage", None)
            (item.get("response") or {}).pop("usage", None)
        return items
    monkeypatch.setattr(gateway, "route", scripted(gateway, protocol, no_usage))
    monkeypatch.setattr(orch, "_oc_client", SimpleNamespace(session_belongs_to=lambda child, root: child == "ses_child"))
    client.post("/api/project/model", json={"pick": model, "pick_effort": "low", "mode": "plan"})
    timing.start_turn("build", turn_id="turn_known", app_id="app_known", conversation_id="thread_known")
    with active(orch) as headers:
        headers["X-Session-Id"] = "ses_child"
        assert dispatch(client, headers, protocol, model).status_code == 200
    record = timing.as_dict(timing.finish_turn())
    call = record["calls"][0]
    assert record["turnId"] == call["turnId"] == "turn_known"
    assert record["appId"] == "app_known" and record["conversationId"] == "thread_known"
    assert call["sessionId"] == "ses_child" and call["rootSessionId"] == "ses_native"
    assert call["effortStatus"] == "explicit" and call["requestedEffort"] == "low"
    assert all(call[k] is None for k in ("inTokens", "outTokens", "cachedTokens", "reasoningTokens"))


def test_cancelled_native_call_stays_on_the_original_turn(running, monkeypatch, caplog):
    import threading

    from .test_native_model_controls import _open_then_close
    client, orch, gateway = running
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "plan"})
    stopped = threading.Event()
    def route(request, labels, *, cancel, **kwargs):
        try:
            for _ in range(10000):
                if cancel.event.is_set():
                    return
                yield b': keepalive\n\n'
        finally:
            stopped.set()
    monkeypatch.setattr(gateway, "route", route)
    timing.start_turn("build", turn_id="old_turn")
    _open_then_close(orch, gateway, monkeypatch, caplog, lambda: None)
    assert stopped.wait(2)
    record = timing.finish_turn()
    snapshot = timing.as_dict(record)
    assert snapshot["calls"][0]["outcome"] == "cancelled"
    assert snapshot["calls"][0]["firstTextMs"] is None
    assert snapshot["calls"][0]["chunks"] > 0
    timing.start_turn("build", turn_id="new_turn")
    late = timing.model_call(record=record)
    late.chunk()
    late.done()
    assert timing.as_dict(record) == snapshot
    assert timing.as_dict(timing.finish_turn())["calls"] == []


def test_closed_handles_cannot_change_their_record_or_the_next_turn(monkeypatch):
    from types import SimpleNamespace

    from sage.gateway.events import StreamEvents
    now = [10.0]
    monkeypatch.setattr(timing, "time", SimpleNamespace(monotonic=lambda: now[0], time=lambda: 1.0))
    timing.start_turn("build")
    record = timing.current()
    handle = timing.model_call()
    now[0] = 11
    handle.prepared()
    handle.first_byte()
    handle.chunk()
    now[0] = 14
    handle.chunk()
    now[0] = 15
    timing.finish_turn()
    snapshot = timing.as_dict(record)
    assert snapshot["calls"][0]["maxChunkGapMs"] == 3000
    assert snapshot["calls"][0]["lastChunkMs"] == 4000
    assert snapshot["calls"][0]["outcome"] == "incomplete"
    timing.start_turn("build")
    for method, args in ((handle.first_byte, ()), (handle.chunk, ()), (handle.prepared, (99,)),
                         (handle.model, ("late",)), (handle.route, ("chat", "high")),
                         (handle.tool, (["read"],)), (handle.usage, (1, 2, 3, 4)),
                         (handle.stream_metadata, (StreamEvents(Protocol.CHAT),)),
                         (handle.request, (999,)), (handle.done, ())):
        method(*args)
    assert timing.as_dict(record) == snapshot
    assert timing.as_dict(timing.finish_turn())["calls"] == []


@pytest.mark.parametrize("protocol", list(Protocol))
def test_unidentified_and_truncated_announcements_are_explicit(protocol):
    from sage.gateway.events import StreamEvents
    events = StreamEvents(protocol)
    if protocol is Protocol.CHAT:
        event = {"choices": [{"delta": {"tool_calls": [{"function": {"name": "read"}}]}}]}
    elif protocol is Protocol.MESSAGES:
        event = {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "read"}}
    else:
        event = {"type": "response.output_item.added", "item": {"type": "function_call", "name": "read"}}
    wire = b"data: " + json.dumps(event).encode() + b"\n\n"
    for _ in range(45):
        events.feed(wire)
    assert len(events.tool_invocations) == 40
    assert events.tools_truncated is True
    assert all(t["identityStatus"] == "unidentifiable" and t["providerId"] is None for t in events.tool_invocations)


@pytest.mark.parametrize("approve", [False, True])
def test_public_turn_starts_timing_before_setup_and_binds_its_actual_context(tmp_path, monkeypatch, approve):
    from sage.orchestrator.service import Orchestrator

    from .fake_opencode import Turn
    from .test_a_turn_records_where_its_time_went import _orch

    orch = _orch(tmp_path, [Turn(text="Built", writes={"src/App.tsx": "export default () => null\n"})])
    if approve:
        orch._project.workspace.write_plan("Add a chart")
    project_reads = []
    tickets = []
    original_project = orch.project
    original_acquire = orch._acquire_turn
    def project(*args, **kwargs):
        project_reads.append(timing.current())
        return original_project(*args, **kwargs)
    def acquire(ticket, **kwargs):
        tickets.append(ticket)
        yield from original_acquire(ticket, **kwargs)
    monkeypatch.setattr(orch, "project", project)
    monkeypatch.setattr(orch, "_acquire_turn", acquire)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *args, **kwargs: None)
    list(orch.approve_stream(conversation="thread_fixture") if approve else
         orch.build_stream("add a chart", conversation="thread_fixture"))
    record = timing.last_finished()
    assert project_reads[0] is record, "Project setup ran before timing started"
    assert record.turn_id == tickets[0].id
    assert record.app_id == orch._project.workspace.app_id
    assert record.conversation_id == orch._project.build_conversation == "thread_fixture"
    assert any(span.name == "turn.acquire" for span in record.spans)


def test_first_byte_is_a_transport_observation_before_the_first_complete_sse_event(running, monkeypatch):
    client, orch, gateway = running
    now = [0.0]
    monkeypatch.setattr(timing, "time", SimpleNamespace(monotonic=lambda: now[0], time=lambda: 1.0))
    def route(request, labels, **kwargs):
        for timestamp, chunk in [(1, b'data: '),
                                 (4, b'{"choices":[{"delta":{"content":"private"}}]}\n\n'),
                                 (9, b': keepalive\n\n'),
                                 (10, b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n')]:
            now[0] = timestamp
            yield chunk
    monkeypatch.setattr(gateway, "route", route)
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "plan"})
    timing.start_turn("build")
    with active(orch) as headers:
        assert dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR").status_code == 200
    call = timing.as_dict(timing.finish_turn())["calls"][0]
    assert call["ttfbMs"] == 1000
    assert call["firstTextMs"] == 4000
    assert call["lastChunkMs"] == 10000
    assert call["maxChunkGapMs"] == 5000
    assert call["chunks"] == 4


@pytest.mark.parametrize("model,protocol", LANES)
def test_replayed_tool_announcements_do_not_duplicate_invocations(running, monkeypatch, model, protocol):
    client, orch, gateway = running
    def repeat_announcements(items):
        return [item for item in items for _ in range(2)]
    monkeypatch.setattr(gateway, "route", scripted(gateway, protocol, repeat_announcements))
    client.post("/api/project/model", json={"pick": model, "mode": "plan"})
    timing.start_turn("build")
    with active(orch) as headers:
        assert dispatch(client, headers, protocol, model).status_code == 200
    assert timing.as_dict(timing.finish_turn())["calls"][0]["tools"] == ["read", "read"]


@pytest.mark.parametrize("private", ["PRIVATE_SENTINEL", "sk-live-secret123"])
def test_provider_model_content_cannot_enter_the_persisted_download(
        running, monkeypatch, tmp_path, caplog, private):
    client, orch, gateway = running

    def poison(items):
        items[0]["model"] = private
        return items

    monkeypatch.setattr(gateway, "route", scripted(gateway, Protocol.CHAT, poison))
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "plan"})
    timing.start_turn("build", turn_id="turn_private", app_id="app_private",
                      conversation_id="thread_private")
    with active(orch) as headers:
        assert dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR").status_code == 200
    record = timing.finish_turn()
    call = timing.as_dict(record)["calls"][0]
    assert call["model"] == call["requestedAlias"] == "GLM 5.3 OR"
    assert call["responseReportedModel"] is None

    identity = {"turnId": "turn_private", "appId": "app_private",
                "conversationId": "thread_private", "kind": "build"}
    row = diagnostics.snapshot(record, identity, terminal=True)
    assert diagnostics.Store(tmp_path).put(row)
    downloaded = diagnostics.Store(tmp_path).get(
        "turn_private", "app_private", "thread_private")
    serialized = json.dumps(downloaded)
    assert private not in serialized and private not in caplog.text
    saved = downloaded["timing"]["calls"][0]
    assert saved["model"] == saved["requestedAlias"] == "GLM 5.3 OR"
    assert "responseReportedModel" not in saved


def test_request_body_arriving_after_turn_close_cannot_record_in_the_next_turn(running, monkeypatch):
    import asyncio

    from sage.orchestrator import app as appmod

    from .test_native_model_controls import request_body

    client, orch, gateway = running
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "plan"})
    monkeypatch.setattr(gateway, "route", scripted(gateway, Protocol.CHAT))
    timing.start_turn("build", turn_id="original")
    original = timing.current()
    async def body():
        timing.finish_turn()
        timing.start_turn("build", turn_id="later")
        return json.dumps(request_body(Protocol.CHAT, "GLM 5.3 OR")).encode()
    endpoint = next(r.endpoint for r in appmod.control_app.routes if r.path == "/v1/sage/chat/completions")
    async def consume(headers):
        request = SimpleNamespace(headers={k.lower(): v for k, v in headers.items()}, body=body,
                                  url=SimpleNamespace(path="/v1/sage/chat/completions"))
        response = await endpoint(request)
        async for _ in response.body_iterator:
            pass
    with active(orch) as headers:
        asyncio.run(consume(headers))
    assert original.calls == []
    later = timing.finish_turn()
    assert later.turn_id == "later" and later.calls == []
