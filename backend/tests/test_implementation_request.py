"""The implementation request is canonical, smaller, and retains every Build capability (#523)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage import build_diagnostics, request_composition
from sage.gateway.protocol import Protocol
from sage.implementation_request import assemble, assemble_for_route
from sage.router.models import Mode
from sage.shim.native import prepare_native

from .test_native_policy import body as native_body
from .test_native_policy import shim as native_shim

FIXTURE = Path(__file__).parent / "fixtures" / "sage_implement_fresh_request.json"
PRIVATE = ("CANONICAL_BUILD_INTENT_PRIVATE_SENTINEL", "TYPED_REFERENCE_PRIVATE_SENTINEL",
           "PRIVATE_SELECTOR", "PRIVATE_PATH", "PRIVATE_FILENAME", "PRIVATE_TOOL_ARGUMENT")


def captured_request() -> dict:
    return json.loads(FIXTURE.read_text())


def names(request: dict) -> list[str]:
    return [tool["function"]["name"] for tool in request["tools"]]


def instruction_text(request: dict) -> list[str]:
    return [message["content"] for message in request["messages"]
            if message["role"] in {"system", "developer"}]


def test_fresh_implementation_capture_is_smaller_and_has_no_duplicate_blocks_or_schemas():
    before = captured_request()
    after, report = assemble(before)

    assert request_composition.wire_bytes(before) == report["beforeBytes"]
    assert request_composition.wire_bytes(after) == report["afterBytes"]
    assert (report["beforeBytes"], report["afterBytes"], report["removedBytes"]) == (
        2040, 1606, 434)
    assert report["afterBytes"] < report["beforeBytes"]
    assert report["removedBytes"] == report["beforeBytes"] - report["afterBytes"]
    assert report["duplicateInstructionBlocksRemoved"] == 1
    assert report["duplicateInstructionBlocksRemovedBySource"] == {"messageSystem": 1}
    assert report["duplicateToolSchemasRemoved"] == 1
    assert report["unreachableToolSchemasRemoved"] == 2
    assert report["removedToolSchemasByName"] == {
        "read": 1, "artifact_write": 1, "delegated_model_call": 1,
    }
    assert len(instruction_text(after)) == len(set(instruction_text(after)))
    assert instruction_text(after) == [
        "OpenCode implementation instructions",
        "Sage implementation safety instructions",
        "Typed references must use the approved data-use path.",
    ]
    assert len(names(after)) == len(set(names(after)))
    assert before == captured_request(), "assembly does not mutate OpenCode's request"
    measured_before = request_composition.measure(before, report["beforeBytes"])
    measured_after = request_composition.measure(after, report["afterBytes"])
    for measured, total in ((measured_before, report["beforeBytes"]),
                            (measured_after, report["afterBytes"])):
        assert sum(measured["categories"].values()) == total
        assert sum(measured["buildBytes"].values()) == total
    assert measured_after["buildBytes"]["fixedBytes"] < measured_before["buildBytes"]["fixedBytes"]


@pytest.mark.parametrize(("capability", "required"), [
    ("list/search/read workspace files", {"glob", "grep", "read"}),
    ("write/edit/apply changes", {"write", "edit", "apply_patch"}),
    ("run bounded shell commands", {"bash"}),
    ("task and progress", {"task", "todowrite"}),
    ("read typed references", {"sage-live-read_live_read_table"}),
])
def test_retained_implementation_capability_has_its_required_schemas(capability, required):
    after, _ = assemble(captured_request())
    assert required <= set(names(after)), capability


@pytest.mark.parametrize(("capability", "required"), [
    ("list/search/read workspace files", {"glob", "grep", "read"}),
    ("write/edit/apply changes", {"write", "edit", "apply_patch"}),
    ("run bounded shell commands", {"bash"}),
    ("task and progress", {"task", "todowrite"}),
    ("read typed references", {"sage-live-read_live_read_table"}),
])
def test_removing_a_required_schema_breaks_its_capability_check(capability, required):
    after, _ = assemble(captured_request())
    removed = next(iter(required))
    after["tools"] = [tool for tool in after["tools"]
                      if tool["function"]["name"] != removed]
    assert not required <= set(names(after)), capability


def test_build_intent_typed_reference_instruction_and_completion_error_protocol_survive():
    before = captured_request()
    after, _ = assemble(before)
    assert after["messages"][-2:] == before["messages"][-2:]
    assert after["model"] == before["model"]
    assert after["stream"] is True


@pytest.mark.parametrize(("mode", "phase", "thread"), [
    ("plan", "plan", None),
    ("ask", "implement", None),
    ("implement", "implement", "thread_chat"),
    ("data-only", "implement", None),
])
def test_non_implementation_routes_are_byte_identical(mode, phase, thread):
    before = captured_request()
    original = request_composition.wire_bytes(before)
    after, report = assemble_for_route(before, mode=mode, phase=phase, chat_thread_id=thread)
    assert after is before
    assert request_composition.wire_bytes(after) == original
    assert report == {}


@pytest.mark.parametrize(("mode", "phase"), [
    ("implement", "implement"), ("auto", "implement"),
])
def test_only_implementation_routes_use_the_canonical_assembler(mode, phase):
    before = captured_request()
    after, report = assemble_for_route(before, mode=mode, phase=phase, chat_thread_id=None)
    assert after is not before
    assert report["removedBytes"] > 0


@pytest.mark.parametrize("protocol", [Protocol.MESSAGES, Protocol.RESPONSES])
def test_native_implementation_route_assembles_before_protocol_serialization(protocol):
    original = native_body(protocol, opaque=False)
    duplicate_schema = dict(original["tools"][0])
    original["tools"].append(duplicate_schema)
    if protocol is Protocol.MESSAGES:
        original["system"].append(dict(original["system"][0]))
    else:
        original["input"].insert(0, {"role": "system", "content": "instruction"})
    enforcement, _ = native_shim(protocol, mode=Mode.IMPLEMENT)
    rewrites = {}

    result, *_ = prepare_native(
        enforcement, original, protocol, "p", "ses_implement",
        rewrite_counts=rewrites)

    tool_names = [tool["name"] for tool in result["tools"]]
    assert tool_names == ["read"]
    assert rewrites["implementationAssembly"]["duplicateToolSchemasRemoved"] == 1
    assert rewrites["implementationAssembly"]["duplicateInstructionBlocksRemoved"] == 1
    assert duplicate_schema == original["tools"][0]


def test_diagnostics_reconcile_and_export_only_content_free_attribution():
    before = captured_request()
    before["messages"].extend([
        {"role": "assistant", "tool_calls": [{"type": "function", "function": {
            "name": "read", "arguments": json.dumps({"path": PRIVATE[-1]})}}]},
        {"role": "tool", "content": "PRIVATE_TOOL_RESULT"},
        {"role": "assistant", "content": "PRIVATE_MODEL_OUTPUT"},
    ])
    before["referenceMetadata"] = {
        "selector": PRIVATE[2], "path": PRIVATE[3], "filename": PRIVATE[4],
    }
    after, assembly = assemble(before)
    total = request_composition.wire_bytes(after)
    measured = request_composition.measure(
        after, total, {"implementationAssembly": assembly})

    assert measured["status"] == "complete"
    assert sum(measured["categories"].values()) == total
    assert sum(measured["buildBytes"].values()) == total
    assert measured["exactDuplicateInstructionBlocks"] == {"count": 0, "bytes": 0}
    assert all(row["count"] == 1 for row in measured["toolSchemasByName"].values())
    assert sum(row["count"] for row in measured["toolSchemasByName"].values()) == (
        measured["toolSchemaCount"])
    assert measured["systemInstructionsBySource"]["messageSystem"]["count"] == 2
    assert measured["systemInstructionsBySource"]["messageDeveloper"]["count"] == 1
    assert sum(row["bytes"] for row in measured["systemInstructionsBySource"].values()) == (
        measured["categories"]["instructionsBytes"])
    exported = build_diagnostics._request_composition(measured)
    assert exported is not None
    assert exported["buildBytes"] == measured["buildBytes"]
    assert exported["toolSchemasByName"] == measured["toolSchemasByName"]
    assert exported["implementationAssembly"][
        "duplicateInstructionBlocksRemovedBySource"] == {
            "topLevelInstructions": 0, "topLevelSystem": 0,
            "messageSystem": 1, "messageDeveloper": 0,
        }
    encoded = json.dumps(measured, ensure_ascii=False)
    for sentinel in (*PRIVATE, "PRIVATE_TOOL_RESULT", "PRIVATE_MODEL_OUTPUT"):
        assert sentinel not in encoded


def test_baseline_capture_attributes_duplicates_before_assembly():
    before = captured_request()
    measured = request_composition.measure(before, request_composition.wire_bytes(before))
    assert measured["exactDuplicateInstructionBlocks"] == {"count": 1, "bytes": 38}
    assert measured["toolSchemasByName"]["read"]["count"] == 2


def test_removing_owned_instruction_breaks_its_capability_check():
    after, _ = assemble(captured_request())
    after["messages"] = [message for message in after["messages"]
                         if message.get("role") != "developer"]
    assert "Typed references must use the approved data-use path." not in instruction_text(after)
