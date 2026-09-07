"""Two `.table.json` Artifacts reached a Thread as blank cards: a correct "Adverse Events Summary"
title over a bordered box with nothing in it, because `TableBlock` handed antd a table with no
columns and no rows. It read as a rendering fault. It named neither what was missing nor anything
to do about it, and the `Copy` button was no better — it wrote `|  |` over `|  |`, which is an
empty markdown table wherever it is pasted, and is how the report arrived.

`test_a_table_artifact_whose_rows_are_not_under_rows_still_fills_its_cells.py` is the other half of
this fix and stops at the data: which `columns` and `rows` a wrapper recovers. A recovery can only
ever cover the shapes someone has seen. This half is the floor under all of them — whatever arrives
next, the card says something and offers the file.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "table_empty_card_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

_PATH = "examples/thr_ae/adverse-events-summary.table.json"


def _card(block: dict) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(block), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _empty(**over: object) -> dict:
    return {"type": "table", "title": "Adverse Events Summary", "path": _PATH,
            "columns": [], "rows": [], **over}


@needs_node
def test_the_card_says_why_it_is_empty_and_offers_the_file():
    card = _card(_empty())
    # The title still identifies which Artifact this is — it was never the missing half.
    assert card["title"] == "Adverse Events Summary"
    assert card["said"] == ["This table came through with no rows.", "Open the file"]
    # Wording the reader can act on either way. A frame that really was empty lands on this card
    # too, so it must not claim a fault it cannot know about; the file settles which it was.
    assert card["links"] == [
        "./api/project/file/raw?path=examples%2Fthr_ae%2Fadverse-events-summary.table.json"]


@needs_node
def test_the_card_renders_no_antd_table_at_all():
    """The blank box WAS an antd table — a header with no columns over a body with no rows. Not
    drawing one is the fix, so assert on its absence rather than on what it would have painted."""
    assert _card(_empty())["table"] is False


@needs_node
def test_copying_an_empty_table_does_not_paste_an_empty_markdown_table():
    """`|  |` over `|  |` renders as a table everywhere it is pasted, which carried this bug out of
    the product and into a bug report."""
    assert _card(_empty())["copied"] == "Adverse Events Summary\n(no rows)"


@needs_node
def test_a_table_with_rows_is_untouched():
    """The empty card must sit in front of the real one, not in place of it."""
    card = _card(_empty(columns=["drug", "events"], rows=[["Sertraline", 22]]))
    assert card["table"] is True
    assert card["said"] == []
    assert card["copied"] == (
        "Adverse Events Summary\n| drug | events |\n| --- | --- |\n| Sertraline | 22 |")


@needs_node
def test_a_block_with_no_path_still_says_something():
    """A card with nowhere to send anyone drops the offer and keeps the sentence. Better than the
    blank box it replaces, and better than a link that resolves to nothing."""
    block = _empty()
    del block["path"]
    card = _card(block)
    assert card["said"] == ["This table came through with no rows."]
    assert card["links"] == []
