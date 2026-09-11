"""What the thin card offers, and when it asks the server for anything (#256, ADR-0045).

The card is the one on `main` — `tableReceiptLines` — extended rather than rebuilt, and its stamp
still reads the date in the FILE. That is deliberate and it is the only state on this card that
outlives a press: the rows a press puts on screen are held in memory, a reload loses them, and a
stamp that moved to today would claim a freshness the card cannot keep.

`table_empty_card_harness.mjs` renders the card, presses one button by its label, and renders it
again. Its `fetch` counts every request, because "nothing is re-read on open or on scroll" is a
claim about a count and not about a shape.
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

_PATH = "examples/thr_kept/gong-calls.table.json"
_SOURCE = {"kind": "table", "binding": "bnd_1", "table": "DWH.MARTS.GONG__CALLS", "limit": 500}

_RECEIPT = {
    "type": "table",
    "path": _PATH,
    "title": "GONG__CALLS",
    "columns": ["ID", "EMAIL"],
    "rows": [],
    "rowCount": 5,
    "readAt": "2026-03-04T18:04:00+00:00",
    "keptRows": False,
    "source": _SOURCE,
}

_FRESH = {"columns": ["ID", "EMAIL"], "rows": [[1, "a@acme.com"], [2, "b@acme.com"]],
          "rowCount": 2, "truncated": False, "readAt": "2026-09-11T09:00:00Z"}


def _card(block: dict, *, press: str = "", answers: dict | None = None) -> dict:
    env = {**os.environ}
    if press:
        env["PRESS"] = press
    if answers is not None:
        env["READ_AGAIN"] = json.dumps(answers)
    out = subprocess.run(["node", str(_JS / "table_empty_card_harness.mjs")],
                         input=json.dumps(block), capture_output=True, text=True,
                         timeout=60, env=env, check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_a_card_that_records_a_read_offers_to_run_it_again():
    assert _card(_RECEIPT)["buttons"] == ["Read again"]


@needs_node
def test_a_card_with_no_binding_offers_no_button_and_still_says_what_it_is():
    """#258, option A. A table the Chat agent composed is a composition and not a read, so nothing
    can reproduce it — and the card must not look broken for lack of a button it could never use."""
    card = _card({**_RECEIPT, "source": None})

    assert card["buttons"] == []
    assert card["said"][0] == "ID, EMAIL"
    assert card["said"][1] == "5 rows, read March 4, 2026"


@needs_node
def test_nothing_is_read_until_somebody_presses():
    assert _card(_RECEIPT)["fetched"] == []


@needs_node
def test_a_press_puts_todays_rows_on_the_card():
    card = _card(_RECEIPT, press="Read again", answers=_FRESH)

    assert card["table"] is True
    assert card["headers"] == ["ID", "EMAIL"]
    assert card["cells"] == [1, "a@acme.com"]
    assert [u for u in card["fetched"] if "live-read" in u], card["fetched"]


@needs_node
def test_the_stamp_still_says_when_the_file_was_read():
    """The two dates are two facts. The card's own stamp is about the Artifact, which is durable;
    the line under the fresh rows is about the screen, which is not (#243)."""
    card = _card(_RECEIPT, press="Read again", answers=_FRESH)

    assert card["said"][1] == "5 rows, read March 4, 2026"
    assert card["said"][-1] == ("Now on screen: 2 rows, read September 11, 2026. This Project "
                                "doesn't keep data rows in its files, so these are not saved.")


@needs_node
def test_a_refused_read_says_so_and_the_card_keeps_its_shape():
    """A viewer whose access went away. The sentence is the server's — it names the thing and the
    act — and the card goes on showing the columns, the count and the date it always had."""
    card = _card(_RECEIPT, press="Read again",
                 answers={"refused": "Sage cannot reach DWH from this conversation."})

    assert card["table"] is False
    assert card["said"][0] == "ID, EMAIL"
    assert card["said"][1] == "5 rows, read March 4, 2026"
    assert "Sage cannot reach DWH from this conversation." in card["said"]
    assert card["buttons"] == ["Read again"], "still pressable — access can come back"
