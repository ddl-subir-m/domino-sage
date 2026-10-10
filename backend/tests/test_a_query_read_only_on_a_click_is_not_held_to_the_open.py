"""A screen is held only to the queries it reads as it opens, not to those behind a button (#767).

#765 fails the data stage when an opened screen read none of the queries its plan step names. The
page check never presses an action button (#722, #764), so a step whose query runs only on a press
— the Product insights tab's "Write product brief" reading `customer_voice_sample` — failed though
the app was correct. `useQuery` is a hook and fires as its component mounts; a handler can only
call `runQuery`. A query the app calls by name only through `runQuery` is not held to the open.
"""
from __future__ import annotations

import json

from .fake_opencode import Turn
from .test_a_dead_alias_stops_the_turn_before_it_starts import _no_waiting  # noqa: F401
from .test_a_followup_screen_is_held_to_its_defaults import PIPELINE, QUERIES, READS, _followup
from .test_a_planned_build_is_reviewed_against_its_done_when import _done


def _plan(step_do: str) -> Turn:
    return Turn(text=f"""# Signal Room

Add a Product insights tab.

## Problem & outcome
Product managers cannot see usage and what customers say about it in one place.

## Who uses this
A product manager.

## What it does
- Shows product usage metrics, and writes a product brief from customer voice on request.

## Screens
- **Product insights** — usage metrics, and a Write product brief button.

## Done when
- Opening the Product insights tab shows the usage metrics.

## Plan
### 1. Insight queries
- Files — .sage/queries.json
- Do — Add `product_metrics` and `customer_voice_sample` to the catalog.
- Verify — each query is declared.

### 2. Product insights tab
- Files — src/App.tsx, src/Insights.tsx
- Do — {step_do}
- Verify — opening the Product insights tab shows the usage metrics.
""")


BOTH = _plan("Add the Product insights tab drawing `product_metrics`, with a Write product brief "
             "button that reads `customer_voice_sample`.")
CLICK_ONLY = _plan("Add a Write product brief button to the Product insights tab that reads "
                   "`customer_voice_sample`.")

# `product_metrics` is read as the tab mounts, and asked again from a Refresh button; the brief's
# query only from its button's handler.
BUILT = Turn(writes={
    QUERIES: json.dumps([{"name": n, "sql": "select 1"} for n in (
        "open_deals", "product_metrics", "customer_voice_sample")]) + "\n",
    "src/App.tsx": 'import { Insights } from "./Insights";\n'
                   "export default function App() { return <Insights />; }\n",
    "src/Insights.tsx": (
        'import { runQuery, useQuery } from "./appQuery";\n'
        "export function Insights() {\n"
        '  const metrics = useQuery("product_metrics");\n'
        '  const refresh = () => runQuery("product_metrics", {});\n'
        '  const writeBrief = async () => brief(await runQuery("customer_voice_sample", {}));\n'
        '  return page("Product insights", metrics, button("Refresh", refresh),\n'
        '              button("Write product brief", writeBrief));\n'
        "}\n"),
})

INSIGHTS = {"screen": "Product insights", "charts": 1, "tables": 0, "loading": False,
            "texts": ["Pipeline overview", "Product insights", "Write product brief"]}
METRICS_READ = {"kind": "query", "path": "/api/queries/product_metrics", "resourceIds": [],
                "status": 200, "outcome": "passed"}


def _not_failed(events: list[dict]) -> None:
    assert _done(events)["verification"]["stages"]["data"] == "unverified"
    assert not [e for e in events if e["type"] == "data-source-failed"]


def test_a_screen_that_reads_its_open_query_passes_with_its_click_query_unread(
        tmp_path, monkeypatch):
    """The ticket's first case: two named queries, the open one read, the button's not."""
    events, _, _ = _followup(tmp_path, monkeypatch, (), plan=BOTH, built=BUILT,
                             screens=(PIPELINE, INSIGHTS), reads=[*READS, METRICS_READ])
    _not_failed(events)


def test_a_screen_that_reads_neither_still_fails_on_its_open_query_alone(tmp_path, monkeypatch):
    """The ticket's second case: the same screen reading nothing at open still fails, and is
    blamed only for the query it should have read then. `product_metrics` is held although a
    Refresh button also asks for it with `runQuery`, because the tab reads it as it mounts."""
    events, _, _ = _followup(tmp_path, monkeypatch, (), plan=BOTH, built=BUILT,
                             screens=(PIPELINE, INSIGHTS))

    done = _done(events)
    assert done["ok"] is False
    assert done["verification"]["stages"]["data"] == "failed"
    reason = done["verification"]["reason"]
    assert ('The "Product insights" screen (plan step 2) read none of product_metrics '
            "when it opened") in reason
    assert "customer_voice_sample" not in reason


def test_a_step_whose_only_named_query_is_behind_a_button_is_not_failed(tmp_path, monkeypatch):
    """The false failure itself: the step names only the button's query, the screen opened and read
    what it reads at open, and nothing pressed the button."""
    events, _, _ = _followup(tmp_path, monkeypatch, (), plan=CLICK_ONLY, built=BUILT,
                             screens=(PIPELINE, INSIGHTS), reads=[*READS, METRICS_READ])
    _not_failed(events)
