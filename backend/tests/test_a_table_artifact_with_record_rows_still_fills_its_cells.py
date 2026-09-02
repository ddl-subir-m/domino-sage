"""A `sage-chat` turn analyzing an uploaded CSV wrote three `.table.json` Artifacts whose `rows`
were pandas records (`{"Adverse Event": "Nausea", "Count": 12}`) rather than the documented
positional array (`["Nausea", 12]`). The Thread still showed the right title, the right columns,
and the right "Show all N rows" count — `TableBlock` looks cells up by a numeric `dataIndex`, which
a record row has none of, so every cell rendered blank. See `js/table_artifact_harness.mjs`: it
drives the real `store.openThread` against a stubbed fetch and mounts nothing, so this is a claim
about the block's `columns`/`rows` data, not about antd painting it.
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


def _thread_with_table(thread_id: str, table_path: str, table_body: dict) -> dict:
    return {
        "id": thread_id,
        "history": [{
            "type": "done",
            "artifacts": [{"kind": "table", "path": table_path, "title": table_body.get("title")}],
        }],
    }


@needs_node
def test_record_shaped_rows_are_reordered_into_the_columns_they_name():
    """The exact shape from the bug report: `rows` keyed by column name, not position."""
    thread_id = "thr_adverse"
    path = f"examples/{thread_id}/top-adverse-events.table.json"
    body = {
        "title": "Top Adverse Events",
        "columns": ["Adverse Event", "Count"],
        "rows": [
            {"Count": 12, "Adverse Event": "Nausea"},
            {"Adverse Event": "Headache", "Count": 9},
        ],
    }
    out = _run([
        {"thread": _thread_with_table(thread_id, path, body), "file": {"path": path, "body": body}},
        {"open": thread_id},
    ])
    tables = out[1]["tables"]
    assert len(tables) == 1
    assert tables[0]["columns"] == ["Adverse Event", "Count"]
    assert tables[0]["rows"] == [["Nausea", 12], ["Headache", 9]]


@needs_node
def test_already_positional_rows_pass_through_unchanged():
    """The documented contract shape must not be disturbed by the record-row fix."""
    thread_id = "thr_positional"
    path = f"examples/{thread_id}/top-adverse-events.table.json"
    body = {
        "title": "Top Adverse Events",
        "columns": ["Adverse Event", "Count"],
        "rows": [["Nausea", 12], ["Headache", 9]],
    }
    out = _run([
        {"thread": _thread_with_table(thread_id, path, body), "file": {"path": path, "body": body}},
        {"open": thread_id},
    ])
    tables = out[1]["tables"]
    assert tables[0]["rows"] == [["Nausea", 12], ["Headache", 9]]


@needs_node
def test_a_record_row_missing_a_column_fills_that_cell_with_null_not_a_shift():
    """A short/uneven record must not shift later columns into the wrong cell."""
    thread_id = "thr_ragged"
    path = f"examples/{thread_id}/by-drug.table.json"
    body = {
        "title": "Adverse Events by Drug",
        "columns": ["Drug", "Total Events", "Fatal Outcomes"],
        "rows": [{"Drug": "DrugA", "Total Events": 5}],
    }
    out = _run([
        {"thread": _thread_with_table(thread_id, path, body), "file": {"path": path, "body": body}},
        {"open": thread_id},
    ])
    tables = out[1]["tables"]
    assert tables[0]["rows"] == [["DrugA", 5, None]]
