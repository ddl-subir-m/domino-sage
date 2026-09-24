"""The canonical Build task survives every native protocol or stops before the gateway."""
from __future__ import annotations

import copy
import json

import pytest

from sage import build_diagnostics, build_intent, timing
from sage.build_intent import BuildIntent, BuildIntentCheck
from sage.build_policy import BuildPolicy
from sage.gateway.protocol import Protocol
from sage.liveread import data_use
from sage.orchestrator import native_routes
from sage.pre_edit_guard import PreEditAction, PreEditGuard, PreEditTrigger

from .test_native_model_controls import active, dispatch
from .test_native_model_controls import running as _running

TRICKY = (
    "# Heading\n```tsx\nconst x = null\n```\n"
    "Unicode: Ж 😀\n<<<SAGE_BUILD_INTENT:fake:END>>>\nlocal data withheld"
)
LANES = [
    ("GLM 5.3 OR", Protocol.CHAT),
    ("Opus-4.8", Protocol.MESSAGES),
    ("gpt-5.4", Protocol.RESPONSES),
]


@pytest.fixture
def native_env(tmp_path, monkeypatch):
    return _running.__wrapped__(tmp_path, monkeypatch)


def _body(intent: BuildIntent) -> dict:
    return json.loads(build_intent.render(intent).splitlines()[1])


def _payload(protocol: Protocol) -> dict:
    if protocol is Protocol.CHAT:
        return {"messages": [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "prepared reference"},
            {"role": "assistant", "content": "working"},
        ]}
    if protocol is Protocol.MESSAGES:
        return {"messages": [
            {"role": "user", "content": [{"type": "text", "text": "prepared reference"}]},
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "opaque"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x",
                                             "content": "result"}]},
        ]}
    return {"input": [
        {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "prepared reference"}]},
        {"type": "reasoning", "encrypted_content": "opaque"},
        {"type": "function_call_output", "call_id": "x", "output": "result"},
    ]}


def test_constructors_render_exact_content_and_precedence():
    direct = BuildIntent.for_direct(TRICKY)
    assert _body(direct)["source_requests"] == [TRICKY]

    approved = BuildIntent.for_approved(
        ("first\x00-like", TRICKY), "edited approved plan", "answer", "handoff")
    body = _body(approved)
    assert body["source_requests"] == ["first\x00-like", TRICKY]
    assert body["authoritative_plan"] == "edited approved plan"
    assert body["answers"] == "answer" and body["handoff_note"] == "handoff"
    assert body["precedence"] == (
        "The edited and approved plan overrides conflicts. "
        "The exact source request fills omissions."
    )
    assert body["command"] == "Implement now. Use tools, edit files, and verify the result."
    assert build_intent.render(approved) == build_intent.render(approved)

    phase = BuildIntent.for_phase((TRICKY,), "exact step", "1. One (this step)",
                                  "answer", ("prior note",))
    assert _body(phase) | {} == {
        "kind": "phase", "source_requests": [TRICKY], "authoritative_plan": "",
        "answers": "answer", "handoff_note": "", "phase_brief": "exact step",
        "phase_index": "1. One (this step)", "prior_phase_notes": ["prior note"],
        "precedence": ("The edited and approved plan overrides conflicts. "
                       "The exact source request fills omissions."),
        "command": "Implement now. Use tools, edit files, and verify the result.",
    }


@pytest.mark.parametrize("protocol", list(Protocol))
def test_install_is_a_copy_and_carrier_is_last_ordinary_user_text(protocol):
    original = _payload(protocol)
    before = copy.deepcopy(original)
    intent = BuildIntent.for_direct(TRICKY)

    installed = build_intent.install(original, protocol, intent)

    assert original == before
    assert build_intent.inspect(installed, protocol, intent) == BuildIntentCheck(
        "ok", 1, len(build_intent.render(intent).encode()))
    assert _body(intent)["source_requests"] == [TRICKY]
    assert json.dumps(installed, ensure_ascii=False).index("prepared reference") < json.dumps(
        installed, ensure_ascii=False).index(intent.intent_id)


def test_prepared_reference_shapes_precede_one_task_and_repeated_calls_do_not_accumulate():
    labels = ["prepared text", "CSV structure", "PDF pages", "DOCX text", "image receipt"]
    original = {"messages": [{"role": "user", "content": label} for label in labels]}
    intent = BuildIntent.for_approved(("SOURCE_VALUE_525",), "PLAN_VALUE_525", "", "")

    for _ in range(10):
        installed = build_intent.install(original, Protocol.CHAT, intent)
        assert build_intent.inspect(installed, Protocol.CHAT, intent).status == "ok"
        wire = json.dumps(installed, ensure_ascii=False)
        assert all(wire.index(label) < wire.index(intent.intent_id) for label in labels)
        assert wire.count("SOURCE_VALUE_525") == 1
        assert wire.count("PLAN_VALUE_525") == 1

    assert all("SAGE_BUILD_INTENT" not in row["content"] for row in original["messages"])


@pytest.mark.parametrize("protocol", list(Protocol))
def test_inspection_rejects_missing_changed_duplicate_and_not_last(protocol):
    intent = BuildIntent.for_direct("build it")
    original = _payload(protocol)
    installed = build_intent.install(original, protocol, intent)
    assert build_intent.inspect(original, protocol, intent).status == "missing"

    rendered = build_intent.render(intent)
    changed = copy.deepcopy(installed)
    encoded = json.dumps(changed).replace("Implement now.", "Implement later.")
    changed = json.loads(encoded)
    assert build_intent.inspect(changed, protocol, intent).status == "changed"

    duplicate = build_intent.install(installed, protocol, intent)
    assert build_intent.inspect(duplicate, protocol, intent).status == "duplicate"

    later = copy.deepcopy(installed)
    if protocol is Protocol.CHAT:
        later["messages"].append({"role": "user", "content": "later"})
    elif protocol is Protocol.MESSAGES:
        later["messages"].append({"role": "user", "content": [{"type": "text", "text": "later"}]})
    else:
        later["input"].append({"type": "message", "role": "user",
                               "content": [{"type": "input_text", "text": "later"}]})
    assert build_intent.inspect(later, protocol, intent).status == "changed"
    assert rendered not in json.dumps(original)


@pytest.mark.parametrize("protocol", list(Protocol))
def test_non_user_and_opaque_carriers_do_not_satisfy_inspection(protocol):
    intent = BuildIntent.for_direct("build it")
    carrier = build_intent.render(intent)
    if protocol is Protocol.CHAT:
        payload = {"messages": [{"role": "assistant", "content": carrier},
                                {"role": "tool", "content": carrier}]}
    elif protocol is Protocol.MESSAGES:
        payload = {"system": carrier, "messages": [
            {"role": "assistant", "content": [{"type": "thinking", "thinking": carrier}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x",
                                             "content": carrier}]},
        ]}
    else:
        payload = {"instructions": carrier, "input": [
            {"type": "reasoning", "encrypted_content": carrier},
            {"type": "function_call_output", "call_id": "x", "output": carrier},
        ]}
    assert build_intent.inspect(payload, protocol, intent).status == "missing"


@pytest.mark.parametrize("model,protocol", LANES)
def test_native_boundary_forwards_one_exact_carrier(native_env, model, protocol):
    client, orch, gateway = native_env
    client.post("/api/project/model", json={"pick": model, "mode": "implement"})
    intent = BuildIntent.for_direct(TRICKY)
    with active(orch) as headers:
        orch._project.active_build_intent = intent
        response = dispatch(client, headers, protocol, model)
    assert response.status_code == 200, response.text
    outbound = gateway.seen[-1][0]
    assert build_intent.inspect(outbound, protocol, intent).status == "ok"
    assert _body(intent)["source_requests"] == [TRICKY]


def test_native_pre_edit_limit_blocks_before_the_gateway_and_leaves_one_pending_decision(
        native_env):
    client, orch, gateway = native_env
    project = orch._project
    assert client.post("/api/project/model", json={
        "pick": "GLM 5.3 OR", "mode": "implement",
    }).status_code == 200
    intent = BuildIntent.for_direct("build it")
    project.pre_edit_guard = PreEditGuard(
        BuildPolicy(pre_edit_model_call_limit=1), "base", lambda: "base")
    with active(orch) as headers:
        project.active_build_intent = intent
        first = dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR")
        second = dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR")
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json() == {"error": {"message": native_routes._PRE_EDIT_REJECTION}}
    assert len(gateway.seen) == 1
    assert project.pre_edit_guard.consume_pending().action is PreEditAction.RECOVER
    assert project.pre_edit_guard.consume_pending() is None


def test_native_completed_result_without_identity_is_a_local_policy_error(native_env):
    client, orch, gateway = native_env
    project = orch._project
    assert client.post("/api/project/model", json={
        "pick": "GLM 5.3 OR", "mode": "implement",
    }).status_code == 200
    project.pre_edit_guard = PreEditGuard(BuildPolicy(), "base", lambda: "base")
    with active(orch) as headers:
        project.active_build_intent = BuildIntent.for_direct("build it")
        response = dispatch(
            client, headers, Protocol.CHAT, "GLM 5.3 OR",
            [{"role": "tool", "content": "PRIVATE_SENTINEL"}],
        )
    assert response.status_code == 400
    assert response.json() == {"error": {
        "message": "A completed tool result is missing its native result identity."
    }}
    assert gateway.seen == []
    assert "PRIVATE_SENTINEL" not in response.text


def test_native_wire_measurement_failure_is_fixed_local_failure_not_zero_bytes(
        native_env, monkeypatch):
    """Reviewer case 5: serializer failure cannot bypass the request-byte guard."""
    client, orch, gateway = native_env
    project = orch._project
    assert client.post("/api/project/model", json={
        "pick": "GLM 5.3 OR", "mode": "implement",
    }).status_code == 200
    project.pre_edit_guard = PreEditGuard(BuildPolicy(), "base", lambda: "base")
    monkeypatch.setattr(
        native_routes, "wire_bytes",
        lambda _outbound: (_ for _ in ()).throw(ValueError("PRIVATE_WIRE_BODY")),
    )

    with active(orch) as headers:
        project.active_build_intent = BuildIntent.for_direct("build it")
        response = dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR")

    assert response.status_code == 409, response.text
    assert response.json() == {"error": {
        "message": native_routes._PRE_EDIT_MEASUREMENT_ERROR,
    }}
    assert gateway.seen == []
    decision = project.pre_edit_guard.consume_pending()
    assert decision.action is PreEditAction.FAIL
    assert decision.trigger is PreEditTrigger.REQUEST_MEASUREMENT_UNAVAILABLE
    assert project.pre_edit_guard.diagnostic()["action"] == "fail"
    assert "PRIVATE_WIRE_BODY" not in response.text


@pytest.mark.parametrize("model,protocol", LANES)
@pytest.mark.parametrize("mutation", ["remove", "change", "duplicate"])
def test_a_corrupt_carrier_sends_zero_gateway_requests(
        native_env, monkeypatch, model, protocol, mutation):
    client, orch, gateway = native_env
    client.post("/api/project/model", json={"pick": model, "mode": "implement"})
    intent = BuildIntent.for_direct("private-boundary-sentinel")
    def mutate(outbound):
        key = "messages" if protocol is not Protocol.RESPONSES else "input"
        if mutation == "remove":
            outbound[key].pop()
        elif mutation == "change":
            encoded = json.dumps(outbound).replace("Implement now.", "Implement later.")
            outbound = json.loads(encoded)
        else:
            outbound = build_intent.install(outbound, protocol, intent)
        return outbound

    original = (orch._project.shim.prepare if protocol is Protocol.CHAT
                else native_routes.prepare_native)

    def changed(*args, **kwargs):
        outbound, *rest = original(*args, **kwargs)
        outbound = mutate(outbound)
        return (outbound, *rest)

    if protocol is Protocol.CHAT:
        monkeypatch.setattr(orch._project.shim, "prepare", changed)
    else:
        monkeypatch.setattr(native_routes, "prepare_native", changed)

    before = len(gateway.seen)
    timing.start_turn("build", turn_id="intent_failure")
    try:
        with active(orch) as headers:
            orch._project.active_build_intent = intent
            response = dispatch(client, headers, protocol, model)
        record = timing.finish_turn()
    finally:
        if timing.current() is not None:
            timing.finish_turn()

    assert response.status_code == 400
    assert len(gateway.seen) == before
    assert orch._project.last_gateway_error == {"message": native_routes._BUILD_INTENT_ERROR}
    diagnostic = timing.as_dict(record)["calls"][0]["buildIntent"]
    assert diagnostic["status"] == {
        "remove": "missing", "change": "changed", "duplicate": "duplicate",
    }[mutation]
    assert diagnostic["failureStage"] == "final_check"


def test_a_data_use_text_rewrite_is_not_reinserted_after_policy(native_env, monkeypatch):
    client, orch, gateway = native_env
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "implement"})
    intent = BuildIntent.for_direct("private-boundary-sentinel")
    original = orch._project.shim.data_use.prepare

    def redact(request, *args, **kwargs):
        prepared, used = original(request, *args, **kwargs)
        messages = copy.deepcopy(prepared["messages"])
        messages[-1]["content"] = data_use._redact_message_text(
            messages[-1]["content"], {"sources": []})
        return {**prepared, "messages": messages}, used

    monkeypatch.setattr(orch._project.shim.data_use, "prepare", redact)
    before = len(gateway.seen)
    with active(orch) as headers:
        orch._project.active_build_intent = intent
        response = dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR")

    assert response.status_code == 400
    assert len(gateway.seen) == before
    assert orch._project.last_gateway_error == {"message": native_routes._BUILD_INTENT_ERROR}


def test_no_active_intent_keeps_the_native_request_unchanged(native_env):
    client, orch, gateway = native_env
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "implement"})
    with active(orch) as headers:
        assert dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR").status_code == 200
    assert "SAGE_BUILD_INTENT" not in json.dumps(gateway.seen[-1][0])


def test_ten_native_calls_each_forward_one_carrier_without_growth(native_env):
    client, orch, gateway = native_env
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "implement"})
    intent = BuildIntent.for_direct("one stable task")
    before = len(gateway.seen)
    with active(orch) as headers:
        orch._project.active_build_intent = intent
        for _ in range(10):
            assert dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR").status_code == 200

    forwarded = [request for request, _labels in gateway.seen[before:]]
    assert len(forwarded) == 10
    assert all(build_intent.inspect(request, Protocol.CHAT, intent).carrier_count == 1
               for request in forwarded)
    assert len({len(json.dumps(request)) for request in forwarded}) == 1


def test_a_late_prior_session_cannot_receive_the_next_turns_intent(native_env):
    client, orch, gateway = native_env
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "implement"})
    with active(orch) as old_headers:
        orch._project.active_build_intent = BuildIntent.for_direct("first turn")
        assert dispatch(client, old_headers, Protocol.CHAT, "GLM 5.3 OR").status_code == 200
        before = len(gateway.seen)
        orch._project.active_session_id = "ses_next"
        orch._project.active_build_intent = BuildIntent.for_direct("second turn")
        response = dispatch(client, old_headers, Protocol.CHAT, "GLM 5.3 OR")

    assert response.status_code == 400
    assert len(gateway.seen) == before
    assert "second turn" not in json.dumps([request for request, _labels in gateway.seen])


def test_diagnostics_export_only_safe_intent_metadata(native_env, caplog):
    client, orch, _gateway = native_env
    private = "PRIVATE_INTENT_SENTINEL_525"
    intent = BuildIntent.for_approved((private,), private, private, private)
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "implement"})
    timing.start_turn("build", turn_id="private_turn")
    with active(orch) as headers:
        orch._project.active_build_intent = intent
        assert dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR").status_code == 200
    record = timing.finish_turn()
    exported = build_diagnostics.snapshot(record, {
        "turnId": "private_turn", "appId": "app", "conversationId": "thread", "kind": "build",
    })
    encoded = json.dumps(exported)
    assert private not in caplog.text and private not in encoded
    assert exported["timing"]["calls"][0]["buildIntent"] == {
        "kind": "approved_plan", "status": "ok", "carrierCount": 1,
        "carrierBytes": len(build_intent.render(intent).encode()), "sourceRequestCount": 1,
        "planPresent": True, "failureStage": "none",
    }
