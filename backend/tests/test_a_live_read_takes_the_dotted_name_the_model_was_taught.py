"""ADR-0041 — the model names a table the way we taught it to.

MEASURED on 2026-09-09, in production, one turn after the failure log went in:

    live read: live_read_table failed — ValueError: Sage will not send
    'DWH.MARTS.ANTHROPIC__API_KEY_DAILY_USAGE' to a database as a name.

The model put the whole dotted path in `table`. It had every reason to: the Chat context line names
that table `DWH.MARTS.ANTHROPIC__API_KEY_DAILY_USAGE` and hands it a `SELECT * FROM` over the same
string. Our prose disagreed with our own argument, and the argument lost.

`safe_identifier` refused, correctly — it is an allowlist in front of a credential that reads the
whole warehouse. So the name comes apart above it, and every piece still goes through it singly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sage.liveread import run


@dataclass
class FakeRows:
    columns: list
    rows: list


def read(tmp_path, args, **kw):
    asked: list = []
    seen = object()

    def sample_rows(source, database, schema, table, limit):
        asked.append((database, schema, table))
        return FakeRows(["ID"], [[1]])

    base = {
        "thread_id": "thr_a",
        "examples_dir": tmp_path / "examples" / "thr_a",
        "bound": {"datasource": ("DWH",)},
        "source_for": lambda n: seen if n == "DWH" else None,
        "sample_rows": sample_rows,
    }
    base.update(kw)
    said = run.perform("live_read_table", {"source": "DWH", **args}, run.Turn(**base))
    return asked[0], said


def test_the_dotted_name_from_the_context_line_is_taken_apart(tmp_path):
    asked, _ = read(tmp_path, {"table": "DWH.MARTS.ANTHROPIC__API_KEY_DAILY_USAGE"})
    assert asked == ("DWH", "MARTS", "ANTHROPIC__API_KEY_DAILY_USAGE")


def test_two_parts_are_a_schema_and_a_table(tmp_path):
    # What a store with no database level is named by, and what the second `_SAMPLE` spelling reads.
    asked, _ = read(tmp_path, {"table": "public.events"})
    assert asked == ("", "public", "events")


def test_a_level_missing_from_a_two_part_name_still_comes_from_the_record(tmp_path):
    asked, _ = read(tmp_path, {"table": "MARTS.SALES"},
                    scope_for={("DWH", ""): ("WAREHOUSE", "RAW")})
    assert asked == ("WAREHOUSE", "MARTS", "SALES"), "the name it gave wins, the rest is filled"


def test_the_path_the_model_wrote_beats_a_level_it_named_beside_it(tmp_path):
    # A model that wrote the whole path meant that path. Two disagreeing levels are not a case to
    # split down the middle.
    asked, _ = read(tmp_path, {"table": "DWH.MARTS.SALES", "database": "OTHER", "schema": "RAW"})
    assert asked == ("DWH", "MARTS", "SALES")


def test_a_plain_name_is_left_exactly_as_it_is(tmp_path):
    asked, _ = read(tmp_path, {"table": "SALES", "database": "DWH", "schema": "MARTS"})
    assert asked == ("DWH", "MARTS", "SALES")


def test_a_name_this_cannot_read_goes_down_whole_to_be_refused(tmp_path):
    # Four levels, or an empty one. Splitting either would drop a level in silence, and the store's
    # own refusal names the string — which is how this bug was found in a single turn.
    assert read(tmp_path, {"table": "a.b.c.d"})[0] == ("", "", "a.b.c.d")
    assert read(tmp_path, {"table": "DWH..SALES"})[0] == ("", "", "DWH..SALES")


def test_the_card_is_named_for_the_table_and_not_for_the_path(tmp_path):
    """The slug comes from the table's last level, so `DWH.MARTS.SALES` lands as `sales`.

    Asserted on the file itself rather than through the sentence the assistant reads. It used to
    be checked the other way, which worked only because that sentence named the path — and naming
    it is the thing that let a `read` fetch the rows back out of the card (see
    `test_a_live_read_reaches_the_person_end_to_end`). The slug is a property of the file; test it
    where it lives.
    """
    _, said = read(tmp_path, {"table": "DWH.MARTS.SALES"})
    assert (tmp_path / "examples" / "thr_a" / "sales.table.json").is_file()
    assert "examples/thr_a" not in said and ".table.json" not in said
    card = json.loads((tmp_path / "examples" / "thr_a" / "sales.table.json").read_text())
    assert card["title"] == "SALES"
