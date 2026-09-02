"""A `sage-chat` turn asked what was in an uploaded forecast JSON wrote `forecasts.table.json` as a
bare `df.to_dict("records")` array — no `{title, columns, rows}` wrapper at all. `columns` and
`rows` both fell through to empty, so the card painted antd's "No data" placeholder beside a
`forecasts.png` chart that had plotted the same five days correctly. The half-fix in
`test_a_table_artifact_with_record_rows_still_fills_its_cells.py` only recovers record rows found
*inside* the wrapper; a missing wrapper still lost every row. Same harness, same claim: this is
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


def _thread_with_wrapperless_table(thread_id: str, table_path: str, title: str) -> dict:
    # No `title` to read off the body — a bare array carries none, so the card falls back to the
    # Artifact title the manifest derived from the filename.
    return {
        "id": thread_id,
        "history": [{
            "type": "done",
            "artifacts": [{"kind": "table", "path": table_path, "title": title}],
        }],
    }


@needs_node
def test_a_bare_record_array_becomes_columns_and_positional_rows():
    """The exact file from the bug report: `df.to_dict("records")` with no wrapper."""
    thread_id = "thr_forecasts"
    path = f"examples/{thread_id}/forecasts.table.json"
    body = [
        {"date": "2023-07-15", "Narnia_LensLogic": 71129.94, "Narnia_OptiGlimpse": 672205.74},
        {"date": "2023-07-16", "Narnia_LensLogic": 438.73, "Narnia_OptiGlimpse": 1494.06},
        {"date": "2023-07-17", "Narnia_LensLogic": 1911296.51, "Narnia_OptiGlimpse": 3136962.36},
    ]
    out = _run([
        {"thread": _thread_with_wrapperless_table(thread_id, path, "forecasts.table"),
         "file": {"path": path, "body": body}},
        {"open": thread_id},
    ])
    tables = out[1]["tables"]
    assert len(tables) == 1
    assert tables[0]["columns"] == ["date", "Narnia_LensLogic", "Narnia_OptiGlimpse"]
    assert tables[0]["rows"] == [
        ["2023-07-15", 71129.94, 672205.74],
        ["2023-07-16", 438.73, 1494.06],
        ["2023-07-17", 1911296.51, 3136962.36],
    ]
    assert tables[0]["title"] == "forecasts.table"


@needs_node
def test_a_bare_array_of_positional_rows_is_not_given_index_headers():
    """Reading column names off a positional row would header the table "0", "1" — worse than the
    empty header it renders today, so the derivation only reads names off record rows."""
    thread_id = "thr_headless"
    path = f"examples/{thread_id}/forecasts.table.json"
    body = [["2023-07-15", 71129.94], ["2023-07-16", 438.73]]
    out = _run([
        {"thread": _thread_with_wrapperless_table(thread_id, path, "forecasts.table"),
         "file": {"path": path, "body": body}},
        {"open": thread_id},
    ])
    tables = out[1]["tables"]
    assert tables[0]["columns"] == []
    assert tables[0]["rows"] == [["2023-07-15", 71129.94], ["2023-07-16", 438.73]]


@needs_node
def test_an_empty_bare_array_stays_an_empty_table():
    """A turn whose dataframe really was empty must still reach the "No data" card, not a crash
    that demotes the Artifact to a bare file row."""
    thread_id = "thr_empty"
    path = f"examples/{thread_id}/forecasts.table.json"
    out = _run([
        {"thread": _thread_with_wrapperless_table(thread_id, path, "forecasts.table"),
         "file": {"path": path, "body": []}},
        {"open": thread_id},
    ])
    tables = out[1]["tables"]
    assert len(tables) == 1
    assert tables[0]["columns"] == []
    assert tables[0]["rows"] == []
