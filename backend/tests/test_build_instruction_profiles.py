"""Build stage instructions are selected once at the final provider boundary (#531)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage import build_diagnostics, request_composition
from sage.gateway.protocol import Protocol
from sage.implementation_request import (
    IMPLEMENT_SECTIONS,
    BuildInstructionProfileError,
    apply_instruction_profile,
)
from sage.router.models import Mode
from sage.shim.native import NativeView, prepare_native

from .test_native_policy import body as native_body
from .test_native_policy import shim as native_shim

ROOT = Path(__file__).parents[2]
TEMPLATES = (
    ROOT / "template/react-vite/AGENTS.md",
    ROOT / "template/fastapi-antd/AGENTS.md",
)
COMMON = "A reference selected for this turn is authoritative."
IMPLEMENT = "Every implementation turn must end with edits"
PRIVATE = "PRIVATE_PROFILE_SENTINEL"
PLATFORM_OWNED = "Never write example values for anything the platform owns"


def request_for(template: Path, *, effort: object = "high") -> dict:
    config = json.loads((ROOT / "opencode.json").read_text())
    return {
        "model": "model",
        "reasoning_effort": effort,
        "messages": [
            {"role": "system", "content": template.read_text()},
            {"role": "developer", "content": config["agent"]["sage-plan"]["prompt"]},
            {"role": "user", "content": PRIVATE},
        ],
    }


def has_system_text(request: dict, expected: str) -> bool:
    for message in request.get("messages", []):
        if message.get("role") != "system":
            continue
        content = message.get("content")
        if content == expected:
            return True
        if isinstance(content, list) and any(
                isinstance(part, dict) and part.get("text") == expected for part in content):
            return True
    return False


@pytest.mark.parametrize("template", TEMPLATES, ids=("react-vite", "fastapi-antd"))
def test_plan_profile_keeps_common_and_plan_contract_but_removes_implementation(template):
    before = request_for(template)
    after, report = apply_instruction_profile(before, "plan")
    encoded = json.dumps(after, ensure_ascii=False)

    assert COMMON in encoded
    assert "The ONE required output of this turn is the written plan" in encoded
    for removed in (
        IMPLEMENT, "planning alone is a failed turn", "Do not touch", "Don't run the typechecker",
        "Don't run a check or a server", "Read git history without printing an email address",
    ):
        assert removed not in encoded
    assert "sage:build-profile" not in encoded
    assert report["status"] == "valid"
    assert report["removedStageBlocksById"]["implement"]["count"] == 1
    assert report["instructionBytesAfter"] < report["instructionBytesBefore"]


@pytest.mark.parametrize("template", TEMPLATES, ids=("react-vite", "fastapi-antd"))
def test_the_plan_profile_carries_what_only_the_platform_knows(template):
    """The planner never saw the platform section (#556): on react-vite it sits inside `implement`,
    on fastapi-antd in the optional `platform` block, and the plan profile keeps `common` alone. So
    a request for governance tags was planned by a model that had never heard of the relay — with
    example values, and "Not doing: connecting to a live governance system or API", approved."""
    after, _ = apply_instruction_profile(request_for(template), "plan")
    encoded = json.dumps(after, ensure_ascii=False)

    assert PLATFORM_OWNED in encoded
    for word in ("governance", "lineage", "classification"):
        assert word in encoded, word
    assert IMPLEMENT not in encoded


@pytest.mark.parametrize("effort", [None, "low", "high", "max", "provider-private-value"])
def test_profile_keeps_the_reasoning_setting_byte_for_byte(effort):
    before = request_for(TEMPLATES[0], effort=effort)
    after, _ = apply_instruction_profile(before, "plan")
    assert "reasoning_effort" in after
    assert after["reasoning_effort"] == effort


@pytest.mark.parametrize("template", TEMPLATES, ids=("react-vite", "fastapi-antd"))
def test_implementation_profile_keeps_common_and_all_implementation_rules(template):
    # `sections` when a turn asked for every optional block. The shim chooses a subset per turn;
    # that choice is covered by `test_an_implement_turn_can_leave_a_section_behind.py`.
    after, report = apply_instruction_profile(
        request_for(template), "implement", sections=IMPLEMENT_SECTIONS)
    encoded = json.dumps(after, ensure_ascii=False)

    assert COMMON in encoded and IMPLEMENT in encoded
    assert "sage:build-profile" not in encoded
    assert report["removedStageBlocksById"] == {}
    assert report["status"] == "valid"


def marked(common="common", implementation="implementation") -> str:
    return (
        "<!-- sage:build-profile:v1:common:begin -->\n" + common + "\n"
        "<!-- sage:build-profile:v1:common:end -->\n"
        "<!-- sage:build-profile:v1:implement:begin -->\n" + implementation + "\n"
        "<!-- sage:build-profile:v1:implement:end -->\n"
    )


@pytest.mark.parametrize("bad", [
    marked().replace("<!-- sage:build-profile:v1:common:end -->\n", ""),
    marked().replace("common\n", "common\n<!-- sage:build-profile:v1:implement:begin -->\n", 1),
    marked() + marked(),
    marked().replace(
        "<!-- sage:build-profile:v1:common:end -->\n<!-- sage:build-profile:v1:implement:begin -->",
        "<!-- sage:build-profile:v1:implement:begin -->\n<!-- sage:build-profile:v1:common:end -->"),
    marked(common="   "),
    marked().replace(":implement:begin", ":review:begin"),
], ids=("unmatched", "nested", "duplicate", "out-of-order", "empty-common", "unknown-id"))
def test_invalid_profile_markers_fail_closed(bad):
    with pytest.raises(BuildInstructionProfileError):
        apply_instruction_profile({"messages": [{"role": "system", "content": bad}]}, "plan")


def test_legacy_unmarked_instructions_are_reported_and_left_byte_identical():
    before = {"messages": [{"role": "system", "content": "legacy instructions"}]}
    after, report = apply_instruction_profile(before, "plan")
    assert after == before
    assert report["status"] == "absent"


@pytest.mark.parametrize("protocol", [Protocol.MESSAGES, Protocol.RESPONSES])
def test_native_plan_profile_runs_before_provider_serialization(protocol):
    original = native_body(protocol, opaque=False)
    template = TEMPLATES[0].read_text()
    if protocol is Protocol.MESSAGES:
        original["system"] = [{"type": "text", "text": template}]
    else:
        original["instructions"] = template
    enforcement, control = native_shim(protocol, mode=Mode.AUTO, effort="high")
    control.arm_read_only("plan")
    rewrites = {}

    result, _, _, normalized, _ = prepare_native(
        enforcement, original, protocol, "p", "ses_plan", rewrite_counts=rewrites)
    encoded = json.dumps(result, ensure_ascii=False)

    assert COMMON in encoded and IMPLEMENT not in encoded
    assert "sage:build-profile" not in encoded
    assert normalized["reasoning_effort"] == "high"
    assert rewrites["buildInstructionProfile"]["status"] == "valid"


@pytest.mark.parametrize("protocol", [Protocol.MESSAGES, Protocol.RESPONSES])
def test_native_implementation_profile_keeps_full_rules_before_serialization(protocol):
    original = native_body(protocol, opaque=False)
    template = TEMPLATES[1].read_text()
    if protocol is Protocol.MESSAGES:
        original["system"] = [{"type": "text", "text": template}]
    else:
        original["instructions"] = template
    enforcement, _ = native_shim(protocol, mode=Mode.IMPLEMENT, effort="high")
    rewrites = {}

    result, _, _, normalized, _ = prepare_native(
        enforcement, original, protocol, "p", "ses_implement", rewrite_counts=rewrites)
    encoded = json.dumps(result, ensure_ascii=False)

    assert COMMON in encoded and IMPLEMENT in encoded
    assert "sage:build-profile" not in encoded
    assert "reasoning_effort" not in normalized
    assert rewrites["buildInstructionProfile"]["profile"] == "implement"
    assert rewrites["buildInstructionProfile"]["status"] == "valid"


@pytest.mark.parametrize("protocol", [Protocol.MESSAGES, Protocol.RESPONSES])
def test_native_ask_does_not_apply_a_build_instruction_profile(protocol):
    original = native_body(protocol, opaque=False)
    template = TEMPLATES[0].read_text()
    if protocol is Protocol.MESSAGES:
        original["system"] = [{"type": "text", "text": template}]
    else:
        original["instructions"] = template
    enforcement, _ = native_shim(protocol, mode=Mode.ASK)
    rewrites = {}

    result, *_ = prepare_native(
        enforcement, original, protocol, "p", "ses_ask", rewrite_counts=rewrites)
    restored = NativeView(result, protocol).request
    assert has_system_text(restored, template)
    assert "buildInstructionProfile" not in rewrites


@pytest.mark.parametrize("protocol", [Protocol.MESSAGES, Protocol.RESPONSES])
def test_native_chat_does_not_apply_a_build_instruction_profile(protocol):
    original = native_body(protocol, opaque=False)
    template = TEMPLATES[0].read_text()
    if protocol is Protocol.MESSAGES:
        original["system"] = [{"type": "text", "text": template}]
    else:
        original["instructions"] = template
    enforcement, control = native_shim(protocol, mode=Mode.AUTO)
    control.arm_chat("thread_1")
    rewrites = {}

    result, *_ = prepare_native(
        enforcement, original, protocol, "p", "ses_chat", rewrite_counts=rewrites)

    restored = NativeView(result, protocol).request
    assert has_system_text(restored, template)
    assert "buildInstructionProfile" not in rewrites


def test_profile_diagnostics_reconcile_and_export_only_fixed_identifiers():
    before = request_for(TEMPLATES[0])
    before["messages"][0]["content"] += PRIVATE
    after, profile = apply_instruction_profile(
        before, "plan", removed_tools={"todowrite": 1, "private-prefix_task": 1})
    total = request_composition.wire_bytes(after)
    measured = request_composition.measure(after, total, {"buildInstructionProfile": profile})
    exported = build_diagnostics._request_composition(measured)

    assert measured["status"] == "complete"
    assert sum(measured["categories"].values()) == total
    assert measured["buildInstructionProfile"]["removedStageBlocksById"].keys() == {"implement"}
    assert set(measured["buildInstructionProfile"]["removedToolSchemasByName"]) <= {
        "todowrite", "task", "unknown",
    }
    assert exported["buildInstructionProfile"] == measured["buildInstructionProfile"]
    assert PRIVATE not in json.dumps(measured, ensure_ascii=False)


def test_one_component_owns_the_final_check_and_repair_rule():
    config = json.loads((ROOT / "opencode.json").read_text())
    prompt = config["agent"]["sage-implement"]["prompt"]
    assert "Sage runs the stack's final check" in prompt
    assert "existing repair loop" in prompt
    for duplicate in ("npx tsc", "npm run build", "python -m py_compile", "node --check"):
        assert duplicate not in prompt
    assert "Read the changed lines after editing" in prompt


@pytest.mark.parametrize("template", TEMPLATES, ids=("react-vite", "fastapi-antd"))
def test_direct_keeps_common_and_implement_even_when_sections_name_the_rest(template):
    """Direct drops the essay and the platform table. Naming them in `sections` does not put them back."""
    after, report = apply_instruction_profile(
        request_for(template), "direct", sections=IMPLEMENT_SECTIONS)
    encoded = json.dumps(after, ensure_ascii=False)

    assert COMMON in encoded
    assert IMPLEMENT in encoded
    assert "Design system — build a polished product, not a prototype" not in encoded
    assert "sage:build-profile" not in encoded
    assert report["profile"] == "direct"
    assert set(report["removedStageBlocksById"]) == {"design", "platform"}


def test_an_unknown_build_profile_is_still_refused():
    with pytest.raises(BuildInstructionProfileError, match="Unknown Build instruction profile"):
        apply_instruction_profile(request_for(TEMPLATES[0]), "bare")


def test_a_direct_profile_report_survives_measurement():
    before = request_for(TEMPLATES[0])
    after, profile = apply_instruction_profile(
        before, "direct", sections=IMPLEMENT_SECTIONS)
    total = request_composition.wire_bytes(after)
    measured = request_composition.measure(after, total, {"buildInstructionProfile": profile})
    exported = build_diagnostics._request_composition(measured)

    assert measured["buildInstructionProfile"]["profile"] == "direct"
    assert exported["buildInstructionProfile"]["profile"] == "direct"


@pytest.mark.parametrize("protocol", [Protocol.MESSAGES, Protocol.RESPONSES])
def test_a_direct_implement_turn_sends_the_direct_profile(protocol):
    original = native_body(protocol, opaque=False)
    template = TEMPLATES[1].read_text()
    if protocol is Protocol.MESSAGES:
        original["system"] = [{"type": "text", "text": template}]
    else:
        original["instructions"] = template
    enforcement, control = native_shim(protocol, mode=Mode.IMPLEMENT, effort="high")
    control.arm_direct()
    rewrites = {}

    result, *_ = prepare_native(
        enforcement, original, protocol, "p", "ses_direct", rewrite_counts=rewrites)
    encoded = json.dumps(result, ensure_ascii=False)

    assert COMMON in encoded and IMPLEMENT in encoded
    assert "Design system — build a polished product, not a prototype" not in encoded
    assert rewrites["buildInstructionProfile"]["profile"] == "direct"
    assert set(rewrites["buildInstructionProfile"]["removedStageBlocksById"]) == {"design", "platform"}


@pytest.mark.parametrize("template", TEMPLATES, ids=("react-vite", "fastapi-antd"))
def test_the_platform_section_answers_the_request_instead_of_refusing_it(template):
    """Two refusals were read as the task. A person who asked for rejected, or for the studies
    they can access, got neither: the prompt forbade the tri-state and forbade "what the current
    user can access". The same section left `creationTime` unnamed as the header date, and left
    `{"data": null}` off the governance call that returns it.
    """
    text = " ".join(template.read_text().split())
    assert "approved/pending/rejected" not in text
    assert "what the current user can access" not in text
    assert (
        'An approval is `Approved` or not approved, including when the person said "rejected."'
        in text
    )
    assert "A study list is the publisher's list, and the screen says whose access that is" in text
    assert "this is the header date" in text
    assert "<cut date>" in text
    row = next(line for line in template.read_text().splitlines() if "attachment-overviews" in line)
    assert '{"data": null}' in row
    assert text.count('{"data": null}') == 1
    if template.name == "AGENTS.md" and "fastapi-antd" in str(template):
        assert 'body.get("data") or []' in row
    else:
        assert "body.data ?? []" in row
