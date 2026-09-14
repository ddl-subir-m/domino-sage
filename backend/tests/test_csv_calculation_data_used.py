"""CSV totals, authorized sources, outgoing requests, and the persistent user record."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from sage.liveread import run
from sage.liveread.data_use import DataUse

from .test_a_live_read_reaches_the_person_end_to_end import Warehouse, _call, _orch

SALES = "region,revenue,email\n" + "".join(
    f"{'North' if i % 2 == 0 else 'South'},{(i + 1) * 10},person{i}@example.invalid\n"
    for i in range(12))


def args(**over):
    return {"operation": "sum", "dataset": "upload", "path": "sales.csv",
            "group_by": "region", "sum_column": "revenue",
            "selected_fields": ["region", "revenue", "total"], **over}


def setup_turn(tmp_path, **over):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "sales.csv"
    source.write_text(SALES)
    journal = []
    data = DataUse()
    turn = run.Turn(thread_id="t1", examples_dir=tmp_path / "examples" / "t1",
                    keep_rows=True, data_use_enabled=True, upload_for=lambda p: source if p == "sales.csv" else None,
                    record_data_use=lambda ev, reply: data.record(ev, reply, journal.append, "turn1"))
    return replace(turn, **over), data, journal


def test_one_call_calculates_writes_and_selects_without_unrelated_values(tmp_path):
    turn, data, journal = setup_turn(tmp_path)
    reply = json.loads(run.perform("live_read_files", args(), turn))
    assert reply["selected"] == {"rows": [["North", "360"], ["South", "420"]], "total": "780"}
    table = json.loads((tmp_path / reply["local_reference"]).read_text())
    assert table["columns"] == ["region", "revenue"]
    assert table["rows"] == reply["selected"]["rows"]
    event = data.events("turn1")[0]
    assert event["coverage"] == {"total": 12, "processed": 12, "excluded": 0, "failed": 0, "unfinished": 0}
    assert event["source_sha256"]
    assert "North" not in json.dumps(journal)
    assert "780" not in json.dumps(journal)
    assert "@example.invalid" not in json.dumps(reply)


def test_default_selection_is_structure_and_kept_rows_still_controls_artifact(tmp_path):
    turn, _, _ = setup_turn(tmp_path, keep_rows=False)
    reply = json.loads(run.perform("live_read_files", args(selected_fields=None), turn))
    assert reply["selected"] == {}
    assert "North" not in json.dumps(reply)
    table = json.loads((tmp_path / reply["local_reference"]).read_text())
    assert not table.get("rows")


@pytest.mark.parametrize("over,text", [
    ({"selected_fields": ["email"]}, "valid result fields"),
    ({"selected_fields": ["total", "total"]}, "valid result fields"),
    ({"sum_column": "missing", "selected_fields": []}, "both selected columns"),
    ({"path": "other.csv"}, "not available"),
    ({"result_name": "../outside"}, "one filename"),
    ({"row_limit": -1}, "positive integer"),
    ({"selected_fields": {}}, "valid result fields"),
])
def test_invalid_operation_does_not_write_a_result(tmp_path, over, text):
    turn, _, journal = setup_turn(tmp_path)
    assert text in run.perform("live_read_files", args(**over), turn)
    assert not turn.examples_dir.exists()
    assert journal == []


def test_explicit_limit_records_excluded_rows(tmp_path):
    turn, data, _ = setup_turn(tmp_path)
    reply = json.loads(run.perform("live_read_files", args(row_limit=2), turn))
    assert reply["selected"]["total"] == "30"
    assert data.events("turn1")[0]["coverage"]["excluded"] == 10


@pytest.mark.parametrize("content", ["region,revenue\nNorth,NaN\n", "region,revenue\nNorth,bad\n",
                                    "region,revenue\nNorth,10,extra\n", "region,region\nNorth,10\n"])
def test_bad_data_is_not_reported_as_a_complete_calculation(tmp_path, content):
    turn, _, journal = setup_turn(tmp_path)
    (tmp_path / "sales.csv").write_text(content)
    reply = run.perform("live_read_files", args(), turn)
    assert "data_use" not in reply
    assert journal == []


def test_known_artifact_read_is_structure_but_source_code_read_is_unchanged(tmp_path):
    turn, data, _ = setup_turn(tmp_path)
    reply = json.loads(run.perform("live_read_files", args(), turn))
    for path, masked in [(reply["local_reference"], True), ("src/main.py", False)]:
        request = {"model": "alias", "messages": [
            {"role": "assistant", "tool_calls": [{"id": "read1", "function": {
                "name": "read", "arguments": json.dumps({"filePath": path})}}]},
            {"role": "tool", "tool_call_id": "read1", "content": "North person@example.invalid"},
        ]}
        prepared, _ = data.prepare(request)
        assert ("person@example.invalid" not in prepared["messages"][-1]["content"]) == masked
        assert prepared["messages"][0] == request["messages"][0]


@pytest.mark.parametrize("chunks,state", [
    ([b'data: {"choices":[{"finish_reason":"stop"}]}\n\n'], "response_completed"),
    ([b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'], "interrupted"),
    ([b'data: {"error":{"message":"denied"}}\n\n'], "failed"),
])
def test_each_request_has_its_own_alias_and_unknown_provider_evidence(tmp_path, chunks, state):
    turn, data, journal = setup_turn(tmp_path)
    reply = run.perform("live_read_files", args(), turn)
    request = {"model": "first", "messages": [{"role": "tool", "content": reply}]}
    prepared, used = data.prepare(request)
    assert list(data.observe(iter(chunks), prepared, used)) == chunks
    assert list(data.observe(iter(chunks), {**prepared, "model": "second"}, used)) == chunks
    requests = data.events("turn1")[0]["requests"]
    assert [r["requested_alias"] for r in requests] == ["first", "second"]
    assert len({r["request_id"] for r in requests}) == 2
    assert all(r["state"] == state and r["serving_model"] is None for r in requests)
    assert all(r["provider_receipt"] == "unknown" for r in requests)
    assert "person0" not in json.dumps(journal)


@pytest.mark.parametrize("mode", ["chat", "build"])
def test_upload_access_and_persistent_record_use_existing_conversation_controls(tmp_path, mode):
    orch, _ = _orch(tmp_path, Warehouse())
    project = orch.project(start_preview=False)
    assert project.record.read_settings().get("dataUseVersion") == 1
    tid = orch.create_thread()["id"]
    upload = (orch.upload_scratch("sales.csv", SALES.encode()) if mode == "chat"
              else orch.upload_file("sales.csv", SALES.encode()))
    source = upload["path"]
    orch.add_thread_context(tid, {"kind": "file", "path": source, "name": "sales.csv"})
    if mode == "chat":
        control_token = project.control.arm_chat(tid)
    else:
        project.build_conversation = tid
        control_token = None
    try:
        token = orch._mint_live_read_token(tid)
        reply = json.loads(_call(orch, "live_read_files", args(token=token, path=source)))
        assert reply["selected"]["total"] == "780"
        if mode == "chat":
            history = orch.thread_history(tid)
        else:
            history = project.workspace.read_history(tid)
        assert history[-1]["dataUsed"][0]["source"] == source
        withheld = project.control.arm_withheld({"file:" + source})
        try:
            assert "not available" in _call(orch, "live_read_files", args(token=token, path=source))
        finally:
            project.control.disarm_withheld(withheld)
    finally:
        if control_token:
            project.control.disarm_chat(control_token)


def test_existing_project_does_not_enable_calculation(tmp_path):
    from sage.workspace.manager import WorkspaceManager

    template = tmp_path / "template"
    template.mkdir()
    root = tmp_path / "old"
    root.mkdir()
    (root / "existing.txt").write_text("old")
    manager = WorkspaceManager(root, template)
    manager.ensure("old", seed_app=False)
    assert "dataUseVersion" not in manager.project_record("old").read_settings()
    turn, _, journal = setup_turn(tmp_path, data_use_enabled=False)
    assert "new projects only" in run.perform("live_read_files", args(), turn)
    assert journal == []


def test_build_refresh_renders_one_updated_data_used_detail(tmp_path):
    turn, data, journal = setup_turn(tmp_path)
    reply = run.perform("live_read_files", args(), turn)
    request, used = data.prepare({"model": "requested-alias", "messages": [{"role": "tool", "content": reply}]})
    list(data.observe(iter([]), request, used))
    history = [{"type": "user", "text": "Calculate totals"}, *journal,
               {"type": "done", "ok": True, "dataUsed": data.events("turn1")}]
    harness = Path(__file__).parent / "js" / "build_events_harness.mjs"
    output = subprocess.run(["node", str(harness)], input=json.dumps({"history": history}),
                            text=True, capture_output=True, check=True)
    assert json.loads(output.stdout)["types"].count("data_used") == 1


def test_restart_restores_latest_evidence_and_default_artifact_view(tmp_path):
    turn, data, journal = setup_turn(tmp_path)
    reply = json.loads(run.perform("live_read_files", args(), turn))
    request, used = data.prepare({"model": "alias", "messages": [{"role": "tool", "content": json.dumps(reply)}]})
    list(data.observe(iter([]), request, used))
    restored = DataUse()
    restored.restore(journal, journal.append)
    assert restored.events("turn1")[0]["requests"][0]["state"] == "interrupted"
    request = {"messages": [
        {"role": "assistant", "tool_calls": [{"id": "r", "function": {"name": "read",
            "arguments": json.dumps({"filePath": reply["local_reference"]})}}]},
        {"role": "tool", "tool_call_id": "r", "content": "North 360 South 420"},
    ]}
    prepared, _ = restored.prepare(request)
    assert "North" not in prepared["messages"][-1]["content"]
    assert json.loads(prepared["messages"][-1]["content"])["selected_fields"] == []


def test_result_symlink_cannot_write_outside_artifacts(tmp_path):
    turn, _, journal = setup_turn(tmp_path)
    turn.examples_dir.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("unchanged")
    (turn.examples_dir / "totals.table.json").symlink_to(outside)
    assert "must stay" in run.perform("live_read_files", args(result_name="totals"), turn)
    assert outside.read_text() == "unchanged"
    assert not journal


def test_transport_failure_keeps_delivery_unknown(tmp_path):
    turn, data, journal = setup_turn(tmp_path)
    reply = run.perform("live_read_files", args(), turn)
    request, used = data.prepare({"model": "alias", "messages": [{"role": "tool", "content": reply}]})

    def fail():
        raise OSError("connection lost")
        yield

    with pytest.raises(OSError):
        list(data.observe(fail(), request, used))
    event = journal[-1]["dataUsed"][0]
    assert event["requests"][0]["state"] == "failed"
    assert event["requests"][0]["delivery"] == "unknown"


def test_build_cannot_calculate_a_chat_only_upload(tmp_path):
    orch, _ = _orch(tmp_path, Warehouse())
    tid = orch.create_thread()["id"]
    upload = orch.upload_scratch("sales.csv", SALES.encode())
    orch.add_thread_context(tid, {"kind": "file", "path": upload["path"], "name": "sales.csv"})
    token = orch._mint_live_read_token(tid)
    assert "not available" in _call(orch, "live_read_files", args(token=token, path=upload["path"]))


def test_artifact_receipt_does_not_restore_withheld_content(tmp_path):
    from sage.shim.chat_paths import apply_withheld

    turn, data, _ = setup_turn(tmp_path)
    reply = json.loads(run.perform("live_read_files", args(), turn))
    path = reply["local_reference"]
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "r", "function": {"name": "read",
            "arguments": json.dumps({"filePath": path})}}]},
        {"role": "tool", "tool_call_id": "r", "content": "North 360"},
    ]
    withheld = apply_withheld(messages, {"file:" + path})
    request, used = data.prepare({"messages": withheld})
    assert request["messages"] == withheld
    assert not used


def test_chat_live_result_keeps_the_data_used_detail(tmp_path):
    turn, data, _ = setup_turn(tmp_path)
    run.perform("live_read_files", args(), turn)
    frames = [{"type": "agent", "kind": "text", "text": "Total 780."},
              {"type": "done", "ok": True, "dataUsed": data.events("turn1")}]
    harness = Path(__file__).parent / "js" / "chat_stream_harness.mjs"
    output = subprocess.run(["node", str(harness)], input=json.dumps(frames),
                            text=True, capture_output=True, check=True)
    blocks = json.loads(output.stdout)["final"]
    details = [b for b in blocks if b["type"] == "data_used"]
    assert len(details) == 1
    assert details[0]["event"]["coverage"]["processed"] == 12
    assert details[0]["event"]["source"] == "sales.csv"


def test_artifact_directory_cannot_point_outside_the_project(tmp_path):
    turn, _, journal = setup_turn(tmp_path / "project")
    outside = tmp_path / "outside"
    outside.mkdir()
    turn.examples_dir.parent.mkdir(parents=True)
    turn.examples_dir.symlink_to(outside, target_is_directory=True)
    assert "must stay" in run.perform("live_read_files", args(), turn)
    assert not list(outside.iterdir())
    assert not journal


def test_empty_git_checkout_is_a_fresh_project(tmp_path):
    from sage.workspace.manager import WorkspaceManager

    root = tmp_path / "new"
    (root / ".git").mkdir(parents=True)
    template = tmp_path / "template"
    template.mkdir()
    manager = WorkspaceManager(root, template)
    manager.ensure("new", seed_app=False)
    assert manager.project_record("new").read_settings()["dataUseVersion"] == 1
