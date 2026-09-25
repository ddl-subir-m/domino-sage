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


def test_ordinary_chat_keeps_its_one_table_instruction(tmp_path: Path):
    orch, oc, tid = _setup(tmp_path)
    list(orch.chat_stream(tid, "How many accounts are there?"))
    assert "do not query another table" in oc.prompts[-1]["text"]


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
