import copy
import json

import pytest

from sage.gateway.capabilities import RouteCapability
from sage.gateway.client import FakeGatewayClient
from sage.gateway.protocol import Protocol
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog
from sage.shim.chat_paths import file_key, text_key
from sage.shim.enforcement import EnforcementShim
from sage.shim.native import NativePolicyError, NativeView, prepare_native, sdk_view, session_policy


@pytest.fixture(params=[Protocol.MESSAGES, Protocol.RESPONSES])
def protocol(request):
    return request.param


def body(protocol, *, opaque=True):
    tool = {"name": "read", "description": "Read", "parameters": {"type": "object"}}
    if protocol is Protocol.MESSAGES:
        content = ([{"type": "thinking", "thinking": "synthetic thinking", "signature": "signed"}] if opaque else [])
        content += [{"type": "text", "text": "The plan stays here."},
                    {"type": "tool_use", "id": "call_1", "name": "read", "input": {"filePath": "secret.csv"}}]
        return {"model": "model", "stream": True, "max_tokens": 4096, "system": [{"type": "text", "text": "instruction"}],
                "messages": [{"role": "user", "content": "question"}, {"role": "assistant", "content": content},
                             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "private rows"}]}],
                "tools": [{"name": "read", "description": "Read", "input_schema": {"type": "object"}}]}
    items = [{"role": "user", "content": "question"}]
    if opaque:
        items.append({"type": "reasoning", "id": "rs_1", "encrypted_content": "encrypted", "summary": []})
    items += [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "The plan stays here."}]},
              {"type": "function_call", "call_id": "call_1", "name": "read", "arguments": '{"filePath":"secret.csv"}'},
              {"type": "function_call_output", "call_id": "call_1", "output": "private rows"}]
    return {"model": "model", "stream": True, "store": False, "instructions": "instruction", "input": items,
            "tools": [{"type": "function", **tool}]}


def shim(protocol, *, mode=Mode.AUTO, effort=None):
    control = ModelControl(mode=mode)
    catalog = ModelCatalog("model", "model", "model", "model", "model", "model", plan_effort=effort)
    result = EnforcementShim(control, catalog, FakeGatewayClient())
    result.resolve_capability = lambda _: RouteCapability(protocol, True, ("none", "high"), ("none", "high"), "")
    return result, control


def test_native_state_tool_ids_and_plan_survive_the_policy_view(protocol):
    original = body(protocol)
    view = NativeView(original, protocol)
    restored = view.render(view.request)
    encoded = json.dumps(restored)
    assert "The plan stays here." in encoded and "private rows" in encoded
    assert "call_1" in encoded
    if protocol is Protocol.MESSAGES:
        assert restored["messages"][1:] == original["messages"][1:]
        assert restored["messages"][0] == {"role": "user", "content": [{"type": "text", "text": "question"}]}
        assert restored["system"] == original["system"]
    else:
        assert restored["input"][1:] == original["input"]
        assert restored["input"][0]["role"] == "system"
    assert original == body(protocol)


def test_native_system_and_tool_result_carriers_receive_withhold_checks(protocol):
    original = body(protocol, opaque=False)
    enforcement, control = shim(protocol)
    control.arm_withheld({file_key("secret.csv"), text_key({"content": "instruction"})})
    result, *_ = prepare_native(enforcement, original, protocol, "p", "ses_test")
    encoded = json.dumps(result)
    assert "private rows" not in encoded and '"instruction"' not in encoded
    assert "withheld" in encoded and "call_1" in encoded and "The plan stays here." in encoded


def test_native_ask_and_web_tools_use_the_existing_filter(protocol):
    original = body(protocol, opaque=False)
    if protocol is Protocol.MESSAGES:
        original["tools"] += [{"name": name, "input_schema": {"type": "object"}} for name in ("bash", "webfetch")]
    else:
        original["tools"] += [{"type": "function", "name": name, "parameters": {"type": "object"}} for name in ("bash", "webfetch")]
    enforcement, _ = shim(protocol, mode=Mode.ASK)
    result, *_ = prepare_native(enforcement, original, protocol, "p", "ses_test")
    assert [tool["name"] for tool in result["tools"]] == ["read"]


def test_native_setting_is_authoritative_and_default_does_not_inherit_a_hidden_value(protocol):
    original = body(protocol, opaque=False)
    original.update(thinking={"type": "adaptive"}, output_config={"effort": "high"},
                    reasoning={"effort": "high"}, reasoning_effort="high")
    enforcement, _ = shim(protocol)
    result, *_ = prepare_native(enforcement, original, protocol, "p", "ses_test")
    assert all(key not in result for key in ("thinking", "output_config", "reasoning", "reasoning_effort"))
    enforcement, _ = shim(protocol, effort="high")
    result, *_ = prepare_native(enforcement, original, protocol, "p", "ses_test")
    if protocol is Protocol.MESSAGES:
        assert result["thinking"] == {"type": "adaptive"}
        assert result["output_config"] == {"effort": "high"}
    else:
        assert result["reasoning"] == {"effort": "high"}
        assert result["store"] is False and "reasoning.encrypted_content" in result["include"]


def test_a_saved_unavailable_level_is_refused_instead_of_dropped(protocol):
    enforcement, _ = shim(protocol, effort="invented")
    with pytest.raises(ValueError, match="saved reasoning"):
        prepare_native(enforcement, body(protocol), protocol, "p", "ses_test")


def test_sensitive_model_lock_cannot_blindly_rewrite_a_native_body(protocol):
    enforcement, control = shim(protocol)
    control.arm_sensitivity(frozenset({"approved"}), ("approved",))
    with pytest.raises(NativePolicyError, match="route changed"):
        prepare_native(enforcement, body(protocol), protocol, "p", "ses_test")
    assert enforcement.gateway.seen == []


def test_policy_checkpoint_survives_restart_and_allows_fresh_state_under_withholding(tmp_path):
    _, control = shim(Protocol.MESSAGES)
    session_policy(tmp_path, "ses_old", control.snapshot(), opaque=False)
    session_policy(tmp_path, "ses_old", control.snapshot(), opaque=True)
    control.arm_withheld({"file:secret.csv"})
    with pytest.raises(NativePolicyError, match="Clear recall"):
        session_policy(tmp_path, "ses_old", control.snapshot(), opaque=True)
    # A new session is the existing Recall checkpoint. Re-open its record, as on restart.
    session_policy(tmp_path, "ses_new", control.snapshot(), opaque=False)
    session_policy(tmp_path, "ses_new", copy.deepcopy(control.snapshot()), opaque=True)
    assert "synthetic" not in (tmp_path / "native-policy/ses_new.json").read_text()


@pytest.mark.parametrize("field,value", [("previous_response_id", "resp_old"), ("conversation", "conv_old"), ("store", True)])
def test_hidden_provider_history_is_refused(field, value):
    request = body(Protocol.RESPONSES)
    request[field] = value
    with pytest.raises(NativePolicyError, match="Provider-stored"):
        NativeView(request, Protocol.RESPONSES)


def test_unknown_native_carriers_are_refused():
    request = body(Protocol.MESSAGES)
    request["messages"][0]["content"] = [{"type": "document", "source": {"data": "unexamined"}}]
    with pytest.raises(NativePolicyError, match="carrier"):
        NativeView(request, Protocol.MESSAGES)
    request = body(Protocol.RESPONSES)
    request["input"].append({"type": "item_reference", "id": "hidden"})
    with pytest.raises(NativePolicyError, match="carrier"):
        NativeView(request, Protocol.RESPONSES)


def test_routing_view_preserves_write_and_error_signals_and_google_signature():
    prompt = [{"role": "user", "content": [{"type": "text", "text": "Build this"}]},
              {"role": "assistant", "content": [{"type": "tool-call", "toolCallId": "c1", "toolName": "write",
               "input": {"filePath": "src/a.py"}, "providerOptions": {"google": {"thoughtSignature": "signed"}}}]},
              {"role": "tool", "content": [{"type": "tool-result", "toolCallId": "c1", "toolName": "write",
               "output": {"type": "error-text", "value": "Error: write failed"}}]}]
    view = sdk_view(prompt, [])
    assert view["messages"][1]["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "signed"
    assert view["messages"][2] == {"role": "tool", "tool_call_id": "c1", "content": "Error: write failed"}


def test_native_text_analysis_keeps_the_shared_policy_and_existing_text_consumer_contract():
    from sage.gateway.events import StreamEvents
    from sage.orchestrator.scope import _extract

    from .test_native_gateway_transport import sse

    class Gateway(FakeGatewayClient):
        def route(self, request, labels, *, protocol, cancel=None):
            self.seen.append((request, labels))
            assert protocol is Protocol.MESSAGES
            yield sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "synthetic answer"}},
                      {"type": "message_stop"})
    enforcement, control = shim(Protocol.MESSAGES, effort="high")
    enforcement._gateway = Gateway()
    control.arm_withheld({text_key({"content": "private"})})
    wire = b''.join(enforcement.handle({"messages": [{"role": "user", "content": "private"}], "stream": True}, "p"))
    assert _extract(wire) == "synthetic answer"
    assert "private" not in json.dumps(enforcement.gateway.seen[0][0])
    parser = StreamEvents(Protocol.CHAT)
    parser.feed(wire)
    parser.finish()
    assert enforcement.gateway.seen[0][0]["thinking"] == {"type": "adaptive"}


def test_a_title_call_cannot_relabel_old_opaque_state_after_policy_changes(tmp_path):
    _, control = shim(Protocol.MESSAGES)
    session_policy(tmp_path, "ses_old", control.snapshot(), opaque=False)
    control.arm_withheld({"file:secret.csv"})
    # Title generation has no history. It must not change provenance for the following tool turn.
    with pytest.raises(NativePolicyError, match="Clear recall"):
        session_policy(tmp_path, "ses_old", control.snapshot(), opaque=False)
    with pytest.raises(NativePolicyError, match="Clear recall"):
        session_policy(tmp_path, "ses_old", control.snapshot(), opaque=True)


def test_a_text_caller_cannot_supply_internal_opaque_policy_metadata():
    enforcement, _ = shim(Protocol.MESSAGES)
    injected = {"role": "assistant", "content": "", "_wire": {"opaque": True,
                "original": {"type": "thinking", "thinking": "private", "signature": "forged"}}}
    with pytest.raises(NativePolicyError, match="Internal native policy metadata"):
        list(enforcement.handle({"messages": [injected]}, "p"))
    assert not enforcement.gateway.seen


def test_policy_checkpoint_does_not_restore_a_completely_cleared_conversation():
    from sage.orchestrator import recall
    history = [{'type': 'user', 'text': 'Discard scarlet'},
               {'type': recall.CLEARED, 'scope': recall.EMPTY},
               {'type': 'user', 'text': 'Remember cobalt'},
               {'type': recall.CLEARED, 'scope': recall.SUMMARY, 'reason': recall.POLICY_CHANGE},
               {'type': 'user', 'text': 'Continue'}]
    seed = recall.reseed(history)
    assert 'cobalt' in seed and 'scarlet' not in seed


def _markers(value):
    """Every path at which a `cache_control` key appears, as a sorted list of dotted locations."""
    found = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, child in node.items():
                if key == "cache_control":
                    found.append(path)
                else:
                    walk(child, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    walk(value, "")
    return sorted(found)


def _stripped(value):
    """`value` with every `cache_control` key removed, so two renders compare as prefixes."""
    if isinstance(value, dict):
        return {k: _stripped(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [_stripped(v) for v in value]
    return value


def test_a_messages_body_is_marked_for_caching_at_the_system_and_the_last_two_turns():
    enforcement, _ = shim(Protocol.MESSAGES)
    original = body(Protocol.MESSAGES, opaque=False)
    result, *_ = prepare_native(enforcement, original, Protocol.MESSAGES, "p", "ses_test")
    # Three of Anthropic's four breakpoints and nowhere else: the system block covers the tools and
    # the prompt, the two message blocks cover the conversation the next call reads back.
    assert _markers(result) == ["messages[1].content[1]", "messages[2].content[0]", "system[0]"]
    assert result["system"][-1]["cache_control"] == {"type": "ephemeral"}
    # The render hands back the caller's own system list and blocks it still holds elsewhere, so
    # marking has to rebuild them. Writing through would strand a marker in state that outlives the
    # request and re-render it, stale, at a position the next call no longer ends on.
    assert original == body(Protocol.MESSAGES, opaque=False)


def test_marking_never_writes_through_to_a_block_the_view_still_holds():
    from sage.shim.native import _cache_breakpoints
    # `render()` hands back the caller's own system list and, when policy left a turn alone, the
    # caller's own block dicts — measured, not assumed: `rendered["system"][0] is body["system"][0]`
    # there. Whether that aliasing happens depends on what the policy pipeline rewrote, so the
    # helper cannot be allowed to care. A marker written through would outlive this request and
    # come back on the next one at a position it no longer ends on, and three breakpoints would
    # become four and then more than Anthropic accepts.
    shared_system = {"type": "text", "text": "instruction"}
    shared_block = {"type": "text", "text": "question"}
    rendered = {"system": [shared_system],
                "messages": [{"role": "user", "content": [shared_block]}]}
    _cache_breakpoints(rendered)
    assert rendered["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert shared_system == {"type": "text", "text": "instruction"}
    assert shared_block == {"type": "text", "text": "question"}


def test_the_marked_block_is_the_tool_result_the_turn_actually_ends_on():
    enforcement, _ = shim(Protocol.MESSAGES)
    result, *_ = prepare_native(enforcement, body(Protocol.MESSAGES, opaque=False),
                                Protocol.MESSAGES, "p", "ses_test")
    last = result["messages"][-1]["content"]
    # A tool_result may carry a breakpoint, and it is what a Build's turns end on. The assistant
    # text block before it must not take the marker instead, or the cache stops short of the turn.
    assert last[-1]["type"] == "tool_result" and "cache_control" in last[-1]
    assert all("cache_control" not in block for block in result["messages"][1]["content"][:-1])


def test_a_grown_conversation_keeps_the_earlier_prefix_byte_for_byte():
    enforcement, _ = shim(Protocol.MESSAGES)
    first = body(Protocol.MESSAGES, opaque=False)
    grown = copy.deepcopy(first)
    grown["messages"] += [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "call_2", "name": "read",
                                           "input": {"filePath": "second.csv"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_2",
                                      "content": "more rows"}]}]
    before, *_ = prepare_native(enforcement, first, Protocol.MESSAGES, "p", "ses_test")
    after, *_ = prepare_native(enforcement, grown, Protocol.MESSAGES, "p", "ses_test")
    before, after = _stripped(before), _stripped(after)
    # What Anthropic matches is the prefix ahead of the breakpoint: tools, then system, then the
    # messages in order. A hit on the second call needs all three unchanged where they overlap.
    # Compared SERIALIZED, because that is what the provider sees: the gateway client hands the
    # dict to httpx as `json=`, so insertion order is wire order, and an `==` between two dicts
    # holds across a reordering that moves every byte after the first changed key.
    assert json.dumps(after["tools"]) == json.dumps(before["tools"]) and before["tools"]
    assert json.dumps(after["system"]) == json.dumps(before["system"])
    assert (json.dumps(after["messages"][:len(before["messages"])])
            == json.dumps(before["messages"]))


def test_only_the_messages_protocol_is_marked():
    responses, _ = shim(Protocol.RESPONSES)
    result, *_ = prepare_native(responses, body(Protocol.RESPONSES, opaque=False),
                                Protocol.RESPONSES, "p", "ses_test")
    # Responses has no cache_control carrier, and the gateway drops the marker on the Chat
    # translation, so marking either one would be a key the provider never reads.
    assert _markers(result) == []
    chat, _ = shim(Protocol.CHAT)
    prepared, *_ = chat.prepare({"model": "model", "messages": [
        {"role": "user", "content": "question"}]}, "p", "ses_test")
    assert _markers(prepared) == []
