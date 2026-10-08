"""#700: the pinned OpenCode discovers `sage_source_map`, and a Build turn's request carries it only when
the Project switched it on.

The offered tool set changed, so this asks the real binary rather than a fake: the filename is the
tool name, OpenCode decides whether the module loads, and only the request that reaches the gateway
says what the model was actually offered. With the map on, the model's call goes through the real
custom tool to `/mcp/source-map` and the answer comes back in the next request. With it off, the tool
is not in the request at all.

Real-OpenCode only: run from the repo root with `--opencode`.
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from sage.driver.opencode import OpenCodeClient
from sage.orchestrator.app import _install_opencode_tools

from .opencode_server import BINARY, _opencode_server
from .test_chat_turn import _orch

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.opencode
@pytest.mark.skipif(not BINARY.exists(), reason="Install the pinned OpenCode package for the real flow")
@pytest.mark.parametrize("offered", [True, False], ids=["switched-on", "switched-off"])
def test_a_build_turn_is_offered_the_map_only_when_switched_on_and_its_answer_comes_back(
        tmp_path, offered):
    orch, _ = _orch(tmp_path)
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    project.build_conversation = tid
    token = orch._mint_live_read_token(tid)
    armed = project.control.arm_source_map() if offered else None
    calls: list[dict] = []
    failures: list[str] = []

    def frames(delta: dict, finish: str):
        frame = {"id": "controlled", "object": "chat.completion.chunk", "model": "alias",
                 "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
        yield ("data: " + json.dumps(frame) + "\n\n").encode()
        frame["choices"] = [{"index": 0, "delta": {}, "finish_reason": finish}]
        yield ("data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n").encode()

    class Gateway:
        def route(self, body, labels):
            calls.append(body)
            if len(calls) == 1:
                tools = {t["function"]["name"] for t in body.get("tools", [])}
                assert ("sage_source_map" in tools) is offered, sorted(tools)
                if offered:
                    arguments = {"token": token, "symbol": "App", "paths": None}
                    yield from frames({"tool_calls": [{
                        "index": 0, "id": "map", "type": "function",
                        "function": {"name": "sage_source_map",
                                     "arguments": json.dumps(arguments)}}]}, "tool_calls")
                    return
            yield from frames({"content": "Done."}, "stop")

    project.shim._gateway = Gateway()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/mcp/source-map":
                    response = json.dumps(orch.source_map_call(body)).encode()
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
                             "delta": {"content": "Map"}, "finish_reason": "stop"}]}
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
    _install_opencode_tools(REPO, runtime / "config" / "opencode")
    env = dict(os.environ)
    env.update(OPENCODE_CONFIG=str(config_path), OPENCODE_DISABLE_AUTOUPDATE="true",
               XDG_CONFIG_HOME=str(runtime / "config"), XDG_DATA_HOME=str(runtime / "data"),
               XDG_CACHE_HOME=str(runtime / "cache"), XDG_STATE_HOME=str(runtime / "state"),
               OPENCODE_CONFIG_DIR=str(runtime / "config" / "opencode"),
               SAGE_CONTROL_PORT=str(server.server_port))
    want = 2 if offered else 1
    try:
        with _opencode_server(runtime, env) as url:
            client = OpenCodeClient(url)
            directory = str(project.workspace.path)
            listed = httpx.get(url + "/experimental/tool", timeout=120, params={
                "provider": "sage-gateway", "model": config["model"].split("/", 1)[1],
                "agent": "sage-implement", "directory": directory})
            listed.raise_for_status()
            assert "sage_source_map" in {str(t.get("id") or t.get("name")) for t in listed.json()}, (
                "OpenCode did not make a tool of sage_source_map.ts")
            sid = client.create_session(directory)
            client.send_prompt(sid, f"Read token: {token}. Which file defines App?",
                               agent="sage-implement")
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                assert not failures, failures
                if len(calls) >= want and not client.is_running(sid, directory=directory):
                    break
                time.sleep(0.1)
            else:
                pytest.fail(f"OpenCode turn did not complete: {failures}; {len(calls)} calls")
            assert not failures, failures
            assert len(calls) == want
            if offered:
                answered = json.dumps(calls[1]["messages"])
                assert "schemaVersion" in answered and "src/App.tsx" in answered
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        if armed is not None:
            project.control.disarm_source_map(armed)
