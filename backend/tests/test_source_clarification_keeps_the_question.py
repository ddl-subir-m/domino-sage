"""A source reply supplies the missing input to the question already asked (#557 P5)."""
from pathlib import Path

import pytest

from sage.orchestrator import recall
from sage.workspace.threads import ThreadStore

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, _orch

ASK = "Fuse data from sfdc cases and gong transcripts and tell me which of our customers have asked for ARM support?"
REPLY = "the data warehouse is here @Snowflake-Data-Warehouse"
SOURCE = {"kind": "data_source", "id": "ds1", "name": "Snowflake-Data-Warehouse"}


def _setup(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="Please attach the warehouse."), Turn(text="Answered.")],
                     gateway=IntentGateway({"label": "data_answer", "confidence": 0.93}))
    tid = orch.create_thread()["id"]
    return orch, oc, tid


def test_attaching_the_source_resumes_arm_after_a_context_reload(tmp_path: Path):
    orch, oc, tid = _setup(tmp_path)
    list(orch.chat_stream(tid, ASK))
    root = orch.project(start_preview=False).record.path
    pending = ThreadStore(root).read_context(tid)["pendingTask"]
    assert pending["question"] == ASK
    assert pending["awaiting"] == "source"
    orch.add_thread_context(tid, SOURCE)

    events = list(orch.chat_stream(tid, REPLY))

    offer = next(e for e in events if e["type"] == "investigation-offer")
    assert offer["prompt"] == ASK
    assert offer["taskId"] == pending["id"]
    assert len(oc.prompts) == 1
    assert [e["text"] for e in orch.thread_history(tid) if e["type"] == "user"] == [ASK, REPLY]
    assert ASK in str(orch.project(start_preview=False).shim.gateway.seen)


def test_a_new_question_retires_an_old_card_before_it_can_grant_access(tmp_path: Path):
    orch, _, tid = _setup(tmp_path)
    orch.add_thread_context(tid, SOURCE)
    offer = next(e for e in orch.chat_stream(tid, ASK) if e["type"] == "investigation-offer")
    list(orch.chat_stream(tid, "Hello"))
    with pytest.raises(ValueError, match="question"):
        orch.decide_thread_investigation(tid, "open", task_id=offer["taskId"])
    assert orch.thread_context(tid).get("investigation", {}).get("state") != "open"


@pytest.mark.parametrize("replacement", ["What is ARM?", "cancel", "never mind"])
def test_a_substantive_reply_or_cancel_cannot_revive_the_prior_task(tmp_path: Path, replacement):
    orch, _, tid = _setup(tmp_path)
    list(orch.chat_stream(tid, ASK))
    orch.add_thread_context(tid, SOURCE)
    events = list(orch.chat_stream(tid, replacement))
    assert not any(e["type"] == "investigation-offer" for e in events)
    assert not orch.thread_context(tid).get("pendingTask")


@pytest.mark.parametrize("scope,kept", [(recall.SUMMARY, True), (recall.EMPTY, False)])
def test_only_full_recall_clear_removes_the_pending_task(tmp_path: Path, scope, kept):
    orch, _, tid = _setup(tmp_path)
    list(orch.chat_stream(tid, ASK))
    orch.clear_recall(tid, scope)
    assert bool(orch.thread_context(tid).get("pendingTask")) is kept


def test_the_reply_needs_a_new_attached_source_in_the_same_thread(tmp_path: Path):
    orch, _, tid = _setup(tmp_path)
    list(orch.chat_stream(tid, ASK))
    other = orch.create_thread()["id"]
    orch.add_thread_context(other, SOURCE)
    events = list(orch.chat_stream(tid, REPLY))
    assert not any(e["type"] == "investigation-offer" for e in events)
    assert not orch.thread_context(other).get("pendingTask")


def test_accepting_resumes_once_and_a_new_question_keeps_the_grant(tmp_path: Path):
    orch, oc, tid = _setup(tmp_path)
    list(orch.chat_stream(tid, ASK))
    orch.add_thread_context(tid, SOURCE)
    offer = next(e for e in orch.chat_stream(tid, REPLY) if e["type"] == "investigation-offer")
    task_id = offer["taskId"]
    granted = orch.decide_thread_investigation(tid, "open", task_id=task_id)
    assert orch.decide_thread_investigation(tid, "open", task_id=task_id) == granted
    list(orch.chat_stream(tid, "do not trust this card text", skip_investigation_gate=True,
                          task_id=task_id))
    assert oc.prompts[-1]["text"].endswith(ASK)
    assert orch.thread_context(tid)["pendingTask"]["awaiting"] == ""
    count = len(oc.prompts)
    replay = list(orch.chat_stream(tid, ASK, skip_investigation_gate=True, task_id=task_id))
    assert replay[-1]["decision"] == "stale question"
    assert len(oc.prompts) == count
    oc.turns.append(Turn(text="Next answer."))
    list(orch.chat_stream(tid, "What is ARM?"))
    assert orch.thread_context(tid)["investigation"]["state"] == "open"
    assert not orch.thread_context(tid).get("pendingTask")


def test_decline_ranks_tables_for_arm_and_the_table_card_keeps_that_task(tmp_path: Path):
    orch, oc, tid = _setup(tmp_path)
    orch._resources.tree["ds-dwh"] = {"DWH": {"MARTS": [
        "SFDC__CASE", "SFDC__ACCOUNT", "GONG__CALLS", "GONG__CALL_TRANSCRIPTS", "FCT_USAGE"]}}
    list(orch.chat_stream(tid, ASK))
    orch.add_thread_context(tid, {**SOURCE, "bindingKey": ["data_source", "ds-dwh"]})
    offer = next(e for e in orch.chat_stream(tid, REPLY) if e["type"] == "investigation-offer")
    orch.decide_thread_investigation(tid, "decline", task_id=offer["taskId"])
    events = list(orch.chat_stream(tid, ASK, skip_investigation_gate=True, task_id=offer["taskId"]))
    card = next(e for e in events if e["type"] == "table-candidates")
    assert card["prompt"] == ASK
    assert card["matched"] > 0
    assert card["taskId"] == offer["taskId"]
    assert "GONG__CALL_TRANSCRIPTS" in str(card["allGroups"])
    assert "SFDC__CASE" in str(card["allGroups"])
    orch.confirm_thread_table_candidate(tid, "ds-dwh", "DWH", "MARTS", "SFDC__ACCOUNT",
                                        task_id=card["taskId"])
    events = list(orch.chat_stream(tid, ASK, skip_table_gate=True, task_id=card["taskId"]))
    assert not any(e["type"] == "investigation-offer" for e in events)
    assert oc.prompts[-1]["text"].endswith(ASK)


def test_a_stale_card_is_rejected_by_the_http_decision_door(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from sage.orchestrator import app as app_module

    orch, _, tid = _setup(tmp_path)
    monkeypatch.setattr(app_module, "orchestrator", orch)
    with TestClient(app_module.control_app) as client:
        response = client.post(f"/api/threads/{tid}/investigation",
                               json={"decision": "open", "taskId": "retired-task"})
    assert response.status_code == 400
    assert "earlier question" in response.json()["error"]


def test_the_workbench_carries_the_task_id_on_both_parts_of_acceptance():
    from .test_the_investigation_card_is_drawn_and_its_buttons_decide import HISTORY, _run

    history = [{**e, "taskId": "task_arm"} if e["type"] == "investigation-offer" else e
               for e in HISTORY]
    result = _run(0, history=history)
    assert result["click"]["taskId"] == "task_arm"
    assert result["replay"]["taskId"] == "task_arm"
