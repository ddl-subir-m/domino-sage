"""What the transcript shows for a table whose Project does not keep data rows (ADR-0045).

The file that reaches the card holds a title, its columns, a count and a date, and no rows at all.
Rendered by the path that was already there, that is the blank grid this product has twice been
reported for — "No data" under a correct title, which reads as a fault and sends someone looking
for one. It is not a fault: the rows were read, the answer was given, and the Project said not to
write them down. So the card says that, and says where the setting is.

Two harnesses, because the claim has two halves and neither settles the other.
`table_artifact_harness.mjs` runs the real store over a stubbed fetch and reports the BLOCK — the
count and the date have to survive the recovery ladder to reach the card at all.
`table_empty_card_harness.mjs` takes one block and reports WHICH SENTENCES ARE ON SCREEN.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_JS = Path(__file__).resolve().parent / "js"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

_PATH = "examples/thr_kept/sample-5-rows.table.json"
# Old enough that `relativeTime` reaches for an absolute date, which is the Domino writing rule:
# relative inside seven days, "Month Day, Year" beyond it. A card read months later must not say
# "2 hours ago" about the last time anyone saw the rows.
_READ_AT = "2026-03-04T18:04:00+00:00"

_RECEIPT = {
    "title": "Sample rows from card_panel_transactions_RAW.csv",
    "columns": ["email", "card_number", "ssn"],
    "rows": [],
    "rowCount": 5,
    "readAt": _READ_AT,
    "keptRows": False,
}


def _run(harness: str, payload: object, env: dict | None = None) -> object:
    out = subprocess.run(["node", str(_JS / harness)], input=json.dumps(payload), check=False,
                         capture_output=True, text=True, timeout=60,
                         env={**os.environ, **(env or {})})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _block(body: dict) -> dict:
    """The table block the store builds for a Thread holding one `.table.json` Artifact."""
    out = _run("table_artifact_harness.mjs", [
        {"thread": {"id": "thr_kept", "history": [{
            "type": "done",
            "artifacts": [{"kind": "table", "path": _PATH, "title": "sample 5 rows table"}],
        }]},
         "file": {"path": _PATH, "body": body}},
        {"open": "thr_kept"},
    ])
    tables = out[1]["tables"]
    assert len(tables) == 1
    return tables[0]


def _card(block: dict, *, project_keeps_rows: bool = False) -> dict:
    return _run("table_empty_card_harness.mjs", block,
                {"KEPT_ROWS": "1"} if project_keeps_rows else None)


@needs_node
def test_the_count_and_the_date_reach_the_block():
    block = _block(_RECEIPT)
    assert block["columns"] == ["email", "card_number", "ssn"]
    assert block["rows"] == []
    assert block["rowCount"] == 5
    assert block["readAt"] == _READ_AT
    assert block["keptRows"] is False


@needs_node
def test_a_table_that_kept_its_rows_says_nothing_about_the_setting():
    """`keptRows` is absent from every file written before this and from every file an opted-in
    Project writes. Absent must not read as false, or the card would announce a rule to Projects
    that are not under it."""
    block = _block({"title": "Sample rows", "columns": ["drug"], "rows": [["Sertraline"]]})
    assert "keptRows" not in block
    assert _card(block)["table"] is True


@needs_node
def test_the_card_names_the_columns_the_count_and_the_date():
    card = _card(_block(_RECEIPT))
    assert card["title"] == "Sample rows from card_panel_transactions_RAW.csv"
    assert card["said"] == [
        "email, card_number, ssn",
        "5 rows, read March 4, 2026",
        ('Rows aren\'t kept in this Project\'s files. To keep them, switch on '
         '"Keep data rows" in Add people.'),
    ]


@needs_node
def test_the_card_draws_no_empty_grid():
    """The failure this replaces was an antd table with column defs and no rows under them — a
    header over "No data". Not drawing one is the fix, so assert on its absence."""
    assert _card(_block(_RECEIPT))["table"] is False


@needs_node
def test_copying_the_card_pastes_the_receipt_and_not_an_empty_table():
    assert _card(_block(_RECEIPT))["copied"] == (
        "Sample rows from card_panel_transactions_RAW.csv\n"
        "email, card_number, ssn\n"
        "5 rows, read March 4, 2026\n"
        'Rows aren\'t kept in this Project\'s files. To keep them, switch on '
        '"Keep data rows" in Add people.')


@needs_node
def test_one_row_is_not_1_rows():
    card = _card({**_RECEIPT, "type": "table", "path": _PATH, "rowCount": 1})
    assert card["said"][1] == "1 row, read March 4, 2026"


@needs_node
def test_the_stamp_is_the_date_the_rows_were_read_in_the_zone_they_were_read_in():
    """An early-morning UTC read dates to the day before for every viewer west of UTC if the stamp
    renders in the viewer's zone — the same read, two dates, depending on who opens the transcript.
    The stamp is written in UTC and is pinned to it. This is also why it is an absolute date and not
    "2 hours ago": a relative one re-reads the clock on every render, so a tab left open overnight
    re-dates a read that never moved."""
    card = _card({**_RECEIPT, "type": "table", "path": _PATH,
                  "readAt": "2026-03-04T01:00:00+00:00"})
    assert card["said"][1] == "5 rows, read March 4, 2026"


@needs_node
def test_a_stamp_nobody_can_parse_is_left_off_rather_than_printed():
    """The value comes out of a file, and a file can hold anything. `Invalid Date` on the card is
    worse than a sentence with no date in it."""
    card = _card({**_RECEIPT, "type": "table", "path": _PATH, "readAt": "the other day"})
    assert card["said"][1] == "5 rows read"


@needs_node
def test_a_frame_that_was_empty_is_not_offered_rows_it_never_had():
    """Zero rows read is a table that had none, not one this Project declined to keep. Offering the
    setting there would promise rows back that turning it on cannot produce."""
    card = _card({**_RECEIPT, "type": "table", "path": _PATH, "rowCount": 0})
    assert card["said"] == ["email, card_number, ssn", "0 rows, read March 4, 2026"]


@needs_node
def test_a_project_that_has_since_turned_the_setting_on_is_not_told_to_turn_it_on():
    """The file records what IT holds, and that is right — an Artifact written before the switch
    was flipped still holds no rows, and repairing it later would be writing rows nobody asked
    anyone to write. But the sentence is in the present tense and about the Project, so after the
    flip it sends someone to switch on a thing they have already switched on. The count and the
    date stay: they are still what this table is."""
    card = _card(_block(_RECEIPT), project_keeps_rows=True)
    assert card["said"] == ["email, card_number, ssn", "5 rows, read March 4, 2026"]


@needs_node
def test_a_table_nobody_could_read_keeps_the_offer_of_the_file():
    """The receipt for a `.table.json` that is not JSON has no count, because none was read — and
    without one this card cannot tell an emptied table from a wrapper the ladder could not follow.
    It falls back to the older card, which says so and hands over the file rather than printing a
    zero it never counted."""
    card = _card({"type": "table", "path": _PATH, "columns": [], "rows": [], "keptRows": False})
    assert card["said"] == ["This table came through with no rows.", "Open the file"]
    assert card["table"] is False
