"""The parser that decides whether a plan can be built in phases, and what each phase is told.

Pure functions, no fakes: this is the one part of a phased build that can be pinned exactly, so the
leniency (models drift on shape) and the strictness (a step without acceptance criteria isn't a
brief) are both asserted here rather than discovered in a live build.
"""
from sage.orchestrator.plan_steps import is_phasable, parse_steps, step_index

WELL_FORMED = """A dashboard for exploring trade data.

## Plan

### 1. Sample data module
- Files — src/data/trades.ts
- Do — Define a typed Trade record and export 200 generated rows.
- Done when — src/data/trades.ts exports `trades: Trade[]` and the app compiles.

### 2. Trades table
- Files — src/components/TradesTable.tsx, src/App.tsx
- Do — Render the trades in a sortable table and mount it in App.
- Done when — The preview shows 200 rows with clickable column sorting.
- Don't touch — src/data/trades.ts

### 3. Currency filter
- Files — src/components/Filters.tsx
- Do — Add a currency dropdown that filters the table.
- Done when — Picking a currency narrows the visible rows.

## Open questions
- Should amounts show in USD or the trade's own currency?
"""


def test_parses_every_field():
    steps = parse_steps(WELL_FORMED)
    assert [s.n for s in steps] == [1, 2, 3]
    assert steps[0].label == "Sample data module"
    assert steps[0].files == ["src/data/trades.ts"]
    assert steps[0].do.startswith("Define a typed Trade record")
    assert "app compiles" in steps[0].done_when
    assert steps[1].files == ["src/components/TradesTable.tsx", "src/App.tsx"]
    assert steps[1].dont_touch == ["src/data/trades.ts"]
    # Omitted, not "None" — the prompt forbids the literal, and an empty list is what the executor
    # prompt checks before mentioning it at all.
    assert steps[0].dont_touch == []


def test_open_questions_is_not_a_step():
    # "## Open questions" follows the last step; without a heading-terminates rule its bullets would
    # be swallowed into step 3 and shipped to the executor as instructions.
    steps = parse_steps(WELL_FORMED)
    assert len(steps) == 3
    assert "Open questions" not in steps[-1].raw
    assert "USD" not in steps[-1].raw


def test_raw_carries_the_whole_section():
    # `raw` is what the executor actually receives, so anything the parser didn't model must survive.
    step = parse_steps(WELL_FORMED)[1]
    assert "Trades table" in step.raw
    assert "Don't touch" in step.raw


def test_step_without_acceptance_criteria_is_dropped():
    # A step with work but no "done" gives a cold model nothing to aim at — the failure mode is a
    # phase that returns having "finished" nothing. Dropping it makes the whole plan non-phasable.
    plan = WELL_FORMED.replace(
        "- Done when — Picking a currency narrows the visible rows.\n", ""
    )
    steps = parse_steps(plan)
    assert [s.n for s in steps] == [1, 2]
    assert not is_phasable(plan)


def test_step_without_work_is_dropped():
    plan = WELL_FORMED.replace("- Do — Add a currency dropdown that filters the table.\n", "")
    assert [s.n for s in parse_steps(plan)] == [1, 2]


def test_missing_files_is_tolerated():
    # The file hint saves the executor a whole-tree grep, but it's a hint — a step is still usable
    # without it, so losing it must not silently drop the step.
    plan = WELL_FORMED.replace("- Files — src/data/trades.ts\n", "")
    steps = parse_steps(plan)
    assert len(steps) == 3
    assert steps[0].files == []


def test_tolerates_heading_and_separator_drift():
    # Every variant here is something a model actually produces when it half-remembers the format.
    plan = """## Plan

#### 1) Data module
* Touch: src/data.ts
* Change: Export sample rows.
* Verify: The module compiles.

**2. Table**
- Files – src/Table.tsx
- Work - Render the rows.
- Done – Rows appear.

### 3. Filter
- Files — src/Filter.tsx
- Do — Add a dropdown.
- Do not touch — src/data.ts
- Done when — Selecting narrows the rows.
"""
    steps = parse_steps(plan)
    assert [s.n for s in steps] == [1, 2, 3]
    assert steps[0].files == ["src/data.ts"]
    assert steps[0].done_when == "The module compiles."
    assert steps[1].label == "Table"
    assert steps[1].do == "Render the rows."
    assert steps[2].dont_touch == ["src/data.ts"]


def test_prose_plan_is_not_phasable():
    # Today's non-phased shape. It must fall back rather than produce one giant mangled step.
    plan = """A dashboard.

## Plan
1. **Sample data** — Define a typed Trade record.
2. **Trades table** — Render them in a sortable table.
3. **Filters** — Add a currency dropdown.
"""
    assert parse_steps(plan) == []
    assert not is_phasable(plan)


def test_too_few_steps_is_not_phasable():
    # Two phases don't repay two session bootstraps, so the threshold is a cost decision, not taste.
    two = WELL_FORMED.split("### 3.")[0]
    assert len(parse_steps(two)) == 2
    assert not is_phasable(two)
    assert is_phasable(two, min_steps=2)


def test_step_index_places_the_current_step():
    steps = parse_steps(WELL_FORMED)
    assert step_index(steps, 2) == (
        "1. Sample data module (done)\n"
        "2. Trades table (this step)\n"
        "3. Currency filter (later)"
    )


def test_empty_plan_is_safe():
    assert parse_steps("") == []
    assert not is_phasable("")


def test_a_file_in_both_files_and_dont_touch_is_not_fenced_off():
    """Seen live 2026-08-06: the planner wrote "Files — src/App.tsx, src/types.ts" and
    "Don't touch — src/types.ts" in the SAME step, i.e. create this file and also leave it alone. An
    agent that honours the fence cannot finish the step it was given, so Files wins."""
    plan = """
## Plan

### 1. Define review data
- Files — src/App.tsx, src/types.ts
- Do — Declare the transaction record type and export sample rows.
- Done when — src/types.ts exports Transaction and the app compiles.
- Don't touch — src/types.ts
"""
    step = parse_steps(plan)[0]

    assert step.files == ["src/App.tsx", "src/types.ts"]
    assert step.dont_touch == []          # the contradiction is dropped, not obeyed


def test_dont_touch_still_fences_files_the_step_does_not_own():
    plan = """
## Plan

### 4. Create row drawer
- Files — src/App.tsx, src/components/Drawer.tsx
- Do — Add a detail drawer opened from the queue.
- Done when — Selecting a row opens the drawer.
- Don't touch — src/types.ts, src/components/FilterBar.tsx
"""
    step = parse_steps(plan)[0]

    assert step.dont_touch == ["src/types.ts", "src/components/FilterBar.tsx"]
