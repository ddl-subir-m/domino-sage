"""The Control rule is still in the instruction surfaces that carry it (ADR-0016).

These are presence tests and nothing more. They prove the sentences a later refactor could quietly
delete are still where they were put; they do NOT prove a build now ships a Control. ADR-0016 is
explicit that verification is a fixed prompt set scored against the three-clause bar, and that set
is `docs/live-runs/2026-08-31-controls.md`, which is recorded as unrun.

Two of the three surfaces are here — the `sage-plan` prompt, which carries the judgement, and
`template/react-vite/AGENTS.md`, which carries the no-store mechanics. The third, the store-backed
mechanics in `bound_schema.py`, is asserted in `test_bound_schema.py` beside the rest of that block.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "template" / "react-vite" / "AGENTS.md"


def plan_prompt() -> str:
    return json.loads((ROOT / "opencode.json").read_text())["agent"]["sage-plan"]["prompt"]


# ---- the plan prompt: whether this app wants a Control, and over which column --------------------


def test_the_plan_names_the_control_where_the_capability_is_named():
    # ADR-0016 puts the judgement in the reversible half. It has to land in `## What it does`,
    # because that is the section the creator reads to decide, and a line there can be deleted
    # before any code exists.
    prompt = plan_prompt()
    what, _, screens = prompt.partition("## Screens")
    assert "## What it does" in what
    assert "one bullet names a **Control**" in what
    assert "a select, a date range, a search box or a toggle" in what
    assert "at least two things on the screen move with it" in what
    assert screens, "the plan prompt no longer has a Screens section"


def test_the_plan_fires_the_rule_on_the_data_shape_rather_than_the_word_dashboard():
    prompt = plan_prompt()
    assert "When the app shows a collection — two or more rows —" in prompt
    assert "holds only a handful of values (a category, a status, a date)" in prompt
    # The word the rule deliberately does NOT key on.
    assert "dashboard" not in prompt


def test_the_plan_names_all_three_exemptions_so_the_agent_cannot_infer_them():
    # Keeping a Control away is the half that regresses, so each exemption is written out rather
    # than left to be derived from "when it makes sense".
    prompt = plan_prompt()
    assert "Three kinds of app get no Control, and you never propose one for them" in prompt
    assert "an app that shows a single number" in prompt
    assert "data with no category, status or date column" in prompt
    assert "a screen that is not a collection — a form, a calculator, a chat screen" in prompt


def test_the_plan_carries_the_third_clause_of_the_bar_into_the_screens_section():
    _, _, screens = plan_prompt().partition("## Screens")
    assert "Where a screen has a Control, that sentence names the Control" in screens
    assert "writes the current selection out in words" in screens
    assert "March 2026 · EMEA · 412 rows" in screens


def test_the_plan_prompt_still_writes_no_code():
    # The Control text is prose for a non-technical reader, not mechanics. If it ever grows a
    # fenced block, it has crossed into the half AGENTS.md owns.
    prompt = plan_prompt()
    assert "```" not in prompt
    assert "useState" not in prompt


# ---- the template's AGENTS.md: the mechanics, for an app with no store ---------------------------


def test_the_controls_section_sits_next_to_charts():
    # ADR-0016 puts it there on purpose: an agent reading the chart rules is the one about to
    # decide whether the chart is clickable.
    body = AGENTS.read_text()
    assert "### Controls" in body
    assert body.index("### Charts") < body.index("### Controls") < body.index("### States")


def test_the_template_defines_the_control_and_the_shape_that_gets_one():
    body = AGENTS.read_text()
    assert ("A **Control** is an element that changes what the app shows without a rebuild: a "
            "select, a date") in body
    assert "a collection over two or more rows, where one column" in body
    assert "a category, a status, a date" in body


def test_the_template_says_the_filter_library_is_already_installed():
    # ADR-0016 adds no dependency. `<select>` is the filter library, and saying so is what stops a
    # build reaching for one it cannot have.
    body = AGENTS.read_text()
    assert "**No package is needed for this.**" in body
    assert '`<select>`, `<input type="date">` and `<input type="search">`' in body
    assert "Hold the selection in `useState`, derive the filtered rows with `useMemo`" in body


def test_the_template_carries_clauses_two_and_three_of_the_bar():
    body = AGENTS.read_text()
    assert "**At least two views respond to it.**" in body
    assert "A Control that moves one chart is a chart option" in body
    assert "**State the current selection in words**" in body
    assert "March 2026 · EMEA · 412 rows" in body


def test_the_template_says_a_chart_click_writes_the_control():
    # The whole cross-filtering decision in one rule, plus its other half: a chart over a column
    # with no Control is not clickable, because a selection nobody can see or undo is worse.
    body = AGENTS.read_text()
    assert "**A chart click writes the Control; it never filters beside it.**" in body
    assert "the select visibly moves" in body
    assert "A chart over a column that has no Control is **not clickable**" in body


def test_the_template_hands_a_store_backed_app_over_to_the_managed_region():
    # The no-store path and the store path are not additive: one replaces the other. Said here
    # because this file is seeded once and cannot be revised.
    body = AGENTS.read_text()
    assert ('If this app reads a store, "The app\'s data" says how a Control filters there instead'
            in body)
    assert "replaces the `useMemo` above rather than adding to it" in body
