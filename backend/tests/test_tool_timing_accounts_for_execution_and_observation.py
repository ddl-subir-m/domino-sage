"""Execution, observation and elapsed accounting are different measurements (#394)."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sage import timing, tool_timing
from sage.driver.opencode import map_session_event
from sage.orchestrator.service import _call_fingerprint, _RepeatBrake

pytestmark = pytest.mark.usefixtures("ledger")


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    source = SimpleNamespace(monotonic=lambda: now[0], time=lambda: 1000 + now[0])
    monkeypatch.setenv("SAGE_TIMING", "1")
    monkeypatch.setattr(timing, "time", source)
    monkeypatch.setattr(tool_timing, "time", source)
    return now


def event(call, tool="read", status="running", **extra):
    return {"call_id": call, "tool": tool, "status": status, **extra}


def readout():
    return timing.as_dict(timing.current())


def test_slow_tool_and_observation_lag_do_not_change_on_repeated_snapshots(clock):
    timing.start_turn("build")
    observer = timing.tool_observer()
    clock[0] = 2
    observer.event("s", event("c", harness_time={"start": 1001000, "startSource": "opencode.state.time.start"}))
    clock[0] = 10
    observer.event("s", event("c", tool="", status="success",
                              harness_time={"end": 1008000, "endSource": "opencode.state.time.end"}))
    clock[0] = 15
    observer.event("s", event("c", status="success", harness_time={"end": 1008000}))
    row = readout()["tools"][0]
    assert len(readout()["tools"]) == 1
    assert row["tool"] == "read" and row["executionMs"] == 7000
    assert row["completionLagMs"] == 2000 and row["completedObservedMs"] == 10000
    assert row["startSource"] == "opencode.state.time.start"
    assert row["endSource"] == "opencode.state.time.end"
    assert row["observedMs"] == 8000 and row["clockPlacement"] == "wall_clock"


def test_missing_completion_and_missing_harness_clock_remain_unknown(clock):
    timing.start_turn("chat")
    observer = timing.tool_observer()
    observer.event("s", event("hung", tool="glob"))
    clock[0] = 241
    observer.interval("poll.read", 240, ok=False)
    row = readout()["tools"][0]
    assert row["status"] == "running" and row["completedObservedMs"] is None
    assert row["executionMs"] is None and row["observedMs"] == 241000
    assert readout()["intervals"][-1]["ok"] is False
    assert "not exact" in row["observationLimit"]


def test_parallel_sessions_with_the_same_call_id_are_distinct(clock):
    timing.start_turn("build")
    observer = timing.tool_observer()
    observer.event("parent", event("same", tool="read"))
    observer.event("child", event("same", tool="bash"))
    observer.event("child", event("same", tool="", status="success"))
    assert [(t["sessionId"], t["tool"], t["status"]) for t in readout()["tools"]] == [
        ("parent", "read", "running"), ("child", "bash", "completed")]


def test_read_ranges_edits_and_opaque_shell_never_claim_unchanged_content(clock):
    timing.start_turn("build")
    observer = timing.tool_observer()
    def tool(call, name, args):
        observer.event("s", event(call, name, "success", input=args), directory="/workspace/app")
    tool("r1", "read", {"filePath": "data/private.csv", "offset": 10, "limit": 20})
    tool("r2", "read", {"filePath": "data/private.csv", "offset": 30, "limit": 20})
    tool("e", "edit", {"filePath": "data/private.csv", "newString": "PRIVATE ROW"})
    tool("r3", "read", {"filePath": "data/private.csv"})
    tool("shell", "bash", {"command": "PRIVATE COMMAND"})
    tool("r4", "read", {"filePath": "data/private.csv"})
    rows = readout()["tools"]
    assert rows[0]["targetFingerprint"] == rows[1]["targetFingerprint"] == rows[3]["targetFingerprint"]
    assert rows[0]["range"] == {"offset": 10, "limit": 20}
    assert rows[1]["targetState"] == "unknown" and rows[1]["editSincePreviousRead"] is None
    assert rows[3]["editSincePreviousRead"] is True and rows[3]["targetState"] == "observed_edit"
    assert rows[5]["targetState"] == "unknown" and rows[5]["opaqueOperationSincePreviousRead"] is True
    encoded = json.dumps(readout())
    assert "private.csv" not in encoded and "PRIVATE" not in encoded


def test_observer_and_fingerprints_survive_phase_reentry_and_reject_late_updates(clock):
    timing.start_turn("build")
    first = timing.tool_observer()
    first.event("s1", event("r", input={"filePath": "same.py"}), directory="/app")
    second = timing.tool_observer()
    assert second is first
    second.event("s2", event("r", input={"filePath": "same.py"}), directory="/app")
    assert readout()["tools"][0]["targetFingerprint"] == readout()["tools"][1]["targetFingerprint"]
    clock[0] = 1
    record = timing.finish_turn()
    snapshot = timing.as_dict(record)
    timing.start_turn("build")
    second.event("s1", event("r", status="success"))
    second.interval("poll.read", 0)
    assert timing.as_dict(record) == snapshot
    assert readout()["tools"] == []


def test_repeat_brake_keeps_ids_name_and_counts_but_no_command_and_no_late_turn(clock):
    timing.start_turn("chat")
    observer = timing.tool_observer()
    brake = _RepeatBrake()
    fingerprint = _call_fingerprint("bash", {"command": "SECRET COMMAND"})
    for i in range(3):
        observer.event("s", event(str(i), "bash"))
        assert brake.saw(fingerprint, "private label", session_id="s", call_id=str(i)) is (i == 2)
    rows = readout()["repeatBrake"]
    assert [row["consecutive"] for row in rows] == [1, 2, 3]
    assert [row["harnessCallId"] for row in rows] == ["0", "1", "2"]
    assert all(row["tool"] == "bash" and row["limit"] == 3 for row in rows)
    assert "SECRET" not in json.dumps(rows) and fingerprint not in json.dumps(rows)
    timing.finish_turn()
    timing.start_turn("build")
    brake.saw(fingerprint, "label", session_id="s", call_id="late")
    assert readout()["repeatBrake"] == []


def test_driver_preserves_available_tool_clock_and_omitted_completion_name(clock):
    started = map_session_event({"type": "session.next.tool.called", "properties": {
        "sessionID": "s", "callID": "c", "tool": "read", "timestamp": 1001000}}, "s")
    completed = map_session_event({"type": "session.next.tool.success", "properties": {
        "sessionID": "s", "callID": "c", "timestamp": 1004000}}, "s")
    timing.start_turn("build")
    observer = timing.tool_observer()
    observer.event("s", started.payload)
    clock[0] = 5
    observer.event("s", completed.payload)
    row = readout()["tools"][0]
    assert row["tool"] == "read" and row["executionMs"] == 3000
    assert row["endSource"] == "opencode.session.next.tool.success.timestamp"


def test_future_or_invalid_clock_does_not_get_an_elapsed_slot(clock):
    timing.start_turn("build")
    observer = timing.tool_observer()
    observer.event("s", event("future", harness_time={"start": 2000000}))
    assert readout()["tools"][0]["clockPlacement"] == "outside_turn_or_unknown"
    assert readout()["tools"][0]["startAtMs"] is None


def report_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "turn-timing.py"
    spec = importlib.util.spec_from_file_location("turn_timing_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_unions_parallel_tools_models_and_nested_tail_with_visible_unknown():
    rec = {"ms": 10000, "calls": [{"atMs": 1000, "ms": 4000}, {"atMs": 3000, "ms": 3000}],
           "spans": [{"name": "setup.session", "atMs": 0, "ms": 1000},
                     {"name": "after.save", "atMs": 8000, "ms": 1000},
                     {"name": "after.commit", "atMs": 8200, "ms": 200}],
           "tools": [{"startAtMs": 4000, "executionMs": 3000, "clockPlacement": "wall_clock"},
                     {"startAtMs": 5000, "executionMs": 2500, "clockPlacement": "wall_clock"}],
           "intervals": [{"name": "poll.sleep", "atMs": 1000, "ms": 6000}],
           "observations": {}, "counters": {}}
    result = report_module().split(rec)
    assert result["model"] == 3000 and result["tools"] == 1500
    assert result["overlap"] == 2000 and result["finalization"] == 1000
    assert result["other"] == 1500 and result["polling"] == 0
    assert sum(result[k] for k in ("gates", "model", "tools", "unconfirmed_tools", "polling", "finalization", "overlap", "other")) == rec["ms"]


def test_legacy_poll_sums_are_not_assigned_to_unknown_time():
    result = report_module().split({"ms": 10000, "calls": [], "spans": [], "observations": {
        "poll.sleep_ms": {"sum": 9000}}, "counters": {}})
    assert result["other"] == 10000 and result["polling"] == 0 and result["polling_placed"] is False


def test_public_build_records_tool_lifecycle_poll_health_and_slow_tail(tmp_path, monkeypatch, clock):
    from sage.orchestrator.service import Orchestrator

    from .fake_opencode import Turn
    from .test_a_turn_records_where_its_time_went import _orch

    orch = _orch(tmp_path, [Turn(text="Built", writes={"src/App.tsx": "export default () => null\n"})])
    def runtime(*args, **kwargs):
        clock[0] += 6
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", runtime)
    list(orch.build_stream("add a chart"))
    rec = timing.as_dict(timing.last_finished())
    assert any(tool["tool"] == "write" and tool["status"] == "completed" for tool in rec["tools"])
    assert any(interval["name"] == "poll.read" and interval["ok"] for interval in rec["intervals"])
    runtime_spans = [span for span in rec["spans"] if span["name"] == "after.runtime_wait"]
    assert len(runtime_spans) == 1 and runtime_spans[0]["ms"] == 6000
    assert {"after.git_save", "after.data_scan", "after.restore_attachments"} <= {span["name"] for span in rec["spans"]}


def test_unknown_tool_execution_is_not_charged_as_polling_delay():
    rec = {"ms": 241000, "calls": [], "spans": [], "observations": {}, "counters": {},
           "tools": [{"executionMs": None, "firstObservedMs": 1000, "completedObservedMs": None}],
           "intervals": [{"name": "poll.sleep", "atMs": 1000, "ms": 240000}]}
    result = report_module().split(rec)
    assert result["unconfirmed_tools"] == 240000 and result["polling"] == 0
    assert result["other"] == 1000


def test_public_chat_stream_keeps_the_start_name_on_its_brake_decision(tmp_path, monkeypatch):
    from .fake_opencode import Turn
    from .test_a_repeated_call_stops_the_turn import ChatLoopOpenCode, _orch

    monkeypatch.setenv("SAGE_TIMING", "1")
    oc = ChatLoopOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _orch(tmp_path, oc, "CHAT")
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "whats in my uploads"))
    record = timing.as_dict(timing.last_finished())
    decisions = record["repeatBrake"]
    assert [row["consecutive"] for row in decisions] == [1, 2, 3]
    assert decisions[-1]["stopped"] is True and decisions[-1]["tool"] == "bash"
    assert decisions[-1]["harnessCallId"] == "c2"
    assert record["tools"] and all(row["sessionId"] for row in record["tools"])


def test_v1_normalizer_preserves_state_clock():
    from sage.driver.opencode import SessionEvents

    stream = SessionEvents("unused", "s")
    stream._message_event({"type": "message.updated", "properties": {
        "sessionID": "s", "info": {"id": "m", "role": "assistant"}}})
    result = stream._message_event({"type": "message.part.updated", "properties": {
        "sessionID": "s", "part": {"id": "part", "messageID": "m", "type": "tool", "tool": "read",
                                   "callID": "c", "state": {"status": "completed",
                                                             "time": {"start": 1000, "end": 8000}}}}})
    assert result.payload["harness_time"] == {
        "start": 1000, "end": 8000,
        "startSource": "opencode.state.time.start", "endSource": "opencode.state.time.end"}


def test_capped_records_say_that_the_trace_is_incomplete(clock, monkeypatch):
    monkeypatch.setattr(tool_timing, "MAX_TOOLS", 2)
    monkeypatch.setattr(tool_timing, "MAX_INTERVALS", 2)
    timing.start_turn("build")
    observer = timing.tool_observer()
    for i in range(3):
        observer.event("s", event(str(i)))
        observer.interval("poll.read", 0)
        observer.brake(session_id="s", call_id=str(i), tool="read", fingerprint="fingerprint",
                       consecutive=i + 1, limit=3, stopped=i == 2)
    record = readout()
    assert record["toolsTruncated"] and record["intervalsTruncated"] and record["repeatBrakeTruncated"]
    assert len(record["tools"]) == len(record["intervals"]) == len(record["repeatBrake"]) == 2


def test_partial_input_is_replaced_by_final_target_and_range_without_duplicate_bookkeeping(clock):
    timing.start_turn("build")
    observer = timing.tool_observer()
    observer.event("s", event("r1", input={"filePath": "src/part"}))
    observer.event("s", event("r1", input={"filePath": "src/whole.py", "offset": 20}))
    observer.event("s", event("r1", status="success", input={"filePath": "src/whole.py", "offset": 20, "limit": 10}))
    observer.event("s", event("edit", "edit", "success", input={"filePath": "src/whole.py"}))
    # Repeated completed snapshots must not turn the original read into a read after its edit.
    observer.event("s", event("r1", status="success", input={"filePath": "src/whole.py", "offset": 20, "limit": 10}))
    observer.event("s", event("r2", status="success", input={"filePath": "src/whole.py"}))
    rows = readout()["tools"]
    assert len(rows) == 3
    assert rows[0]["range"] == {"offset": 20, "limit": 10}
    assert rows[0]["targetFingerprint"] == rows[1]["targetFingerprint"] == rows[2]["targetFingerprint"]
    assert rows[0]["targetMetadataFinal"] is True and rows[0]["targetState"] == "unknown"
    assert rows[2]["editSincePreviousRead"] is True


def test_stale_running_input_cannot_replace_completed_target_metadata(clock):
    timing.start_turn("build")
    observer = timing.tool_observer()
    observer.event("s", event("r1", status="success", input={"filePath": "src/whole.py", "offset": 20, "limit": 10}))
    observer.event("s", event("r1", input={"filePath": "src/part", "offset": 1}))
    observer.event("s", event("r2", status="success", input={"filePath": "src/whole.py"}))
    rows = readout()["tools"]
    assert rows[0]["targetFingerprint"] == rows[1]["targetFingerprint"]
    assert rows[0]["range"] == {"offset": 20, "limit": 10}
    assert rows[0]["status"] == "completed" and rows[0]["targetMetadataFinal"] is True
