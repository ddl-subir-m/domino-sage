"""CSV text analysis uses relevant text and proves complete record coverage."""

import json
import os
import re
import socket
import subprocess
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from sage.driver.opencode import OpenCodeClient
from sage.liveread import run
from sage.liveread.data_use import DataUse

from .test_a_live_read_reaches_the_person_end_to_end import Warehouse, _orch

REPO = Path(__file__).resolve().parents[2]
BINARY = REPO / "node_modules" / ".bin" / "opencode"


COMPLAINTS = "ticket,complaint,email\n" + "\n".join([
    "A-1,Package arrived late,person0@example.invalid",
    "A-2,Delivery driver went to the wrong door,person1@example.invalid",
    "A-3,Tracking said delivered but it was missing,person2@example.invalid",
    "A-4,Shipment was delayed for three days,person3@example.invalid",
    "B-1,Item arrived broken,person4@example.invalid",
    "B-2,The box was crushed and the product cracked,person5@example.invalid",
    "B-3,Screen was scratched when I opened it,person6@example.invalid",
    "B-4,One part was dented in transit,person7@example.invalid",
    "C-1,I was charged twice,person8@example.invalid",
    "C-2,The refund never posted,person9@example.invalid",
    "C-3,Invoice shows the wrong tax,person10@example.invalid",
    "C-4,Promo credit is missing from my bill,person11@example.invalid",
]) + "\n"


def analysis_args(**over):
    return {"operation": "analyze_text", "dataset": "upload", "path": "complaints.csv",
            "text_column": "complaint", "id_column": "ticket",
            "labels": ["delivery", "damage", "billing"], "output_field": "label",
            "batch_size": 4, **over}


def sse(text):
    frame = {"id": "controlled", "choices": [{"index": 0, "delta": {"content": text},
                                               "finish_reason": None}]}
    finish = {"id": "controlled", "choices": [{"index": 0, "delta": {},
                                                "finish_reason": "stop"}]}
    return [("data: " + json.dumps(frame) + "\n\n").encode(),
            ("data: " + json.dumps(finish) + "\n\ndata: [DONE]\n\n").encode()]


def error_sse(message):
    return [("data: " + json.dumps({"error": {"message": message, "type": "policy"}})
             + "\n\n").encode()]


def labels_for(request):
    body = json.loads(request["messages"][1]["content"])
    rows = []
    for record in body["records"]:
        text = record["text"].lower()
        if any(word in text for word in ("broken", "crushed", "cracked", "scratched", "dented")):
            label = "damage"
        elif any(word in text for word in ("charged", "refund", "invoice", "credit", "bill")):
            label = "billing"
        else:
            label = "delivery"
        rows.append({"id": record["id"], "label": label})
    return {"records": rows}


def setup_turn(tmp_path, content=COMPLAINTS, provider=None, **over):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "complaints.csv"
    source.write_text(content)
    journal = []
    data = DataUse()
    if provider is None:
        provider = lambda request: sse(json.dumps(labels_for(request)))
    turn = run.Turn(thread_id="t1", examples_dir=tmp_path / "examples" / "t1",
                    keep_rows=True, data_use_enabled=True,
                    upload_for=lambda p: source if p == "complaints.csv" else None,
                    analyze_text_batch=provider,
                    record_data_use=lambda ev, reply: data.record(ev, reply, journal.append, "turn1"))
    return replace(turn, **over), data, journal, source


def test_complaint_analysis_sends_relevant_text_and_reports_complete_coverage(tmp_path):
    calls = []

    def provider(request):
        calls.append(request)
        return sse(json.dumps(labels_for(request)))

    turn, data, journal, _ = setup_turn(tmp_path, provider=provider)
    reply = json.loads(run.perform("live_read_files", analysis_args(), turn))
    rows = reply["selected"]["rows"]
    assert len(rows) == 12
    counts = {}
    for _, label in rows:
        counts[label] = counts.get(label, 0) + 1
    assert counts == {"delivery": 4, "damage": 4, "billing": 4}
    assert ["r000005", "damage"] in rows
    assert reply["coverage"] == {"total": 12, "processed": 12, "excluded": 0,
                                 "failed": 0, "unfinished": 0}
    table = json.loads((tmp_path / reply["local_reference"]).read_text())
    assert table["columns"] == ["Record ID", "label"]
    assert table["rows"] == rows
    event = data.events("turn1")[0]
    assert event["manifest"]["source_sha256"] if "source_sha256" in event["manifest"] else event["source_sha256"]
    assert event["manifest"]["text_column"] == "complaint"
    traffic = json.dumps(calls)
    assert "Item arrived broken" in traffic
    assert "person4@example.invalid" not in traffic
    assert "email" not in traffic
    assert "Item arrived broken" not in json.dumps(journal)


@pytest.mark.parametrize("bad", [
    lambda ids: {"records": [{"id": ids[0], "label": "delivery"}]},
    lambda ids: {"records": [{"id": ids[0], "label": "delivery"},
                             {"id": ids[0], "label": "damage"}]},
    lambda ids: {"records": [{"id": "outside", "label": "delivery"}]},
    lambda ids: {"records": [{"id": ids[0], "label": 2}]},
])
def test_bad_returned_ids_are_not_complete_output(tmp_path, bad):
    def provider(request):
        ids = [r["id"] for r in json.loads(request["messages"][1]["content"])["records"]]
        return sse(json.dumps(bad(ids)))

    turn, data, _, _ = setup_turn(tmp_path, provider=provider)
    reply = json.loads(run.perform("live_read_files", analysis_args(batch_size=12), turn))
    assert reply["coverage"]["processed"] == 0
    assert reply["coverage"]["failed"] == 12
    assert data.events("turn1")[0]["batches"][0]["error"]


def test_missing_and_duplicated_source_ids_still_get_task_local_ids(tmp_path):
    content = "ticket,complaint,email\n" + "\n".join([
        "DUP,Package arrived late,one@example.invalid",
        "DUP,Item arrived broken,two@example.invalid",
        ",I was charged twice,three@example.invalid",
    ]) + "\n"
    calls = []

    def provider(request):
        calls.append(request)
        return sse(json.dumps(labels_for(request)))

    turn, _, _, _ = setup_turn(tmp_path, content=content, provider=provider)
    reply = json.loads(run.perform("live_read_files", analysis_args(batch_size=3), turn))
    assert reply["selected"]["rows"] == [
        ["r000001", "delivery"],
        ["r000002", "damage"],
        ["r000003", "billing"],
    ]
    body = json.loads(calls[0]["messages"][1]["content"])
    assert [r["id"] for r in body["records"]] == ["r000001", "r000002", "r000003"]
    assert "DUP" not in json.dumps(body)
    assert "email" not in json.dumps(body)


def test_ordinary_retry_does_not_double_count_records(tmp_path):
    calls = []

    def provider(request):
        calls.append(request)
        if len(calls) == 1:
            return sse(json.dumps({"records": []}))
        return sse(json.dumps(labels_for(request)))

    turn, _, _, _ = setup_turn(tmp_path, provider=provider)
    reply = json.loads(run.perform("live_read_files", analysis_args(batch_size=12), turn))
    assert len(calls) == 2
    assert reply["coverage"]["processed"] == 12
    assert reply["coverage"]["failed"] == 0


def test_policy_denial_is_not_retried_unchanged(tmp_path):
    calls = []

    def provider(request):
        calls.append(request)
        return error_sse("denied by policy")

    turn, data, _, _ = setup_turn(tmp_path, provider=provider)
    reply = json.loads(run.perform("live_read_files", analysis_args(batch_size=12), turn))
    assert len(calls) == 1
    assert reply["coverage"]["processed"] == 0
    assert reply["coverage"]["failed"] == 12
    assert data.events("turn1")[0]["requests"][0]["state"] == "failed"


def test_interrupted_batch_is_unfinished_even_with_json_content(tmp_path):
    calls = []

    def provider(request):
        calls.append(request)
        frame = {"id": "partial", "choices": [{"index": 0,
                 "delta": {"content": json.dumps(labels_for(request))}, "finish_reason": None}]}
        return [("data: " + json.dumps(frame) + "\n\n").encode()]

    turn, data, _, _ = setup_turn(tmp_path, provider=provider)
    reply = json.loads(run.perform("live_read_files", analysis_args(batch_size=12), turn))
    assert len(calls) == 2
    assert reply["coverage"]["processed"] == 0
    assert reply["coverage"]["unfinished"] == 12
    assert data.events("turn1")[0]["batches"][0]["error"] == "interrupted"


def test_ten_thousand_records_are_processed_by_bounded_batches(tmp_path):
    content = "complaint,email\n" + "\n".join(
        f"Package arrived late {i},person{i}@example.invalid" for i in range(10_000)
    ) + "\n"
    calls = []

    def provider(request):
        calls.append(request)
        return sse(json.dumps(labels_for(request)))

    turn, _, _, _ = setup_turn(tmp_path, content=content, provider=provider)
    reply = json.loads(run.perform("live_read_files", analysis_args(
        id_column=None, batch_size=100, max_concurrency=4), turn))
    assert reply["coverage"] == {"total": 10_000, "processed": 10_000, "excluded": 0,
                                 "failed": 0, "unfinished": 0}
    assert len(calls) == 100
    assert reply["selected"] == {"counts": {"delivery": 10_000}}
    assert "email" not in json.dumps(calls[0])


def test_source_change_prevents_a_complete_claim(tmp_path):
    calls = 0
    holder = {}

    def provider(request):
        nonlocal calls
        calls += 1
        Path(holder["source"]).write_text(COMPLAINTS + "C-5,Another bill problem,later@example.invalid\n")
        return sse(json.dumps(labels_for(request)))

    turn, data, _, source = setup_turn(tmp_path, provider=provider)
    holder["source"] = source
    reply = json.loads(run.perform("live_read_files", analysis_args(batch_size=12), turn))
    assert calls == 1
    assert reply["coverage"]["processed"] == 0
    assert reply["coverage"]["unfinished"] == 12
    assert "source changed" in reply["warning"]
    assert data.events("turn1")[0]["manifest"]["source_changed"] is True


def test_limit_over_ten_thousand_offers_labelled_sample_instead_of_sampling(tmp_path):
    content = "complaint\n" + "\n".join(f"Late delivery {i}" for i in range(10_001)) + "\n"
    turn, _, journal, _ = setup_turn(tmp_path, content=content)
    reply = run.perform("live_read_files", analysis_args(id_column=None), turn)
    assert "above the 10000 record analysis limit" in reply
    assert "explicitly labelled sample" in reply
    assert journal == []


def test_chat_live_result_keeps_text_analysis_data_used_detail(tmp_path):
    turn, data, _journal, _ = setup_turn(tmp_path)
    run.perform("live_read_files", analysis_args(), turn)
    frames = [{"type": "agent", "kind": "text", "text": "Done."},
              {"type": "done", "ok": True, "dataUsed": data.events("turn1")}]
    harness = Path(__file__).parent / "js" / "chat_stream_harness.mjs"
    output = subprocess.run(["node", str(harness)], input=json.dumps(frames),
                            text=True, capture_output=True, check=True)
    details = [b for b in json.loads(output.stdout)["final"] if b["type"] == "data_used"]
    assert len(details) == 1
    assert details[0]["event"]["operation"] == "text_analysis"
    assert details[0]["event"]["coverage"]["processed"] == 12


@pytest.mark.skipif(not BINARY.exists(), reason="Install the pinned OpenCode package for the real flow")
def test_real_opencode_analyzes_complaints_without_sending_email_column(tmp_path):
    orch, _ = _orch(tmp_path, Warehouse())
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    upload = orch.upload_scratch("complaints.csv", COMPLAINTS.encode())
    orch.add_thread_context(tid, {"kind": "file", "path": upload["path"], "name": "complaints.csv"})
    project.build_conversation = tid
    control_token = project.control.arm_chat(tid)
    token = orch._mint_live_read_token(tid)
    calls = []
    gateway_state = {"tool_called": False}

    class Gateway:
        def route(self, body, labels):
            calls.append(body)
            text = json.dumps(body.get("messages", []))
            if body.get("tools") and not gateway_state["tool_called"]:
                gateway_state["tool_called"] = True
                arguments = analysis_args(token=token, path=upload["path"], batch_size=12)
                delta = {"tool_calls": [{"index": 0, "id": "analyze_complaints", "type": "function",
                                         "function": {"name": "live_read_files",
                                                      "arguments": json.dumps(arguments)}}]}
                finish = "tool_calls"
            elif "Item arrived broken" in text:
                assert "@example.invalid" not in text
                assert '"email"' not in text
                delta = {"content": json.dumps(labels_for(body))}
                finish = "stop"
            else:
                assert "delivery" in text and "damage" in text and "billing" in text
                delta = {"content": "Delivery 4. Damage 4. Billing 4."}
                finish = "stop"
            frame = {"id": "controlled", "object": "chat.completion.chunk", "model": "alias",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
            yield ("data: " + json.dumps(frame) + "\n\n").encode()
            frame["choices"] = [{"index": 0, "delta": {}, "finish_reason": finish}]
            yield ("data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n").encode()

    project.shim._gateway = Gateway()
    failures = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/mcp/live-read":
                    response = json.dumps(orch.live_read_call(body)).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(response)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if "title generator" in str(body.get("messages", [{}])[0].get("content", "")).lower():
                    frame = {"id": "title", "choices": [{"index": 0,
                             "delta": {"content": "Complaint analysis"}, "finish_reason": "stop"}]}
                    self.wfile.write(("data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n").encode())
                    return
                for chunk in project.shim.handle(body, project="synthetic", session="controlled"):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except Exception as error:
                failures.append(repr(error))

        def log_message(self, *ignored):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    config = json.loads((REPO / "opencode.json").read_text())
    config["provider"]["sage-gateway"]["options"]["baseURL"] = f"http://127.0.0.1:{server.server_port}/v1"
    config["plugin"] = []
    config["mcp"] = {}
    config["permission"] = {"*": "allow"}
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    config_path = runtime / "opencode.json"
    config_path.write_text(json.dumps(config))
    tools = runtime / "config" / "opencode" / "tools"
    tools.mkdir(parents=True)
    (tools / "live_read.ts").write_text((REPO / "backend/sage/liveread/tools/live_read.ts").read_text())
    env = dict(os.environ)
    env.update(OPENCODE_CONFIG=str(config_path), OPENCODE_DISABLE_AUTOUPDATE="true",
               XDG_CONFIG_HOME=str(runtime / "config"), XDG_DATA_HOME=str(runtime / "data"),
               XDG_CACHE_HOME=str(runtime / "cache"), XDG_STATE_HOME=str(runtime / "state"),
               OPENCODE_CONFIG_DIR=str(runtime / "config" / "opencode"),
               SAGE_CONTROL_PORT=str(server.server_port))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log = (runtime / "opencode.log").open("w")
    process = subprocess.Popen([str(BINARY), "serve", "--port", str(port), "--hostname", "127.0.0.1"],
                               cwd=runtime, env=env, stdout=log, stderr=log)
    try:
        url = f"http://127.0.0.1:{port}"
        for _ in range(150):
            try:
                if httpx.get(url + "/global/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            assert process.poll() is None, (runtime / "opencode.log").read_text()
            time.sleep(0.1)
        else:
            pytest.fail("Isolated OpenCode did not start")
        client = OpenCodeClient(url)
        directory = str(project.record.path)
        sid = client.create_session(directory)
        prompt = (f"Thread id: {tid}. Read token: {token}. Classify every complaint in "
                  f"{upload['path']} as delivery, damage or billing. Include complete coverage. "
                  + orch._data_use_note())
        httpx.get(url + "/agent", params={"directory": directory}, timeout=120).raise_for_status()
        client.send_prompt(sid, prompt, agent="sage-chat")
        deadline = time.monotonic() + 150
        messages = []
        while time.monotonic() < deadline:
            assert not failures, failures
            try:
                messages = client.messages(sid)
            except httpx.ReadTimeout:
                continue
            if len(calls) >= 3 and not client.is_running(sid, directory=directory):
                break
            time.sleep(0.1)
        else:
            pytest.fail(f"OpenCode task did not complete: {failures}; {len(calls)} calls; {messages}")
        assert not failures, failures
        assert len(calls) == 3
        assert "@example.invalid" not in json.dumps(calls)
        assert not re.search(r'"email"', json.dumps(calls[1]["messages"]))
        tables = list((project.record.path / "examples" / tid).glob("*.table.json"))
        assert len(tables) == 1
        table = json.loads(tables[0].read_text())
        assert table["rows"].count(["r000005", "damage"]) == 1
        counts = {}
        for _, label in table["rows"]:
            counts[label] = counts.get(label, 0) + 1
        assert counts == {"delivery": 4, "damage": 4, "billing": 4}
        events = project.shim.data_use.events(orch._data_use_turns[tid])
        assert events[0]["coverage"]["processed"] == 12
        assert events[0]["requests"][0]["state"] == "response_completed"
    finally:
        process.terminate()
        process.wait(timeout=10)
        log.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        project.control.disarm_chat(control_token)
