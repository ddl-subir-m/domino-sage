"""A `.table.json` that deliberately holds no rows must read as a receipt, not as a rendering fault.

ADR-0045 has a Live read commit the shape of what it read and, unless the Project said otherwise,
none of the values. The renderer had no idea such a file could exist: `blocksForArtifacts` looks for
an array under `rows`/`data`/`records`, finds none, runs the pandas salvage path written to rescue
malformed dumps, finds nothing there either, and pushes a table with a correct title, real column
names and zero rows — the captioned blank grid `store.js` has been hardened against twice.

So the file says `shapeOnly` in so many words and the renderer tests for it. Getting that wrong in
either direction is bad: a malformed dump treated as shape-only loses the salvage it earned, and a
shape-only file treated as malformed paints the blank box again.

Two seams, one harness each: `js/table_artifact_harness.mjs` for what the store makes of the file,
`js/table_empty_card_harness.mjs` for the sentences on screen.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_JS = Path(__file__).resolve().parent / "js"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

_PATH = "examples/thr_gong/gong-calls.table.json"

_SHAPE = {
    "title": "GONG__CALLS",
    "columns": ["ID", "EMAIL"],
    "shapeOnly": True,
    "rowCount": 500,
    "cap": 500,
    "truncated": True,
    "readAt": "2026-09-10T09:00:00Z",
}


def _node(harness: str, stdin: object) -> object:
    out = subprocess.run(["node", str(_JS / harness)], input=json.dumps(stdin), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _opened(body: dict) -> dict:
    thread = {
        "id": "thr_gong",
        "history": [{
            "type": "done",
            "artifacts": [{"kind": "table", "path": _PATH, "title": body.get("title")}],
        }],
    }
    out = _node("table_artifact_harness.mjs",
                [{"thread": thread, "file": {"path": _PATH, "body": body}}, {"open": "thr_gong"}])
    tables = out[1]["tables"]
    assert len(tables) == 1
    return tables[0]


# ---- what the store makes of the file -----------------------------------------------------------


@needs_node
def test_a_shape_only_file_becomes_a_block_that_knows_it_has_no_rows():
    block = _opened(_SHAPE)

    assert block["shape"]["rowCount"] == 500
    assert block["shape"]["truncated"] is True
    assert block["shape"]["readAt"] == "2026-09-10T09:00:00Z"
    assert block["columns"] == ["ID", "EMAIL"], "the shape is the point: the columns still show"
    assert block["rows"] == []


@needs_node
def test_a_malformed_dump_is_still_salvaged():
    """The salvage path exists because real turns write pandas orients, and it must keep running.
    Only a file that SAYS it holds no rows skips it."""
    block = _opened({"title": "Tokens", "data": {"tokens": {"0": 12, "1": 9}}})

    assert "shape" not in block or not block["shape"]
    assert block["rows"] == [[12], [9]]


# ---- what the card says -------------------------------------------------------------------------


def _card(block: dict) -> dict:
    return _node("table_empty_card_harness.mjs", block)


def _block(**over: object) -> dict:
    return {"type": "table", "title": "GONG__CALLS", "path": _PATH,
            "columns": ["ID", "EMAIL"], "rows": [],
            "shape": {"rowCount": 500, "cap": 500, "truncated": True,
                      "readAt": "2026-09-10T09:00:00Z"},
            **over}


@needs_node
def test_the_card_says_the_shape_and_when_it_was_read():
    card = _card(_block())

    assert card["title"] == "GONG__CALLS"
    assert card["table"] is False, "antd given columns and no rows paints the blank grid"
    said = " ".join(card["said"])
    assert "2 columns" in said
    assert "the first 500 rows" in said, "truncation is a fact the card reads, not a silence"
    assert "Read " in said and "2026" in said
    assert "not kept in this Project's files" in said, "a thin card says why it is thin"


@needs_node
def test_a_read_that_was_not_cut_short_does_not_say_it_was():
    card = _card(_block(shape={"rowCount": 5, "cap": 500, "truncated": False,
                               "readAt": "2026-09-10T09:00:00Z"}))
    said = " ".join(card["said"])
    assert "5 rows" in said
    assert "first" not in said


@needs_node
def test_copying_a_thin_card_does_not_paste_an_empty_table():
    """`|  |` over `|  |` renders as an empty table wherever it lands, which is how the blank grid
    reached a bug report in the first place."""
    copied = _card(_block())["copied"]

    assert copied.startswith("GONG__CALLS")
    assert "|" not in copied
    assert "500" in copied


@needs_node
def test_the_stamp_is_the_day_the_read_ran_wherever_the_viewer_is():
    """`readAt` is written in UTC. Rendered in the viewer's zone, a read at 01:00 would date to the
    day before for anyone west of it — a silent off-by-one on the one number this card reports."""
    card = _card(_block(shape={"rowCount": 5, "cap": 500, "truncated": False,
                               "readAt": "2026-09-10T01:00:00Z"}))
    assert "Read September 10, 2026." in " ".join(card["said"])


@needs_node
def test_a_date_the_card_cannot_read_is_left_off_rather_than_stamped_invalid():
    """`readAt` comes out of a file. `Read Invalid Date.` is worse than no date at all."""
    card = _card(_block(shape={"rowCount": 5, "cap": 500, "truncated": False, "readAt": "sometime"}))
    said = " ".join(card["said"])
    assert "5 rows" in said
    assert "Invalid" not in said and "Read" not in said


@needs_node
def test_a_card_with_rows_is_untouched():
    card = _card(_block(rows=[[1, "a@acme.com"]], shape=None))
    assert card["table"] is True
    assert card["said"] == []
