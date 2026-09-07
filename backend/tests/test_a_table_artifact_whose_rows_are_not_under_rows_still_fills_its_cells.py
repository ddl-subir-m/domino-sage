"""Two `.table.json` Artifacts reached a Thread as captioned blank boxes: a correct
"Adverse Events Summary" title over an antd table with no columns and no rows, beside three PNGs
that had plotted the same 220 rows fine. The title proves the turn wrote a wrapper and put a
`title` in it — the fallback title is derived from the filename and would have read
"adverse events summary table" — so this is neither of the two shapes already recovered
(`test_a_table_artifact_with_record_rows_still_fills_its_cells.py` recovers record rows *inside*
the wrapper, `test_a_table_artifact_written_without_its_wrapper_still_fills_its_cells.py` recovers
a wrapper that is missing altogether). It is a wrapper whose rows are under some key other than
`rows`, which is what every `df.to_json(orient=…)` that keeps a wrapper writes.

Same harness, same claim as its two siblings: this is about the block's `columns`/`rows` data, not
about antd painting it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "table_artifact_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(steps: list[dict]) -> list[dict]:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(steps), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _open_with(thread_id: str, body: object) -> dict:
    """The one table block a Thread holding a single `.table.json` Artifact opens to."""
    path = f"examples/{thread_id}/adverse-events-summary.table.json"
    out = _run([
        {"thread": {"id": thread_id, "history": [{
            "type": "done",
            "artifacts": [{"kind": "table", "path": path,
                           "title": "adverse events summary table"}],
        }]},
         "file": {"path": path, "body": body}},
        {"open": thread_id},
    ])
    tables = out[1]["tables"]
    assert len(tables) == 1
    return tables[0]


ROWS = [["Sertraline", 22], ["Levothyroxine", 15], ["Atorvastatin", 14]]
RECORDS = [{"drug": d, "events": n} for d, n in ROWS]


@needs_node
def test_a_wrapper_that_names_its_rows_data_fills_its_cells():
    """`{"title": …, "data": [records]}` — the wrapper is right, the row key is not."""
    table = _open_with("thr_data", {"title": "Adverse Events Summary", "data": RECORDS})
    assert table["title"] == "Adverse Events Summary"
    assert table["columns"] == ["drug", "events"]
    assert table["rows"] == ROWS


@needs_node
def test_a_wrapper_that_names_its_rows_records_fills_its_cells():
    """`records` is the other name a turn half-remembering the contract reaches for."""
    table = _open_with("thr_records", {"title": "Adverse Events Summary", "records": RECORDS})
    assert table["columns"] == ["drug", "events"]
    assert table["rows"] == ROWS


@needs_node
def test_a_split_orient_dump_fills_its_cells():
    """`df.to_json(orient="split")` already carried readable `columns`, so this rendered a correct
    header over no rows at all — a subtler blank than the one above and the same lost table."""
    table = _open_with("thr_split", {"columns": ["drug", "events"],
                                     "index": [0, 1, 2], "data": ROWS})
    assert table["columns"] == ["drug", "events"]
    assert table["rows"] == ROWS


@needs_node
def test_a_table_orient_dump_reads_its_columns_off_the_schema():
    """`df.to_json(orient="table")` names its columns in a JSON Table Schema and nowhere else, and
    the `index` field in it is pandas' own, not a column of the frame."""
    table = _open_with("thr_schema", {
        "schema": {"fields": [{"name": "index", "type": "integer"},
                              {"name": "drug", "type": "string"},
                              {"name": "events", "type": "integer"}],
                   "primaryKey": ["index"], "pandas_version": "1.4.0"},
        "data": RECORDS,
    })
    assert table["columns"] == ["drug", "events"]
    assert table["rows"] == ROWS


@needs_node
def test_the_documented_shape_still_wins_over_every_recovery():
    """A turn that followed the contract must not be re-derived. `rows` is read before `data`, and
    a `columns` list that is present is the header even when the rows could name one."""
    table = _open_with("thr_contract", {
        "title": "Adverse Events Summary",
        "columns": ["Drug", "Events"],
        "rows": ROWS,
        "data": [["ignored", 0]],
    })
    assert table["columns"] == ["Drug", "Events"]
    assert table["rows"] == ROWS


@needs_node
def test_a_table_that_recovers_nothing_carries_its_path():
    """The card offers the file when it has nothing to show, and it can only do that if the block
    remembers where the file was. A frame that really was empty lands here too, which is why the
    card offers the file rather than claiming a fault."""
    table = _open_with("thr_unreadable", {"title": "Adverse Events Summary", "sheets": {}})
    assert table["columns"] == []
    assert table["rows"] == []
    assert table["path"] == "examples/thr_unreadable/adverse-events-summary.table.json"
