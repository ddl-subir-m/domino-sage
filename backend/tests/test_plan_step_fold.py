"""A build step's file bookkeeping is folded away from the person approving it (#542).

`Files` and `Don't touch` exist so a phased build can hand a step to a session that never read
the plan. They are the machine's half of a step, and they were most of what a non-technical
reader saw. The fold is a view: nothing is stored differently, the parser is untouched, and the
edit box still holds the file line for line.

The spellings are read out of `plan_steps._CANON` and out of `_FIELD`'s own separator class
rather than listed here. A list written from the examples in front of the author is short by
construction, and the failure it buys is silent: a plan that spells a field `- Touch: a.ts`
executes exactly the same and shows the reader a file path anyway.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from sage.orchestrator import plan_steps

HARNESS = Path(__file__).parent / "js" / "plan_step_fold_harness.mjs"

# Every field name the phased-build parser canonicalises, and every separator it accepts between
# the name and the value. The separators are collected out of `_FIELD`'s character classes — all
# of them, bullet and whitespace included — and then the parser itself says which ones it takes.
# Reading the pattern for the answer is how a first attempt at this picked `[-*]`, the bullet
# class, and reported a clean pass over four separators none of which were the real ones.
_CANDIDATE_SEPARATORS = {
    ch for cls in re.findall(r"\[([^\]]+)\]", plan_steps._FIELD.pattern) for ch in cls
}
SEPARATORS = sorted(
    ch for ch in _CANDIDATE_SEPARATORS if plan_steps._FIELD.match(f"- Files {ch} a.ts")
)
FOLDED_FIELDS = sorted(k for k, v in plan_steps._CANON.items() if v in {"files", "dont_touch"})
KEPT_FIELDS = sorted(k for k, v in plan_steps._CANON.items() if v in {"do", "done_when"})

# A derivation that came back empty would parametrize into nothing and report green over no
# cases, and a canon field that fell into neither half would be a spelling nobody asked about.
assert SEPARATORS, "no separator survived the parser"
assert set(FOLDED_FIELDS) | set(KEPT_FIELDS) == set(plan_steps._CANON)

WELL_FORMED = """### 1. Sample data module
- Files — src/data/trades.ts
- Do — Define a typed Trade record and export 200 generated rows.
- Done when — The app compiles.

### 2. Trades table
- Files — src/components/TradesTable.tsx
- Do — Render the trades in a sortable table.
- Done when — The preview shows 200 rows.
- Don't touch — src/data/trades.ts
"""


def drawn(mode: str, plan: str) -> dict:
    result = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps({"mode": mode, "plan": plan}),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_a_step_folds_its_files_and_keeps_what_the_step_is_for():
    view = drawn("util", WELL_FORMED)

    assert view["folds"] == 2
    assert view["summaries"] == ["Files for this step"] * 2
    # Closed until asked for. A disclosure that opens itself is the noise back.
    assert view["opens"] == [False, False]

    for path in ("src/data/trades.ts", "src/components/TradesTable.tsx"):
        assert path in view["hidden"]
        assert path not in view["visible"]
    for sentence in ("Define a typed Trade record", "The preview shows 200 rows"):
        assert sentence in view["visible"]
        assert sentence not in view["hidden"]


def test_a_step_that_names_no_files_gets_no_fold():
    view = drawn(
        "util",
        "### 1. Wire the filter\n- Do — Add a currency dropdown.\n- Done when — Rows narrow.\n",
    )

    assert view["folds"] == 0
    assert "Add a currency dropdown." in view["visible"]


@pytest.mark.parametrize("separator", SEPARATORS)
def test_every_field_spelling_the_parser_accepts_lands_on_the_right_side(separator: str):
    # A value per field, numbered rather than named: `dont touch` and `don't touch` are two
    # spellings the parser keeps apart, and a value derived from the name would give them the
    # same one — so a fold that recognised only one of the two would read as covering both.
    fields = {name: f"sentinel/{i}.ts" for i, name in enumerate(FOLDED_FIELDS + KEPT_FIELDS)}
    body = "\n".join(f"- {name.capitalize()} {separator} {value}" for name, value in fields.items())
    view = drawn("util", f"### 1. One step\n{body}\n")

    assert view["folds"] == 1
    for name in FOLDED_FIELDS:
        assert fields[name] in view["hidden"], name
        assert fields[name] not in view["visible"], name
    for name in KEPT_FIELDS:
        assert fields[name] in view["visible"], name
        assert fields[name] not in view["hidden"], name


@pytest.mark.parametrize("mode", ["page", "card"])
def test_both_places_that_draw_the_plan_fold_it(mode: str):
    view = drawn(mode, WELL_FORMED)

    assert view["folds"] == 2
    assert view["summaries"] == ["Files for this step"] * 2
    assert "src/data/trades.ts" in view["hidden"]
    assert "src/data/trades.ts" not in view["visible"]
    assert "Render the trades in a sortable table." in view["visible"]


def test_editing_the_plan_still_shows_the_file_line_for_line():
    view = drawn("edit", WELL_FORMED)

    assert view["folds"] == 0
    assert view["textarea"] == WELL_FORMED


def test_a_field_line_outside_a_step_is_left_alone():
    # `parse_steps` ends a step at the next heading, so a bullet under "Open questions" is prose
    # the person wrote, not a brief for a build session.
    view = drawn(
        "util",
        "### 1. One step\n- Do — Ship it.\n- Done when — It ships.\n\n"
        "## Open questions\n- Files — do we keep src/legacy.ts?\n",
    )

    assert view["folds"] == 0
    assert "do we keep src/legacy.ts?" in view["visible"]
