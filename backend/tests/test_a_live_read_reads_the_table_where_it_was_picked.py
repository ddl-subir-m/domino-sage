"""ADR-0041 — the read knows where the table is, so it does not ask the model.

The person picks a table off a card and the pick records a database and a schema. The read then
asked the MODEL to name both again, and the tool declaration told it to send null when it did not
need them — so a turn that named neither reached the warehouse as `SELECT * FROM ..GONG__CALLS`,
which no store will run. The failure came back as text, the assistant read it as an answer, and it
went off to query the same table in Python.

The recorded position is a default, not a fence: a model that names a level still gets it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sage.liveread import run


@dataclass
class FakeRows:
    columns: list
    rows: list


def turn_for(tmp_path, asked, **kw):
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
    return run.Turn(**base)


def read(tmp_path, args, **kw):
    asked: list = []
    run.perform("live_read_table", {"source": "DWH", **args}, turn_for(tmp_path, asked, **kw))
    return asked[0]


def test_a_table_named_alone_is_read_where_the_person_picked_it(tmp_path):
    assert read(tmp_path, {"table": "GONG__CALLS"},
                scope_for={("DWH", "GONG__CALLS"): ("WAREHOUSE", "MARTS")}) \
        == ("WAREHOUSE", "MARTS", "GONG__CALLS")


def test_a_store_with_one_recorded_position_lends_it_to_a_table_picked_at_none(tmp_path):
    # A second table in the same store, never itself pinned. Its neighbour's position is a better
    # guess than no position at all, which is a statement the store cannot parse.
    assert read(tmp_path, {"table": "STG_GONG__CALLS"},
                scope_for={("DWH", "GONG__CALLS"): ("WAREHOUSE", "MARTS"),
                           ("DWH", ""): ("WAREHOUSE", "MARTS")}) \
        == ("WAREHOUSE", "MARTS", "STG_GONG__CALLS")


def test_the_table_the_model_named_beats_the_store_default(tmp_path):
    assert read(tmp_path, {"table": "T"},
                scope_for={("DWH", "T"): ("WAREHOUSE", "RAW"),
                           ("DWH", ""): ("WAREHOUSE", "MARTS")}) \
        == ("WAREHOUSE", "RAW", "T")


def test_a_level_the_model_names_wins_over_the_recorded_one(tmp_path):
    # Not a fence: the same store holds fifteen schemas, and reaching one nobody pinned is a real
    # request rather than a mistake to correct.
    assert read(tmp_path, {"table": "T", "schema": "RAW"},
                scope_for={("DWH", ""): ("WAREHOUSE", "MARTS")}) \
        == ("WAREHOUSE", "RAW", "T")


def test_a_store_with_nothing_recorded_sends_what_it_was_given(tmp_path):
    # Unchanged, and deliberately: which levels a dialect needs is the provider's to know, and a
    # two-level store reads a bare table name correctly. A three-level one errors, and since the
    # read now says so out loud that error is readable instead of silent.
    assert read(tmp_path, {"table": "T"}) == ("", "", "T")
