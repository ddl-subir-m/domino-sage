"""The selected Build capture survives restart and exports only bounded metadata (#512)."""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage import build_diagnostics as diagnostics
from sage import timing

from .ledger import needs_ledger, own_ledger
from .test_a_dropped_mention_reaches_the_agents_prompt import _orch


@pytest.fixture(autouse=True)
def isolate_capture():
    yield
    diagnostics._current.set(None)
    diagnostics._active.clear()


def identity(turn="turn_a", app="app_a", conversation="thr_a"):
    return {"turnId": turn, "appId": app, "conversationId": conversation, "kind": "build"}


def record(turn="turn_a"):
    rec = timing.TurnRecord("build", time.time(), time.monotonic(), turn_id=turn,
                            app_id="app_a", conversation_id="thr_a")
    rec.t1 = rec.t0 + 1
    return rec


def saved(root, turn="turn_a", **kwargs):
    value = diagnostics.snapshot(record(turn), identity(turn), terminal=True, revision="a" * 40, **kwargs)
    assert diagnostics.Store(root).put(value)
    return value


def test_finished_capture_survives_a_real_process_restart(tmp_path):
    saved(tmp_path, outcome="success")
    code = ("import json; from pathlib import Path; from sage.build_diagnostics import Store; "
            f"print(json.dumps(Store(Path({str(tmp_path)!r})).get('turn_a','app_a','thr_a')))")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    row = json.loads(result.stdout)
    assert row["turn"]["turnId"] == "turn_a"
    assert row["capture"]["complete"] is True
    assert row["buildOutcome"]["status"] == "success"
    assert row["sourceRevision"] == "a" * 40


def test_retention_has_twenty_records_and_an_explicit_drop_count(tmp_path):
    for n in range(23):
        saved(tmp_path, f"turn_{n}")
    store = diagnostics.Store(tmp_path)
    assert store.get("turn_0", "app_a", "thr_a") is None
    retained = store.get("turn_3", "app_a", "thr_a")
    assert retained["retention"]["droppedRecords"] == 3
    assert len(json.loads(store.path.read_text())["records"]) == 20
    assert store.path.stat().st_size <= diagnostics.MAX_STORE_BYTES


@own_ledger
def test_interrupted_start_remains_incomplete_after_restart(tmp_path):
    timing.start_turn("build", turn_id="turn_a", app_id="app_a", conversation_id="thr_a")
    diagnostics.begin(tmp_path, turn_id="turn_a", app_id="app_a", conversation_id="thr_a", kind="build")
    assert diagnostics.Store(tmp_path).get("turn_a", "app_a", "thr_a")["capture"]["status"] == "running"
    code = ("import json; from pathlib import Path; from sage.build_diagnostics import Store; "
            f"print(json.dumps(Store(Path({str(tmp_path)!r})).get('turn_a','app_a','thr_a')))")
    row = json.loads(subprocess.check_output([sys.executable, "-c", code], text=True))
    assert row["capture"]["status"] == "interrupted" and row["capture"]["complete"] is False
    assert row["buildOutcome"]["status"] == "unknown"
    timing.finish_turn()


def test_finished_capture_does_not_invent_a_build_outcome(tmp_path):
    row = saved(tmp_path)
    assert row["capture"]["status"] == "finished"
    assert row["buildOutcome"]["status"] == "unknown"


def test_private_payloads_and_unknown_fields_never_reach_the_export(tmp_path):
    private = "PRIVATE_SENTINEL_credential_code_row"
    rec = record()
    rec.prompt = rec.decision = private
    rec.spans = [timing.Span("agent-turn.1", 0, rec.t0, rec.t1,
                            {"why": private, "error": private, "code": private,
                             "stack": "fastapi-static", "no_edit_attempt": 1,
                             "wrote_code": False, "retry_reason": "no_edit", "retry_exhausted": True})]
    call = timing.ModelCall(1, rec.t0, model="GLM", phase="implement", t1=rec.t1, error=private)
    call.tool_invocations = [{"name": "read", "providerId": "call_a", "arguments": private,
                              "reasoning": private, "signature": private, "content": private}]
    rec.calls = [call]
    observer = timing.ToolObserver(rec, diagnostics._lock)
    rec.t1 = None
    observer.event("session_a", {"call_id": "tool_a", "tool": "read", "status": "completed",
                                 "input": {"filePath": "/private/" + private, "offset": 2}})
    rec.t1 = rec.t0 + 1
    rec.intervals = [{"name": "poll.read", "atMs": 1, "ms": 20, "ok": True, "error": private}]
    rec.repeat_brake = [{"sessionId": "session_a", "tool": "bash", "stopped": True,
                         "inputFingerprint": "abc123", "command": private,
                         "argumentKeys": ["command", "description"],
                         "argumentKeysTruncated": False, "executableVariant": 1,
                         "metadataVariant": 2, "detectedCycleLength": 2}]
    rec.counters[private] = 9
    row = diagnostics.snapshot(rec, identity(), terminal=True)
    assert diagnostics.Store(tmp_path).put(row)
    serialized = json.dumps(diagnostics.Store(tmp_path).get("turn_a", "app_a", "thr_a"))
    assert private not in serialized and private not in diagnostics.Store(tmp_path).path.read_text()
    span = row["timing"]["spans"][0]
    assert span["retry_reason"] == "no_edit" and span["retry_exhausted"] is True
    assert span["retryCategory"] == "unknown" and "why" not in span
    assert row["timing"]["tools"][0]["range"] == {"offset": 2}
    assert row["timing"]["tools"][0]["targetFingerprint"]
    assert row["timing"]["repeatBrake"][0]["stopped"] is True
    assert row["timing"]["repeatBrake"][0] == {
        "sessionId": "session_a", "tool": "bash", "inputFingerprint": "abc123",
        "stopped": True, "argumentKeysTruncated": False, "executableVariant": 1,
        "metadataVariant": 2, "detectedCycleLength": 2,
        "argumentKeys": ["command", "description"],
    }


def test_nested_events_and_bytes_are_capped_and_reported(monkeypatch):
    monkeypatch.setattr(diagnostics, "MAX_EVENTS", 8)
    rec = record()
    for n in range(10):
        call = timing.ModelCall(n, rec.t0, t1=rec.t1)
        call.tool_invocations = [{"name": "read", "providerId": "p" * 100} for _ in range(10)]
        rec.calls.append(call)
    row = diagnostics.snapshot(rec, identity(), terminal=True)
    assert len(row["timing"]["calls"]) + sum(len(c["toolInvocations"]) for c in row["timing"]["calls"]) <= 8
    assert row["capture"]["droppedEvents"]["toolInvocations"] > 0
    assert row["capture"]["complete"] is False
    monkeypatch.setattr(diagnostics, "MAX_RECORD_BYTES", 2000)
    row = diagnostics.snapshot(rec, identity(), terminal=True)
    assert len(diagnostics._encode(row)) <= 2000
    assert row["capture"]["droppedEvents"]["calls"] > 0


def test_upstream_truncation_stays_visible():
    rec = record()
    rec.tools_truncated = rec.intervals_truncated = rec.repeat_brake_truncated = True
    row = diagnostics.snapshot(rec, identity(), terminal=True)
    assert all(row["capture"]["upstreamTruncated"][key] for key in
               ("toolsTruncated", "intervalsTruncated", "repeatBrakeTruncated"))
    assert row["capture"]["complete"] is False


@needs_ledger
def test_real_build_stamps_the_same_selected_turn_on_history_and_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    orch, _ = _orch(tmp_path)
    writes = []
    put = diagnostics.Store.put

    def count_write(store, record):
        writes.append(record)
        return put(store, record)

    monkeypatch.setattr(diagnostics.Store, "put", count_write)
    list(orch.build_stream("Build a table"))
    assert len(writes) == 2
    project = orch.project(start_preview=False)
    user = next(row for row in project.workspace.read_history() if row["type"] == "user")
    row = diagnostics.Store(project.record.path).get(user["turnId"], user["app"], user.get("conversation", ""))
    assert row and row["turn"]["turnId"] == user["turnId"]
    assert row["capture"]["status"] == "finished"
    assert row["buildOutcome"]["status"] == "success"
    assert row["capture"]["complete"] is True
    subprocess.run(["git", "-C", str(project.record.path), "init", "-q"], check=True)
    ignored = subprocess.run(["git", "-C", str(project.record.path), "check-ignore", ".sage/build-diagnostics.json"],
                             capture_output=True, text=True, check=False)
    assert ignored.returncode == 0


def test_failed_persistence_does_not_fail_build(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    orch, oc = _orch(tmp_path)

    def broken(*args):
        raise OSError("private filesystem error")

    monkeypatch.setattr(diagnostics.Store, "put", broken)
    events = list(orch.build_stream("Build a table"))
    assert oc.prompts and any(e["type"] == "done" and e["ok"] for e in events)


def test_exact_scope_and_prefix_on_existing_workspace_route(tmp_path, monkeypatch):
    import sage.orchestrator.app as appmod

    orch, _ = _orch(tmp_path)
    root = orch.project(start_preview=False).record.path
    saved(root)
    project = orch.project(start_preview=False)

    def read_project(*, start_preview, seed_app):
        assert start_preview is False and seed_app is False
        return project

    monkeypatch.setattr(orch, "project", read_project)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    prefix = "/owner/project/notebookSession/run"
    client = TestClient(appmod._PrefixMiddleware(appmod.control_app, prefix))
    path = prefix + "/api/project/build-diagnostics/turn_a"
    response = client.get(path, params={"app_id": "app_a", "conversation_id": "thr_a"})
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.json()["turn"]["turnId"] == "turn_a"
    for app, conversation in (("app_b", "thr_a"), ("app_a", "thr_b")):
        response = client.get(path, params={"app_id": app, "conversation_id": conversation})
        assert response.status_code == 404
    assert client.get(path.replace("turn_a", "expired"), params={"app_id": "app_a"}).status_code == 404


def test_history_download_selects_older_turn_and_uses_prefix_safe_api():
    harness = Path(__file__).parent / "js" / "build_diagnostics_harness.mjs"
    result = subprocess.run(["node", str(harness)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["ok"] is True


def test_disabled_recorder_is_explicit_and_does_not_reuse_another_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGE_TIMING", "0")
    monkeypatch.setattr(timing, "current", lambda: record("another_turn"))
    diagnostics.begin(tmp_path, turn_id="turn_a", app_id="app_a", conversation_id="thr_a", kind="build")
    start = diagnostics.Store(tmp_path).get("turn_a", "app_a", "thr_a")
    assert start["capture"]["recordAvailable"] is False
    diagnostics.history_metadata("app_a", "thr_a", {"type": "done", "ok": True})
    diagnostics.finish(None)
    end = diagnostics.Store(tmp_path).get("turn_a", "app_a", "thr_a")
    assert end["capture"]["status"] == "finished"
    assert end["capture"]["complete"] is False and end["capture"]["recorderEnabled"] is False
    assert end["buildOutcome"]["status"] == "success"


def test_atomic_write_failure_keeps_last_record_and_logs_no_error_payload(tmp_path, monkeypatch, caplog):
    saved(tmp_path)
    before = diagnostics.Store(tmp_path).path.read_bytes()

    def fail(*args):
        raise OSError("PRIVATE_DISK_PATH")

    monkeypatch.setattr(diagnostics.os, "replace", fail)
    assert diagnostics.Store(tmp_path).put(diagnostics.snapshot(record("turn_b"), identity("turn_b"))) is False
    assert diagnostics.Store(tmp_path).path.read_bytes() == before
    assert "PRIVATE_DISK_PATH" not in caplog.text
    assert not list((tmp_path / ".sage").glob(".build-diagnostics-*"))


def test_nested_metadata_truncation_means_capture_is_incomplete():
    rec = record()
    call = timing.ModelCall(1, rec.t0, t1=rec.t1)
    call.tool_invocations = [{"name": "read", "metadataTruncated": True}]
    rec.calls = [call]
    row = diagnostics.snapshot(rec, identity(), terminal=True)
    assert row["capture"]["upstreamTruncated"]["toolInvocations"] is True
    assert row["capture"]["complete"] is False
