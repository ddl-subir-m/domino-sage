"""Real OpenCode custom tools, Sage operations, and a controlled HTTP gateway.

No real credentials or shared runtime state. The installed pinned OpenCode binary is required.
"""

import fcntl
import json
import os
import re
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from sage.driver.opencode import OpenCodeClient

from .test_a_live_read_reaches_the_person_end_to_end import Warehouse, _orch
from .test_csv_calculation_data_used import SALES, args

REPO = Path(__file__).resolve().parents[2]
BINARY = REPO / "node_modules" / ".bin" / "opencode"


@contextmanager
def _opencode_server(runtime, env):
    lock_path = Path(os.environ.get("SAGE_OPENCODE_TEST_LOCK", "/tmp/sage-opencode-test.lock"))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        log = (runtime / "opencode.log").open("w")
        process = subprocess.Popen([str(BINARY), "serve", "--port", str(port),
                                    "--hostname", "127.0.0.1"],
                                   cwd=runtime, env=env, stdout=log, stderr=log)
        try:
            url = f"http://127.0.0.1:{port}"
            for _ in range(600):
                try:
                    if httpx.get(url + "/global/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert process.poll() is None, (runtime / "opencode.log").read_text()
                time.sleep(0.1)
            else:
                pytest.fail("Isolated OpenCode did not start")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            log.close()
            fcntl.flock(lock, fcntl.LOCK_UN)


@pytest.mark.skipif(not BINARY.exists(), reason="Install the pinned OpenCode package for the real flow")
@pytest.mark.parametrize("mode", ["chat", "build"])
def test_real_opencode_calculates_sales_without_sending_the_email_column(tmp_path, mode):
    orch, _ = _orch(tmp_path, Warehouse())
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    upload = (orch.upload_scratch("sales.csv", SALES.encode()) if mode == "chat"
              else orch.upload_file("sales.csv", SALES.encode()))
    orch.add_thread_context(tid, {"kind": "file", "path": upload["path"], "name": "sales.csv"})
    project.build_conversation = tid
    control_token = project.control.arm_chat(tid) if mode == "chat" else None
    token = orch._mint_live_read_token(tid)
    calls = []

    class Gateway:
        def route(self, body, labels):
            calls.append(body)
            if len(calls) == 1:
                tools = {t["function"]["name"] for t in body.get("tools", [])}
                assert "live_read_files" in tools
                assert "task" in tools and "todowrite" in tools
                arguments = args(token=token, path=upload["path"])
                delta = {"tool_calls": [{"index": 0, "id": "calculate_sales", "type": "function",
                                         "function": {"name": "live_read_files",
                                                      "arguments": json.dumps(arguments)}}]}
                finish = "tool_calls"
            else:
                assert len(calls) == 2, "No extra result-selection round"
                text = json.dumps(body["messages"])
                assert "780" in text and "360" in text and "420" in text
                delta = {"content": "North 360. South 420. Total 780."}
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
                # OpenCode's title request is auxiliary, with no CSV values or data operation.
                if "title generator" in str(body.get("messages", [{}])[0].get("content", "")).lower():
                    frame = {"id": "title", "choices": [{"index": 0,
                             "delta": {"content": "Sales totals"}, "finish_reason": "stop"}]}
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
    try:
        with _opencode_server(runtime, env) as url:
            client = OpenCodeClient(url)
            directory = str(project.record.path)
            sid = client.create_session(directory)
            prompt = (f"Thread id: {tid}. Read token: {token}. Calculate revenue by region from "
                      f"{upload['path']}. State the regional totals and grand total. "
                      + orch._data_use_note())
            httpx.get(url + "/agent", params={"directory": directory}, timeout=120).raise_for_status()
            client.send_prompt(sid, prompt, agent="sage-chat" if mode == "chat" else "build")
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                assert not failures, failures
                try:
                    messages = client.messages(sid)
                except httpx.ReadTimeout:
                    continue
                if len(calls) >= 2 and not client.is_running(sid, directory=directory):
                    break
                time.sleep(0.1)
            else:
                pytest.fail(f"OpenCode task did not complete: {failures}; {len(calls)} calls; {messages}")
            assert not failures, failures
            assert len(calls) == 2
            assert "@example.invalid" not in json.dumps(calls)
            assert not re.search(r'"email"', json.dumps(calls[1]["messages"]))
            tables = list((project.record.path / "examples" / tid).glob("*.table.json"))
            assert len(tables) == 1
            assert json.loads(tables[0].read_text())["rows"] == [["North", "360"], ["South", "420"]]
            events = project.shim.data_use.events(orch._data_use_turns[tid])
            assert events[0]["requests"][0]["state"] == "response_completed"
            assert events[0]["requests"][0]["serving_model"] is None
            (runtime / "requests.json").write_text(json.dumps(calls, indent=2))
            (runtime / "data-used.json").write_text(json.dumps(events, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        if control_token:
            project.control.disarm_chat(control_token)


@pytest.mark.skipif(not BINARY.exists(), reason="Install the pinned OpenCode package for the real flow")
def test_real_opencode_direct_read_and_python_output_stay_local_on_next_requests(tmp_path):
    orch, _ = _orch(tmp_path, Warehouse())
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    upload = orch.upload_scratch("sales.csv", SALES.encode())
    orch.add_thread_context(tid, {"kind": "file", "path": upload["path"], "name": "sales.csv"})
    project.build_conversation = tid
    control_token = project.control.arm_chat(tid)
    calls = []

    def tool_call(turn):
        if turn == 1:
            return {"name": "read", "arguments": {"filePath": upload["path"]}, "id": "read_call"}
        return {"name": "bash", "arguments": {"command": (
            "python - <<'PY'\n"
            "from pathlib import Path\n"
            f"import sys; sys.stderr.write(Path({upload['path']!r}).read_text())\n"
            "raise SystemExit(1)\n"
            "PY"
        )}, "id": "python_stderr_call"}

    class Gateway:
        def route(self, body, labels):
            calls.append(body)
            if len(calls) in (1, 3):
                tool = tool_call(1 if len(calls) == 1 else 2)
                tools = {t["function"]["name"] for t in body.get("tools", [])}
                assert tool["name"] in tools
                delta = {"tool_calls": [{"index": 0, "id": tool["id"], "type": "function",
                                         "function": {"name": tool["name"],
                                                      "arguments": json.dumps(tool["arguments"])}}]}
                finish = "tool_calls"
            else:
                assert len(calls) in (2, 4)
                text = json.dumps(body["messages"])
                assert "person0@example.invalid" not in text
                assert "local_execution_receipt" in text
                assert "tool_call_id" in text
                delta = {"content": "I checked the file shape." if len(calls) == 2
                         else "I checked the failing command."}
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
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if "title generator" in str(body.get("messages", [{}])[0].get("content", "")).lower():
                    frame = {"id": "title", "choices": [{"index": 0,
                             "delta": {"content": "Sales file"}, "finish_reason": "stop"}]}
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
    env = dict(os.environ)
    env.update(OPENCODE_CONFIG=str(config_path), OPENCODE_DISABLE_AUTOUPDATE="true",
               XDG_CONFIG_HOME=str(runtime / "config"), XDG_DATA_HOME=str(runtime / "data"),
               XDG_CACHE_HOME=str(runtime / "cache"), XDG_STATE_HOME=str(runtime / "state"),
               OPENCODE_CONFIG_DIR=str(runtime / "config" / "opencode"))
    try:
        with _opencode_server(runtime, env) as url:
            client = OpenCodeClient(url)
            directory = str(project.record.path)
            sid = client.create_session(directory)
            attachment = {"path": upload["path"], "name": "sales.csv",
                          "summary": "CSV - 3 columns, 12 rows",
                          "detail": "columns: region, revenue, email"}
            client.send_prompt(sid, "Read @sales.csv and report its shape.",
                               agent="sage-chat", attachments=[attachment], chat=True)
            deadline = time.monotonic() + 150
            messages = []
            while time.monotonic() < deadline:
                assert not failures, failures
                try:
                    messages = client.messages(sid)
                except httpx.ReadTimeout:
                    continue
                if len(calls) >= 2 and not client.is_running(sid, directory=directory):
                    break
                time.sleep(0.1)
            else:
                pytest.fail(f"OpenCode task did not complete: {failures}; {len(calls)} calls; {messages}")
            assert "person0@example.invalid" in json.dumps(messages)

            client.send_prompt(sid, "Run Python over @sales.csv and report failure shape.",
                               agent="sage-chat", attachments=[attachment], chat=True)
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                assert not failures, failures
                try:
                    messages = client.messages(sid)
                except httpx.ReadTimeout:
                    continue
                if len(calls) >= 4 and not client.is_running(sid, directory=directory):
                    break
                time.sleep(0.1)
            else:
                pytest.fail(f"OpenCode task did not complete: {failures}; {len(calls)} calls; {messages}")

            assert not failures, failures
            assert len(calls) == 4
            assert "person0@example.invalid" in json.dumps(messages)
            assert "person0@example.invalid" not in json.dumps(calls)
            (runtime / "requests.json").write_text(json.dumps(calls, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        project.control.disarm_chat(control_token)
