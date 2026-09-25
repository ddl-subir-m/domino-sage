"""Metadata through the real native endpoint, parser, pump and JSON readout (#511)."""
import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from sage import build_diagnostics as diagnostics
from sage import timing
from sage.build_intent import BuildIntent
from sage.build_policy import BuildPolicy
from sage.context_rollover import ContextRolloverState
from sage.gateway.protocol import Protocol
from sage.router import llm_router
from sage.router.models import EffortSource, Mode, Phase, Reason, SessionState

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
        assert {key: call[key] for key in ("configuredEffort", "effectiveEffort", "effortSource",
                                           "effortStatus", "requestedEffort")} == {
            "configuredEffort": None, "effectiveEffort": None,
            "effortSource": "provider_default", "effortStatus": "provider_default",
            "requestedEffort": None,
        }
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
    assert call["outcome"] == {
        "missing": "incomplete", "cap": "model_output_limit",
        "error": "error", "refusal": "refusal",
    }[ending]
    assert call["ok"] is False
    assert "private refusal" not in json.dumps(call)
    gateway_error = orch._project.last_gateway_error or {}
    if ending == "cap":
        assert set(gateway_error) == {"message", "code", "finish_reason"}
        assert gateway_error["code"] == "model_output_limit"
        assert gateway_error["finish_reason"] == {
            Protocol.CHAT: "length",
            Protocol.MESSAGES: "max_tokens",
            Protocol.RESPONSES: "max_output_tokens",
        }[protocol]
        assert "private" not in json.dumps(gateway_error)
    else:
        assert "code" not in gateway_error


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
    assert {key: call[key] for key in ("configuredEffort", "effectiveEffort", "effortSource",
                                       "effortStatus", "requestedEffort")} == {
        "configuredEffort": "low", "effectiveEffort": "low", "effortSource": "user",
        "effortStatus": "applied", "requestedEffort": "low",
    }
    assert all(call[k] is None for k in ("inTokens", "outTokens", "cachedTokens", "reasoningTokens"))


def _automatic_stage_call(running, monkeypatch, *, phase, model="GLM 5.3 OR",
                          saved_slots=frozenset(), effort=None, approved=None, recovery=False,
                          protocol=Protocol.CHAT):
    # `protocol` because an alias's route is a property of the ALIAS, not of the caller: `haiku` is
    # served over `/anthropic/messages`, and `native_routes` refuses a body whose route disagrees
    # with the capability before any of this is reached.
    client, orch, gateway = running
    monkeypatch.setattr(gateway, "route", scripted(gateway, protocol))
    project = orch._project
    project.control.pick(None)
    project.control.set_mode(Mode.AUTO)
    project.control.set_phase(Phase.PLAN if recovery else phase)
    catalog = replace(project.shim.catalog, plan=model, implement=model,
                      plan_effort=effort if phase is Phase.PLAN else None,
                      implement_effort=effort if phase is Phase.IMPLEMENT else None)
    if approved:
        catalog = replace(catalog, sovereign_plan=next(iter(approved)),
                          sovereign_implement=next(iter(approved)))
    project.shim.set_catalog(catalog)
    rows_token = project.control.arm_saved_effort_slots(saved_slots)
    mode_token = None
    if recovery:
        mode_token = project.control.arm_turn_mode(Mode.AUTO)
        project.control.set_turn_mode(Mode.IMPLEMENT)
    sensitivity_token = (project.control.arm_sensitivity(frozenset(approved), tuple(approved))
                         if approved else None)
    messages = ([{"role": "assistant", "tool_calls": [
        {"id": "edit_1", "type": "function",
         "function": {"name": "edit", "arguments": "{}"}}]}]
        if phase is Phase.IMPLEMENT and protocol is Protocol.CHAT else None)
    timing.start_turn("build")
    try:
        with active(orch) as headers:
            final_model = next(iter(approved)) if approved else model
            response = dispatch(client, headers, protocol, final_model, messages)
        assert response.status_code == 200, response.text
        return gateway.seen[-1][0], timing.as_dict(timing.finish_turn())["calls"][0]
    finally:
        if timing.current() is not None:
            timing.finish_turn()
        if sensitivity_token is not None:
            project.control.disarm_sensitivity(sensitivity_token)
        if mode_token is not None:
            project.control.disarm_turn_mode(mode_token)
        project.control.disarm_saved_effort_slots(rows_token)


@pytest.mark.parametrize(("phase", "expected"), [(Phase.PLAN, "high"),
                                                   (Phase.IMPLEMENT, "low")])
def test_automatic_build_stages_send_their_policy_effort_and_record_its_source(
        running, monkeypatch, phase, expected):
    outbound, call = _automatic_stage_call(running, monkeypatch, phase=phase)

    assert outbound["reasoning_effort"] == expected
    assert {key: call[key] for key in ("configuredEffort", "effectiveEffort", "effortSource",
                                       "effortStatus", "requestedEffort")} == {
        "configuredEffort": expected, "effectiveEffort": expected,
        "effortSource": "stage_default", "effortStatus": "applied",
        "requestedEffort": expected,
    }


def test_stage_default_uses_any_measured_alias_and_omits_an_unsupported_one(running, monkeypatch):
    supported, supported_call = _automatic_stage_call(
        running, monkeypatch, phase=Phase.PLAN, model="domino/gemini-3.7-flash")
    assert supported["reasoning_effort"] == "high"
    assert supported_call["effortStatus"] == "applied"

    unsupported, unsupported_call = _automatic_stage_call(
        running, monkeypatch, phase=Phase.PLAN, model="unprobed")
    assert "reasoning_effort" not in unsupported
    assert (unsupported_call["configuredEffort"], unsupported_call["effectiveEffort"],
            unsupported_call["effortSource"], unsupported_call["effortStatus"]) == (
                "high", None, "stage_default", "unsupported")


def test_a_level_the_person_saved_beats_the_stage_default(running, monkeypatch):
    """A chosen level is still theirs and still goes out as-is. #545 does not touch this row."""
    outbound, call = _automatic_stage_call(
        running, monkeypatch, phase=Phase.PLAN, saved_slots=frozenset({"plan"}), effort="max")

    assert outbound.get("reasoning_effort") == "max"
    assert (call["configuredEffort"], call["effectiveEffort"], call["effortSource"],
            call["effortStatus"]) == ("max", "max", "user", "applied")


@pytest.mark.parametrize(("phase", "slot", "expected"), [(Phase.PLAN, "plan", "high"),
                                                          (Phase.IMPLEMENT, "implement", "low")])
def test_an_assigned_model_with_no_level_now_takes_the_stage_default(
        running, monkeypatch, phase, slot, expected):
    """#545, and the whole of it: the row EXISTS and names no level, which is what assigning a
    model leaves behind. That used to read as "the person asked for the provider's own default",
    so GLM 5.3 OR assigned to Implement reasoned for 120 s twice and ended with no edit."""
    outbound, call = _automatic_stage_call(
        running, monkeypatch, phase=phase, saved_slots=frozenset({slot}), effort=None)

    assert outbound.get("reasoning_effort") == expected
    assert (call["configuredEffort"], call["effectiveEffort"], call["effortSource"],
            call["effortStatus"]) == (expected, expected, "stage_default", "applied")


@pytest.mark.parametrize(("model", "protocol"), [("haiku", Protocol.MESSAGES),
                                                  ("bedrock-qwen3-coder", Protocol.CHAT)])
@pytest.mark.parametrize(("phase", "slot"), [(Phase.PLAN, "plan"), (Phase.IMPLEMENT, "implement")])
def test_a_model_that_takes_no_level_sends_no_field_and_raises_nothing(
        running, monkeypatch, model, protocol, phase, slot):
    """The stage level reaches the same measured table a saved one does, and is dropped there.

    `_automatic_stage_call` asserts the 200 itself, which is the "raises nothing" half — and that
    half is not free. `haiku` carries a gateway IDENTITY, and the send path RAISES on a level an
    identified alias will not take rather than dropping it. The exemption it is dropped under is
    `source is not EffortSource.STAGE_DEFAULT`: without it, #545 turns every alias that never
    listed a level into a first-turn 400, which is a worse defect than the one it fixes.

    Two routes because the route is the alias's, not the caller's: `haiku` is served over
    `/anthropic/messages`, where a surviving level would be rendered as `thinking` rather than
    `reasoning_effort` — so both spellings are asserted absent, on both."""
    outbound, call = _automatic_stage_call(
        running, monkeypatch, phase=phase, model=model, protocol=protocol,
        saved_slots=frozenset({slot}), effort=None)

    assert "reasoning_effort" not in outbound and "thinking" not in outbound
    assert (call["effectiveEffort"], call["effortSource"], call["effortStatus"]) == (
        None, "stage_default", "unsupported")


@pytest.mark.parametrize(("mode", "phase", "expected"), [(Mode.PLAN, Phase.PLAN, "high"),
                                                          (Mode.IMPLEMENT, Phase.IMPLEMENT, "low")])
def test_a_pick_made_with_no_level_takes_the_stage_default_too(running, mode, phase, expected):
    """An override is the other way to be unset (#545). Left on the provider default, the one act
    that replaces a model would also be the one way back to the unlimited thinking this removes."""
    _client, orch, _gateway = running
    catalog = replace(orch._project.shim.catalog, plan="GLM 5.3 OR", implement="GLM 5.3 OR")
    state = SessionState(mode=mode, phase=phase, picked_model="GLM 5.3 OR", picked_effort=None,
                         effort_rows_armed=True)

    decision = llm_router.resolve(state, catalog)

    assert decision.effort_source is EffortSource.STAGE_DEFAULT
    # The router names the SOURCE and `enforcement` reads the value off the policy, which is why
    # the decision carries None here. Asserted against the policy so the two cannot drift.
    assert decision.effort is None
    assert (orch._build_policy.plan_reasoning_effort if phase is Phase.PLAN
            else orch._build_policy.implement_reasoning_effort) == expected


def test_chat_and_ask_still_send_no_field_when_no_level_was_picked(running):
    """Chat and Ask are untouched by #545, and they are untouched HERE rather than downstream: the
    stage is a Build plan/implement fact, so the source they resolve to is still the provider's."""
    _client, orch, _gateway = running
    catalog = replace(orch._project.shim.catalog, ask="GLM 5.3 OR")

    chat = llm_router.resolve(
        SessionState(mode=Mode.AUTO, phase=Phase.PLAN, chat_thread_id="thread",
                     effort_rows_armed=True), catalog)
    ask = llm_router.resolve(
        SessionState(mode=Mode.ASK, phase=Phase.PLAN, saved_effort_slots=frozenset({"ask"}),
                     effort_rows_armed=True), catalog)

    assert chat.effort_source is EffortSource.PROVIDER_DEFAULT and chat.effort is None
    assert ask.effort_source is EffortSource.PROVIDER_DEFAULT and ask.effort is None


def test_sensitivity_validates_the_existing_stage_decision_against_the_final_alias(
        running, monkeypatch):
    approved = {"domino/gemini-3.7-flash"}
    outbound, call = _automatic_stage_call(
        running, monkeypatch, phase=Phase.PLAN, model="GLM 5.3 OR", approved=approved)

    assert outbound["model"] == "domino/gemini-3.7-flash"
    assert outbound["reasoning_effort"] == "high"
    assert (call["effortSource"], call["effortStatus"]) == ("stage_default", "applied")


def test_sensitivity_keeps_a_persons_saved_effort_and_validates_the_final_alias(
        running, monkeypatch):
    approved = {"domino/gemini-3.7-flash"}
    outbound, call = _automatic_stage_call(
        running, monkeypatch, phase=Phase.PLAN, model="GLM 5.3 OR",
        saved_slots=frozenset({"plan"}), effort="max", approved=approved)

    assert outbound["model"] == "domino/gemini-3.7-flash"
    assert outbound["reasoning_effort"] == "max"
    assert (call["configuredEffort"], call["effectiveEffort"], call["effortSource"],
            call["effortStatus"]) == ("max", "max", "user", "applied")


def test_signing_pin_uses_the_saved_row_for_the_slot_that_actually_runs(running):
    _client, orch, _gateway = running
    catalog = replace(
        orch._project.shim.catalog,
        plan="GLM 5.3 OR", implement="gemini-3.7-flash",
        plan_effort="max", implement_effort="low",
    )
    state = SessionState(
        mode=Mode.AUTO, phase=Phase.PLAN,
        saved_effort_slots=frozenset({"plan", "implement"}), effort_rows_armed=True,
    )

    decision = llm_router.resolve(state, catalog)

    assert (decision.model, decision.reason, decision.effort, decision.effort_source) == (
        "gemini-3.7-flash", Reason.SIGNING_PIN, "low", EffortSource.USER)


def test_clean_pre_edit_recovery_keeps_the_implement_stage_policy(running, monkeypatch):
    outbound, call = _automatic_stage_call(
        running, monkeypatch, phase=Phase.IMPLEMENT, recovery=True)

    assert outbound["reasoning_effort"] == "low"
    assert (call["configuredEffort"], call["effectiveEffort"], call["effortSource"],
            call["effortStatus"]) == ("low", "low", "stage_default", "applied")


def test_effort_export_rejects_the_whole_record_when_any_enum_or_value_is_invalid():
    valid = {"configuredEffort": "high", "effectiveEffort": "high",
             "requestedEffort": "high", "effortSource": "stage_default",
             "effortStatus": "applied"}
    assert diagnostics._effort_metadata(valid) == valid
    for key, bad in (("configuredEffort", "PRIVATE_CONFIGURED"),
                     ("effectiveEffort", "PRIVATE_EFFECTIVE"),
                     ("requestedEffort", "PRIVATE_REQUESTED"),
                     ("effortSource", "PRIVATE_SOURCE"),
                     ("effortStatus", "PRIVATE_STATUS")):
        row = {**valid, key: bad}
        assert diagnostics._effort_metadata(row) is None
    assert diagnostics._effort_metadata({**valid, "effortSource": "provider_default"}) is None
    assert diagnostics._effort_metadata({**valid, "requestedEffort": "low"}) is None


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
def test_public_turn_starts_timing_after_admission_and_binds_its_actual_context(
        tmp_path, monkeypatch, approve):
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
    assert project_reads[0] is None, "queue admission must not start or replace timing"
    assert record in project_reads[1:]
    assert tickets[0].timing_record is record
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
    project = orch._project
    original, state = orch.prepare_stream_turn(
        "original", kind="build", conversation="thread_fixture", app=True)
    assert state == "running"
    original.timing_record = timing.start_turn("build", turn_id=original.id)
    later, state = orch.prepare_stream_turn(
        "later", kind="build", conversation="thread_fixture", app=True)
    assert state == "pending"
    project.active_session_id = "ses_native"

    async def body():
        # Keep the same Project, root session, and request header. The ticket transition alone must
        # make this request stale.
        orch.release_stream_turn(original)
        assert orch._turns.running() is later
        return json.dumps(request_body(Protocol.CHAT, "GLM 5.3 OR")).encode()
    endpoint = next(r.endpoint for r in appmod.control_app.routes if r.path == "/v1/sage/chat/completions")
    async def consume(headers):
        request = SimpleNamespace(headers={k.lower(): v for k, v in headers.items()}, body=body,
                                  url=SimpleNamespace(path="/v1/sage/chat/completions"))
        response = await endpoint(request)
        if hasattr(response, "body_iterator"):
            async for _ in response.body_iterator:
                pass
        return response
    try:
        response = asyncio.run(consume({"X-Session-Id": "ses_native"}))
        assert response.status_code == 400
        assert json.loads(response.body)["error"]["code"] == "sage_turn_scope_changed"
        assert gateway.seen == []
        assert original.timing_record.calls == []
        assert later.timing_record is None
    finally:
        project.active_session_id = None
        timing.finish_turn(record=original.timing_record)
        timing.finish_turn(record=later.timing_record)
        orch.release_stream_turn(later)


@pytest.mark.parametrize("timing_enabled", [True, False])
def test_queued_turn_timing_cannot_take_the_running_native_request(
        running, monkeypatch, timing_enabled):
    import asyncio

    from sage.orchestrator import app as appmod

    from .test_native_model_controls import request_body

    monkeypatch.setenv("SAGE_TIMING", "1" if timing_enabled else "0")
    client, orch, gateway = running
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "plan"})
    monkeypatch.setattr(gateway, "route", scripted(gateway, Protocol.CHAT))
    project = orch._project
    original, state = orch.prepare_stream_turn(
        "running-turn", kind="build", conversation="thread_fixture", app=True)
    assert state == "running"
    original.timing_record = timing.start_turn("build", turn_id=original.id)
    project.active_session_id = "ses_native"
    queued = None

    async def body():
        nonlocal queued
        queued, queued_state = orch.prepare_stream_turn(
            "queued-turn", kind="build", conversation="thread_fixture", app=True)
        assert queued_state == "pending"
        assert queued.timing_record is None
        assert timing.current() is original.timing_record
        return json.dumps(request_body(Protocol.CHAT, "GLM 5.3 OR")).encode()

    endpoint = next(
        route.endpoint for route in appmod.control_app.routes
        if route.path == "/v1/sage/chat/completions")
    request = SimpleNamespace(
        headers={"x-session-id": "ses_native"}, body=body,
        url=SimpleNamespace(path="/v1/sage/chat/completions"))

    async def consume():
        response = await endpoint(request)
        async for _ in response.body_iterator:
            pass
        return response

    try:
        response = asyncio.run(consume())
        assert response.status_code == 200
        assert len(gateway.seen) == 1
        if timing_enabled:
            assert len(original.timing_record.calls) == 1
            assert original.timing_record.calls[0].outcome == "success"
            assert queued.timing_record is None
        else:
            assert original.timing_record is queued.timing_record is None
    finally:
        project.active_session_id = None
        timing.finish_turn(record=original.timing_record)
        timing.finish_turn(record=queued.timing_record if queued is not None else None)
        orch.release_stream_turn(original)
        if queued is not None:
            orch.release_stream_turn(queued)


@pytest.mark.parametrize("timing_enabled", [True, False])
def test_native_body_rejects_a_successor_ticket_without_using_timing(
        running, monkeypatch, timing_enabled):
    import asyncio

    from sage.orchestrator import app as appmod

    from .test_native_model_controls import request_body

    monkeypatch.setenv("SAGE_TIMING", "1" if timing_enabled else "0")
    client, orch, gateway = running
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "plan"})
    monkeypatch.setattr(gateway, "route", scripted(gateway, Protocol.CHAT))
    project = orch._project
    successor_intent = BuildIntent.for_direct("successor request")
    successor_context = ContextRolloverState(BuildPolicy(), "successor-baseline")
    original, state = orch.prepare_stream_turn(
        "original-turn", kind="build", conversation="thread_fixture", app=True)
    assert state == "running"
    original.timing_record = timing.start_turn("build", turn_id=original.id)
    successor, state = orch.prepare_stream_turn(
        "successor-turn", kind="build", conversation="thread_fixture", app=True)
    assert state == "pending"
    project.active_session_id = "ses_native"

    async def body():
        orch.release_stream_turn(original)
        assert orch._turns.running() is successor
        project.active_session_id = "ses_successor"
        project.active_build_intent = successor_intent
        project.context_rollover = successor_context
        return json.dumps(request_body(Protocol.CHAT, "GLM 5.3 OR")).encode()

    endpoint = next(
        route.endpoint for route in appmod.control_app.routes
        if route.path == "/v1/sage/chat/completions")
    request = SimpleNamespace(
        headers={"x-session-id": "ses_native"}, body=body,
        url=SimpleNamespace(path="/v1/sage/chat/completions"))
    model_calls_before = project.model_calls
    try:
        response = asyncio.run(endpoint(request))
        assert response.status_code == 400
        assert json.loads(response.body)["error"]["code"] == "sage_turn_scope_changed"
        assert gateway.seen == []
        assert project.active_build_intent is successor_intent
        assert successor_context.diagnostic()["action"] == "route"
        assert project.model_calls == model_calls_before
        if timing_enabled:
            assert original.timing_record.calls == []
            assert successor.timing_record is None
        else:
            assert original.timing_record is successor.timing_record is None
    finally:
        project.active_session_id = None
        timing.finish_turn(record=original.timing_record)
        timing.finish_turn(record=successor.timing_record)
        orch.release_stream_turn(successor)


@pytest.mark.parametrize("timing_enabled", [True, False])
def test_running_ticket_routes_normally_with_or_without_timing(
        running, monkeypatch, timing_enabled):
    import asyncio

    from sage.orchestrator import app as appmod

    from .test_native_model_controls import request_body

    monkeypatch.setenv("SAGE_TIMING", "1" if timing_enabled else "0")
    client, orch, gateway = running
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "plan"})
    monkeypatch.setattr(gateway, "route", scripted(gateway, Protocol.CHAT))
    project = orch._project
    ticket, state = orch.prepare_stream_turn(
        "normal-turn", kind="build", conversation="thread_fixture", app=True)
    assert state == "running"
    ticket.timing_record = timing.start_turn("build", turn_id=ticket.id)
    project.active_session_id = "ses_native"

    async def body():
        return json.dumps(request_body(Protocol.CHAT, "GLM 5.3 OR")).encode()

    endpoint = next(
        route.endpoint for route in appmod.control_app.routes
        if route.path == "/v1/sage/chat/completions")
    request = SimpleNamespace(
        headers={"x-session-id": "ses_native"}, body=body,
        url=SimpleNamespace(path="/v1/sage/chat/completions"))

    async def consume():
        response = await endpoint(request)
        async for _ in response.body_iterator:
            pass
        return response

    try:
        response = asyncio.run(consume())
        assert response.status_code == 200
        assert len(gateway.seen) == 1
        if timing_enabled:
            assert len(ticket.timing_record.calls) == 1
            assert ticket.timing_record.calls[0].outcome == "success"
        else:
            assert ticket.timing_record is None
    finally:
        project.active_session_id = None
        timing.finish_turn(record=ticket.timing_record)
        orch.release_stream_turn(ticket)


@pytest.mark.parametrize("timing_enabled", [True, False])
def test_raw_lock_without_a_turn_ticket_cannot_route_native_calls(
        running, monkeypatch, timing_enabled):
    monkeypatch.setenv("SAGE_TIMING", "1" if timing_enabled else "0")
    client, orch, gateway = running
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "plan"})
    monkeypatch.setattr(gateway, "route", scripted(gateway, Protocol.CHAT))
    project = orch._project
    orch._turn_lock.acquire()
    project.active_session_id = "ses_native"
    try:
        response = dispatch(
            client, {"X-Session-Id": "ses_native"}, Protocol.CHAT, "GLM 5.3 OR")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "sage_turn_scope_changed"
        assert gateway.seen == []
    finally:
        project.active_session_id = None
        orch._turn_lock.release()


def test_late_native_body_cannot_read_or_mutate_successor_turn_state(running, monkeypatch):
    import asyncio

    from sage.orchestrator import app as appmod

    from .test_native_model_controls import request_body

    client, orch, gateway = running
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "plan"})
    monkeypatch.setattr(gateway, "route", scripted(gateway, Protocol.CHAT))
    project = orch._project
    successor_intent = BuildIntent.for_direct("successor request")
    successor_context = ContextRolloverState(BuildPolicy(), "successor-baseline")
    timing.start_turn("build", turn_id="original")
    original = timing.current()

    async def body():
        timing.finish_turn()
        timing.start_turn("build", turn_id="successor")
        project.active_session_id = "ses_successor"
        project.active_build_intent = successor_intent
        project.context_rollover = successor_context
        return json.dumps(request_body(Protocol.CHAT, "GLM 5.3 OR")).encode()

    endpoint = next(
        route.endpoint for route in appmod.control_app.routes
        if route.path == "/v1/sage/chat/completions")
    request = SimpleNamespace(
        headers={"x-session-id": "ses_native"}, body=body,
        url=SimpleNamespace(path="/v1/sage/chat/completions"))
    model_calls_before = project.model_calls
    with active(orch):
        response = asyncio.run(endpoint(request))
    assert response.status_code == 400
    assert json.loads(response.body)["error"]["code"] == "sage_turn_scope_changed"
    assert gateway.seen == []
    assert project.active_build_intent is successor_intent
    assert successor_context.diagnostic()["action"] == "route"
    assert project.model_calls == model_calls_before
    assert original.calls == []
    successor = timing.finish_turn()
    assert successor.turn_id == "successor" and successor.calls == []


def test_native_body_with_the_same_owner_still_routes_and_records(running, monkeypatch):
    client, orch, gateway = running
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "plan"})
    monkeypatch.setattr(gateway, "route", scripted(gateway, Protocol.CHAT))
    timing.start_turn("build", turn_id="same-owner")
    with active(orch) as headers:
        response = dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR")
    record = timing.finish_turn()
    assert response.status_code == 200
    assert len(gateway.seen) == 1
    assert len(record.calls) == 1 and record.calls[0].outcome == "success"
