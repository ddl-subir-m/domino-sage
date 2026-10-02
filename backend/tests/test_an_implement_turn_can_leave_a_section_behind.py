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


def test_both_templates_carry_the_platform_api_as_an_optional_section():
    """The React API block moved into the same optional section FastAPI already had.

    Withholding it drops the API table. The build rule under it — there is no UI kit — stays in
    the required implement section, so a chart turn still hears that.
    """
    fastapi = (REPO / "template" / "fastapi-antd" / "AGENTS.md").read_text()
    react = (REPO / "template" / "react-vite" / "AGENTS.md").read_text()

    assert "v1:platform:begin" in fastapi
    assert "v1:platform:begin" in react
    assert "### The {platformName} API" in react
    withheld, report = _run(react, "implement")
    assert "### The {platformName} API" not in withheld
    assert "There is no UI component kit" in withheld
    assert "platform" in report["removedStageBlocksById"]


def test_the_shim_chooses_platform_and_design_for_the_turn():
    """Include the platform API unless the turn is positively only about rows already here.

    A chart of an attached file drops it. Naming the platform, asking what jobs ran, or asking
    who owns something keeps it, including when a file happens to be attached. A Dataset cited
    as itself keeps it; a file inside that Dataset does not. React always keeps the design
    system. A FastAPI route-only turn drops design; an unclear FastAPI turn keeps it.
    """
    from sage.implementation_request import InstructionFacts, choose_instruction_sections

    def chosen(**kwargs) -> frozenset[str]:
        return choose_instruction_sections(InstructionFacts(**kwargs))

    assert choose_instruction_sections() == frozenset({"design", "platform"})
    attached = {"platform_name": "Domino", "local_names": ("adae.csv",)}
    assert "platform" not in chosen(
        stack="react-vite", ask_and_plan="Chart this attached file", **attached)
    assert "design" in chosen(
        stack="react-vite", ask_and_plan="Chart this attached file", **attached)
    assert "platform" in chosen(
        stack="react-vite", ask_and_plan="Show the Domino datasets", **attached)
    assert "platform" in chosen(
        stack="react-vite", ask_and_plan="what jobs ran", **attached)
    assert "platform" in chosen(
        stack="react-vite", ask_and_plan="who owns this", **attached)
    assert "platform" in chosen(
        stack="react-vite",
        ask_and_plan="Chart this attached file @ABC123_ADAE",
        dataset_names=("ABC123_ADAE",), **attached)
    assert "platform" not in chosen(
        stack="react-vite",
        ask_and_plan="Chart this attached file @ABC123_ADAE/adae.csv",
        dataset_names=("ABC123_ADAE",), **attached)
    assert "design" in chosen(stack="react-vite", ask_and_plan="Add a route in app.py")
    assert chosen(
        stack="fastapi-antd", ask_and_plan="Add a route in app.py",
        platform_name="Domino") == frozenset({"platform"})
    assert "design" in chosen(stack="fastapi-antd", ask_and_plan="make it better")
    assert "design" in chosen(
        stack="fastapi-antd", ask_and_plan="Add a route in app.py and a chart on the page")


@pytest.mark.parametrize("section", ["design", "platform"])
def test_a_replaced_section_is_dropped_whatever_the_turn_asked(section):
    """ADR-0071: while a Project skill replacing a section is on, the chooser leaves it out."""
    from sage.implementation_request import InstructionFacts, choose_instruction_sections

    other = ({"design", "platform"} - {section}).pop()
    assert choose_instruction_sections(InstructionFacts(replaced=frozenset({section}))) == \
        frozenset({other})
    assert choose_instruction_sections(InstructionFacts(replaced=frozenset())) == \
        frozenset({"design", "platform"})


GUARDRAIL = ("hold the app together", "Never hardcode hex values",
             "quietly falls back to a system font")
STYLE = ("One clear primary action", "## Design system")


@pytest.mark.parametrize("stack", TEMPLATES)
def test_replacing_design_keeps_the_theme_and_font_guardrail(stack):
    """The theme variables and the Inter `@font-face` sit outside the replaceable style part."""
    text = (REPO / "template" / stack / "AGENTS.md").read_text()

    withheld, _ = _run(text, "implement", sections=frozenset({"platform"}))

    for line in GUARDRAIL:
        assert line in withheld, line
    for line in STYLE:
        assert line not in withheld, line


def test_api_text_buried_in_an_old_implement_section_still_goes_out():
    """An app seeded before the move has the API table inside implement, and it is never re-seeded."""
    text = V1.replace(
        "<!-- sage:build-profile:v1:implement:begin -->\nI\n",
        "<!-- sage:build-profile:v1:implement:begin -->\n"
        "I\n### The {platformName} API\nGET /api/domino\n",
    )

    kept, report = _run(text, "implement")

    assert "### The {platformName} API" in kept
    assert report["status"] == "valid"
    assert report["removedStageBlocksById"] == {}


@pytest.mark.parametrize("stack", TEMPLATES)
def test_the_shipped_template_still_holds_every_section_it_held_before(stack):
    """The sections moved out of the `implement` block; none of them left the file."""
    text = (REPO / "template" / stack / "AGENTS.md").read_text()
    carried, _ = _run(text, "implement", sections=IMPLEMENT_SECTIONS)

    for heading in [line for line in text.split("\n") if line.startswith("## ")]:
        assert heading in carried, heading


@pytest.mark.parametrize("stack", TEMPLATES)
def test_asking_for_every_section_removes_nothing(stack):
    """`IMPLEMENT_SECTIONS` is still "carry all of them". The shim no longer passes it blindly."""
    text = (REPO / "template" / stack / "AGENTS.md").read_text()

    from sage.implementation_request import _OPTIONAL_BLOCKS

    assert IMPLEMENT_SECTIONS == frozenset(_OPTIONAL_BLOCKS)
    _, report = _run(text, "implement", sections=IMPLEMENT_SECTIONS)
    assert report["removedStageBlocksById"] == {}
    assert json.dumps(report)  # the report stays content-free and serialisable
