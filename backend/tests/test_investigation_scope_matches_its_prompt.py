"""Accepted investigation reaches attached warehouses, with matching instructions (#557 P6)."""
import re
from pathlib import Path

from sage.liveread.grant import reachable

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, _orch


def _setup(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="Answered.")],
                     gateway=IntentGateway({"label": "data_answer", "confidence": 0.93}))
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "data_source", "name": "Snowflake-Data-Warehouse",
        "bindingKey": ["data_source", "ds-dwh"],
        "scope": {"database": "DWH", "schema": "MARTS", "table": "SFDC__ACCOUNT"},
    })
    return orch, oc, tid


def test_open_investigation_treats_the_selected_table_as_a_starting_point(tmp_path: Path):
    orch, oc, tid = _setup(tmp_path)
    orch.decide_thread_investigation(tid, "open")
    list(orch.chat_stream(tid, "Find ARM requests in cases and call transcripts."))
    prompt = oc.prompts[-1]["text"]
    assert "Investigation is open" in prompt
    assert "starting table" in prompt
    assert "relevant tables" in prompt
    assert "only sources attached to this conversation" in prompt
    assert "do not query another table" not in prompt
    assert "classifying, summarising, extracting, or deciding" in prompt
    assert "one model call per row" in prompt
    assert "findings file" in prompt
    assert "live_read_files" in prompt
    assert "dataset=upload" in prompt
    assert "Dataset folder" in prompt
    assert "Do not ask the person to attach a file or a table" in prompt
    assert "live_read_table" in prompt
    assert "operation=analyze_text" in prompt
    assert "Count from those judgments" in prompt
    assert "not for a Data Source table" not in prompt


def test_ordinary_chat_keeps_its_one_table_instruction(tmp_path: Path):
    orch, oc, tid = _setup(tmp_path)
    list(orch.chat_stream(tid, "How many accounts are there?"))
    prompt = oc.prompts[-1]["text"]
    assert "do not query another table" in prompt
    # Judging text is analyze_text on the chosen table. Sampling rows stays for a look
    # at the shape; the text column itself is not selected into the answering model.
    assert "operation=analyze_text" in prompt
    assert "text_column" in prompt
    assert "alias set to the model this conversation names" in prompt
    assert "Do not select the text column to read it." in prompt


def test_selected_tables_are_told_to_judge_text_and_still_ask_before_another(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="Answered.")],
                     gateway=IntentGateway({"label": "data_answer", "confidence": 0.93}))
    tid = orch.create_thread()["id"]
    for table in ("GONG__CALL_TRANSCRIPTS", "SFDC__CASE"):
        orch.add_thread_context(tid, {
            "kind": "data_source", "name": "Snowflake-Data-Warehouse",
            "bindingKey": ["data_source", "ds-dwh"],
            "resourceId": f"table:ds-dwh:DWH.MARTS.{table}",
            "scope": {"database": "DWH", "schema": "MARTS", "table": table},
        })
    list(orch.chat_stream(tid, "How many accounts are there?"))
    prompt = oc.prompts[-1]["text"]
    assert "GONG__CALL_TRANSCRIPTS" in prompt
    assert "SFDC__CASE" in prompt
    assert "before using any other table" in prompt
    assert "operation=analyze_text" in prompt
    assert "Do not select the text column to read it." in prompt


def test_chat_token_cannot_gain_an_unrelated_app_binding_after_the_turn(tmp_path: Path):
    orch, oc, tid = _setup(tmp_path)
    project = orch.project(start_preview=False)
    project.workspace.update_bindings(lambda _: [{
        "id": "ds-unrelated", "kind": "data_source", "name": "Unrelated warehouse",
        "display_name": "Unrelated warehouse", "database": "PRIVATE", "schema": "DATA",
    }])
    orch.decide_thread_investigation(tid, "open")
    list(orch.chat_stream(tid, "Find ARM requests in cases and call transcripts."))
    token = re.search(r"Read token: (lrt_[\w-]+)", oc.prompts[-1]["text"])[1]
    # The Chat control pin is gone. The token must retain its original scope.
    assert not project.control.snapshot().chat_thread_id
    turn = orch._live_read_turn(token)
    assert turn.bound == {}
    assert reachable("datasource", "Snowflake-Data-Warehouse",
                     chips=turn.chips["datasource"]) is None
    assert reachable("datasource", "Unrelated warehouse",
                     bound=turn.bound.get("datasource", ()),
                     chips=turn.chips["datasource"]).tag == "not-in-range"
    assert reachable("datasource", "Never attached", chips=turn.chips["datasource"])

    # Build and explicit Read again keep their existing app-binding behavior.
    build_token = orch._mint_live_read_token(tid)
    assert "Unrelated warehouse" in orch._live_read_turn(build_token).bound["datasource"]
    assert "Unrelated warehouse" in orch._live_read_turn_for(tid).bound["datasource"]
    assert orch._live_read_turn(token) is None
