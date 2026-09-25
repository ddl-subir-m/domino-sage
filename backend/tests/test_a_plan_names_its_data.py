"""A plan says which data the app reads, and a step's check is called `Verify` (#543).

Two halves of one complaint from someone reading a plan document. It named no dataset, data
source or column, so the one question a data person asks — *what does it read?* — could only be
answered by reading the code. And it said "Done when" twice: once as the document's acceptance
list, and again inside every build step, where the words mean a different thing.

Neither half is a parser change. `plan_doc` grows one optional section, `plan_steps._CANON` has
always read `verify` as `done_when`, and nothing re-seeds an existing app or an existing plan —
so both halves are checked here against a plan saved before either of them existed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sage.orchestrator import brand
from sage.orchestrator.plan_steps import (
    _REQUIRED_SECTIONS,
    parse_steps,
    validate_execution_contract,
)
from sage.orchestrator.service import (
    _PLAN_DOC_SECTIONS,
    _PLAN_EXAMPLES,
    _PLAN_SHAPE_PHASED,
    _PLAN_STEP_SHAPE,
    _execution_contract_problems,
)
from sage.workspace import plan_doc

PLAN_JS = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"
           / "components" / "plan.js")

# A plan document exactly as it was saved before this issue: no `## Data`, and steps whose check
# is spelled `Done when`. Nothing re-seeds one of these, so this is the shape most plans on disk
# have and the shape both halves below must leave alone.
A_PLAN_SAVED_BEFORE_ALL_THIS = """# Sample Intake Log

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
### 1. Arrivals data
- Files — src/arrivals.ts
- Do — Read the arrivals file and return each sample.
- Done when — Each sample comes back with its due date and a late flag.

### 2. Arrivals table
- Files — src/App.tsx
- Do — Replace the starter screen with a table of samples.
- Done when — The preview shows the table.
- Don't touch — src/arrivals.ts
"""

WITH_DATA = A_PLAN_SAVED_BEFORE_ALL_THIS.replace(
    "## Done when\n",
    "## Data\n"
    "- `arrivals.csv`, one row per sample: `sample_id` labels the tube, `due_date` is when it "
    "was expected, `received_at` is when it came.\n"
    "- The Snowflake warehouse's `LAB.SAMPLES` table, for the site each sample came from.\n"
    "\n"
    "## Done when\n",
)


# ---- (a) the document names the data ------------------------------------------------------------


def test_a_data_section_is_parsed_out_of_the_document_and_survives_a_round_trip():
    parsed = plan_doc.parse_sections(WITH_DATA)

    assert parsed["sections"]["data"] == [
        ("`arrivals.csv`, one row per sample: `sample_id` labels the tube, `due_date` is when it "
         "was expected, `received_at` is when it came."),
        "The Snowflake warehouse's `LAB.SAMPLES` table, for the site each sample came from.",
    ]
    # Not swept into `plan` as an unrecognised heading, which is where it landed before.
    assert "arrivals.csv" not in parsed["sections"]["plan"]

    # The round trip is the document's whole contract: an edit re-renders every section, so a
    # section that parsed but did not render would delete itself the first time anyone saved.
    rendered = plan_doc.render(parsed["summary"], parsed["sections"], parsed["title"])
    assert "## Data" in rendered
    assert plan_doc.parse_sections(rendered)["sections"] == parsed["sections"]


@pytest.mark.parametrize("label", ["Data", "Datasets", "Data sources", "DATA"])
def test_the_ways_a_planner_spells_the_heading_all_land_in_the_same_section(label: str):
    """`_SYNONYMS` exists because models drift off the heading they are given."""
    parsed = plan_doc.parse_sections(f"An app.\n\n## {label}\n- One CSV.\n")
    assert parsed["sections"]["data"] == ["One CSV."]


def test_the_page_draws_the_same_sections_the_document_parses():
    """`plan_doc.SECTIONS` and `plan.js` SECTIONS are one list written twice, and a comment in
    each is all that has ever held them together. The page renders straight off these keys, so a
    key on one side only is a section that parses and is never shown, or a heading the page
    offers that nothing fills in.

    Derived from both files rather than listed here, so the section added tomorrow is covered.
    """
    source = PLAN_JS.read_text()
    block = source[source.index("const SECTIONS = ["):source.index("];", source.index(
        "const SECTIONS = ["))]
    js = re.findall(r"\{\s*key:\s*'([^']+)',\s*label:\s*'([^']+)',\s*kind:\s*'([^']+)'\s*\}", block)
    assert len(js) == len(plan_doc.SECTIONS), js

    # `plan` is `raw` on the server (kept verbatim for `parse_steps`) and `markdown` on the page
    # (drawn through `planMarkdown`). That is the one deliberate difference, named rather than
    # skipped: every other kind has to agree, or the page draws a list as a paragraph.
    kinds = {"raw": "markdown"}
    assert [(k, label, kinds.get(kind, kind)) for k, label, kind in js] == [
        (s.key, s.label, kinds.get(s.kind, s.kind)) for s in plan_doc.SECTIONS
    ]

    # And every kind the page is handed is one `renderBody` has a branch for; the default arm
    # draws a `<p>`, so a list with no case renders as `[object Object]`-ish prose.
    drawn = set(re.findall(r"case '([a-z]+)':", source)) | {"text"}
    assert {kind for _, _, kind in js} <= drawn, sorted({k for _, _, k in js} - drawn)


def test_the_turn_prompt_asks_for_the_data_section_after_the_screens():
    """Order matters twice over: it is the order of the page and the order `render` writes."""
    assert _PLAN_DOC_SECTIONS.index("'## Screens'") < _PLAN_DOC_SECTIONS.index("'## Data'")
    assert _PLAN_DOC_SECTIONS.index("'## Data'") < _PLAN_DOC_SECTIONS.index("'## Not doing'")
    # Optional, and said so in the prompt — an app that reads nothing has no bullets to write.
    assert "ONLY if the app reads data" in _PLAN_DOC_SECTIONS


def test_the_data_section_is_not_required_of_a_plan():
    """Out of scope for #543, and load-bearing: making it required would refuse every plan on
    disk, none of which has one."""
    assert "data" not in _REQUIRED_SECTIONS


def test_the_prompt_names_the_workspaces_own_word_for_the_nouns(tmp_path, monkeypatch):
    """ADR-0014: a partner's word, not Domino's. No lint checks this one. The shape is voiced
    through `brand.apply_voice`, which is what prompt bodies use and is not one of the positions
    `brand_lint` scans — `brand.text` is, and putting a 4 KB turn format through it reported the
    format's own headings as unkeyed names. So a bare `Dataset` written into this constant would
    pass every gate in the repo and reach a partner's planner. This is the check instead.
    """
    nouns = brand.DEFAULT["nouns"]
    # Written as tokens, never spelled out.
    assert "{dataset}" in _PLAN_DOC_SECTIONS and "{dataSource}" in _PLAN_DOC_SECTIONS
    for key in ("dataset", "dataSource"):
        for form in ("singular", "plural"):
            assert nouns[key][form] not in _PLAN_DOC_SECTIONS, (key, form)

    # And a pack that renames them reaches the planner. The default pack renders the same
    # literals the constant would have held, so only a renamed pack can tell the two apart.
    pack = tmp_path / "brand.json"
    pack.write_text(json.dumps({"nouns": {
        "dataset": {"singular": "Cube", "plural": "Cubes"},
        "dataSource": {"singular": "Warehouse", "plural": "Warehouses"},
    }}))
    monkeypatch.setattr("sage.orchestrator.brand._BAKED", tmp_path / "absent.json")
    monkeypatch.setenv("SAGE_BRAND_FILE", str(pack))

    # The same call the two sends make, so a change of door here is a change the test sees.
    resolved = brand.apply_voice(_PLAN_SHAPE_PHASED)
    assert "one per Cube, Warehouse or file" in resolved
    assert "Dataset" not in resolved and "Data Source" not in resolved
    # The HEADING is a parsing contract, not prose: renaming a noun must not move a section.
    assert "'## Data'" in resolved


def test_the_prompt_a_gated_plan_turn_actually_sends_carries_the_resolved_noun(
        tmp_path: Path, monkeypatch):
    """The two tests above read the constant. This one reads what went out on the wire.

    A constant full of tokens and a send that forgot to voice it both pass every check that
    only inspects the string; what a planner would then be told is to name each `{dataset}`,
    and a literal brace in a prompt is worse than the Domino word it replaced.
    """
    from .fake_opencode import Turn
    from .test_a_prompt_naming_no_app_asks_what_to_build import _REFUSAL, _build, _run

    pack = tmp_path / "brand.json"
    pack.write_text(json.dumps({"nouns": {
        "dataset": {"singular": "Cube", "plural": "Cubes"},
        "dataSource": {"singular": "Warehouse", "plural": "Warehouses"},
    }}))
    monkeypatch.setattr("sage.orchestrator.brand._BAKED", tmp_path / "absent.json")
    monkeypatch.setenv("SAGE_BRAND_FILE", str(pack))

    orch, oc = _build(tmp_path, [Turn(text=_REFUSAL)])
    _run(orch, "build me a table of lab samples")

    sent = oc.prompts[0]["text"]
    assert "one per Cube, Warehouse or file" in sent
    assert "{dataset}" not in sent and "{dataSource}" not in sent
    # And the step field the same turn asks for.
    assert "- Verify —" in sent and "- Done when —" not in sent


@pytest.mark.parametrize("stack", sorted(_PLAN_EXAMPLES))
def test_the_worked_example_names_its_data_and_is_still_a_plan_the_validator_accepts(stack: str):
    """A weaker model copies the example. One that showed no Data section would teach a planner
    not to write one, and one the validator refuses would teach it to write a refused plan."""
    example = _PLAN_EXAMPLES[stack]
    document = example[example.index("# "):]

    assert plan_doc.parse_sections(document)["sections"]["data"], stack
    check = validate_execution_contract(document)
    assert check.valid, check
    assert check.step_count == 2


# ---- (b) a plan saved before any of this is untouched --------------------------------------------


def test_a_plan_with_no_data_section_reads_and_renders_exactly_as_it_did():
    parsed = plan_doc.parse_sections(A_PLAN_SAVED_BEFORE_ALL_THIS)

    assert parsed["sections"]["data"] == []
    rendered = plan_doc.render(parsed["summary"], parsed["sections"], parsed["title"])

    # The document it re-renders to carries the headings it arrived with and no others, in the
    # order it arrived in. Byte equality is the wrong assertion and always was: `render` puts a
    # blank line under every heading whether or not the planner did, so a plan written without
    # them is re-spaced on the first edit — which was true before #543 and is not what this is
    # about. What must not happen is a heading appearing or moving.
    assert re.findall(r"^#{1,2} .*$", rendered, re.MULTILINE) == re.findall(
        r"^#{1,2} .*$", A_PLAN_SAVED_BEFORE_ALL_THIS, re.MULTILINE)
    assert "## Data" not in rendered            # an absent section, not a bare heading to fill in
    assert plan_doc.parse_sections(rendered)["sections"] == parsed["sections"]
    assert validate_execution_contract(A_PLAN_SAVED_BEFORE_ALL_THIS).valid
    assert validate_execution_contract(rendered).valid


# ---- (c) a step is checked by `Verify` -----------------------------------------------------------


def test_the_step_shape_asks_for_verify_and_no_longer_for_done_when():
    assert "- Verify —" in _PLAN_STEP_SHAPE
    assert "- Done when —" not in _PLAN_STEP_SHAPE
    # The document's acceptance list keeps the words; that is the heading, not the step field.
    assert "'## Done when'" in _PLAN_DOC_SECTIONS


def test_the_validators_own_words_name_the_field_the_prompt_asks_for():
    """The retry prompt restates `_PLAN_STEP_SHAPE` verbatim, so this list is the other half: a
    planner told to fix its `Done when` fields would be looking for a bullet it never wrote."""
    class _Check:
        missing_sections = ()
        malformed_steps = 1
        invalid_file_fields = 0
        contradictory_file_fields = 0
        step_count = 2

    problems = _execution_contract_problems(_Check())
    assert problems == ("steps with unique labels and nonempty Files, Do, and Verify fields",)


def test_a_step_that_says_verify_is_read_as_the_steps_done_when():
    steps = parse_steps(
        "### 1. Arrivals data\n"
        "- Files — src/arrivals.ts\n"
        "- Do — Read the arrivals file.\n"
        "- Verify — Each sample comes back with a late flag.\n"
    )
    assert [s.done_when for s in steps] == ["Each sample comes back with a late flag."]


def test_a_saved_step_still_spelled_done_when_is_still_a_valid_step():
    """The population nothing re-seeds. `_CANON` reads both spellings, and it has to keep doing
    it: every plan written before today says `Done when`, and a build reads them off disk."""
    steps = parse_steps(A_PLAN_SAVED_BEFORE_ALL_THIS)
    assert [s.done_when for s in steps] == [
        "Each sample comes back with its due date and a late flag.",
        "The preview shows the table.",
    ]
    assert validate_execution_contract(A_PLAN_SAVED_BEFORE_ALL_THIS).valid


def test_a_plan_mixing_both_spellings_parses_both():
    """What an edited plan looks like: the model rewrote one step and left the others alone."""
    mixed = A_PLAN_SAVED_BEFORE_ALL_THIS.replace(
        "- Done when — The preview shows the table.",
        "- Verify — The preview shows the table.",
    )
    assert [s.done_when for s in parse_steps(mixed)] == [
        "Each sample comes back with its due date and a late flag.",
        "The preview shows the table.",
    ]
    assert validate_execution_contract(mixed).valid
