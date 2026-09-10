"""A `sage-chat` turn asked to visualise a warehouse table wrote
`ai_consumption_daily_sample.table.json` with pandas' default `df.to_json()` — column name to
`{index: value}` object, no `{title, columns, rows}` wrapper at all.

The Thread showed the filename-derived title ("ai consumption daily sample.table") over
"This table came through with no rows." The PNG written in the same turn was a broken-image
icon. `orient="split"` and `orient="table"` already recover because they keep an array under
`data`; the default `orient="columns"` (and `orient="index"`) keep no array, so every earlier
recovery fell through to `{}`.

Same harness as the rest of this family (`js/table_artifact_harness.mjs`): the claim is the
block's `columns`/`rows` data, not about antd painting it.
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


def _open_with(thread_id: str, body: object, title: str = "ai consumption daily sample.table") -> dict:
    path = f"examples/{thread_id}/ai_consumption_daily_sample.table.json"
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


ROWS = [
    ["2026-01-01", "Product", "cursor", 12.5],
    ["2026-01-02", "Engineering", "cursor", 8.0],
]
COLUMNS = ["REPORT_DATE", "TEAM", "PRODUCT", "GROSS_SPEND"]


@needs_node
def test_a_pandas_columns_orient_dump_fills_its_cells():
    """`df.to_json()` / `orient="columns"` — the default, and the file the screenshot held."""
    body = {
        "REPORT_DATE": {"0": "2026-01-01", "1": "2026-01-02"},
        "TEAM": {"0": "Product", "1": "Engineering"},
        "PRODUCT": {"0": "cursor", "1": "cursor"},
        "GROSS_SPEND": {"0": 12.5, "1": 8.0},
    }
    table = _open_with("thr_columns", body)
    assert table["columns"] == COLUMNS
    assert table["rows"] == ROWS


@needs_node
def test_a_pandas_index_orient_dump_fills_its_cells():
    """`df.to_json(orient="index")` is the transpose of the default and also has no array."""
    body = {
        "0": {"REPORT_DATE": "2026-01-01", "TEAM": "Product", "PRODUCT": "cursor",
              "GROSS_SPEND": 12.5},
        "1": {"REPORT_DATE": "2026-01-02", "TEAM": "Engineering", "PRODUCT": "cursor",
              "GROSS_SPEND": 8.0},
    }
    table = _open_with("thr_index", body)
    assert table["columns"] == COLUMNS
    assert table["rows"] == ROWS


@needs_node
def test_a_date_indexed_columns_dump_still_fills_its_cells():
    """`df.set_index("REPORT_DATE").to_json()` uses the dates as inner keys, not 0/1.

    This first landed asserting the dates were dropped, which recognised the shape and then
    deleted the column the person had indexed BY. A labelled index now becomes the first
    column — see
    `test_a_table_artifact_with_a_labelled_index_keeps_its_row_names.py`, where the same
    defect makes a correlation matrix unreadable rather than merely poorer.
    """
    body = {
        "TEAM": {"2026-01-01": "Product", "2026-01-02": "Engineering"},
        "PRODUCT": {"2026-01-01": "cursor", "2026-01-02": "cursor"},
        "GROSS_SPEND": {"2026-01-01": 12.5, "2026-01-02": 8.0},
    }
    table = _open_with("thr_dated", body)
    assert table["columns"] == ["", "TEAM", "PRODUCT", "GROSS_SPEND"]
    assert table["rows"] == [
        ["2026-01-01", "Product", "cursor", 12.5],
        ["2026-01-02", "Engineering", "cursor", 8.0],
    ]
