"""A `sage-chat` turn visualising a warehouse table wrote `api_usage_detail.table.json` whose
`columns` were not a list of strings. After reload the Thread showed the filename-derived title
("api usage detail.table"), "Show all 50 rows", and a grid with no cells.

Two wrappers produced that card. A turn half-remembering JSON Table Schema puts
`{"name": "date", "type": "string"}` in `columns` and pandas records in `rows`;
`TableBlock` looks cells up by a numeric `dataIndex` after `blocksForArtifacts` maps
`row[column]`, and an object is not a key any record has, so every cell is null. The same
turn "pretties" the header (`"API Key"`) while `df.to_dict("records")` keeps the frame's
own names (`api_key`). And pandas-style `columns` as `{0: "date", 1: "tokens"}` is not an
array, so the header is dropped and a positional body is handed to antd with no column
defs — the other way to get a 50-row blank grid.

Same harness as the rest of this family (`js/table_artifact_harness.mjs`): the claim is
the block's `columns`/`rows` data. Painting a positional body that has no header at all is
the sibling `test_a_table_with_positional_rows_and_no_header_still_fills_its_cells.py`.
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


def _open_with(thread_id: str, body: object, title: str = "api usage detail.table") -> dict:
    path = f"examples/{thread_id}/api_usage_detail.table.json"
    out = _run([
        {"thread": {"id": thread_id, "history": [{
            "type": "done",
            "artifacts": [{"kind": "table", "path": path, "title": title}],
        }]},
         "file": {"path": path, "body": body}},
        {"open": thread_id},
    ])
    tables = out[1]["tables"]
    assert len(tables) == 1
    return tables[0]


ROWS = [["2026-01-01", "k-sales", 1200], ["2026-01-02", "k-eng", 80]]
RECORDS = [{"date": d, "api_key": k, "tokens": n} for d, k, n in ROWS]


@needs_node
def test_schema_field_objects_as_columns_fill_record_rows():
    """`columns: [{name, type}, …]` with record rows — every cell was null because the object
    itself was the lookup key."""
    table = _open_with("thr_fields", {
        "columns": [{"name": "date", "type": "string"},
                    {"name": "api_key", "type": "string"},
                    {"name": "tokens", "type": "integer"}],
        "rows": RECORDS,
    })
    assert table["title"] == "api usage detail.table"
    assert table["columns"] == ["date", "api_key", "tokens"]
    assert table["rows"] == ROWS


@needs_node
def test_schema_field_objects_as_columns_keep_positional_rows():
    table = _open_with("thr_fields_pos", {
        "columns": [{"name": "date", "type": "string"},
                    {"name": "api_key", "type": "string"},
                    {"name": "tokens", "type": "integer"}],
        "rows": ROWS,
    })
    assert table["columns"] == ["date", "api_key", "tokens"]
    assert table["rows"] == ROWS


@needs_node
def test_columns_dumped_as_an_index_object_still_name_the_header():
    """`json.dumps({"columns": dict(enumerate(df.columns)), "rows": df.values.tolist()})`."""
    table = _open_with("thr_index", {
        "columns": {"0": "date", "1": "api_key", "2": "tokens"},
        "rows": ROWS,
    })
    assert table["columns"] == ["date", "api_key", "tokens"]
    assert table["rows"] == ROWS


@needs_node
def test_pretty_headers_still_find_the_record_keys_they_name():
    """The wrapper's header is what a person reads; `to_dict("records")` keeps the frame's names."""
    table = _open_with("thr_pretty", {
        "columns": ["Date", "API Key", "Tokens"],
        "rows": RECORDS,
    })
    assert table["columns"] == ["Date", "API Key", "Tokens"]
    assert table["rows"] == ROWS
