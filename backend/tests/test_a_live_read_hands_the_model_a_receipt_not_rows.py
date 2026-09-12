"""ADR-0041, "A Live read reaches the person without reaching the model".

A person with a Data Source bound asked, in Build, "show me 1 sample conversation". Sage answered
that only the running app could query it, and then that it had no row "in the project text" to
quote. Both are dead ends, and the second narrates the mechanism.

The fix is not a shell. A row read into an answer enters Recall, and Recall goes to whatever model
the session runs on — so on a Vendor-backed Alias the row leaves Domino, which is a decision that
belongs to the creator and had never been asked for. `SampleRows` already says so in as many words:
putting rows in a model's context "belongs to the person who knows what is in the table — never to
a rule that inferred it would be helpful".

So the rows go to the Artifact the person sees, and the assistant is handed a receipt. These tests
hold that seam, the grant that governs it, and the two ways a result can be short.
"""

from __future__ import annotations

import json

from sage.liveread import grant, result


def test_the_artifact_holds_the_rows_and_the_receipt_holds_only_their_shape(tmp_path):
    # `keep_rows` because this is the claim about the split between the card and the receipt, and
    # a Project that keeps no rows has no card rows to split off (ADR-0045).
    r = result.record(
        tmp_path / "examples" / "thr_abc", "sample-conversation", "Sample conversation",
        ["ID", "TITLE"], [[1, "Acme <> Domino"]], keep_rows=True,
    )

    assert r.columns == ["ID", "TITLE"]
    assert r.rows == 1
    assert r.values is None, "nothing was shared, so the assistant is told the shape and no more"
    assert r.path == "examples/thr_abc/sample-conversation.table.json"

    on_disk = json.loads((tmp_path / "examples" / "thr_abc" / "sample-conversation.table.json").read_text())
    assert on_disk["rows"] == [[1, "Acme <> Domino"]], "the person's card holds the real row"


def test_the_table_artifact_is_written_in_the_shape_the_workbench_reads(tmp_path):
    # `{title, columns, rows}` with rows as positional arrays. A bare array of objects renders as
    # "No data" on a card, which is the failure Chat's own instructions spell out at length.
    result.record(tmp_path / "examples" / "thr_abc", "t", "Sample conversation", ["A"], [[1]],
                  keep_rows=True)
    on_disk = json.loads((tmp_path / "examples" / "thr_abc" / "t.table.json").read_text())

    assert sorted(on_disk) == ["columns", "rows", "title"]
    assert on_disk["title"] == "Sample conversation"
    assert on_disk["rows"] and all(isinstance(row, list) for row in on_disk["rows"])


def test_the_assistant_sees_values_only_where_the_creator_already_shared_that_table(tmp_path):
    shared = (("bnd_1", "GONG__CALLS"),)

    seen = result.record(
        tmp_path / "e" / "thr_a", "s", "t", ["ID"], [[1]],
        binding="bnd_1", table="GONG__CALLS", shared=shared,
    )
    unseen = result.record(
        tmp_path / "e" / "thr_a", "s2", "t", ["ID"], [[1]],
        binding="bnd_1", table="STG_GONG__CALLS", shared=shared,
    )

    assert seen.values == [[1]], "the creator put this table's rows in front of the agent already"
    assert unseen.values is None, "a neighbouring table is a table nobody shared"


def test_a_sample_shared_before_bindings_were_recorded_still_matches_by_table_name():
    # `bound_schema.parse_samples` reads an entry written before #33 back with an empty binding, and
    # the caller resolves it. Being stricter here would silently withdraw a share the creator made.
    assert grant.values_allowed("bnd_1", "GONG__CALLS", shared=(("", "GONG__CALLS"),))
    assert not grant.values_allowed("bnd_1", "GONG__CALLS", shared=(("bnd_2", "GONG__CALLS"),))


def test_an_unreadable_share_record_shows_the_assistant_nothing():
    assert not grant.values_allowed("bnd_1", "GONG__CALLS", shared=())
    assert not grant.values_allowed("bnd_1", "", shared=(("bnd_1", ""),))


def test_a_result_over_the_cap_is_cut_and_says_so(tmp_path):
    r = result.record(tmp_path / "e" / "thr_a", "s", "t", ["N"], [[i] for i in range(12)], cap=5,
                      keep_rows=True)

    assert r.rows == 5 and r.truncated is True and r.cap == 5
    assert len(json.loads((tmp_path / "e" / "thr_a" / "s.table.json").read_text())["rows"]) == 5


def test_a_result_that_exactly_fills_the_cap_is_not_truncated(tmp_path):
    r = result.record(tmp_path / "e" / "thr_a", "s", "t", ["N"], [[i] for i in range(5)], cap=5)
    assert r.rows == 5 and r.truncated is False


def test_a_reader_that_stopped_short_upstream_still_reads_as_short(tmp_path):
    # A capped Dataset listing arrives already truncated. Being short twice must read as short once.
    r = result.record(tmp_path / "e" / "thr_a", "s", "t", ["N"], [[1]], cap=5, truncated=True)
    assert r.rows == 1 and r.truncated is True


def test_the_statement_lands_beside_the_result(tmp_path):
    r = result.record(
        tmp_path / "e" / "thr_a", "s", "t", ["N"], [[1]], statement="SELECT * FROM T LIMIT 1",
    )
    assert r.statement == "examples/thr_a/s.sql"
    assert (tmp_path / "e" / "thr_a" / "s.sql").read_text() == "SELECT * FROM T LIMIT 1\n"


def test_no_statement_leaves_no_file_behind(tmp_path):
    r = result.record(tmp_path / "e" / "thr_a", "s", "t", ["N"], [[1]])
    assert r.statement is None
    assert not (tmp_path / "e" / "thr_a" / "s.sql").exists()


def test_a_model_api_is_called_and_so_is_never_read():
    refused = grant.reachable("modelapi", "churn-scorer", chips=["churn-scorer"])
    assert refused is not None and refused.tag == "not-readable"
    assert "Model API" in refused.says
    assert grant.reachable("llmalias", "gpt-5.4", chips=["gpt-5.4"]) is not None


def test_a_data_source_the_conversation_never_named_is_refused_by_name_and_act():
    refused = grant.reachable("datasource", "Snowflake-Data-Warehouse")

    assert refused is not None and refused.tag == "not-in-range"
    assert "Snowflake-Data-Warehouse" in refused.says, "name the missing thing"
    assert "Use it in this conversation" in refused.says, "and the act that fixes it"
    for mechanism in ("blocked", "unable", "read-only", "tool", "project text"):
        assert mechanism not in refused.says.lower()


def test_a_binding_or_a_chip_puts_a_thing_in_range():
    assert grant.reachable("datasource", "DWH", bound=["DWH"]) is None
    assert grant.reachable("dataset", "gong-exports", chips=["gong-exports"]) is None
    assert grant.reachable("upload", "notes.csv", chips=["notes.csv"]) is None
    # The Working set is orientation and never context (ADR-0020), so it grants nothing here.
    assert grant.reachable("datasource", "DWH", bound=[], chips=[]) is not None


def test_the_card_keeps_every_row_but_the_model_only_gets_what_it_can_afford(tmp_path):
    """Measured against the real `MARTS.GONG__CALLS`: 31 columns, ~890 characters a row.

    Handing the model the card's 500-row cap would have put ~112,000 tokens in one prompt — on the
    one path allowed to carry real values, arriving silently. The card and the model are different
    budgets, so they are two numbers.
    """
    wide = [[f"value-{i}-{c}" for c in range(31)] for i in range(500)]
    shared = (("bnd_1", "GONG__CALLS"),)

    r = result.record(tmp_path / "e" / "t", "s", "t", [f"C{c}" for c in range(31)], wide,
                      binding="bnd_1", table="GONG__CALLS", shared=shared)

    assert r.rows == 500, "the person's card keeps everything that was read"
    assert 0 < len(r.values) < 500, "the model does not"
    assert len(str(r.values)) <= result.VALUES_BUDGET_CHARS + len(str(wide[0]))


def test_a_narrow_result_reaches_the_model_whole(tmp_path):
    # The budget must not cost anything on the ordinary case, which is a handful of small rows.
    r = result.record(tmp_path / "e" / "t", "s", "t", ["ID"], [[i] for i in range(5)],
                      binding="b", table="T", shared=(("b", "T"),))
    assert r.values == [[0], [1], [2], [3], [4]]


def test_one_row_over_budget_still_reaches_the_model(tmp_path):
    # An escape hatch that answers nothing on exactly the wide tables somebody would ask about is
    # not an escape hatch. One row always goes.
    huge = [["x" * (result.VALUES_BUDGET_CHARS * 2)]]
    r = result.record(tmp_path / "e" / "t", "s", "t", ["BLOB"], huge,
                      binding="b", table="T", shared=(("b", "T"),))
    assert len(r.values) == 1
