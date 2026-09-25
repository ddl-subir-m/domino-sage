"""A plan turn is given exactly one description of the plan's format (#540).

Before the fix, the `sage-plan` agent prompt asked for a `## Plan` of single-line steps ("a bolded
2-4 word label, then ' — ', then one sentence. No paragraph-length steps, no sub-lists") while the
same call's turn prompt asked for `### N. Label` headings with Files / Do / Done when bullets.
`validate_execution_contract` reads only the second, so a plan written in the layout the AGENT
prompt taught parsed to `step_count=0` and the person saw "The plan is missing or has invalid at
least one numbered execution step … The clean planning retry was also invalid". The retry named no
layout either, so a planner told only "at least one numbered execution step" — about a plan that
looked numbered already — tended to write the same plan again.

These are presence tests over prompt text, in the spirit of `test_a_dashboard_ships_with_a_control`.
They prove the two surfaces still describe one format and that the retry names it. They do NOT
prove a live model now writes it; that is a live run.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from sage.orchestrator.plan_steps import validate_execution_contract
from sage.orchestrator.service import (
    _PLAN_EXAMPLES,
    _PLAN_SHAPE_PHASED,
    _PLAN_STEP_SHAPE,
    _execution_contract_error,
    _execution_contract_recovery_prompt,
    _plan_example_for,
)

ROOT = Path(__file__).resolve().parents[2]

# The two sections whose CONTENT judgement stays with the agent (ADR-0016's Control rule, read by
# test_a_dashboard_ships_with_a_control). The agent names them; it never says how they are laid out.
JUDGED_BY_THE_AGENT = {"## What it does", "## Screens"}

# The step layout's distinctive strings. Used in both directions below: every one of them belongs
# in the retry prompt, and none of them in the sentence the person reads.
# `- Verify —` rather than `- Done when —` since #543: the document's acceptance list is the
# '## Done when' heading, and the step field was renamed so a reader does not meet the same two
# words twice. `plan_steps._CANON` still reads both.
LAYOUT_TOKENS = ("'## Plan'", "'### N. Label'", "- Files —", "- Do —", "- Verify —",
                 "- Don't touch —")

# A plan in the layout the agent prompt used to teach: numbered, bolded label, one sentence each.
# Every product section the validator requires is present, so the only thing wrong with it is the
# shape of its steps.
A_PLAN_IN_THE_OLD_AGENT_LAYOUT = """# Sample Intake Log

Logs lab samples as they arrive and flags the late ones.

## Problem & outcome
Arrivals are tracked by email, so a late sample is found days later.

## Who uses this
The lab coordinator who checks arrivals each morning.

## What it does
- Lists samples from the attached arrivals file.

## Screens
- **Arrivals** — a table of samples with a late flag and a site filter.

## Done when
- The table shows every row of the arrivals file.

## Plan
1. **Arrivals data** — Read the arrivals file and return each sample.
2. **Arrivals table** — Replace the starter screen with a table of samples.

## Open questions
None — ready to build.
"""


def plan_prompt() -> str:
    return json.loads((ROOT / "opencode.json").read_text())["agent"]["sage-plan"]["prompt"]


def headings_the_turn_owns() -> set[str]:
    """Every Markdown heading `_PLAN_SHAPE_PHASED` quotes, derived rather than listed.

    Keyed on the derivation so a section added to the turn prompt tomorrow is covered without
    anyone remembering to add it here.
    """
    found = set(re.findall(r"'(#{2,4} [^']+)'", _PLAN_SHAPE_PHASED))
    assert len(found) >= 8, found  # the shape really did stop naming its headings in quotes
    return found


# ---- (a) the agent prompt describes no layout of its own ----------------------------------------


def test_the_agent_prompt_names_no_section_the_turn_prompt_lays_out():
    """One owner for the format. The agent may name the two sections it judges, nothing more."""
    prompt = plan_prompt()
    trespassing = sorted(h for h in headings_the_turn_owns() - JUDGED_BY_THE_AGENT if h in prompt)
    assert trespassing == []


def test_the_agent_prompt_teaches_no_step_layout_of_its_own():
    prompt = plan_prompt()
    assert "Each step is a SINGLE line" not in prompt
    assert "bolded 2-4 word label" not in prompt
    assert "No paragraph-length steps" not in prompt
    # It says instead that the turn owns the format, and that another shape is thrown away.
    assert "never substitute a layout of your own" in prompt


def test_the_agent_prompt_does_not_ask_for_none_under_open_questions():
    """The turn prompt says to leave the heading out; the agent used to say to write 'None'."""
    prompt = plan_prompt()
    assert "None — ready to build" not in prompt
    assert re.search(r"write 'None", prompt) is None


# ---- (b) the retry names the layout and shows a worked example -----------------------------------


def test_the_retry_for_a_plan_with_no_steps_names_the_layout_and_shows_a_working_example():
    check = validate_execution_contract(A_PLAN_IN_THE_OLD_AGENT_LAYOUT)
    assert (check.valid, check.step_count) == (False, 0)

    example = _PLAN_EXAMPLES["react-vite"]
    retry = _execution_contract_recovery_prompt(check, example)

    # The layout by identity with the turn's own constant, not a second wording of it.
    assert _PLAN_STEP_SHAPE in retry
    assert [t for t in LAYOUT_TOKENS if t in retry] == list(LAYOUT_TOKENS)
    # In plain words, so the planner learns that its numbered lines were not steps.
    assert "A numbered execution step is not a numbered line" in retry
    # And the example it carries is itself a plan this validator accepts.
    assert retry.endswith(example)
    assert validate_execution_contract(example[example.index("# "):]).valid


def test_the_retry_takes_its_example_from_the_stack_the_gate_would_have_used(tmp_path: Path):
    """Same lookup, same react-vite fallback. The Chat handoff plans before an app exists."""
    class _App:
        def __init__(self, path: Path) -> None:
            self.path = path

    class _Project:
        def __init__(self, path: Path) -> None:
            self._app = _App(path)

        def app_for_turn(self) -> _App:
            return self._app

    no_record = tmp_path / "unbuilt"
    no_record.mkdir()
    assert _plan_example_for(_Project(no_record)) == _PLAN_EXAMPLES["react-vite"]

    python_app = tmp_path / "built"
    (python_app / ".sage").mkdir(parents=True)
    (python_app / ".sage" / "settings.json").write_text(json.dumps({"stack": "fastapi-antd"}))
    assert _plan_example_for(_Project(python_app)) == _PLAN_EXAMPLES["fastapi-antd"]


# ---- (c) the person-facing sentence stays plain --------------------------------------------------


def test_the_person_facing_error_for_that_plan_carries_no_layout_jargon():
    """`_execution_contract_problems` feeds both surfaces; only the retry may spell the shape."""
    check = validate_execution_contract(A_PLAN_IN_THE_OLD_AGENT_LAYOUT)
    error = _execution_contract_error(check)

    assert [t for t in LAYOUT_TOKENS if t in error] == []
    assert "###" not in error
    assert error == "The plan is missing or has invalid at least one numbered execution step."


def test_a_plan_whose_steps_are_not_briefs_is_still_refused():
    """The door did not widen. A step with no `Done when` is still not a step."""
    half = A_PLAN_IN_THE_OLD_AGENT_LAYOUT.replace(
        "1. **Arrivals data** — Read the arrivals file and return each sample.\n"
        "2. **Arrivals table** — Replace the starter screen with a table of samples.",
        "### 1. Arrivals data\n"
        "- Files — src/arrivals.ts\n"
        "- Do — Read the arrivals file and return each sample.")
    check = validate_execution_contract(half)
    assert not check.valid
    assert check.step_count == 0
    assert check.malformed_steps == 1
