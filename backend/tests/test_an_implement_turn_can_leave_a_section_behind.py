"""Optional Build instruction sections, and the grammar that has to keep old apps working (#548).

MEASURED 2026-09-24, real OpenCode 1.18.4 driven against a fake model: a `sage-implement` turn on
the `fastapi-antd` template carries 37,936 raw bytes of instructions, of which the `AGENTS.md`
`implement` block is 28,623. Two of its sections — the design system (7,529) and the platform API
(7,215) — are not needed by every turn, so the profile grammar can now withhold them.

The load-bearing constraint is NOT the saving. It is that **an app keeps its own `AGENTS.md` and is
never re-seeded**, so the v1 four-marker shape has to stay valid forever. A grammar that rejects it
breaks every app already built. `test_the_v1_four_marker_shape_still_validates` is that guard; the
rest of this file exists so the guard cannot be widened by accident.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.implementation_request import (
    IMPLEMENT_SECTIONS,
    BuildInstructionProfileError,
    apply_instruction_profile,
)

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = ("fastapi-antd", "react-vite")

V1 = ("<!-- sage:build-profile:v1:common:begin -->\nC\n"
      "<!-- sage:build-profile:v1:common:end -->\n"
      "<!-- sage:build-profile:v1:implement:begin -->\nI\n"
      "<!-- sage:build-profile:v1:implement:end -->\n")


def _request(text: str) -> dict:
    return {"messages": [{"role": "system", "content": text}]}


def _run(text: str, profile: str, sections=frozenset()) -> tuple[str, dict]:
    result, report = apply_instruction_profile(
        _request(text), profile, sections=sections)
    return result["messages"][0]["content"], report


def _block(name: str, body: str) -> str:
    return (f"<!-- sage:build-profile:v1:{name}:begin -->\n{body}\n"
            f"<!-- sage:build-profile:v1:{name}:end -->\n")


# --- the guard that protects every app already built ---------------------------------------------

def test_the_v1_four_marker_shape_still_validates():
    """An app built before this change has exactly these four markers and will never be re-seeded.

    Plant: add "design" to `_REQUIRED_BLOCKS` and this reds — which is what shipping a grammar
    that demands the new ids would do to every existing app.
    """
    kept, report = _run(V1, "implement")

    assert report["status"] == "valid"
    assert kept == "C\nI\n"
    assert report["removedStageBlocksById"] == {}


def test_the_v1_shape_still_drops_only_implement_on_a_plan_turn():
    kept, report = _run(V1, "plan")

    assert kept == "C\n"
    assert set(report["removedStageBlocksById"]) == {"implement"}


# --- an optional section is withheld, or carried, on request -------------------------------------

@pytest.mark.parametrize("section", ["design", "platform"])
def test_an_optional_section_is_absent_when_the_turn_did_not_ask_for_it(section):
    text = V1.rstrip("\n") + "\n" + _block(section, "SECTION-BODY")

    kept, report = _run(text, "implement")

    assert "SECTION-BODY" not in kept
    assert set(report["removedStageBlocksById"]) == {section}
    assert report["removedStageBlocksById"][section]["bytes"] > 0


@pytest.mark.parametrize("section", ["design", "platform"])
def test_an_optional_section_is_present_when_the_turn_asked_for_it(section):
    text = V1.rstrip("\n") + "\n" + _block(section, "SECTION-BODY")

    kept, report = _run(text, "implement", sections=frozenset({section}))

    assert "SECTION-BODY" in kept
    assert report["removedStageBlocksById"] == {}


def test_asking_for_one_section_does_not_carry_the_other():
    """Two optional ids, one request: a set that leaks would make the gate decorative."""
    text = V1.rstrip("\n") + "\n" + _block("design", "DESIGN-BODY") + _block("platform", "PLAT-BODY")

    kept, _ = _run(text, "implement", sections=frozenset({"design"}))

    assert "DESIGN-BODY" in kept and "PLAT-BODY" not in kept


def test_a_plan_turn_drops_every_optional_section_whatever_was_asked_for():
    """A plan turn writes no code, so it needs neither section even if the caller names them."""
    text = V1.rstrip("\n") + "\n" + _block("design", "DESIGN-BODY") + _block("platform", "PLAT-BODY")

    kept, report = _run(text, "plan", sections=IMPLEMENT_SECTIONS)

    assert "DESIGN-BODY" not in kept and "PLAT-BODY" not in kept
    assert set(report["removedStageBlocksById"]) == {"implement", "design", "platform"}


# --- the validator stays strict ------------------------------------------------------------------

@pytest.mark.parametrize("text,why", [
    (V1 + _block("unknown", "X"), "an id outside the closed set"),
    (V1.replace("v1:implement:end", "v2:implement:end"), "a version that does not match"),
    (V1 + _block("design", "A") + _block("design", "B"), "the same optional id twice"),
    (_block("design", "A") + V1, "an optional block before the required ones"),
    (_block("common", "C"), "a template with no implement block"),
    (_block("implement", "I"), "a template with no common block"),
    ("<!-- sage:build-profile:v1:common:begin -->\nC\n", "an unpaired edge"),
    (V1 + "<!-- sage:build-profile:v1:design:begin -->\n", "a begin with no end"),
])
def test_the_validator_still_rejects(text, why):
    """Widening the grammar must not make it permissive: each of these still raises."""
    with pytest.raises(BuildInstructionProfileError):
        _run(text, "implement", sections=IMPLEMENT_SECTIONS)


def test_a_nested_optional_block_is_rejected():
    """Nesting would make the flat pair-walk silently mis-attribute a body."""
    text = ("<!-- sage:build-profile:v1:common:begin -->\nC\n"
            "<!-- sage:build-profile:v1:common:end -->\n"
            "<!-- sage:build-profile:v1:implement:begin -->\nI\n"
            "<!-- sage:build-profile:v1:design:begin -->\nD\n"
            "<!-- sage:build-profile:v1:design:end -->\n"
            "<!-- sage:build-profile:v1:implement:end -->\n")

    with pytest.raises(BuildInstructionProfileError):
        _run(text, "implement", sections=IMPLEMENT_SECTIONS)


# --- the shipped templates -----------------------------------------------------------------------

@pytest.mark.parametrize("stack", TEMPLATES)
def test_the_template_carries_the_design_section_as_an_optional_block(stack):
    text = (REPO / "template" / stack / "AGENTS.md").read_text()

    withheld, report = _run(text, "implement")
    carried, _ = _run(text, "implement", sections=IMPLEMENT_SECTIONS)

    assert "## Design system" in carried
    assert "## Design system" not in withheld
    assert report["removedStageBlocksById"]["design"]["bytes"] > 5000


def test_only_the_fastapi_template_has_a_platform_section():
    """The two stacks are not symmetric here, and a test that assumed they were would pin a
    section into `react-vite` that its stack has no server for."""
    fastapi = (REPO / "template" / "fastapi-antd" / "AGENTS.md").read_text()
    react = (REPO / "template" / "react-vite" / "AGENTS.md").read_text()

    assert "v1:platform:begin" in fastapi
    assert "v1:platform:begin" not in react
    assert _run(react, "implement")[1]["removedStageBlocksById"].keys() == {"design"}


@pytest.mark.parametrize("stack", TEMPLATES)
def test_the_shipped_template_still_holds_every_section_it_held_before(stack):
    """The sections moved out of the `implement` block; none of them left the file."""
    text = (REPO / "template" / stack / "AGENTS.md").read_text()
    carried, _ = _run(text, "implement", sections=IMPLEMENT_SECTIONS)

    for heading in [line for line in text.split("\n") if line.startswith("## ")]:
        assert heading in carried, heading


@pytest.mark.parametrize("stack", TEMPLATES)
def test_the_shim_carries_every_section_until_a_trigger_is_chosen(stack):
    """Until something decides WHICH turn needs a section, no turn may quietly lose one."""
    text = (REPO / "template" / stack / "AGENTS.md").read_text()

    from sage.implementation_request import _OPTIONAL_BLOCKS

    assert IMPLEMENT_SECTIONS == frozenset(_OPTIONAL_BLOCKS)
    _, report = _run(text, "implement", sections=IMPLEMENT_SECTIONS)
    assert report["removedStageBlocksById"] == {}
    assert json.dumps(report)  # the report stays content-free and serialisable
