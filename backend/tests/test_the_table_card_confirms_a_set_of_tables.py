"""The table card confirms a set: clicks toggle, one confirm writes every table picked.

The card used to submit on the first click, so a question over two tables could only ever record
one. Now a click toggles a table and a confirm writes the set, in Chat and in Build, and the turn
that follows is told to query those tables.

One plant per mode, each end to end: two tables toggled on the card, one confirm, both positions on
the record, and the following prompt naming both. The card half runs the real component with hook
state kept between renders, because the other table-card harnesses forget every update and so
cannot see a toggle at all.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator

from .fake_opencode import Turn, execution_plan
from .test_a_table_confirmed_in_chat_is_carried_into_build import (
    _ask,
    _card,
    _gong_warehouse,
    _thread_with_source,
)
from .test_a_table_confirmed_in_chat_is_carried_into_build import (
    _client as _chat_client,
)
from .test_a_table_confirmed_in_chat_is_carried_into_build import (
    _orch as _chat_orch,
)
from .test_a_table_pick_is_recorded_as_the_table_it_picked import (
    PROMPT as BUILD_PROMPT,
)
from .test_a_table_pick_is_recorded_as_the_table_it_picked import (
    _bind,
    _build,
)
from .test_a_table_pick_is_recorded_as_the_table_it_picked import (
    _client as _build_client,
)
from .test_a_table_pick_is_recorded_as_the_table_it_picked import (
    _orch as _build_orch,
)

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

_TOGGLE = Path(__file__).resolve().parent / "js" / "table_card_toggle_harness.mjs"
_BUILD_HARNESS = Path(__file__).resolve().parent / "js" / "table_candidate_harness.mjs"

GONG = {"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"}
STG = {"database": "DWH", "schema": "STAGING", "table": "STG_GONG__CALLS"}
PARTICIPANTS = {"database": "DWH", "schema": "MARTS", "table": "GONG__CALL_PARTICIPANTS"}

_CARD = {
    "type": "table_candidates", "live": True, "prompt": "chart the gong calls",
    "message": "Pick the Tables to start from, then confirm.",
    "sourceId": "ds-dwh", "sourceName": "Snowflake-Data-Warehouse",
    "groups": [{"database": "DWH", "schema": "MARTS", "tables": ["GONG__CALLS"]},
               {"database": "DWH", "schema": "STAGING", "tables": ["STG_GONG__CALLS"]}],
    "allGroups": [{"database": "DWH", "schema": "MARTS", "tables": ["GONG__CALLS"]},
                  {"database": "DWH", "schema": "STAGING", "tables": ["STG_GONG__CALLS"]}],
    "total": 2, "matched": 2, "answered": {},
}


def _toggle(block: dict, clicks: list[str]) -> dict:
    out = subprocess.run(["node", str(_TOGGLE)],
                         input=json.dumps({"block": block, "clicks": clicks}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# ---- the card ------------------------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("door,block", [
    ("chat", {**_CARD, "threadId": "thr_1", "taskId": "task_1"}),
    ("build", _CARD),
])
def test_two_toggles_and_one_confirm_send_both_tables_once(door: str, block: dict):
    """Neither toggle sends anything; the confirm sends the set, through the card's own door."""
    out = _toggle(block, ["GONG__CALLS", "STG_GONG__CALLS", "confirm"])

    assert out["confirmDisabledBefore"] is True, "a confirm with nothing picked writes nothing"
    assert [s["sent"] for s in out["steps"]] == [0, 0, 1]
    assert out["steps"][1]["picked"] == ["GONG__CALLS", "STG_GONG__CALLS"]
    assert out["steps"][1]["confirmDisabled"] is False
    assert out["steps"][1]["confirmLabel"] == "Use these 2 Tables"
    [call] = out["sent"]
    assert call["door"] == door
    tables = call["args"][3] if door == "chat" else call["args"][2]
    assert tables == [GONG, STG]


@needs_node
def test_a_second_click_on_the_same_table_takes_it_back_out():
    out = _toggle(_CARD, ["GONG__CALLS", "STG_GONG__CALLS", "GONG__CALLS", "confirm"])

    assert out["steps"][2]["picked"] == ["STG_GONG__CALLS"]
    assert out["sent"][0]["args"][2] == [STG]


@needs_node
def test_one_table_still_works_by_toggle_and_confirm():
    out = _toggle(_CARD, ["GONG__CALLS", "confirm"])

    assert out["steps"][0]["confirmLabel"] == "Use this Table"
    assert out["sent"][0]["args"][2] == [GONG]


@needs_node
def test_a_replayed_card_draws_no_confirm():
    """History carries no `live`, and the confirm goes with the table buttons."""
    assert _toggle(_CARD, [])["hasConfirm"] is True
    assert _toggle({**_CARD, "live": False}, [])["hasConfirm"] is False


# ---- Build ---------------------------------------------------------------------------------------


def _two_table_warehouse(orch: Orchestrator) -> None:
    orch._resources.columns["GONG__CALLS"] = [("CALL_ID", "TEXT"), ("STARTED_AT", "TIMESTAMP")]
    orch._resources.columns["STG_GONG__CALLS"] = [("RAW_ID", "TEXT")]


def test_build_one_confirm_of_two_tables_is_one_binding_holding_both(
        tmp_path: Path, monkeypatch):
    """One Data Source stays one dependency; both positions ride on it, and the first is also the
    Binding's own table so a one-table reader still sees a real one."""
    orch = _build_orch(tmp_path)
    _two_table_warehouse(orch)
    client = _build_client(orch, monkeypatch, [])
    _bind(client)

    res = client.post("/api/bindings/data_source/ds-dwh/candidate", json={"tables": [GONG, STG]})

    assert res.status_code == 200, res.text
    [entry] = [b for b in orch.project().workspace.read_bindings() if b["id"] == "ds-dwh"]
    assert (entry["database"], entry["schema"], entry["table"]) == ("DWH", "MARTS", "GONG__CALLS")
    assert entry["tables"] == [GONG, STG]


def test_build_the_following_turn_names_both_tables(tmp_path: Path, monkeypatch):
    """The replay bubble, and the data block the build agent reads — columns of both, and the rule
    to qualify each table since they sit in two schemas."""
    orch = _build_orch(tmp_path)
    _two_table_warehouse(orch)
    said: list[str | None] = []
    client = _build_client(orch, monkeypatch, said)
    _bind(client)
    client.post("/api/bindings/data_source/ds-dwh/candidate", json={"tables": [GONG, STG]})

    _build(client, skipTableGate=True)

    assert said == ["Use MARTS.GONG__CALLS and STAGING.STG_GONG__CALLS."]
    agents = (orch.project().workspace.path / "AGENTS.md").read_text()
    assert "**DWH.MARTS.GONG__CALLS** and **DWH.STAGING.STG_GONG__CALLS**" in agents
    assert "`CALL_ID`" in agents and "`RAW_ID`" in agents
    assert "`FROM MARTS.GONG__CALLS`, `FROM STAGING.STG_GONG__CALLS`" in agents


def test_build_a_single_table_keeps_the_manifest_shape(tmp_path: Path, monkeypatch):
    orch = _build_orch(tmp_path)
    client = _build_client(orch, monkeypatch, [])
    _bind(client)

    client.post("/api/bindings/data_source/ds-dwh/candidate", json={"tables": [GONG]})

    [entry] = [b for b in orch.project().workspace.read_bindings() if b["id"] == "ds-dwh"]
    assert "tables" not in entry
    assert entry["table"] == "GONG__CALLS"


def test_build_a_dropped_table_in_the_set_writes_none_of_it(tmp_path: Path, monkeypatch):
    orch = _build_orch(tmp_path)
    client = _build_client(orch, monkeypatch, [])
    _bind(client)

    res = client.post("/api/bindings/data_source/ds-dwh/candidate",
                      json={"tables": [GONG, {**STG, "table": "GONE"}]})

    assert res.status_code == 502
    [entry] = [b for b in orch.project().workspace.read_bindings() if b["id"] == "ds-dwh"]
    assert "table" not in entry


@needs_node
def test_build_the_bubble_the_confirm_draws_names_both_tables():
    """The store draws the bubble before the server answers, and it must match the server's."""
    history = [{"type": "user", "text": BUILD_PROMPT},
               {"type": "table-candidates", "prompt": BUILD_PROMPT, "message": "Pick.",
                "sourceId": "ds-dwh", "sourceName": "Snowflake-Data-Warehouse",
                "groups": [{"database": "DWH", "schema": "MARTS", "tables": ["GONG__CALLS"]}],
                "allGroups": [{"database": "DWH", "schema": "MARTS", "tables": ["GONG__CALLS"]}],
                "total": 1, "matched": 1},
               {"type": "done", "ok": False, "decision": "table candidates"}]
    out = subprocess.run(["node", str(_BUILD_HARNESS)],
                         input=json.dumps({"history": history, "prompt": BUILD_PROMPT,
                                           "answered": {}, "tables": [GONG, STG]}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    said = json.loads(out.stdout.strip().splitlines()[-1])

    assert said["click"] == {"tables": [GONG, STG]}
    assert said["bubbles"] == [BUILD_PROMPT, "Use MARTS.GONG__CALLS and STAGING.STG_GONG__CALLS."]


# ---- Chat ----------------------------------------------------------------------------------------

CHAT_PROMPT = "chart me the daily gong calls from Snowflake"


def _items(client, tid: str) -> list[dict]:
    return client.get(f"/api/threads/{tid}/context").json()["items"]


def test_chat_one_confirm_of_two_tables_records_both_on_the_conversation(
        tmp_path: Path, monkeypatch):
    """The first on the store's own row, which is what retires the card; the second as its own
    table chip, the record the panel already writes for a pinned table."""
    orch, _ = _chat_orch(tmp_path)
    _gong_warehouse(orch)
    orch._resources.columns["GONG__CALL_PARTICIPANTS"] = [("PERSON_ID", "TEXT")]
    client = _chat_client(orch, monkeypatch)
    tid = _thread_with_source(orch)
    _card(_ask(client, tid, CHAT_PROMPT))

    res = client.post(f"/api/threads/{tid}/context/data_source/ds-dwh/candidate",
                      json={"tables": [GONG, PARTICIPANTS]})

    assert res.status_code == 200, res.text
    rows = [i for i in _items(client, tid) if i.get("kind") == "data_source"]
    assert [r["scope"] for r in rows] == [GONG, PARTICIPANTS]
    assert rows[1]["resourceId"] == "table:ds-dwh:DWH.MARTS.GONG__CALL_PARTICIPANTS"
    assert rows[1]["sourceName"] == "Snowflake-Data-Warehouse"
    assert rows[1]["columns"], "the second table's columns were not read for the prompt"
    assert "table-candidates" not in _ask(client, tid, CHAT_PROMPT)


def test_chat_the_following_prompt_names_both_tables(tmp_path: Path, monkeypatch):
    orch, oc = _chat_orch(tmp_path)
    _gong_warehouse(orch)
    orch._resources.columns["GONG__CALL_PARTICIPANTS"] = [("PERSON_ID", "TEXT")]
    client = _chat_client(orch, monkeypatch)
    tid = _thread_with_source(orch)
    _card(_ask(client, tid, CHAT_PROMPT))
    client.post(f"/api/threads/{tid}/context/data_source/ds-dwh/candidate",
                json={"tables": [GONG, PARTICIPANTS]})

    _ask(client, tid, CHAT_PROMPT, skipTableGate=True)

    text = oc.prompts[-1]["text"]
    assert "tables DWH.MARTS.GONG__CALLS (columns: CALL_ID TEXT" in text
    assert "DWH.MARTS.GONG__CALL_PARTICIPANTS (columns: PERSON_ID TEXT)" in text
    assert "these are the selected tables in this conversation" in text
    assert "is the one table in this conversation" not in text


def test_chat_a_handoff_unions_every_table_chip_onto_one_binding(tmp_path: Path, monkeypatch):
    """A Binding is replaced in place by key, so crossing chip by chip would leave only the last."""
    plan = execution_plan("Gong Call Dashboard", "A gong call dashboard.", "Daily calls",
                          work="Count calls by day.")
    orch, _ = _chat_orch(tmp_path, [Turn(text="Here it is."), Turn(text=plan)])
    _gong_warehouse(orch)
    client = _chat_client(orch, monkeypatch)
    tid = _thread_with_source(orch)
    _card(_ask(client, tid, CHAT_PROMPT))
    client.post(f"/api/threads/{tid}/context/data_source/ds-dwh/candidate",
                json={"tables": [GONG, PARTICIPANTS]})
    _ask(client, tid, CHAT_PROMPT, skipTableGate=True)
    orch.draft_handoff_plan(tid)

    orch.confirm_handoff(tid, {"resources": True, "artifacts": True, "transcript": False})

    bound = [b for b in orch.project(start_preview=False).workspace.read_bindings()
             if b.get("id") == "ds-dwh"]
    assert len(bound) == 1
    assert bound[0]["table"] == "GONG__CALLS"
    assert bound[0]["tables"] == [GONG, PARTICIPANTS]
    assert bound[0]["name"] == "Snowflake-Data-Warehouse"


def test_chat_a_confirm_naming_no_table_is_still_refused(tmp_path: Path, monkeypatch):
    orch, _ = _chat_orch(tmp_path)
    _gong_warehouse(orch)
    client = _chat_client(orch, monkeypatch)
    tid = _thread_with_source(orch)

    for body in ({"tables": []}, {"tables": [GONG, {"database": "DWH", "schema": "MARTS"}]}):
        res = client.post(f"/api/threads/{tid}/context/data_source/ds-dwh/candidate", json=body)
        assert res.status_code == 400, body
    assert not any((i.get("scope") or {}).get("table") for i in _items(client, tid))
