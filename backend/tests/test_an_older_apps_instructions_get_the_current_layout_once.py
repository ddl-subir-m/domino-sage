"""An app seeded before the screen-first layout gets the current layout and query rules, once (#699).

Apps keep their own `AGENTS.md` and are never re-seeded, so an app built before #697 still reads "put
the UI in the entry file and split it as it grows". The Build instruction profile — the one place a
request's instructions are assembled — adds a short, versioned supplement for those apps. It
supersedes only the screen-layout and query guidance; the app's own rules, branding and the rest of
its file stay as they are. A current template carries the version marker, so it gets nothing, and a
request that already carries the supplement does not get a second copy.

Checked on the request that leaves for the provider, on both stacks and both native protocols.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.gateway.protocol import Protocol
from sage.implementation_request import (
    GUIDANCE_MARKER,
    GUIDANCE_SUPPLEMENTS,
    IMPLEMENT_SECTIONS,
    InstructionFacts,
    apply_instruction_profile,
)
from sage.router.models import Mode
from sage.shim.native import prepare_native

from .test_native_policy import body as native_body
from .test_native_policy import shim as native_shim

REPO = Path(__file__).resolve().parents[2]
STACKS = ["fastapi-antd", "react-vite"]
HEADING = "## Current screen layout and query rules"
CUSTOM = "- CUSTOM_RULE_SENTINEL: invoices are always sorted by due date."
PATHS = {"fastapi-antd": "static/components/<Name>.js", "react-vite": "src/screens/<Name>.tsx"}


def _current(stack: str) -> str:
    return (REPO / "template" / stack / "AGENTS.md").read_text()


def _older(stack: str) -> str:
    """An app's file from before the marker, with a rule of the app's own in its implement block."""
    text = _current(stack)
    assert text.count(GUIDANCE_MARKER) == 1
    text = text.replace(GUIDANCE_MARKER + "\n", "")
    return text.replace("<!-- sage:build-profile:v1:implement:end -->",
                        CUSTOM + "\n<!-- sage:build-profile:v1:implement:end -->")


def _payload(stack: str, instructions: str, protocol, *, mode=Mode.IMPLEMENT, arm=None,
             requests: int = 1) -> list[str]:
    enforcement, control = native_shim(protocol, mode=mode, effort="high")
    enforcement.set_instruction_facts(InstructionFacts(stack=stack))
    if arm == "direct":
        control.arm_direct()
    elif arm == "plan":
        control.arm_read_only("plan")
    out = []
    for n in range(requests):
        original = native_body(protocol, opaque=False)
        if protocol is Protocol.MESSAGES:
            original["system"] = [{"type": "text", "text": instructions}]
        else:
            original["instructions"] = instructions
        result, *_ = prepare_native(enforcement, original, protocol, "p", f"ses_{n}",
                                    rewrite_counts={})
        out.append(json.dumps(result, ensure_ascii=False))
    return out


PROTOCOLS = pytest.mark.parametrize("protocol", [Protocol.MESSAGES, Protocol.RESPONSES])


@PROTOCOLS
@pytest.mark.parametrize("stack", STACKS)
def test_an_older_apps_request_carries_the_supplement_once(stack: str, protocol):
    (encoded,) = _payload(stack, _older(stack), protocol)
    assert encoded.count(HEADING) == 1
    assert encoded.count(GUIDANCE_MARKER) == 1
    assert PATHS[stack] in encoded


@PROTOCOLS
@pytest.mark.parametrize("stack", STACKS)
def test_the_apps_own_rules_and_the_rest_of_its_file_stay(stack: str, protocol):
    (encoded,) = _payload(stack, _older(stack), protocol)
    assert CUSTOM in encoded
    assert "Every implementation turn must end with edits" in encoded
    assert "Everything else in this file still applies" in encoded


@PROTOCOLS
@pytest.mark.parametrize("stack", STACKS)
def test_a_current_apps_request_carries_no_supplement(stack: str, protocol):
    (encoded,) = _payload(stack, _current(stack), protocol)
    assert HEADING not in encoded


@pytest.mark.parametrize("stack", STACKS)
def test_every_request_of_a_turn_carries_exactly_one(stack: str):
    for encoded in _payload(stack, _older(stack), Protocol.MESSAGES, requests=3):
        assert encoded.count(HEADING) == 1


@pytest.mark.parametrize("stack", STACKS)
def test_instructions_that_already_carry_it_get_no_second_copy(stack: str):
    """Pasted into the app's own file, or arriving with the request already — either way, once."""
    text = _older(stack).replace("<!-- sage:build-profile:v1:implement:end -->",
                                 GUIDANCE_SUPPLEMENTS[stack]
                                 + "<!-- sage:build-profile:v1:implement:end -->")
    (encoded,) = _payload(stack, text, Protocol.MESSAGES)
    assert encoded.count(HEADING) == 1


@pytest.mark.parametrize("stack", STACKS)
def test_profiling_a_request_twice_adds_it_once(stack: str):
    request = {"messages": [{"role": "system", "content": _older(stack)}]}
    once, _ = apply_instruction_profile(request, "implement", sections=IMPLEMENT_SECTIONS, stack=stack)
    twice, _ = apply_instruction_profile(once, "implement", sections=IMPLEMENT_SECTIONS, stack=stack)
    assert json.dumps(twice).count(HEADING) == 1


@pytest.mark.parametrize("stack", STACKS)
def test_a_direct_turn_gets_it_too(stack: str):
    (encoded,) = _payload(stack, _older(stack), Protocol.MESSAGES, arm="direct")
    assert encoded.count(HEADING) == 1


@pytest.mark.parametrize("stack", STACKS)
def test_a_plan_turn_gets_none(stack: str):
    """The plan profile keeps `common` alone, and the older layout lines live in `implement`, so
    there is nothing in a plan request for the supplement to supersede."""
    (encoded,) = _payload(stack, _older(stack), Protocol.MESSAGES, mode=Mode.AUTO, arm="plan")
    assert "Every implementation turn must end with edits" not in encoded   # the plan profile ran
    assert HEADING not in encoded


@pytest.mark.parametrize("stack", STACKS)
def test_without_a_known_stack_the_request_is_what_it_was(stack: str):
    """A shim whose turn never recorded its stack sends the older file as it always did."""
    request = {"messages": [{"role": "system", "content": _older(stack)}]}
    unknown, _ = apply_instruction_profile(request, "implement", sections=IMPLEMENT_SECTIONS)
    assert HEADING not in json.dumps(unknown)


@pytest.mark.parametrize("stack", STACKS)
def test_the_supplement_is_bounded(stack: str):
    assert len(GUIDANCE_SUPPLEMENTS[stack].encode()) <= 1800
    assert GUIDANCE_SUPPLEMENTS[stack].startswith(GUIDANCE_MARKER + "\n")
