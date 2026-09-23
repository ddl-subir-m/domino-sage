"""The final request breakdown is exact, bounded, and contains no request content (#511)."""
import json

import pytest

from sage import build_diagnostics as diagnostics
from sage import request_composition as composition
from sage import timing
from sage.driver.opencode import with_attachment_listing
from sage.liveread.data_use import DataUse

PRIVATE = "PRIVATE_SENTINEL_é_credential_path_row"


@pytest.mark.parametrize("payload", [
    {
        "model": "GLM 5.3 OR", "messages": [
            {"role": "system", "content": "rules " + PRIVATE},
            {"role": "user", "content": [
                {"type": "text", "text": "question " + PRIVATE},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + PRIVATE}},
            ]},
            {"role": "assistant", "tool_calls": [{"id": PRIVATE, "type": "function",
                "function": {"name": PRIVATE, "arguments": json.dumps({"value": PRIVATE})}}]},
            {"role": "tool", "tool_call_id": PRIVATE, "content": PRIVATE},
        ],
        "tools": [{"type": "function", "function": {"name": PRIVATE,
                  "description": PRIVATE, "parameters": {"type": "object"}}}],
        "unknown": {"private": PRIVATE},
    },
    {
        "model": "Opus 4.8", "system": [{"type": "text", "text": PRIVATE,
                                             "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": PRIVATE, "signature": PRIVATE},
                {"type": "tool_use", "id": PRIVATE, "name": PRIVATE,
                 "input": {"value": PRIVATE}},
            ]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": PRIVATE,
                                              "content": PRIVATE}]},
        ],
        "tools": [{"name": PRIVATE, "description": PRIVATE,
                  "input_schema": {"type": "object"}}],
    },
    {
        "model": "gpt-5.4", "instructions": PRIVATE,
        "input": [
            {"type": "reasoning", "encrypted_content": PRIVATE},
            {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": PRIVATE},
                {"type": "input_image", "image_url": PRIVATE},
            ]},
            {"type": "function_call", "call_id": PRIVATE, "name": PRIVATE,
             "arguments": json.dumps({"value": PRIVATE})},
            {"type": "function_call_output", "call_id": PRIVATE, "output": PRIVATE},
        ],
        "tools": [{"type": "function", "name": PRIVATE,
                  "description": PRIVATE, "parameters": {"type": "object"}}],
        "metadata": {"sage_route_check": PRIVATE}, "store": False,
    },
])
def test_every_native_shape_reconciles_without_retaining_content(payload):
    before = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    total = composition.wire_bytes(payload)
    result = composition.measure(payload, total, {"redactedCalls": 2})

    assert result["status"] == "complete"
    assert result["totalBytes"] == total
    assert sum(result["categories"].values()) == total
    assert result["toolArgumentsBytes"] <= result["categories"]["toolCallsBytes"]
    assert result["toolSchemaCount"] == 1
    assert result["toolCallCount"] == 1
    assert result["rewrites"]["redactedCalls"] == 2
    assert PRIVATE not in json.dumps(result, ensure_ascii=False)
    assert json.dumps(payload, ensure_ascii=False, sort_keys=True) == before


def test_payload_and_traversal_limits_keep_the_existing_total(monkeypatch):
    payload = {"messages": [{"role": "user", "content": PRIVATE}]}
    total = composition.wire_bytes(payload)
    monkeypatch.setattr(composition, "MAX_PAYLOAD_BYTES", total - 1)
    limited = composition.measure(payload, total)
    assert limited["status"] == "limited" and limited["limitReason"] == "payload_bytes"
    assert limited["categories"]["unclassifiedBytes"] == total
    assert sum(limited["categories"].values()) == total

    monkeypatch.setattr(composition, "MAX_PAYLOAD_BYTES", total)
    monkeypatch.setattr(composition, "MAX_NODES", 1)
    assert composition.measure(payload, total)["limitReason"] == "nodes"


def test_measurement_failure_is_metadata_not_a_request_failure(monkeypatch):
    payload = {"messages": [{"role": "user", "content": PRIVATE}]}
    total = composition.wire_bytes(payload)

    def fail(*_args, **_kwargs):
        raise RuntimeError(PRIVATE)

    monkeypatch.setattr(composition, "_shape", fail)
    result = composition.measure(payload, total)
    assert result["status"] == "unavailable"
    assert result["limitReason"] == "measurement_error"
    assert result["totalBytes"] == result["categories"]["unclassifiedBytes"] == total
    assert PRIVATE not in json.dumps(result)


def test_unicode_and_escaping_are_counted_as_wire_bytes():
    payload = {"messages": [
        {"role": "system", "content": "régle"},
        {"role": "user", "content": [{"type": "text", "text": 'say "hé"'}]},
        {"role": "assistant", "tool_calls": [{"type": "function", "function": {
            "name": "read", "arguments": '{"path":"é.csv"}'}}]},
    ]}
    result = composition.measure(payload, composition.wire_bytes(payload))
    call = payload["messages"][-1]["tool_calls"]
    arguments = call[0]["function"]["arguments"]
    assert result["categories"]["instructionsBytes"] == composition.wire_bytes("régle")
    assert result["categories"]["ordinaryTextBytes"] == composition.wire_bytes('say "hé"')
    assert result["categories"]["toolCallsBytes"] == composition.wire_bytes(call)
    assert result["toolArgumentsBytes"] == composition.wire_bytes(arguments)
    assert sum(result["categories"].values()) == result["totalBytes"]


def test_rewrite_counts_come_from_the_rewrite_sites(caplog):
    data = DataUse()
    path = "public/data/upload/uploads/private.csv"
    prompt = with_attachment_listing(
        "read @private.csv",
        [{"path": path, "name": "private.csv", "summary": "CSV - 1 column, 1 row",
          "detail": "columns: value"}],
        chat=True,
    )
    request = {"messages": [
        {"role": "user", "content": prompt},
        {"role": "assistant", "tool_calls": [{"id": "read1", "type": "function",
            "function": {"name": "read", "arguments": json.dumps({"filePath": path})}}]},
        {"role": "tool", "tool_call_id": "read1", "content": PRIVATE},
        {"role": "assistant", "tool_calls": [{"id": "echo1", "type": "function",
            "function": {"name": "bash", "arguments": json.dumps({
                "command": "echo 'local data withheld'"})}}]},
        {"role": "tool", "tool_call_id": "echo1", "content": "true"},
        {"role": "assistant", "tool_calls": [{"id": "image1", "type": "function",
            "function": {"name": "draw", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "image1", "content": [
            {"type": "image", "mimeType": "image/png", "data": PRIVATE}]},
    ]}
    counts = {}
    prepared, _ = data.prepare(request, rewrite_counts=counts)
    assert PRIVATE not in json.dumps(prepared)
    assert counts == {"redactedCalls": 1, "localExecutionReceipts": 1,
                      "markerEchoCorrections": 1, "externalImageReceipts": 1}
    assert PRIVATE not in caplog.text

    withheld = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + PRIVATE}},
    ]}]}
    counts = {}
    result = data.apply_restrictions(
        withheld, withheld={"file:" + path}, rewrite_counts=counts)
    assert PRIVATE not in json.dumps(result)
    assert counts == {"withheldImageReceipts": 1}


def test_alias_and_nested_composition_survive_the_export_sanitizer(tmp_path):
    total = 123
    measured = composition.measure({"messages": []}, total)
    measured["private"] = PRIVATE
    measured["categories"][PRIVATE] = 99
    measured["messagesByRole"][PRIVATE] = {"count": 9, "bytes": 9}
    rec = timing.TurnRecord("build", 1.0, 1.0, turn_id="turn_a",
                            app_id="app_a", conversation_id="thread_a")
    call = timing.ModelCall(1, 1.0, model="GLM 5.3 OR", requested_alias="GLM 5.3 OR",
                            response_reported_model="GLM 5.3 OR", t1=2.0,
                            forwarded_request_bytes=total, request_composition=measured)
    rec.calls = [call]
    rec.t1 = 2.0
    identity = {"turnId": "turn_a", "appId": "app_a",
                "conversationId": "thread_a", "kind": "build"}
    row = diagnostics.snapshot(rec, identity, terminal=True)
    assert diagnostics.Store(tmp_path).put(row)
    restored = diagnostics.Store(tmp_path).get("turn_a", "app_a", "thread_a")
    saved = restored["timing"]["calls"][0]
    assert saved["model"] == saved["requestedAlias"] == "GLM 5.3 OR"
    assert saved["responseReportedModel"] == "GLM 5.3 OR"
    assert saved["requestComposition"]["totalBytes"] == total
    assert PRIVATE not in json.dumps(restored, ensure_ascii=False)

    # The on-disk record is the restart boundary. Read it in a fresh interpreter.
    import subprocess
    import sys
    code = ("import json; from pathlib import Path; from sage.build_diagnostics import Store; "
            f"print(json.dumps(Store(Path({str(tmp_path)!r})).get("
            "'turn_a','app_a','thread_a')))" )
    restarted = json.loads(subprocess.check_output([sys.executable, "-c", code], text=True))
    restarted_call = restarted["timing"]["calls"][0]
    assert restarted_call["model"] == restarted_call["requestedAlias"] == "GLM 5.3 OR"
    assert restarted_call["requestComposition"]["totalBytes"] == total


def test_malformed_nested_composition_is_zeroed_without_losing_the_capture(tmp_path):
    measured = composition.measure({"messages": []}, 123)
    measured["categories"]["instructionsBytes"] = PRIVATE
    measured["categories"]["ordinaryTextBytes"] = True
    measured["messagesByRole"]["system"] = PRIVATE
    measured["messagesByRole"]["user"] = [PRIVATE]
    measured["rewrites"]["redactedCalls"] = PRIVATE
    measured["rewrites"]["markerEchoCorrections"] = True
    rec = timing.TurnRecord("build", 1.0, 1.0, turn_id="turn_malformed")
    rec.calls = [timing.ModelCall(
        1, 1.0, model="GLM 5.3 OR", t1=2.0,
        forwarded_request_bytes=123, request_composition=measured,
    )]
    rec.t1 = 2.0
    identity = {"turnId": "turn_malformed", "appId": "app_a",
                "conversationId": "thread_a", "kind": "build"}

    row = diagnostics.snapshot(rec, identity, terminal=True)
    assert diagnostics.Store(tmp_path).put(row)
    restored = diagnostics.Store(tmp_path).get(
        "turn_malformed", "app_a", "thread_a")
    saved = restored["timing"]["calls"][0]["requestComposition"]
    assert saved["messagesByRole"]["system"] == {"count": 0, "bytes": 0}
    assert saved["messagesByRole"]["user"] == {"count": 0, "bytes": 0}
    assert saved["categories"]["instructionsBytes"] == 0
    assert saved["categories"]["ordinaryTextBytes"] == 0
    assert saved["rewrites"]["redactedCalls"] == 0
    assert saved["rewrites"]["markerEchoCorrections"] == 0
    assert PRIVATE not in json.dumps(restored, ensure_ascii=False)


@pytest.mark.parametrize("private", ["PRIVATE_SENTINEL", "sk-live-secret123"])
def test_export_drops_untrusted_identifier_shaped_response_models(tmp_path, private):
    rec = timing.TurnRecord("build", 1.0, 1.0, turn_id="turn_untrusted")
    rec.calls = [timing.ModelCall(
        1, 1.0, model="GLM 5.3 OR", requested_alias="GLM 5.3 OR",
        response_reported_model=private, t1=2.0,
    )]
    rec.t1 = 2.0
    identity = {"turnId": "turn_untrusted", "appId": "app_a",
                "conversationId": "thread_a", "kind": "build"}
    row = diagnostics.snapshot(rec, identity, terminal=True)
    assert diagnostics.Store(tmp_path).put(row)
    restored = diagnostics.Store(tmp_path).get(
        "turn_untrusted", "app_a", "thread_a")
    call = restored["timing"]["calls"][0]
    assert call["model"] == call["requestedAlias"] == "GLM 5.3 OR"
    assert "responseReportedModel" not in call
    assert private not in json.dumps(restored)


def test_old_records_without_composition_remain_readable(tmp_path):
    rec = timing.TurnRecord("build", 1.0, 1.0, turn_id="turn_old")
    rec.calls = [timing.ModelCall(1, 1.0, model="old-model", t1=2.0)]
    rec.t1 = 2.0
    row = diagnostics.snapshot(rec, {"turnId": "turn_old", "appId": "app_a",
                               "conversationId": "thread_a", "kind": "build"}, terminal=True)
    assert "requestComposition" not in row["timing"]["calls"][0]
    assert diagnostics.Store(tmp_path).put(row)
    assert diagnostics.Store(tmp_path).get(
        "turn_old", "app_a", "thread_a")["timing"]["calls"][0]["model"] == "old-model"
