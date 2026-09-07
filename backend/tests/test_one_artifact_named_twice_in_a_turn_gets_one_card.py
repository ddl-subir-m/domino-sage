"""A Thread showed "Adverse Events Summary" twice, both blank, under an answer that had written
one table. Two separate faults met there, and only one of them was the blank.

`_chat_stream` sends the turn's artifact list twice — as an `artifacts` frame, then again on the
`done` that closes the turn — and the live reducer is the only thing that turns that back into one
card. It matched a block against an incoming row by whichever of `src`/`path`/`title` the block had
set first. An image sets `src`, so images always matched. A table set neither, so it was matched on
its TITLE — and a table block takes its title from inside the JSON (`data.title`) while the manifest
row takes one derived from the filename. The moment a turn names its own table, those two strings
differ, the row looks new, and the card is appended a second time.

So the duplicate needed a table whose JSON carried a `title`, which is why no existing harness saw
it: `chat_stream_harness.mjs` answers every file read with `{}`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "artifact_dedupe_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

_TABLE = "examples/t1/adverse-events-summary.table.json"
_PNG = "examples/t1/severity-outcome-heatmap.png"


def _cards(items: list[dict], files: dict) -> list[dict]:
    """The Artifact cards one turn leaves behind, given what the server named and what is on disk.

    Both frames carry the same list, verbatim, the way `_chat_stream` sends them."""
    frames = [{"type": "artifacts", "items": items},
              {"type": "done", "ok": True, "decision": "answered", "artifacts": items}]
    out = subprocess.run(["node", str(_HARNESS)],
                         input=json.dumps({"frames": frames, "files": files}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_a_table_that_names_itself_gets_one_card_not_two():
    """The reported shape. The manifest row says "adverse events summary table" — `record_artifact`
    derives that from the filename — and the JSON says "Adverse Events Summary"."""
    cards = _cards(
        [{"kind": "table", "path": _TABLE, "title": "adverse events summary table"}],
        {_TABLE: {"title": "Adverse Events Summary", "columns": ["drug"], "rows": [["Sertraline"]]}},
    )
    assert [c["title"] for c in cards] == ["Adverse Events Summary"]


@needs_node
def test_a_table_that_recovers_nothing_still_gets_one_card():
    """The two faults met on one card, so pin them together: an unreadable wrapper must not also
    come back twice. This is the exact pair that was reported."""
    cards = _cards(
        [{"kind": "table", "path": _TABLE, "title": "adverse events summary table"}],
        {_TABLE: {"title": "Adverse Events Summary", "sheets": {}}},
    )
    assert len(cards) == 1
    assert cards[0]["path"] == _TABLE


@needs_node
def test_a_png_and_a_table_that_share_a_title_both_survive():
    """The guard against fixing this by matching on titles instead. AGENTS.md tells a turn that a
    matrix is BOTH a heatmap PNG and a `.table.json` of the same numbers, so two Artifacts
    deliberately sharing one title is a shape Sage asks for — and folding them into one card would
    lose the numbers the PNG cannot give back."""
    cards = _cards(
        [{"kind": "chart", "path": _PNG, "title": "Severity by outcome"},
         {"kind": "table", "path": _TABLE, "title": "severity outcome heatmap table"}],
        {_TABLE: {"title": "Severity by outcome", "columns": ["a"], "rows": [[1]]}},
    )
    assert [c["type"] for c in cards] == ["image", "table"]
    assert [c["title"] for c in cards] == ["Severity by outcome", "Severity by outcome"]


@needs_node
def test_an_image_named_twice_still_gets_one_card():
    """Images matched on `src` and were never part of the fault. Keep it that way."""
    cards = _cards([{"kind": "chart", "path": _PNG, "title": "events by drug"}], {})
    assert len(cards) == 1
    assert cards[0]["type"] == "image"
