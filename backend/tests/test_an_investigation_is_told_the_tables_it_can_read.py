"""An open investigation is handed its Data Source's table names, not left to guess them (#606).

A mimo replay spent most of its 25 live reads guessing schema names (`ANALYTICS`, `SFDC`, `GONG`)
and hit the ceiling with no answer. The names are metadata Sage reads itself, once per database.
"""
from pathlib import Path

from sage.orchestrator.service import _catalogue_note
from sage.resources.bindings import KIND_DATA_SOURCE, Binding
from sage.resources.table_search import Candidate

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, _orch


def _investigating(tmp_path: Path, turns: int = 1):
    orch, oc = _orch(tmp_path, [Turn(text="Answered.") for _ in range(turns)],
                     gateway=IntentGateway({"label": "data_answer", "confidence": 0.93}))
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "data_source", "name": "Snowflake-Data-Warehouse",
        "resourceId": "data_source:ds-dwh",
    })
    return orch, oc, tid


def test_an_open_investigation_is_told_the_tables_its_source_holds(tmp_path: Path):
    orch, oc, tid = _investigating(tmp_path)
    orch.decide_thread_investigation(tid, "open")
    list(orch.chat_stream(tid, "Which accounts have the most daily usage?"))
    prompt = oc.prompts[-1]["text"]

    assert "- DWH.MARTS: 4 tables" in prompt
    assert "- DWH.REPORTING: 2 tables" in prompt
    assert "- SANDBOX.PUBLIC: 1 table\n" in prompt
    matches = prompt.split("share a word with the question:", 1)[1].split("Any other table", 1)[0]
    assert "- DWH.MARTS.FCT_USAGE_DAILY" in matches
    assert "- DWH.MARTS.DIM_ACCOUNT" in matches
    assert "DIM_DATE" not in matches
    row = next(line for line in prompt.splitlines()
               if line.startswith("- Data Source Snowflake-Data-Warehouse"))
    assert "its tables are listed below" in row
    assert "Discover them before you answer" not in row


def test_a_source_that_could_not_be_listed_is_still_told_to_discover(tmp_path: Path):
    orch, oc, tid = _investigating(tmp_path)
    orch.add_thread_context(tid, {
        "kind": "data_source", "name": "billing-oracle", "resourceId": "data_source:ds-oracle",
    })
    orch.decide_thread_investigation(tid, "open")
    list(orch.chat_stream(tid, "Which accounts have the most daily usage?"))
    prompt = oc.prompts[-1]["text"]
    rows = {line.split(".", 1)[0]: line for line in prompt.splitlines()
            if line.startswith("- Data Source ")}
    assert "its tables are listed below" in rows["- Data Source Snowflake-Data-Warehouse"]
    assert "Discover them before you answer" in rows["- Data Source billing-oracle"]
    assert "Tables in billing-oracle" not in prompt


def test_the_catalogue_is_read_once_a_session(tmp_path: Path):
    orch, oc, tid = _investigating(tmp_path, turns=2)
    orch.decide_thread_investigation(tid, "open")
    resources = orch._resources
    read, calls = resources.list_database_tables, []

    def counted(source, database):
        calls.append(database)
        return read(source, database)

    resources.list_database_tables = counted
    list(orch.chat_stream(tid, "Which accounts have the most daily usage?"))
    list(orch.chat_stream(tid, "Continue"))

    assert sorted(calls) == ["DWH", "SANDBOX"]
    assert "- DWH.MARTS: 4 tables" in oc.prompts[-1]["text"]

def test_an_ordinary_turn_carries_no_catalogue(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="Answered.")],
                     gateway=IntentGateway({"label": "data_answer", "confidence": 0.93}))
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "data_source", "name": "Snowflake-Data-Warehouse",
        "bindingKey": ["data_source", "ds-dwh"],
        "scope": {"database": "DWH", "schema": "MARTS", "table": "DIM_ACCOUNT"},
    })
    list(orch.chat_stream(tid, "How many accounts are there?"))
    assert "read from its catalogue" not in oc.prompts[-1]["text"]


def _dwh() -> Binding:
    return Binding(KIND_DATA_SOURCE, "ds-dwh", "DWH", "DWH")


def test_the_named_tables_stop_at_the_cap():
    tables = [Candidate("DWH", "STAGING", f"STG_GONG__CALL_{i:02}") for i in range(60)]
    note = _catalogue_note("gong call transcripts", _dwh(), tables)
    assert "- DWH.STAGING: 60 tables" in note
    assert note.count("- DWH.STAGING.STG_GONG__CALL_") == 40


def test_a_question_that_names_no_table_says_so():
    note = _catalogue_note("hello there", _dwh(), [Candidate("DWH", "MARTS", "DIM_DATE")])
    assert "No table name shares a word with the question." in note
    assert "DIM_DATE" not in note
