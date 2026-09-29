"""`analyze_text` ties its judgments to accounts without a record id reaching the model (#606).

A mimo replay judged 40 cases and 102 transcripts, then could not say which customers asked: the
reply names each judgment by a task-local id, and the saved table held those same task ids, not the
case ids. The model tried to read the table with a shell, got the withheld placeholder back, copied
it three times, and the repeat guard ended the turn. `group_by` hands back what a SQL GROUP BY
would: label counts per account.
"""

import json
from dataclasses import replace

from sage.liveread import run
from sage.resources.provider import StatementRows

from .test_analyze_text_judges_the_rows_a_statement_chose import SOURCE, SQL
from .test_csv_text_analysis_data_used import _Warehouse, setup_turn, sse

CASES = [
    ["500A", "001A", "We need the agent to run on ARM Graviton nodes before we renew."],
    ["500B", "001B", "Their ARM (accounts receivable module) export is slow."],
    ["500C", "001A", "Is there an ARM64 build of the collector?"],
]


def _turn(tmp_path):
    def provider(request):
        body = json.loads(request["messages"][1]["content"])
        return sse(json.dumps({"records": [
            {"id": r["id"], "label": "casual_mention" if "receivable" in r["text"] else "genuine_ask"}
            for r in body["records"]]}))

    def run_statement(_source, _sql, *, limit, cell_limit=80):
        return StatementRows(["CASE_ID", "ACCOUNT_ID", "DESCRIPTION"], CASES[:limit], False)

    turn, _data, journal, _ = setup_turn(tmp_path, provider=provider)
    turn = replace(turn, upload_for=lambda _path: None, bound={"datasource": (SOURCE,)},
                   source_for=lambda name: _Warehouse() if name == SOURCE else None,
                   run_statement=run_statement)
    return turn, journal


def _args(**over):
    return {"operation": "analyze_text", "source": SOURCE, "sql": SQL,
            "text_column": "DESCRIPTION", "id_column": "CASE_ID",
            "labels": ["genuine_ask", "casual_mention"], **over}


def test_judgments_come_back_counted_per_account(tmp_path):
    turn, journal = _turn(tmp_path)
    said = run.perform("live_read_table", _args(group_by="ACCOUNT_ID"), turn)
    reply = json.loads(said)

    assert reply["selected"] == {"groups": [["001A", "genuine_ask", 2],
                                            ["001B", "casual_mention", 1]],
                                 "total_groups": 2}
    assert reply["selected_fields"] == ["ACCOUNT_ID", "label", "records"]
    for case_id, _account, text in CASES:
        assert case_id not in said and text not in said
        assert case_id not in json.dumps(journal)


def test_the_saved_table_keeps_each_records_real_id(tmp_path):
    turn, _journal = _turn(tmp_path)
    said = run.perform("live_read_table", _args(group_by="ACCOUNT_ID"), turn)
    table = json.loads((tmp_path / json.loads(said)["local_reference"]).read_text())
    assert table["columns"] == ["Record ID", "ACCOUNT_ID", "label"]
    assert table["rows"] == [["500A", "001A", "genuine_ask"], ["500B", "001B", "casual_mention"],
                             ["500C", "001A", "genuine_ask"]]


def test_without_a_group_the_reply_still_names_task_ids_only(tmp_path):
    turn, _journal = _turn(tmp_path)
    said = run.perform("live_read_table", _args(), turn)
    reply = json.loads(said)
    assert [row[0] for row in reply["selected"]["rows"]] == ["r000001", "r000002", "r000003"]
    assert "500A" not in said
    table = json.loads((tmp_path / reply["local_reference"]).read_text())
    assert [row[0] for row in table["rows"]] == ["500A", "500B", "500C"]


def test_a_group_needs_labels_and_a_selected_column(tmp_path):
    turn, _journal = _turn(tmp_path)
    no_labels = run.perform("live_read_table", _args(group_by="ACCOUNT_ID", labels=None), turn)
    assert "needs labels" in no_labels
    missing = run.perform("live_read_table", _args(group_by="OWNER_ID"), turn)
    assert "no OWNER_ID column" in missing
