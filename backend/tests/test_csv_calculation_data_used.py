"""CSV totals, authorized sources, outgoing requests, and the persistent user record."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from sage.driver.opencode import with_attachment_listing
from sage.liveread import mcp, run
from sage.liveread.data_use import OPEN_CODE_DATA_CARRIERS, DataUse
from sage.orchestrator import brand

from .test_a_live_read_reaches_the_person_end_to_end import Warehouse, _call, _orch

SALES = "region,revenue,email\n" + "".join(
    f"{'North' if i % 2 == 0 else 'South'},{(i + 1) * 10},person{i}@example.invalid\n"
    for i in range(12))


class Rows:
    def __init__(self, columns, rows):
        self.columns = columns
        self.rows = rows


class SalesWarehouse(Warehouse):
    def sample_rows(self, source, database, schema, table, limit=5):
        self.asked.append((source.name, database, schema, table, limit))
        rows = [line.split(",") for line in SALES.splitlines()[1:]]
        return Rows(["region", "revenue", "email"], rows[:limit])


def args(**over):
    return {"operation": "sum", "dataset": "upload", "path": "sales.csv",
            "group_by": "region", "sum_column": "revenue",
            "selected_fields": ["region", "revenue", "total"], **over}


def table_args(**over):
    return {"operation": "sum", "source": "Snowflake-Data-Warehouse",
            "database": "DWH", "schema": "MARTS", "table": "SALES",
            "group_by": "region", "sum_column": "revenue",
            "selected_fields": ["region", "revenue", "total"], **over}


def setup_turn(tmp_path, **over):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "sales.csv"
    source.write_text(SALES)
    journal = []
    data = DataUse()
    turn = run.Turn(thread_id="t1", examples_dir=tmp_path / "examples" / "t1",
                    keep_rows=True, upload_for=lambda p: source if p == "sales.csv" else None,
                    record_data_use=lambda ev, reply: data.record(ev, reply, journal.append, "turn1"))
    return replace(turn, **over), data, journal


def test_one_call_calculates_writes_and_selects_without_unrelated_values(tmp_path):
    turn, data, _journal = setup_turn(tmp_path)
    reply = json.loads(run.perform("live_read_files", args(), turn))
    assert reply["selected"] == {"rows": [["North", "360"], ["South", "420"]], "total": "780"}
    table = json.loads((tmp_path / reply["local_reference"]).read_text())
    assert table["columns"] == ["region", "revenue"]
    assert table["rows"] == reply["selected"]["rows"]
    event = data.events("turn1")[0]
    assert event["coverage"] == {"total": 12, "processed": 12, "excluded": 0, "failed": 0, "unfinished": 0}
    assert event["source_sha256"]
    recorded = {k: v for k, v in event.items() if k not in ("operation_id", "source_sha256", "artifact")}
    assert "North" not in json.dumps(recorded)
    assert "780" not in json.dumps(recorded)
    assert "@example.invalid" not in json.dumps(reply)


def test_bound_table_calculates_without_a_shared_sample_rows_grant(tmp_path):
    source = object()
    seen = []
    turn, data, journal = setup_turn(
        tmp_path, bound={"datasource": ("Snowflake-Data-Warehouse",)},
        source_for=lambda name: source if name == "Snowflake-Data-Warehouse" else None,
        sample_rows=lambda s, db, sc, t, lim: (
            seen.append((s, db, sc, t, lim)) or Rows(
                ["region", "revenue", "email"],
                [line.split(",") for line in SALES.splitlines()[1:]][:lim],
            )
        ),
        upload_for=lambda _p: None,
    )

    reply = json.loads(run.perform("live_read_table", table_args(), turn))

    assert reply["selected"] == {"rows": [["North", "360"], ["South", "420"]], "total": "780"}
    assert seen == [(source, "DWH", "MARTS", "SALES", 501)]
    assert data.events("turn1")[0]["source"] == "DWH.MARTS.SALES"
    assert "North" not in json.dumps(journal)


def test_dataset_file_calculates_from_a_downloaded_dataset_file_without_upload_context(tmp_path):
    root = tmp_path / "mounts" / "sales"
    root.mkdir(parents=True)
    (root / "sales.csv").write_text(SALES)
    turn, data, _ = setup_turn(
        tmp_path, bound={"dataset": ("sales",)},
        dataset_file=lambda name, rel: (root / rel)
            if name == "sales" and (root / rel).is_file() else None,
        upload_for=lambda _p: None,
    )

    reply = json.loads(run.perform("live_read_files", args(dataset="sales", path="sales.csv"), turn))

    assert reply["selected"]["total"] == "780"
    assert data.events("turn1")[0]["source"] == "sales/sales.csv"


def test_bound_table_out_of_range_is_refused_before_the_source_is_touched(tmp_path):
    turn, _, journal = setup_turn(
        tmp_path, bound={"datasource": ()},
        source_for=lambda _name: (_ for _ in ()).throw(AssertionError("source touched")),
        sample_rows=lambda *_a: (_ for _ in ()).throw(AssertionError("rows touched")),
    )

    said = run.perform("live_read_table", table_args(), turn)

    assert brand.text("from {project} resources") in said
    assert journal == []


def test_bound_table_refuses_a_partial_unlimited_calculation(tmp_path):
    turn, _, journal = setup_turn(
        tmp_path, bound={"datasource": ("Snowflake-Data-Warehouse",)},
        source_for=lambda _name: object(),
        sample_rows=lambda *_a: Rows(["region", "revenue"], [["North", "1"]] * 501),
        upload_for=lambda _p: None,
    )

    said = run.perform("live_read_table", table_args(), turn)

    assert "500 row calculation limit" in said
    assert journal == []


def test_bound_table_explicit_limit_reports_unfinished_coverage(tmp_path):
    turn, data, _ = setup_turn(
        tmp_path, bound={"datasource": ("Snowflake-Data-Warehouse",)},
        source_for=lambda _name: object(),
        sample_rows=lambda *_a: Rows(["region", "revenue"], [["North", "1"], ["South", "2"]]),
        upload_for=lambda _p: None,
    )

    reply = json.loads(run.perform("live_read_table", table_args(row_limit=1), turn))

    assert reply["selected"]["total"] == "1"
    assert data.events("turn1")[0]["coverage"] == {
        "total": 2, "processed": 1, "excluded": 1, "failed": 0, "unfinished": 1,
    }


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


def test_gateway_evidence_is_preserved_when_the_response_carries_it(tmp_path):
    turn, data, _journal = setup_turn(tmp_path)
    reply = run.perform("live_read_files", args(), turn)
    request, used = data.prepare({"model": "requested-alias", "messages": [{"role": "tool", "content": reply}]})
    frame = {
        "model": "served-alias",
        "gateway": {
            "provider_request_id": "prov_123",
            "decision_stage": "input",
            "delivery": "forwarded",
            "cache_hit": False,
            "fallback": False,
        },
        "choices": [{"finish_reason": "stop"}],
    }

    list(data.observe(iter([("data: " + json.dumps(frame) + "\n\n").encode()]), request, used))

    recorded = data.events("turn1")[0]["requests"][0]
    assert recorded["requested_alias"] == "requested-alias"
    assert recorded["serving_model"] == "served-alias"
    assert recorded["provider_receipt"] == "prov_123"
    assert recorded["decision_stage"] == "input"
    assert recorded["delivery"] == "forwarded"
    assert recorded["cache"] == "miss"
    assert recorded["fallback"] == "no"


@pytest.mark.parametrize("error,kind", [
    ({"status": 401, "message": "invalid token"}, "authentication"),
    ({"status": 429, "message": "rate limit"}, "rate_limited"),
    ({"message": "Blocked by guardrail: Block phone numbers", "type": "guardrail_blocked"},
     "refused"),
])
def test_gateway_failures_are_distinguished_without_replaying_values(tmp_path, error, kind):
    turn, data, _journal = setup_turn(tmp_path)
    reply = run.perform("live_read_files", args(), turn)
    request, used = data.prepare({"model": "alias", "messages": [{"role": "tool", "content": reply}]})

    list(data.observe(iter([("data: " + json.dumps({"error": error}) + "\n\n").encode()]),
                      request, used))

    recorded = data.events("turn1")[0]["requests"][0]
    assert recorded["state"] == "failed"
    assert recorded["failure"] == kind
    assert "person0" not in json.dumps(recorded)


def _build_upload(orch, filename: str, data: bytes) -> dict:
    """The "build" mode's replacement for `upload_file`, which now always refuses (Phase 5 decision
    #4 — no Dataset write API in use anywhere). What these tests actually need is a real file with
    known bytes sitting at a Build app's own served path, and `attach_file` against the default
    seeded Dataset still gives exactly that — a real download, not a mount write."""
    ds = next(a["id"] for a in orch.list_assets() if a["name"] == "sales_2026")
    (orch._assets.roots[ds] / filename).write_bytes(data)
    return orch.attach_file(ds, filename)


@pytest.mark.parametrize("mode", ["chat", "build"])
def test_a_restart_reopens_the_data_operations_this_conversation_already_recorded(tmp_path, mode):
    """`_mint_live_read_token` replays this Conversation's `dataUsed` rows into a shim that has
    forgotten them — what a **Read again** press needs after the process restarts.

    IT RUNS FOR EVERY PROJECT SINCE #431. It used to sit behind `dataUseVersion`, which was the one
    site of seven that was not a refusal, so it was deleted by decision rather than by pattern with
    the other six. A plant proved the decision needed this test: emptying the whole branch to `pass`
    left the four data-use files green, so nothing anywhere was watching the site being changed.
    """
    orch, _ = _orch(tmp_path, Warehouse())
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    upload = (orch.upload_scratch("sales.csv", SALES.encode()) if mode == "chat"
              else _build_upload(orch, "sales.csv", SALES.encode()))
    orch.add_thread_context(tid, {"kind": "file", "path": upload["path"], "name": "sales.csv"})
    control_token = project.control.arm_chat(tid) if mode == "chat" else None
    if mode != "chat":
        project.build_conversation = tid
    try:
        token = orch._mint_live_read_token(tid)
        json.loads(_call(orch, "live_read_files", args(token=token, path=upload["path"])))
        recorded = dict(project.shim.data_use.operations)
        assert recorded, "the read itself did not record anything; the rest proves nothing"

        project.shim.data_use.operations.clear()  # the restart
        orch._mint_live_read_token(tid)

        assert set(project.shim.data_use.operations) == set(recorded)
    finally:
        if control_token:
            project.control.disarm_chat(control_token)


@pytest.mark.parametrize("mode", ["chat", "build"])
def test_upload_access_and_persistent_record_use_existing_conversation_controls(tmp_path, mode):
    orch, _ = _orch(tmp_path, Warehouse())
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    upload = (orch.upload_scratch("sales.csv", SALES.encode()) if mode == "chat"
              else _build_upload(orch, "sales.csv", SALES.encode()))
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


@pytest.mark.parametrize("mode", ["chat", "build"])
def test_bound_table_access_and_persistent_record_use_existing_conversation_controls(tmp_path, mode):
    warehouse = SalesWarehouse()
    orch, _ = _orch(tmp_path, warehouse)
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    if mode == "chat":
        orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1",
                                      "name": "Snowflake-Data-Warehouse"})
        control_token = project.control.arm_chat(tid)
    else:
        project.workspace.bindings_path.parent.mkdir(parents=True, exist_ok=True)
        project.workspace.bindings_path.write_text(json.dumps([{
            "kind": "data_source", "id": "ds1", "name": "Snowflake-Data-Warehouse",
            "display_name": "Snowflake-Data-Warehouse", "database": "DWH",
            "schema": "MARTS", "table": "SALES",
        }]))
        project.build_conversation = tid
        control_token = None
    try:
        token = orch._mint_live_read_token(tid)
        reply = json.loads(_call(orch, "live_read_table", table_args(token=token)))
        assert reply["selected"]["total"] == "780"
        history = (orch.thread_history(tid) if mode == "chat"
                   else project.workspace.read_history(tid))
        assert history[-1]["dataUsed"][0]["source"] == "DWH.MARTS.SALES"
        assert warehouse.asked == [("Snowflake-Data-Warehouse", "DWH", "MARTS", "SALES", 501)]
    finally:
        if control_token:
            project.control.disarm_chat(control_token)


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
    assert event["requests"][0]["failure"] == "transport"
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
    assert details[0]["events"][0]["coverage"]["processed"] == 12
    assert details[0]["events"][0]["source"] == "sales.csv"


def test_artifact_directory_cannot_point_outside_the_project(tmp_path):
    turn, _, journal = setup_turn(tmp_path / "project")
    outside = tmp_path / "outside"
    outside.mkdir()
    turn.examples_dir.parent.mkdir(parents=True)
    turn.examples_dir.symlink_to(outside, target_is_directory=True)
    assert "must stay" in run.perform("live_read_files", args(), turn)
    assert not list(outside.iterdir())
    assert not journal


def attachment_prompt(path="public/data/upload/uploads/sales.csv", *, chat=True):
    return with_attachment_listing(
        "what is in @sales.csv",
        [{"path": path, "name": "sales.csv", "summary": "CSV - 3 columns, 12 rows",
          "detail": "columns: region, revenue, email"}],
        chat=chat,
    )


def direct_request(tool, arguments, content, *, path="public/data/upload/uploads/sales.csv", chat=True):
    return {"model": "alias", "messages": [
        {"role": "user", "content": attachment_prompt(path, chat=chat)},
        {"role": "assistant", "tool_calls": [{"id": "call1", "type": "function",
            "function": {"name": tool, "arguments": json.dumps(arguments)}}]},
        {"role": "tool", "tool_call_id": "call1", "content": content},
    ]}


def test_direct_read_of_an_attached_file_becomes_a_local_execution_receipt():
    data = DataUse()
    request = direct_request("read", {"filePath": "public/data/upload/uploads/sales.csv"}, SALES)

    prepared, used = data.prepare(request)

    assert not used
    assert "person0@example.invalid" in request["messages"][-1]["content"]
    text = json.dumps(prepared["messages"])
    assert "person0@example.invalid" not in text
    assert "local_execution_receipt" in text
    assert prepared["messages"][1]["tool_calls"][0]["id"] == "call1"
    receipt = json.loads(prepared["messages"][-1]["content"])
    assert receipt["sources"][0]["path"] == "public/data/upload/uploads/sales.csv"
    assert receipt["sources"][0]["columns"] == ["region", "revenue", "email"]
    assert receipt["sources"][0]["rows"] == 12


def test_build_attachment_direct_read_becomes_a_local_execution_receipt():
    data = DataUse()
    request = direct_request(
        "read",
        {"filePath": "public/data/upload/uploads/sales.csv"},
        SALES,
        chat=False,
    )

    prepared, _used = data.prepare(request)

    text = json.dumps(prepared["messages"])
    assert "person0@example.invalid" not in text
    assert json.loads(prepared["messages"][-1]["content"])["sources"][0]["rows"] == 12


def test_bash_or_python_output_for_an_attached_file_is_not_sent_back_to_the_model():
    data = DataUse()
    request = direct_request(
        "bash",
        {"command": "python - <<'PY'\nimport pandas as pd\nprint(pd.read_csv('public/data/upload/uploads/sales.csv'))\nPY"},
        SALES + "\nCommand exited with code 0.",
    )

    prepared, _ = data.prepare(request)

    text = json.dumps(prepared["messages"])
    assert "person0@example.invalid" not in text
    assert "local_execution_receipt" in prepared["messages"][-1]["content"]
    assert json.loads(prepared["messages"][-1]["content"])["status"] == "completed"


def test_failed_python_output_and_metadata_are_receipts_too():
    data = DataUse()
    request = {"messages": [
        {"role": "user", "content": attachment_prompt()},
        {"role": "assistant", "content": [{"type": "tool", "tool": "bash", "state": {
            "status": "error",
            "input": {"command": "python broken.py public/data/upload/uploads/sales.csv"},
            "error": SALES + "\nTraceback (most recent call last):",
            "metadata": {"output": SALES},
        }}]},
    ]}

    prepared, _ = data.prepare(request)

    part = prepared["messages"][1]["content"][0]
    text = json.dumps(part)
    assert "person0@example.invalid" not in text
    assert part["state"]["input"]["kind"] == "local_execution_request"
    assert json.loads(part["state"]["error"])["kind"] == "local_execution_receipt"
    assert json.loads(part["state"]["metadata"]["output"])["kind"] == "local_execution_receipt"


def test_later_tool_arguments_cannot_replay_direct_local_output():
    data = DataUse()
    request = {"messages": [
        {"role": "user", "content": attachment_prompt()},
        {"role": "assistant", "content": [{"type": "tool", "tool": "bash", "state": {
            "status": "completed",
            "input": {"command": "cat public/data/upload/uploads/sales.csv"},
            "output": SALES,
        }}]},
        {"role": "assistant", "tool_calls": [{"id": "call2", "type": "function",
            "function": {"name": "bash", "arguments": json.dumps({
                "command": "echo person0@example.invalid",
            })}}]},
        {"role": "tool", "tool_call_id": "call2",
         "content": "person0@example.invalid\nCommand exited with code 0."},
    ]}

    prepared, _ = data.prepare(request)

    text = json.dumps(prepared["messages"])
    assert "person0@example.invalid" not in text
    assert text.count("local_execution_receipt") == 2


def test_a_shell_call_for_a_withheld_file_gets_a_refusal_receipt():
    data = DataUse()
    request = direct_request(
        "bash",
        {"command": "cat public/data/upload/uploads/sales.csv"},
        SALES + "\nCommand exited with code 0.",
    )

    prepared, _ = data.prepare(request, withheld={"file:public/data/upload/uploads/sales.csv"})

    text = json.dumps(prepared["messages"])
    assert "person0@example.invalid" not in text
    receipt = json.loads(prepared["messages"][-1]["content"])
    assert receipt["status"] == "error"
    assert prepared["messages"][1]["tool_calls"][0]["function"]["arguments"].startswith(
        '{"kind": "local_execution_request"')


def test_selected_operation_values_reused_in_tool_arguments_are_tracked(tmp_path):
    turn, data, _ = setup_turn(tmp_path)
    reply = run.perform("live_read_files", args(), turn)
    _first_request, first_used = data.prepare({"messages": [{"role": "tool", "content": reply}]})
    assert first_used
    request = {"messages": [
        {"role": "assistant", "tool_calls": [{"id": "b1", "type": "function",
            "function": {"name": "bash", "arguments": json.dumps({"command": "echo 780"})}}]},
        {"role": "tool", "tool_call_id": "b1", "content": "780\nCommand exited with code 0."},
    ]}

    prepared, used = data.prepare(request)

    assert used == first_used
    assert "780" in json.dumps(prepared["messages"])


def test_selected_operation_values_reused_by_compaction_are_tracked(tmp_path):
    turn, data, journal = setup_turn(tmp_path)
    reply = run.perform("live_read_files", args(), turn)
    oid = json.loads(reply)["data_use"]
    request = {"model": "alias", "messages": [
        {"role": "assistant", "content": [
            {"type": "text", "text": "Earlier compacted context: total revenue was 780."}
        ]},
    ]}

    prepared, used = data.prepare(request)
    list(data.observe(iter([b'data: {"choices":[{"finish_reason":"stop"}]}\n\n']),
                      prepared, used))

    assert used == {oid}
    assert "780" in json.dumps(prepared["messages"])
    event = journal[-1]["dataUsed"][0]
    assert event["requests"][0]["requested_alias"] == "alias"
    assert event["requests"][0]["state"] == "response_completed"


def test_source_code_read_stays_available_even_if_it_mentions_sensitive_shapes():
    data = DataUse()
    request = direct_request("read", {"filePath": "src/main.py"},
                             "def email_label():\n    return 'email'\n")

    prepared, _ = data.prepare(request)

    assert prepared["messages"][-1]["content"] == "def email_label():\n    return 'email'\n"


def test_task_result_for_an_attached_file_becomes_a_local_execution_receipt():
    data = DataUse()
    request = direct_request(
        "task",
        {"description": "Inspect sales", "prompt": "Read public/data/upload/uploads/sales.csv"},
        "Child result:\n" + SALES,
    )

    prepared, _ = data.prepare(request)

    text = json.dumps(prepared["messages"])
    assert "person0@example.invalid" not in text
    assert "local_execution_receipt" in prepared["messages"][-1]["content"]
    assert prepared["messages"][1]["tool_calls"][0]["id"] == "call1"
    assert json.loads(prepared["messages"][-1]["content"])["tool"] == "task"


def test_child_request_can_use_the_parent_source_descriptor():
    data = DataUse()
    parent = {"messages": [{"role": "user", "content": attachment_prompt()}]}
    data.prepare(parent)
    child = {"messages": [
        {"role": "assistant", "tool_calls": [{"id": "child_read", "type": "function",
            "function": {"name": "read",
                         "arguments": json.dumps({"filePath": "public/data/upload/uploads/sales.csv"})}}]},
        {"role": "tool", "tool_call_id": "child_read", "content": SALES},
    ]}

    prepared, _ = data.prepare(child)

    text = json.dumps(prepared["messages"])
    assert "person0@example.invalid" not in text
    receipt = json.loads(prepared["messages"][-1]["content"])
    assert receipt["sources"][0]["path"] == "public/data/upload/uploads/sales.csv"
    assert receipt["provider_receipt"] == "unknown"


@pytest.mark.parametrize("child_text,status", [
    ("Child failed after reading data:\n" + SALES, "error"),
    ("Child cancelled after reading data:\n" + SALES, "cancelled"),
    ("Child interrupted after reading data:\n" + SALES, "interrupted"),
])
def test_failed_cancelled_and_interrupted_task_results_stay_local(child_text, status):
    data = DataUse()
    request = direct_request(
        "task",
        {"description": "Inspect sales", "prompt": "Use public/data/upload/uploads/sales.csv"},
        child_text,
    )

    prepared, _ = data.prepare(request)

    text = json.dumps(prepared["messages"])
    assert "person0@example.invalid" not in text
    receipt = json.loads(prepared["messages"][-1]["content"])
    assert receipt["kind"] == "local_execution_receipt"
    assert receipt["status"] == status


def test_successful_task_result_that_mentions_errors_stays_completed():
    data = DataUse()
    request = direct_request(
        "task",
        {"description": "Inspect sales", "prompt": "Use public/data/upload/uploads/sales.csv"},
        "Child completed. No errors found.\n" + SALES,
    )

    prepared, _ = data.prepare(request)

    receipt = json.loads(prepared["messages"][-1]["content"])
    assert receipt["kind"] == "local_execution_receipt"
    assert receipt["status"] == "completed"


def test_background_completion_repeating_child_output_stays_local():
    data = DataUse()
    request = direct_request(
        "task",
        {"description": "Inspect sales", "prompt": "Read public/data/upload/uploads/sales.csv"},
        "Child result:\n" + SALES,
    )
    request["messages"].append({
        "role": "user",
        "content": "Background task completed with:\n" + SALES,
    })

    prepared, _ = data.prepare(request)

    text = json.dumps(prepared["messages"])
    assert "person0@example.invalid" not in text
    receipt = json.loads(prepared["messages"][-1]["content"])
    assert receipt["tool"] == "background"
    assert receipt["sources"][0]["path"] == "public/data/upload/uploads/sales.csv"


def test_supported_mcp_and_image_carriers_are_inventoried():
    rows = {row["carrier"]: row for row in OPEN_CODE_DATA_CARRIERS}

    assert rows["live_read MCP text result"]["coverage"] == "covered"
    assert rows["live_read MCP error"]["coverage"] == "covered"
    assert rows["user image attachment"]["model_view"] == "image content reaches only vision-capable models"
    assert rows["tool-result image part"]["coverage"] == "unsupported-state handled"
    assert rows["tool-result image part"]["lineage"] == "unknown unless a supported data operation recorded it"


def test_mcp_text_result_parts_track_the_selected_data_operation(tmp_path):
    turn, data, journal = setup_turn(tmp_path)
    reply = run.perform("live_read_files", args(), turn)
    request = {"model": "alias", "messages": [
        {"role": "assistant", "tool_calls": [{"id": "mcp1", "type": "function",
            "function": {"name": "sage-live-read_live_read_files",
                         "arguments": json.dumps(args(path="sales.csv"))}}]},
        {"role": "tool", "tool_call_id": "mcp1",
         "content": [{"type": "text", "text": '{"status": "metadata"}'},
                     {"type": "text", "text": reply}]},
    ]}

    prepared, used = data.prepare(request)
    list(data.observe(iter([b'data: {"choices":[{"finish_reason":"stop"}]}\n\n']),
                      prepared, used))

    assert used == {json.loads(reply)["data_use"]}
    assert "780" in json.dumps(prepared["messages"])
    event = journal[-1]["dataUsed"][0]
    assert event["requests"][0]["state"] == "response_completed"
    assert event["requests"][0]["requested_alias"] == "alias"


def test_mcp_json_rpc_result_parts_track_the_selected_data_operation(tmp_path):
    turn, data, journal = setup_turn(tmp_path)
    framed = mcp.handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "live_read_files", "arguments": args()},
    }, run=lambda name, tool_args: run.perform(name, tool_args, turn))
    request = {"model": "alias", "messages": [
        {"role": "assistant", "tool_calls": [{"id": "mcp1", "type": "function",
            "function": {"name": "sage-live-read_live_read_files",
                         "arguments": json.dumps(args(path="sales.csv"))}}]},
        {"role": "tool", "tool_call_id": "mcp1",
         "content": framed["result"]["content"]},
    ]}

    prepared, used = data.prepare(request)
    list(data.observe(iter([b'data: {"choices":[{"finish_reason":"stop"}]}\n\n']),
                      prepared, used))

    assert used == {json.loads(framed["result"]["content"][0]["text"])["data_use"]}
    assert journal[-1]["dataUsed"][0]["requests"][0]["state"] == "response_completed"


def test_mcp_error_text_parts_preserve_the_error_without_inventing_lineage():
    data = DataUse()
    request = {"messages": [
        {"role": "assistant", "tool_calls": [{"id": "mcp1", "type": "function",
            "function": {"name": "sage-live-read_live_read_files",
                         "arguments": json.dumps({"dataset": "upload", "path": "sales.csv"})}}]},
        {"role": "tool", "tool_call_id": "mcp1",
         "content": [{"type": "text", "text": (
             "The live read did not happen: source failed. Nothing was put on the person's screen."
         )}]},
    ]}

    prepared, used = data.prepare(request)

    assert not used
    assert prepared == request


def test_mcp_json_rpc_error_parts_preserve_the_error_without_data_use():
    data = DataUse()
    framed = mcp.handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "live_read_files", "arguments": {"dataset": "upload"}},
    }, run=lambda _name, _tool_args: (_ for _ in ()).throw(RuntimeError("source failed")))
    request = {"messages": [
        {"role": "assistant", "tool_calls": [{"id": "mcp1", "type": "function",
            "function": {"name": "sage-live-read_live_read_files",
                         "arguments": json.dumps({"dataset": "upload"})}}]},
        {"role": "tool", "tool_call_id": "mcp1",
         "content": framed["result"]["content"]},
    ]}

    prepared, used = data.prepare(request)

    assert framed["result"]["isError"] is True
    assert not used
    assert "source failed" in prepared["messages"][-1]["content"][0]["text"]


def test_user_image_attachment_is_left_for_the_vision_policy():
    data = DataUse()
    messages = [{"role": "user", "content": [
        {"type": "text", "text": attachment_prompt("public/data/design/uploads/shot.png")},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,USERIMAGE"}},
    ]}]

    prepared, used = data.prepare({"messages": messages})

    assert not used
    assert prepared["messages"] == messages


def test_withheld_user_image_attachment_gets_a_clear_receipt():
    data = DataUse()
    messages = [{"role": "user", "content": [
        {"type": "text", "text": attachment_prompt("public/data/design/uploads/shot.png")},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,USERIMAGE"}},
    ]}]

    prepared, used = data.prepare(
        {"messages": messages},
        withheld={"file:public/data/design/uploads/shot.png"},
    )

    assert not used
    text = json.dumps(prepared["messages"])
    assert "USERIMAGE" not in text
    receipt = json.loads(prepared["messages"][0]["content"][1]["text"])
    assert receipt["kind"] == "withheld_image_receipt"
    assert receipt["sources"][0]["path"] == "public/data/design/uploads/shot.png"


def test_tool_result_image_bytes_become_an_unknown_lineage_receipt():
    data = DataUse()
    request = {"messages": [
        {"role": "assistant", "tool_calls": [{"id": "img1", "type": "function",
            "function": {"name": "external_mcp_draw",
                         "arguments": json.dumps({"prompt": "draw the row"})}}]},
        {"role": "tool", "tool_call_id": "img1", "content": [
            {"type": "text", "text": "Image generated."},
            {"type": "image", "mimeType": "image/png", "data": "SECRETIMAGEBYTES"},
        ]},
    ]}

    prepared, used = data.prepare(request)

    assert not used
    text = json.dumps(prepared["messages"])
    assert "SECRETIMAGEBYTES" not in text
    assert "Image generated." in text
    receipt = json.loads(prepared["messages"][-1]["content"][1]["text"])
    assert receipt["kind"] == "external_image_receipt"
    assert receipt["tool"] == "external_mcp_draw"
    assert receipt["lineage"] == "unknown"


def test_opencode_tool_state_image_output_is_not_replayed_to_the_model():
    data = DataUse()
    request = {"messages": [
        {"role": "user", "content": attachment_prompt()},
        {"role": "assistant", "content": [{"type": "tool", "tool": "bash", "state": {
            "status": "completed",
            "input": {"command": "python chart.py public/data/upload/uploads/sales.csv"},
            "output": [{"type": "image", "mimeType": "image/png", "data": "SECRETIMAGEBYTES"}],
        }}]},
    ]}

    prepared, _used = data.prepare(request)

    state = prepared["messages"][1]["content"][0]["state"]
    assert state["input"]["kind"] == "local_execution_request"
    assert "SECRETIMAGEBYTES" not in json.dumps(prepared["messages"])
    assert json.loads(state["output"][0]["text"])["kind"] == "external_image_receipt"
