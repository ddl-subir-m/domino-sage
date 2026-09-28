"""#603 — an over-budget `live_read_query` result hands the model the rows that fit, and its
statement goes to server diagnostics and nowhere else.

Measured live in #600: a result over `VALUES_BUDGET_CHARS` was withheld whole with "Group by fewer
things", and the turn answered that by paging `INFORMATION_SCHEMA.COLUMNS` in `ORDINAL_POSITION`
slices and listing tables with a `GROUP BY … COUNT(*)` that was `N = 1` on every row. And when a
count came back 0 nobody could say why, because only the statement's hash was ever recorded.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

from sage.liveread import result, run
from sage.liveread.data_use import DataUse
from sage.resources.provider import DataSource, ResourceUnavailable

from .test_a_chat_turn_works_a_number_out_in_sql import FakeAnswer, turn_for


def _snowflake(_name):
    return DataSource("dwh", "DWH", "SnowflakeConfig", "Shared", connector_type="SnowflakeConfig")


def _shown(said: str) -> tuple[int, int]:
    found = re.search(r"Result \(first (\d+) of (\d+) rows; the card has all \2\)", said)
    assert found, said
    return int(found.group(1)), int(found.group(2))


def test_a_catalogue_read_over_budget_hands_over_the_first_rows_and_never_says_group_by(tmp_path):
    rows = [["MARTS", f"TABLE_{i:04d}_" + "X" * 40] for i in range(500)]
    turn, recorded = turn_for(tmp_path, answer=FakeAnswer(["TABLE_SCHEMA", "TABLE_NAME"], rows),
                              source_for=_snowflake)
    said = run.perform("live_read_query", {
        "source": "DWH",
        "sql": "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES"}, turn)

    shown, total = _shown(said)
    assert total == 500 and 0 < shown < 500
    assert "TABLE_0000_" in said and "TABLE_0499_" not in said
    assert "group by" not in said.lower(), "GROUP BY on a catalogue read is what #600 measured"
    assert "WHERE on TABLE_NAME or TABLE_SCHEMA" in said
    event, reply = recorded[0]
    assert reply["selected"] == {"rows": rows[:shown]}, "the receipt carries only what was shown"
    assert len(json.dumps(reply["selected"]["rows"])) <= result.VALUES_BUDGET_CHARS
    assert event["disclosed_rows"] == reply["disclosed_rows"] == shown
    assert event["result_rows"] == 500
    assert event["role"] == "working", "being cut for the model is not falling short"


def test_an_aggregate_over_budget_hands_over_the_first_rows_and_names_a_filter_or_limit(tmp_path):
    rows = [[f"account-{i}", i] for i in range(500)]
    turn, recorded = turn_for(tmp_path, answer=FakeAnswer(["ACCOUNT", "N"], rows))
    said = run.perform("live_read_query", {
        "source": "DWH", "sql": "SELECT ACCOUNT, COUNT(*) AS N FROM E GROUP BY 1"}, turn)

    shown, total = _shown(said)
    assert total == 500 and 0 < shown < 500
    assert "'account-0'" in said and "'account-499'" not in said
    assert "filter" in said and "LIMIT" in said
    assert recorded[0][1]["selected"] == {"rows": rows[:shown]}


def test_one_row_wider_than_the_budget_is_still_withheld_with_the_repair(tmp_path):
    row = ["account-" + "x" * 20_000, 1]
    turn, recorded = turn_for(tmp_path, answer=FakeAnswer(["ACCOUNT", "N"], [row]))
    said = run.perform("live_read_query", {
        "source": "DWH", "sql": "SELECT ACCOUNT, COUNT(*) AS N FROM E GROUP BY 1"}, turn)

    assert "too large to read here" in said
    assert row[0] not in said
    assert "filter" in said and "LIMIT" in said
    assert recorded[0][1]["selected"] == {}
    assert recorded[0][0]["disclosed_rows"] == 0


def test_the_statement_reaches_the_server_log_capped_and_nothing_the_thread_keeps(tmp_path,
                                                                                    caplog):
    literal = "ops@northwind.example"
    sql = (f"SELECT COUNT(*) AS N FROM C WHERE EMAIL = '{literal}' AND NOTE <> '"
           + "y" * 3000 + "'")
    data, journal = DataUse(), []
    turn, _ = turn_for(
        tmp_path, keep_rows=True,
        record_data_use=lambda event, reply: data.record(event, reply, journal.append, "turn"))

    with caplog.at_level(logging.INFO, logger="sage.liveread"):
        said = run.perform("live_read_query", {"source": "DWH", "sql": sql}, turn)

    logged = [r.getMessage() for r in caplog.records if r.name == "sage.liveread"]
    line = next(m for m in logged if literal in m)
    assert sql[:2000] in line
    assert f"…(+{len(sql) - 2000} chars)" in line
    assert "y" * 3000 not in line, "capped"
    assert hashlib.sha256(sql.encode()).hexdigest() in line, "it joins to the data-use event"
    assert "rows=1" in line and "discloses=True" in line

    assert literal not in said, "the tool reply is Thread history"
    assert literal not in json.dumps(journal), "the persisted data-use event"
    written = sorted(p.name for p in (tmp_path / "examples" / "thr_q").iterdir())
    assert written == ["query-result.table.json"]
    assert literal not in (tmp_path / "examples" / "thr_q" / "query-result.table.json").read_text()


def test_a_statement_the_store_refused_is_logged_too(tmp_path, caplog):
    sql = "SELECT NOPE FROM E WHERE REGEXP_LIKE(TEXT, '\\barm\\b')"

    def boom(source, sql, limit):
        raise ResourceUnavailable("DWH did not answer: SQL compilation error: invalid identifier")

    turn, recorded = turn_for(tmp_path, run_statement=boom)
    with caplog.at_level(logging.INFO, logger="sage.liveread"):
        said = run.perform("live_read_query", {"source": "DWH", "sql": sql}, turn)

    assert "invalid identifier" in said
    assert any(sql in r.getMessage() and hashlib.sha256(sql.encode()).hexdigest() in r.getMessage()
               for r in caplog.records if r.name == "sage.liveread")
    assert sql not in said and not recorded
