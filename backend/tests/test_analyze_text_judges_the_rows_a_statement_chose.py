"""#602 — `analyze_text` over a Data Source judges the rows ONE statement chose, not a whole table.

A person asked Chat to judge which SFDC cases and Gong transcripts mention ARM seriously. The model
could count the candidates with `live_read_query` and could not hand them to a model: the table
form of `analyze_text` read the first 10,001 rows of one table with no `WHERE` and no join. The
`sql` argument names the candidates, Sage runs it, and only the text column goes to the judging
model through the Gateway. The chat model still never sees a row (ADR-0041), and the statement is
recorded as a hash, never as text, because a literal in a `WHERE` is a row value.
"""

import hashlib
import json
from dataclasses import replace

from sage.liveread import run
from sage.liveread.text_analysis import MAX_RECORDS
from sage.resources.provider import ResourceUnavailable, StatementRows

from .test_csv_text_analysis_data_used import _Warehouse, setup_turn, sse

SOURCE = "Snowflake-Data-Warehouse"
SQL = ("SELECT c.CASE_ID, c.DESCRIPTION FROM DWH.MARTS.SFDC__CASE c "
       "JOIN DWH.MARTS.SFDC__ACCOUNT a ON a.ID = c.ACCOUNT_ID "
       "WHERE a.IS_ACTIVE AND c.DESCRIPTION ILIKE '%ARM%'")
CASES = [
    ["500A", "We need the agent to run on ARM Graviton nodes before we renew."],
    ["500B", "Their ARM (accounts receivable module) export is slow."],
]


def _turn(tmp_path, rows, calls, asked):
    def provider(request):
        calls.append(request)
        body = json.loads(request["messages"][1]["content"])
        return sse(json.dumps({"records": [{"id": r["id"], "label": "genuine_ask"}
                                           for r in body["records"]]}))

    def run_statement(_source, sql, *, limit, cell_limit=80):
        asked.append((sql, limit, cell_limit))
        return StatementRows(["CASE_ID", "DESCRIPTION"], rows[:limit], len(rows) > limit)

    turn, data, journal, _source = setup_turn(tmp_path, provider=provider)
    turn = replace(turn, upload_for=lambda _path: None, bound={"datasource": (SOURCE,)},
                   source_for=lambda name: _Warehouse() if name == SOURCE else None,
                   run_statement=run_statement)
    return turn, data, journal


def _args(**over):
    return {"operation": "analyze_text", "source": SOURCE, "sql": SQL,
            "text_column": "DESCRIPTION", "id_column": "CASE_ID",
            "labels": ["genuine_ask", "casual_mention"], "purpose": "Genuine asks for ARM", **over}


def test_the_statement_names_the_rows_and_only_their_text_reaches_the_judge(tmp_path):
    calls, asked = [], []
    turn, data, journal = _turn(tmp_path, CASES, calls, asked)

    said = run.perform("live_read_table", _args(), turn)
    reply = json.loads(said)

    assert asked == [(SQL, MAX_RECORDS + 1, 2000)]
    assert reply["coverage"]["total"] == 2
    assert reply["coverage"]["processed"] == 2
    assert reply["selected"]["rows"] == [["r000001", "genuine_ask"], ["r000002", "genuine_ask"]]
    judged = [r["text"] for r in json.loads(calls[0]["messages"][1]["content"])["records"]]
    assert judged == [CASES[0][1], CASES[1][1]]
    # The chat model reads `said`; the Thread's history keeps the journal. Neither holds a row
    # or the statement, whose `'%ARM%'` is the kind of literal that is a row value.
    for text in (CASES[0][1], CASES[1][1], "500A", SQL, "'%ARM%'"):
        assert text not in said
        assert text not in json.dumps(journal)
    event = data.events("turn1")[0]
    assert event["statement_sha256"] == hashlib.sha256(SQL.encode()).hexdigest()
    assert SQL not in json.dumps(event)


def test_a_statement_needs_no_table(tmp_path):
    calls, asked = [], []
    turn, _data, _journal = _turn(tmp_path, CASES, calls, asked)

    said = run.perform("live_read_table", _args(), turn)

    assert "attached without a table" not in said
    assert "Name the table" not in said
    assert json.loads(said)["coverage"]["processed"] == 2


def test_a_statement_over_the_limit_is_refused_with_the_fix(tmp_path):
    calls, asked = [], []
    rows = [[f"C{i}", f"ARM mention {i}"] for i in range(MAX_RECORDS + 5)]
    turn, _data, journal = _turn(tmp_path, rows, calls, asked)

    said = run.perform("live_read_table", _args(), turn)

    assert f"more than {MAX_RECORDS}" in said
    assert "WHERE" in said
    assert calls == []
    assert journal == []


def test_a_store_with_no_current_database_gets_the_repair_not_use_database(tmp_path):
    def run_statement(*_args, **_kwargs):
        raise ResourceUnavailable(
            f"{SOURCE} did not answer: Statement does not have a current database. "
            "Call 'USE DATABASE'.")

    turn, _data, _journal = _turn(tmp_path, CASES, [], [])
    turn = replace(turn, run_statement=run_statement)

    said = run.perform("live_read_table", _args(), turn)

    assert said.startswith(f"{SOURCE} did not answer")
    assert "SHOW DATABASES" in said
    assert "Call 'USE DATABASE'" not in said
