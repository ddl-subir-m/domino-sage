"""Act 1 of the consumer-book demo asks "are any of my longs moving together?" and `sage-chat`
answers with `df.corr()`. Every way a turn writes that frame lost the half of it that names the
rows.

`df.corr().to_json()` is a columns-orient dump whose inner keys are the frame's own index — the
tickers — and `pandasOrientedTable` walked those keys to order the rows and then threw them away.
The Thread painted nine columns of floats under nine correct ticker headers with nothing down the
side, so the 0.70 between BRZO and QSRV was on screen and unreadable. A RangeIndex really is
disposable; a labelled one is the answer.

Two neighbouring shapes failed harder. A turn that half-remembers the wrapper writes
`{title, data: df.to_dict()}`, or fills `rows` with the dump instead of a list of rows. The
recovery ladder only looks for an ARRAY under `rows` / `data` / `records`, and
`pandasOrientedTable` was only ever handed the whole wrapper — so both fell through to
"This table came through with no rows" under a correct title.

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


def _open_with(thread_id: str, body: object, title: str = "correlation.table") -> dict:
    path = f"examples/{thread_id}/correlation.table.json"
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


# `df.corr().to_json()` over three of the demo's longs, trimmed to keep the claim readable.
CORR = {
    "BRZO": {"BRZO": 1.0, "QSRV": 0.7, "SLVR": 0.24},
    "QSRV": {"BRZO": 0.7, "QSRV": 1.0, "SLVR": 0.37},
    "SLVR": {"BRZO": 0.24, "QSRV": 0.37, "SLVR": 1.0},
}
COLUMNS = ["", "BRZO", "QSRV", "SLVR"]
ROWS = [
    ["BRZO", 1.0, 0.7, 0.24],
    ["QSRV", 0.7, 1.0, 0.37],
    ["SLVR", 0.24, 0.37, 1.0],
]


@needs_node
def test_a_labelled_index_becomes_the_first_column():
    """The bare dump — `json.dump(df.corr().to_dict(), f)`, no wrapper."""
    table = _open_with("thr_corr_bare", CORR)
    assert table["columns"] == COLUMNS
    assert table["rows"] == ROWS


@needs_node
def test_the_label_column_heads_blank_because_the_dump_carries_no_name():
    """`to_json` writes an index's values and never its name, so inventing one would be a
    guess. A matrix wants an empty top-left corner anyway."""
    assert _open_with("thr_corr_head", CORR)["columns"][0] == ""


@needs_node
def test_a_range_index_is_still_dropped():
    """The counter pandas made up carries nothing, and promoting it would put a column of
    0, 1, 2 in front of every ordinary frame."""
    body = {
        "TEAM": {"0": "Product", "1": "Engineering"},
        "GROSS_SPEND": {"0": 12.5, "1": 8.0},
    }
    table = _open_with("thr_range", body)
    assert table["columns"] == ["TEAM", "GROSS_SPEND"]
    assert table["rows"] == [["Product", 12.5], ["Engineering", 8.0]]


@needs_node
def test_a_dump_nested_under_data_is_found():
    """`{title, data: df.to_dict()}` — the wrapper is there and the rows are not a list."""
    table = _open_with("thr_nested_data", {"title": "Correlation", "data": CORR})
    assert table["title"] == "Correlation"
    assert table["columns"] == COLUMNS
    assert table["rows"] == ROWS


@needs_node
def test_a_dump_nested_under_rows_is_found_and_overrides_the_wrapper_columns():
    """The worst-reading shape: `columns` is right, `rows` holds the dump. The dump's own
    columns must win, or the recovered label column would sit under a header short by one."""
    body = {"title": "Correlation", "columns": ["BRZO", "QSRV", "SLVR"], "rows": CORR}
    table = _open_with("thr_nested_rows", body)
    assert table["columns"] == COLUMNS
    assert table["rows"] == ROWS
