from __future__ import annotations

import copy
import json

import pytest

from sage.build_diagnostics import _request_composition
from sage.build_policy import BuildPolicy
from sage.gateway.capabilities import RouteCapability
from sage.gateway.client import FakeGatewayClient
from sage.gateway.protocol import Protocol
from sage.request_composition import measure, wire_bytes
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase
from sage.shim.enforcement import EnforcementShim
from sage.shim.native import prepare_native
from sage.tool_result_window import (
    COMPACTED_RECEIPT,
    SHORTENED_RECEIPT,
    _content_bytes,
    apply_tool_result_window,
)
from sage.workspace.manager import Workspace

CATALOG = ModelCatalog("model", "model", "model", "model", "model", "model")


def _result(text, identifier="call_1", **fields):
    return {"role": "tool", "tool_call_id": identifier, "content": text, **fields}


def _request(*results):
    messages = []
    for result in results:
        identifier = result["tool_call_id"]
        messages += [
            {"role": "assistant", "tool_calls": [{
                "id": identifier, "type": "function",
                "function": {"name": "read", "arguments": '{"path":"full.txt"}'},
            }]},
            result,
        ]
    return {"model": "model", "messages": messages}


def _tool_contents(request):
    return [message["content"] for message in request["messages"]
            if message.get("role") == "tool"]


@pytest.mark.parametrize("size", [16_383, 16_384])
def test_a_tool_result_at_or_below_the_per_result_limit_is_unchanged(size):
    policy = BuildPolicy()
    original = _request(_result("x" * size))

    bounded, metadata = apply_tool_result_window(original, policy)

    assert bounded == original
    assert _tool_contents(bounded) == ["x" * size]
    assert metadata["perResultShortenedCount"] == 0
    assert metadata["originalModelFacingBytes"] == size
    assert metadata["forwardedModelFacingBytes"] == size


def test_one_byte_over_keeps_utf8_safe_head_and_tail_and_one_fixed_receipt():
    policy = BuildPolicy()
    text = "BEGIN-" + ("🙂" * 4_093) + "-THEEND"
    assert len(text.encode()) == policy.tool_result_max_bytes + 1

    bounded, metadata = apply_tool_result_window(_request(_result(text)), policy)
    content = _tool_contents(bounded)[0]

    assert content.startswith("BEGIN-") and content.endswith("-THEEND")
    assert content.count(SHORTENED_RECEIPT) == 1
    assert "�" not in content
    assert len(content.encode()) <= policy.tool_result_max_bytes
    assert metadata["perResultShortenedCount"] == 1


def test_head_and_tail_use_the_policy_fraction_after_the_exact_receipt_bytes():
    policy = BuildPolicy()
    text = "H" * 20_000 + "T" * 20_000
    bounded, _ = apply_tool_result_window(_request(_result(text)), policy)
    content = _tool_contents(bounded)[0]
    head, tail = content.split(SHORTENED_RECEIPT)
    remaining = policy.tool_result_max_bytes - len(SHORTENED_RECEIPT.encode())

    assert len(head.encode()) == int(remaining * policy.tool_result_head_fraction)
    assert len(tail.encode()) == remaining - len(head.encode())
    assert set(head) == {"H"}
    assert set(tail) == {"T"}


def test_structured_text_stays_valid_and_mixed_media_is_byte_for_byte_unchanged():
    policy = BuildPolicy()
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,PRIVATEIMAGE"}}
    content = [
        {"type": "text", "text": "HEAD" + "x" * 20_000 + "TAIL"},
        image,
    ]
    original = _request(_result(content))

    bounded, _ = apply_tool_result_window(original, policy)
    result = _tool_contents(bounded)[0]

    assert isinstance(result, list) and result[-1] == image
    assert result[-1] is image
    assert result[0]["text"].startswith("HEAD") and result[0]["text"].endswith("TAIL")
    assert _content_bytes(result) <= policy.tool_result_max_bytes
    json.loads(json.dumps(bounded))
    assert original["messages"][-1]["content"][0]["text"].endswith("TAIL")
    assert len(original["messages"][-1]["content"][0]["text"]) > 20_000


@pytest.mark.parametrize("prefix", ["data:image/png;base64,", "data:audio/wav;base64,"])
def test_a_media_data_uri_is_not_limited_as_text(prefix):
    content = prefix + "A" * 40_000
    original = _request(_result(content))
    bounded, metadata = apply_tool_result_window(original, BuildPolicy())

    assert bounded == original
    assert metadata["originalModelFacingBytes"] == 0


def test_an_oversized_structured_result_is_replaced_whole_instead_of_slicing_json():
    policy = BuildPolicy()
    content = {"rows": ["x" * 20_000], "status": "error"}
    bounded, _ = apply_tool_result_window(_request(_result(content, status="error")), policy)

    result = bounded["messages"][-1]
    assert result["content"] == SHORTENED_RECEIPT
    assert result["status"] == "error"
    assert json.loads(json.dumps(bounded)) == bounded


def test_an_aggregate_at_or_below_the_limit_is_unchanged():
    policy = BuildPolicy()
    exact = [_result("x" * policy.tool_result_max_bytes, f"call_{index}") for index in range(8)]
    below = exact[:-1] + [_result("x" * (policy.tool_result_max_bytes - 1), "call_7")]

    assert apply_tool_result_window(_request(*below), policy)[0] == _request(*below)
    bounded, metadata = apply_tool_result_window(_request(*exact), policy)
    assert bounded == _request(*exact)
    assert metadata["forwardedModelFacingBytes"] == policy.tool_result_aggregate_max_bytes


def test_one_byte_over_compacts_the_oldest_and_keeps_the_newest_useful_result():
    policy = BuildPolicy()
    results = [_result("A" * policy.tool_result_max_bytes, f"call_{index}") for index in range(8)]
    results.append(_result("N", "call_newest"))

    bounded, metadata = apply_tool_result_window(_request(*results), policy)
    contents = _tool_contents(bounded)

    assert contents[0] == COMPACTED_RECEIPT
    assert contents[-1] == "N"
    assert metadata["aggregateCompactedCount"] == 1
    assert metadata["forwardedModelFacingBytes"] <= policy.tool_result_aggregate_max_bytes


def test_thousands_of_results_use_empty_fallback_without_breaking_call_pairs():
    policy = BuildPolicy()
    results = [_result("x" * 200, f"call_{index}") for index in range(1_100)]
    bounded, metadata = apply_tool_result_window(_request(*results), policy)
    calls = [call["id"] for message in bounded["messages"] for call in message.get("tool_calls", [])]
    returned = [message["tool_call_id"] for message in bounded["messages"]
                if message.get("role") == "tool"]

    assert metadata["emptyFallbackCount"] > 0
    assert metadata["forwardedModelFacingBytes"] <= policy.tool_result_aggregate_max_bytes
    assert calls == returned
    assert _tool_contents(bounded).count(COMPACTED_RECEIPT) == 1


def test_messages_pathological_fallback_uses_zero_byte_protocol_valid_content():
    policy = BuildPolicy()
    results = []
    for index in range(1_100):
        text = "x" * 200
        result = _result(text, f"call_{index}")
        result["_wire"] = {
            "original": {
                "type": "tool_result", "tool_use_id": f"call_{index}", "content": text,
            },
            "content": text,
            "opaque": False,
        }
        results.append(result)

    bounded, metadata = apply_tool_result_window(_request(*results), policy)
    contents = _tool_contents(bounded)

    assert metadata["emptyFallbackCount"] > 0
    assert metadata["forwardedModelFacingBytes"] <= policy.tool_result_aggregate_max_bytes
    assert contents.count([]) == metadata["emptyFallbackCount"]
    assert contents.count(COMPACTED_RECEIPT) == 1


def test_the_transformation_is_deterministic_and_changes_no_other_content_or_arguments():
    policy = BuildPolicy()
    original = _request(_result("x" * 20_000))
    original["messages"][:0] = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": SHORTENED_RECEIPT},
        {"role": "assistant", "content": "assistant"},
    ]
    saved = copy.deepcopy(original)

    first = apply_tool_result_window(original, policy)
    second = apply_tool_result_window(original, policy)

    assert first == second
    assert original == saved
    assert first[0]["messages"][:3] == original["messages"][:3]
    assert first[0]["messages"][3]["tool_calls"] == original["messages"][3]["tool_calls"]


def test_stored_request_and_build_history_keep_the_full_result(tmp_path):
    full = "FULL_STORED_RESULT_524" + "x" * 20_000
    stored_request = _request(_result(full))
    saved = copy.deepcopy(stored_request)
    workspace = Workspace("Sage", tmp_path / "app", "app_1")
    workspace.append_history(
        {"type": "agent", "kind": "tool", "tool": "read", "detail": full}, "thread_1"
    )

    bounded, _ = apply_tool_result_window(stored_request, BuildPolicy())

    assert SHORTENED_RECEIPT in _tool_contents(bounded)[0]
    assert stored_request == saved
    assert workspace.read_history("thread_1")[0]["detail"] == full


def _shim(protocol=Protocol.CHAT):
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    shim = EnforcementShim(
        control, CATALOG, FakeGatewayClient(), build_policy=BuildPolicy()
    )
    shim.resolve_capability = lambda _model: RouteCapability(protocol, True)
    return shim


def test_chat_completions_keeps_call_result_pairing_after_shortening():
    request = _request(_result("BEGIN" + "x" * 20_000 + "END"))
    bounded, *_ = _shim().prepare(request, "project", "session", native=True,
                                  rewrite_counts={})

    assert bounded["messages"][1]["tool_call_id"] == bounded["messages"][0]["tool_calls"][0]["id"]
    assert SHORTENED_RECEIPT in bounded["messages"][1]["content"]


def test_pinned_implementation_mode_is_bounded_even_if_a_stale_phase_was_supplied():
    control = ModelControl(mode=Mode.IMPLEMENT)
    shim = EnforcementShim(control, CATALOG, FakeGatewayClient(), build_policy=BuildPolicy())
    bounded, *_ = shim.prepare(_request(_result("x" * 20_000)), "project", "session",
                               native=True, rewrite_counts={})

    assert SHORTENED_RECEIPT in _tool_contents(bounded)[0]


def test_repeat_and_repair_classification_reads_the_full_result_before_shortening(monkeypatch):
    from sage.shim import enforcement

    seen = []
    original_assess = enforcement.assess

    def observe(messages):
        seen.append(messages[-1]["content"])
        return original_assess(messages)

    monkeypatch.setattr(enforcement, "assess", observe)
    request = _request(_result("x" * 20_000))
    bounded, *_ = _shim().prepare(request, "project", "session", native=True,
                                  rewrite_counts={})

    assert seen == ["x" * 20_000]
    assert len(_tool_contents(bounded)[0].encode()) <= BuildPolicy().tool_result_max_bytes


def _native_body(protocol):
    if protocol is Protocol.MESSAGES:
        return {
            "model": "model", "stream": True, "max_tokens": 100,
            "messages": [
                {"role": "assistant", "content": [{
                    "type": "tool_use", "id": "call_1", "name": "read", "input": {"path": "full.txt"},
                }]},
                {"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": "call_1",
                    "content": "BEGIN" + "x" * 20_000 + "END",
                }]},
            ],
        }
    return {
        "model": "model", "stream": True, "store": False,
        "input": [
            {"type": "function_call", "call_id": "call_1", "name": "read",
             "arguments": '{"path":"full.txt"}'},
            {"type": "function_call_output", "call_id": "call_1",
             "output": "BEGIN" + "x" * 20_000 + "END"},
        ],
    }


def test_messages_protocol_keeps_tool_use_result_pairing_after_shortening():
    outbound, *_ = prepare_native(_shim(Protocol.MESSAGES), _native_body(Protocol.MESSAGES),
                                  Protocol.MESSAGES, "project", "session", rewrite_counts={})
    call = outbound["messages"][0]["content"][0]
    result = outbound["messages"][1]["content"][0]

    assert call["id"] == result["tool_use_id"] == "call_1"
    assert SHORTENED_RECEIPT in json.dumps(result)
    assert _content_bytes(result["content"]) <= BuildPolicy().tool_result_max_bytes


def test_responses_protocol_keeps_function_call_output_pairing_after_shortening():
    outbound, *_ = prepare_native(_shim(Protocol.RESPONSES), _native_body(Protocol.RESPONSES),
                                  Protocol.RESPONSES, "project", "session", rewrite_counts={})
    call = next(item for item in outbound["input"] if item.get("type") == "function_call")
    result = next(item for item in outbound["input"] if item.get("type") == "function_call_output")

    assert call["call_id"] == result["call_id"] == "call_1"
    assert SHORTENED_RECEIPT in json.dumps(result)
    assert _content_bytes(result["output"]) <= BuildPolicy().tool_result_max_bytes


def test_privacy_replacement_precedes_the_window_and_private_content_reaches_no_output(caplog):
    private = "PRIVATE_SENTINEL_524" + "x" * 20_000
    privacy_receipt = "[withheld: privacy-safe receipt]"
    shim = _shim()

    def privacy_prepare(request, **_kwargs):
        messages = [{**message, "content": privacy_receipt}
                    if message.get("role") == "tool" else message
                    for message in request["messages"]]
        return {**request, "messages": messages}, set()

    shim.data_use.prepare = privacy_prepare
    rewrites = {}
    with caplog.at_level("INFO", logger="sage.shim"):
        bounded, *_ = shim.prepare(_request(_result(private)), "project", "session",
                                   native=True, rewrite_counts=rewrites)
    diagnostic = measure(bounded, wire_bytes(bounded), rewrites)

    assert _tool_contents(bounded) == [privacy_receipt]
    assert private not in json.dumps(bounded)
    assert private not in json.dumps(diagnostic)
    assert private not in caplog.text
    assert diagnostic["toolResultWindow"]["originalModelFacingBytes"] == len(privacy_receipt.encode())


def test_a_transformation_logs_one_content_free_event(caplog):
    beginning = "BEGIN_PRIVATE_LOOKING_TEXT"
    ending = "END_PRIVATE_LOOKING_TEXT"
    with caplog.at_level("INFO", logger="sage.shim"):
        _shim().prepare(_request(_result(beginning + "x" * 20_000 + ending)),
                        "project_524", "session_524", native=True, rewrite_counts={})

    events = [record.getMessage() for record in caplog.records
              if record.getMessage().startswith("tool result window:")]
    assert len(events) == 1
    assert "project=project_524" in events[0] and "session=session_524" in events[0]
    assert beginning not in events[0] and ending not in events[0]


@pytest.mark.parametrize("mode", [Mode.ASK, Mode.PLAN])
def test_non_implementation_modes_keep_tool_results_unchanged(mode):
    control = ModelControl(mode=mode)
    shim = EnforcementShim(control, CATALOG, FakeGatewayClient(), build_policy=BuildPolicy())
    original = _request(_result("x" * 20_000))

    bounded, *_ = shim.prepare(original, "project", "session", native=True, rewrite_counts={})

    assert _tool_contents(bounded) == ["x" * 20_000]


def test_chat_mode_keeps_tool_results_unchanged():
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    control.arm_chat("thread_1")
    shim = EnforcementShim(control, CATALOG, FakeGatewayClient(), build_policy=BuildPolicy())
    original = _request(_result("x" * 20_000))

    bounded, *_ = shim.prepare(original, "project", "session", native=True, rewrite_counts={})

    assert _tool_contents(bounded) == ["x" * 20_000]


def test_request_composition_and_export_metadata_are_content_free():
    policy = BuildPolicy()
    bounded, window = apply_tool_result_window(_request(_result("x" * 20_000)), policy)
    diagnostic = measure(bounded, wire_bytes(bounded), {"toolResultWindow": window})
    diagnostic["toolResultWindow"]["private"] = "PRIVATE_SENTINEL_524"
    exported = _request_composition(diagnostic)

    assert exported["toolResultWindow"] == window
    assert "PRIVATE_SENTINEL_524" not in json.dumps(exported)
    assert set(window) == {
        "policyVersion", "perResultLimitBytes", "aggregateLimitBytes", "resultCount",
        "originalModelFacingBytes", "forwardedModelFacingBytes", "perResultShortenedCount",
        "aggregateCompactedCount", "emptyFallbackCount",
    }
