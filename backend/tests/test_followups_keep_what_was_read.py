"""Follow-ups keep what an earlier turn already established.

A weaker model asks for a table name, reports that a warehouse it just queried is
unreachable, or starts a Build turn with no memory of the one before it. These tests
pin the four doors that were letting that happen: the unscoped-source instruction,
the withhold note, the findings receipt, and a dead Build session's transcript.
"""
from __future__ import annotations

from pathlib import Path

import httpx

from sage.liveread.disclosure import decide
from sage.orchestrator.service import _PLAN_SHAPE
from sage.workspace.threads import FINDINGS_MAX, findings_file

from .test_a_number_reaches_the_model_and_a_stored_value_does_not import _rows
from .test_chat_turn import _orch


def test_a_withheld_result_says_the_query_succeeded():
    verdict = decide("SELECT EMAIL FROM CUSTOMERS", _rows(["a@b.example"]))
    assert verdict.discloses is False
    assert verdict.reason.startswith("The query succeeded.")
    assert "Do not say the source was unreachable." in verdict.reason
    assert "only the card has the result" in verdict.reason


def test_the_plan_shape_asks_for_one_sentence_and_a_fresh_name():
    assert "exactly one sentence" in _PLAN_SHAPE
    assert "A second sentence is rejected." in _PLAN_SHAPE
    assert "must not be the name of an app or a plan already in this project" in _PLAN_SHAPE


def test_a_second_blank_plan_is_numbered(tmp_path: Path):
    orch, _oc = _orch(tmp_path)
    first = orch.create_plan_doc({})
    second = orch.create_plan_doc({})
    assert first["title"] == "Untitled plan"
    assert second["title"] == "Untitled plan 2"


def test_a_plan_does_not_take_an_apps_name(tmp_path: Path):
    orch, _oc = _orch(tmp_path)
    app = orch.project(start_preview=False).workspace
    app.set_display_name("Support Ticket Explorer")
    doc = orch.create_plan_doc({"title": "Support Ticket Explorer"})
    assert doc["title"] == "Support Ticket Explorer 2"


def _arm_read(operation_id: str = "op-arm", *, total: int = 43026, requests: int = 40) -> dict:
    return {
        "operation_id": operation_id,
        "operation": "text_analysis",
        "source": "Snowflake-Data-Warehouse",
        "columns": ["CASE_ID", "DESCRIPTION"],
        "coverage": {"total": total, "processed": 12, "excluded": 0, "failed": 0,
                     "unfinished": 0},
        "artifact": "examples/thr/arm-mentions.table.json",
        "purpose": "ARM mentions for Verizon and Seer Biosciences",
        "selected": [{"account": "Verizon"}],
        "requests": [{
            "requested_alias": "sonnet",
            "state": "response_completed",
            "serving_model": "claude",
            "provider_receipt": "unknown",
            "decision_stage": "unknown",
            "cache": "unknown",
            "fallback": "unknown",
        } for _ in range(requests)],
        "batches": [{"error": "x" * 4000}],
    }


def test_a_finished_turn_writes_the_read_into_findings(tmp_path: Path):
    orch, _oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    root = orch.project(start_preview=False).record.path
    history = [
        {"type": "user", "text": "how many customers asked for ARM?"},
        {"type": "data_used", "dataUsed": [_arm_read()]},
        {"type": "agent", "kind": "text", "text": "Forty customers."},
    ]
    orch._record_chat_findings(root, tid, history)
    text = findings_file(root, tid).read_text()
    assert "Snowflake-Data-Warehouse" in text
    assert "43026 total, 12 processed" in text
    assert "examples/thr/arm-mentions.table.json" in text
    assert "0 excluded" not in text
    assert "Model requests" not in text
    assert "response_completed" not in text
    assert "Verizon" not in text
    assert "a@b" not in text
    orch._record_chat_findings(root, tid, history)
    assert text == findings_file(root, tid).read_text()


def test_a_reread_with_a_longer_request_log_is_not_appended(tmp_path: Path):
    """The same measurement on the next turn gets a new operation id and more request evidence.
    The old writer treated that as a new block and appended the read again."""
    orch, _oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    root = orch.project(start_preview=False).record.path
    first = [
        {"type": "user", "text": "how many customers asked for ARM?"},
        {"type": "data_used", "dataUsed": [_arm_read()]},
    ]
    orch._record_chat_findings(root, tid, first)
    again = [
        *first,
        {"type": "user", "text": "and the Gong calls?"},
        {"type": "data_used", "dataUsed": [_arm_read("op-arm-2", requests=80)]},
    ]
    orch._record_chat_findings(root, tid, again)
    text = findings_file(root, tid).read_text()
    assert text.count("Snowflake-Data-Warehouse") == 1
    assert "response_completed" not in text


def test_a_changed_count_is_appended_once(tmp_path: Path):
    orch, _oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    root = orch.project(start_preview=False).record.path
    first = [
        {"type": "user", "text": "how many customers asked for ARM?"},
        {"type": "data_used", "dataUsed": [_arm_read(total=43026)]},
    ]
    orch._record_chat_findings(root, tid, first)
    again = [
        *first,
        {"type": "user", "text": "recheck the denominator"},
        {"type": "data_used", "dataUsed": [_arm_read("op-arm-2", total=43020)]},
    ]
    orch._record_chat_findings(root, tid, again)
    text = findings_file(root, tid).read_text()
    assert text.count("43026 total") == 1
    assert text.count("43020 total") == 1


def test_a_findings_file_at_the_ceiling_does_not_take_another_receipt(tmp_path: Path):
    orch, _oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    root = orch.project(start_preview=False).record.path
    path = findings_file(root, tid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * FINDINGS_MAX)
    history = [
        {"type": "user", "text": "how many customers asked for ARM?"},
        {"type": "data_used", "dataUsed": [_arm_read()]},
    ]
    orch._record_chat_findings(root, tid, history)
    assert path.read_text() == "x" * FINDINGS_MAX


def _dead(status: int = 404):
    def messages(*_a, **_k):
        request = httpx.Request("GET", "http://127.0.0.1:1/session/s/message")
        response = httpx.Response(status, request=request)
        raise httpx.HTTPStatusError(str(status), request=request, response=response)
    return messages


def test_a_dead_build_session_owes_the_transcript(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    project = orch.project(start_preview=False)
    app = project.app_for_turn()
    app.append_history({"type": "user", "text": "add a table of desks"}, "conv_1")
    app.append_history(
        {"type": "agent", "kind": "text", "text": "The table lists three desks."}, "conv_1")
    project.record.write_session_id("ses_old", "conv_1", app.app_id)
    oc.messages = _dead()
    project.session_id = None
    sid = orch._ensure_session(project, "conv_1")
    assert sid != "ses_old"
    owed, text = orch._build_reseed(project)
    assert owed is True
    assert "three desks" in text
    orch._replace_build_session(project, oc, "conv_1", reason="approved_plan")
    owed_after, text_after = orch._build_reseed(project)
    assert owed_after is False
    assert text_after == ""
