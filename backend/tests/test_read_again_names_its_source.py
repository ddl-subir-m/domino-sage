"""What a Live read's Artifact records so a different viewer can read it again (#256, ADR-0045).

The receipt is a whitelist (`table_shape._KEYS`), so a source that is not named there is dropped on
the way through. These tests hold the two halves apart: what the whitelist carries, and what the
writer puts in front of it.

The source is an IDENTIFIER, never a value. That is why it is allowed past a rule whose whole job is
keeping values out — and why every field of it is type-checked, like every other optional key here.
"""

from __future__ import annotations

import re
from pathlib import Path

from sage.workspace import table_shape

_AT = "2026-09-11T10:00:00Z"


def test_a_recorded_source_survives_the_whitelist():
    kept = table_shape.shape_only(
        {"title": "Calls", "columns": ["ID"], "rows": [[1]],
         "source": {"kind": "table", "binding": "b_1", "table": "DWH.MARTS.CALLS", "limit": 5}},
        read_at=_AT,
    )
    assert kept["source"] == {"kind": "table", "binding": "b_1",
                              "table": "DWH.MARTS.CALLS", "limit": 5}
    assert kept["rows"] == []


def test_a_file_source_names_the_path_and_not_the_table():
    kept = table_shape.shape_only(
        {"columns": ["a"], "rows": [["x"]],
         "source": {"kind": "file", "binding": "b_2", "path": "gong/calls.csv", "limit": 500}},
        read_at=_AT,
    )
    assert kept["source"] == {"kind": "file", "binding": "b_2",
                              "path": "gong/calls.csv", "limit": 500}


def test_a_source_carrying_anything_else_carries_only_what_it_is_allowed_to():
    """The smuggling case the module's docstring names: an optional key is a way back in for a
    value under a name the rule allows. `source` is a nested object, so it needs its own
    whitelist — a row parked under a key nobody checks is still a row in the repo."""
    kept = table_shape.shape_only(
        {"columns": ["email"], "rows": [["a@b.com"]],
         "source": {"kind": "table", "binding": "b_1", "table": "T",
                    "rows": [["a@b.com"]], "sample": {"email": "a@b.com"}}},
        read_at=_AT,
    )
    assert kept["source"] == {"kind": "table", "binding": "b_1", "table": "T"}


def test_a_source_that_is_not_an_object_is_no_source_at_all():
    for junk in ("b_1", ["b_1"], 7, None):
        kept = table_shape.shape_only(
            {"columns": ["a"], "rows": [[1]], "source": junk}, read_at=_AT)
        assert "source" not in kept, junk


def test_a_source_naming_no_kind_is_dropped():
    """A card reads `kind` to know which read to run again. Without it the record identifies
    nothing, and half a record is worse than none: it puts a button on a card that cannot answer."""
    kept = table_shape.shape_only(
        {"columns": ["a"], "rows": [[1]], "source": {"binding": "b_1", "table": "T"}},
        read_at=_AT)
    assert "source" not in kept


def test_a_source_naming_no_binding_is_dropped():
    """#258: a card with no Binding gets no button. A Binding is what a different viewer re-reads
    through, so a source without one cannot be run again by anybody."""
    kept = table_shape.shape_only(
        {"columns": ["a"], "rows": [[1]], "source": {"kind": "table", "table": "T"}},
        read_at=_AT)
    assert "source" not in kept


def test_a_limit_that_is_not_a_whole_number_is_left_off_rather_than_written():
    kept = table_shape.shape_only(
        {"columns": ["a"], "rows": [[1]],
         "source": {"kind": "table", "binding": "b_1", "table": "T", "limit": True}},
        read_at=_AT)
    assert kept["source"] == {"kind": "table", "binding": "b_1", "table": "T"}


def test_a_receipt_read_back_keeps_the_source_it_already_had():
    """`shape_only` runs again over a file it already wrote (#253). The source is part of the
    shape, so the second pass must be the no-op the module promises it is."""
    once = table_shape.shape_only(
        {"columns": ["ID"], "rows": [[1]], "rowCount": 1,
         "source": {"kind": "table", "binding": "b_1", "table": "T"}}, read_at=_AT)
    assert table_shape.shape_only(once, read_at="2027-01-01T00:00:00Z") == once


def test_the_source_is_not_mistaken_for_the_frame(tmp_path=None):
    """The recovery ladder guesses a pandas dump from a dict of scalars, and `source` is one.

    Left alone it read a receipt back as a one-row table headed `kind`, `binding`, `table` — a card
    wearing the names of its own metadata, with the real column names thrown away. Both readers had
    the same hole: this is the Python half, and `store.js` keeps the same list.
    """
    columns, count = table_shape.columns_and_count({
        "title": "Calls", "columns": ["ID", "EMAIL"], "rows": [], "rowCount": 2,
        "readAt": _AT, "keptRows": False, "cap": 2, "truncated": True,
        "source": {"kind": "table", "binding": "b_1", "table": "T", "limit": 2},
    })

    assert columns == ["ID", "EMAIL"]
    assert count == 0


def test_the_renderer_knows_the_same_keys_the_writer_does():
    """`store.js` carries `_KEYS` by hand, and the two are pinned here rather than by convention.

    They are one list doing one job on two sides of a file: what the receipt owns, and therefore
    what the recovery ladder must not read as data. Kept equal only by somebody remembering, the
    next key added here mis-parses in the browser while every test on this side stays green — which
    is exactly how `source` arrived as a column in the first place.
    """
    js = (Path(__file__).resolve().parents[1] / "sage/workbench/js/store.js").read_text()
    listed = re.search(r"const RECEIPT_KEYS = \[(.*?)\];", js, re.DOTALL)
    assert listed, "store.js no longer declares RECEIPT_KEYS"

    assert re.findall(r"'([^']+)'", listed.group(1)) == list(table_shape._KEYS)


def test_a_source_naming_no_target_is_dropped():
    """A store and no table names nothing to read. It would put a button on a card whose press
    reaches the store with an empty name and comes back with the generic failure (#258)."""
    kept = table_shape.shape_only(
        {"columns": ["a"], "rows": [[1]], "source": {"kind": "table", "binding": "b_1"}},
        read_at=_AT)
    assert "source" not in kept


def test_a_real_frame_keeps_a_column_called_title():
    """The receipt-key strip applies to receipts and to nothing else. A column-oriented pandas dump
    with a column named `title` or `source` is somebody's data, and dropping it leaves the blank box
    over a correct heading that this whole ladder exists to prevent."""
    columns, count = table_shape.columns_and_count(
        {"title": {"0": "Dune", "1": "Emma"}, "source": {"0": "library", "1": "gift"}})

    assert columns == ["title", "source"]
    assert count == 2
