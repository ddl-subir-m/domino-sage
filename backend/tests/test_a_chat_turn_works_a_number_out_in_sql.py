"""ADR-0058 — the tool itself. The read-only lane composes SQL and Sage runs it (#408, #402).

The hole this closes, measured live on `cd9fdd9` and not inherited from the ticket: asked for
`COUNT(*)` over one table, a read-only turn read one row of 106 columns, composed Python on a lane
with no shell, then read the sample's own `.table.json` three times until the repeat guard killed it
— `ok=false`, `decision="repeated"`, 61s, eight model calls. The artifact lane dies differently on
the same cause: `decision="table generation failed"` (#402). Two deaths, one hole.

Held here, in order of how badly each would fail if it broke:

- the numbers reach the model and the statement is what did the work, not the model's prose;
- a stored value does not, on a path that runs for real rather than in `disclosure`'s unit tests;
- NOTHING WRITES THE STATEMENT DOWN — the card has no `.sql` beside it and the record carries only
  a hash, because a predicate carries literals and `examples/` is committed;
- a store that objected is quoted, and a statement Sage stopped waiting for is not dressed up as
  the store having refused.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import pytest

from sage.liveread import run
from sage.resources.provider import ResourceUnavailable, StatementTimeout


@dataclass
class FakeAnswer:
    columns: list
    rows: list
    truncated: bool = False


def turn_for(tmp_path, answer=None, **kw):
    """A turn that can run one statement."""
    recorded: list[tuple[dict, dict]] = []
    base = {
        "thread_id": "thr_q",
        "examples_dir": tmp_path / "examples" / "thr_q",
        "bound": {"datasource": ("DWH",)},
        "source_for": lambda n: object() if n == "DWH" else None,
        "run_statement": lambda s, sql, limit: answer or FakeAnswer(["N"], [[41234]]),
        "record_data_use": lambda event, reply: recorded.append((event, reply)),
        "binding_for": {("datasource", "DWH"): "bnd_1"},
    }
    base.update(kw)
    turn = run.Turn(**base)
    return turn, recorded


def _run(turn, sql, **args):
    return run.perform("live_read_query", {"source": "DWH", "sql": sql, **args}, turn)


# --- the answer the lane could not give ---------------------------------------------------------


def test_the_count_that_the_lane_could_not_answer_comes_back_as_a_number(tmp_path):
    """#408's own question, end to end. The computation is IN THE STATEMENT, not in the model's
    prose: the tool is handed SQL and hands back the number the store worked out."""
    turn, recorded = turn_for(tmp_path, keep_rows=True,
                              answer=FakeAnswer(["EVENTS", "USERS"], [[41234, 812]]))
    said = _run(turn, "SELECT COUNT(*) AS EVENTS, COUNT(DISTINCT USER_ID) AS USERS FROM E")

    assert "41234" in said and "812" in said
    assert "Columns: EVENTS, USERS" in said
    card = json.loads((tmp_path / "examples" / "thr_q" / "query-result.table.json").read_text())
    assert card["rows"] == [[41234, 812]], "the person sees it too"
    assert recorded, "a result that reached the model with no record is the second unaudited route"


def test_a_group_by_label_reaches_the_model_with_its_number(tmp_path):
    """ADR-0058 admits the label deliberately — a chart needs exactly this pair per bar."""
    turn, _ = turn_for(tmp_path, keep_rows=True,
                       answer=FakeAnswer(["ACCOUNT", "N"], [["Northwind", 412], ["Bluebird", 208]]))
    said = _run(turn, "SELECT ACCOUNT, COUNT(*) AS N FROM E GROUP BY 1")
    assert "Northwind" in said and "412" in said


def test_a_stored_value_stays_on_the_card_and_the_model_is_told_which_column(tmp_path):
    """The reply has to name the column, or the model can only apologise. It composed this
    statement; told what is wrong with it, it can compose another."""
    turn, recorded = turn_for(tmp_path, keep_rows=True,
                              answer=FakeAnswer(["E"], [["ops@northwind.example"]]))
    said = _run(turn, "SELECT MAX(EMAIL) AS E FROM CUSTOMERS")

    assert "ops@northwind.example" not in said, "the value must not reach the assistant"
    assert "MAX(EMAIL)" in said
    assert "never quote a value" in said
    card = json.loads((tmp_path / "examples" / "thr_q" / "query-result.table.json").read_text())
    assert card["rows"] == [["ops@northwind.example"]], "and the person still has the answer"
    assert recorded, "the read happened whether or not its values were disclosed"
    assert recorded[0][1]["selected"] == {}, "nothing selected reaches the model"


def test_a_result_too_large_to_read_is_refused_with_the_repair(tmp_path):
    """The same budget and the same advice `calculate` gives, because it is the same problem: a
    grouped answer with too many groups."""
    rows = [[f"account-{i}", i] for i in range(500)]
    turn, _ = turn_for(tmp_path, answer=FakeAnswer(["ACCOUNT", "N"], rows))
    said = _run(turn, "SELECT ACCOUNT, COUNT(*) AS N FROM E GROUP BY 1")
    assert "too large to read here" in said
    assert "account-0" not in said


# --- the statement is never written down --------------------------------------------------------


def test_no_sql_sidecar_is_ever_written_beside_the_card(tmp_path):
    """THE ONE THAT MATTERS MOST HERE.

    `result.record` can write a `<slug>.sql` and commits it in BOTH Kept-rows states — a rule that
    was correct while the only statement was `sample_rows`', which takes a table and a limit and
    cannot filter. This tool's statement carries literals, and `examples/` is committed and pushed,
    on the turn that ran it, to a host nobody named. So `WHERE EMAIL = '…'` in a sidecar is a row
    value in the Project's git history, which is exactly what ADR-0041 refuses.

    Kept rows does NOT govern this and must not be made to. That setting is about rows; a literal in
    a predicate is disclosed by the statement whatever it says — which is why this asserts under
    `keep_rows=True`, the setting most likely to tempt someone into making the sidecar conditional.
    """
    turn, _ = turn_for(tmp_path, keep_rows=True)
    _run(turn, "SELECT COUNT(*) AS N FROM C WHERE EMAIL = 'ops@northwind.example'")

    written = sorted(p.name for p in (tmp_path / "examples" / "thr_q").iterdir())
    assert written == ["query-result.table.json"], f"nothing but the card may be written: {written}"
    card = (tmp_path / "examples" / "thr_q" / "query-result.table.json").read_text()
    assert "ops@northwind.example" not in card, "the predicate's literal must not reach the file"


def test_the_record_carries_the_statements_hash_and_not_the_statement(tmp_path):
    """The `data_use` event is persisted into the Thread's history, which is committed. Same
    hazard as the sidecar, same answer — and the same one `calculate` already uses for the CSV
    bytes it read."""
    sql = "SELECT COUNT(*) AS N FROM C WHERE EMAIL = 'ops@northwind.example'"
    turn, recorded = turn_for(tmp_path)
    _run(turn, sql)

    event, _ = recorded[0]
    assert event["source_sha256"] == hashlib.sha256(sql.encode()).hexdigest()
    assert "ops@northwind.example" not in json.dumps(event)
    assert "SELECT" not in json.dumps(event)


# --- the grant, and the two ways a store can fail ------------------------------------------------


def test_a_source_that_is_not_in_this_conversation_is_refused_by_name(tmp_path):
    turn, _ = turn_for(tmp_path, bound={"datasource": ()})
    said = _run(turn, "SELECT COUNT(*) FROM E")
    assert "isn't in this conversation" in said


@pytest.mark.parametrize("missing", ["source_for", "run_statement"])
def test_a_source_in_range_that_cannot_be_opened_is_refused_before_anything_runs(tmp_path, missing):
    """Passing the grant is not the same as being reachable, and both halves are checked.

    The grant asks whether this Conversation NAMES the source; resolving it asks whether Sage can
    open it. A turn that passed the first and fell through the second would call `run_statement`
    with `None` for a source, which is a driver error wearing a person's question. `run_statement`
    is checked beside it because the two are one condition — there is no turn that can resolve a
    source and has no way to query it, and a `None` there would be the same crash one line later.
    """
    turn, recorded = turn_for(tmp_path, **{missing: None})
    said = _run(turn, "SELECT COUNT(*) FROM E")
    assert "could not find DWH" in said
    assert not recorded


def test_a_store_that_objected_is_quoted_rather_than_reported_as_impossible(tmp_path):
    """#399 had to be reopened once to make this distinction. Feature reach is not uniform across
    stores — another warehouse will refuse `CORR` — and the store's own words are the only thing
    that separates a wrong dialect from an empty answer."""
    def boom(source, sql, limit):
        raise ResourceUnavailable("DWH did not answer: SQL compilation error: invalid identifier")

    turn, _ = turn_for(tmp_path, run_statement=boom)
    said = _run(turn, "SELECT NOPE FROM E")
    assert "invalid identifier" in said


def test_a_statement_sage_stopped_waiting_for_does_not_read_as_a_refusal(tmp_path):
    """Sage cannot ask the store to stop — `domino_data` takes no timeout — so this is abandonment
    and the sentence must not imply the store said anything. It very likely did not; it is still
    working."""
    def slow(source, sql, limit):
        raise StatementTimeout("That query was still running after 120 seconds, so Sage stopped "
                               "waiting for it. DWH may still be working on it.")

    turn, _ = turn_for(tmp_path, run_statement=slow)
    said = _run(turn, "SELECT COUNT(*) FROM HUGE")
    assert "stopped waiting" in said
    assert "did not answer" not in said


def test_a_missing_statement_says_so_rather_than_running_nothing(tmp_path):
    turn, _ = turn_for(tmp_path)
    assert "Send the statement" in run.perform("live_read_query", {"source": "DWH"}, turn)


# --- the shape the card and the record agree on -------------------------------------------------


def test_a_truncated_result_says_so_to_the_model_and_in_the_record(tmp_path):
    """A result cut at the cap is a fact the reader is owed, not a silence (ADR-0029).

    The CARD's own `truncated` flag is not asserted here, and the reason is worth writing down
    rather than leaving as a gap: `result.record` writes `cap` and `truncated` into the file only on
    the shape-only path, so a Kept-rows card carries `{title, columns, rows}` and nothing else. That
    is existing behaviour shared with every Live read, not something this tool decides, and the
    shape-only half is covered in the test below.
    """
    turn, recorded = turn_for(tmp_path, keep_rows=True, answer=FakeAnswer(
        ["ACCOUNT", "N"], [["a", 3], ["b", 2]], truncated=True))
    said = _run(turn, "SELECT ACCOUNT, COUNT(*) AS N FROM E GROUP BY 1")

    assert "there are more" in said
    assert recorded[0][0]["coverage"]["unfinished"] == 1


def test_a_shape_only_card_carries_the_truncation_it_was_cut_at(tmp_path):
    turn, _ = turn_for(tmp_path, keep_rows=False, answer=FakeAnswer(
        ["ACCOUNT", "N"], [["a", 3]], truncated=True))
    _run(turn, "SELECT ACCOUNT, COUNT(*) AS N FROM E GROUP BY 1")

    card = json.loads((tmp_path / "examples" / "thr_q" / "query-result.table.json").read_text())
    assert card["truncated"] is True
    assert card["cap"] == 500


def test_a_project_that_keeps_no_rows_gets_a_shape_card_and_the_model_still_gets_the_number(
        tmp_path):
    """ADR-0045 and ADR-0058 answer different questions and this is where that shows.

    With Kept rows off the card carries the shape, so the person sees no number — and the model is
    still handed it, because what the FILE keeps and what the MODEL may see were never the same
    decision. The model's answer is then the only place the number appears, and it is allowed to
    say it.
    """
    turn, _ = turn_for(tmp_path, keep_rows=False)
    said = _run(turn, "SELECT COUNT(*) AS N FROM E")

    assert "41234" in said
    assert "shape" in said
    card = json.loads((tmp_path / "examples" / "thr_q" / "query-result.table.json").read_text())
    assert card["rows"] == [], "the Project keeps no data rows in its files"


def test_an_unknown_tool_name_is_still_refused(tmp_path):
    turn, _ = turn_for(tmp_path)
    with pytest.raises(ValueError):
        run.perform("live_read_nonsense", {}, turn)
