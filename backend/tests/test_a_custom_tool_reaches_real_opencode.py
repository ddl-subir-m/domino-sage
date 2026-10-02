"""A TypeScript and a Python tool added to a Project reach the model on Chat and Build turns (#622).

ADR-0071. Asked of the pinned OpenCode with a scripted model: the tools in the request OpenCode
sent, and what came back when the model called the Python one. A Python exception is the tool's
error, not its output, so the model and the transcript both read it as a failure.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sage import extensions
from sage.driver.opencode import OpenCodeClient

from .opencode_server import BINARY, _opencode_server
from .test_a_custom_tool_is_added_in_typescript_or_python import ADDER

REPO = Path(__file__).resolve().parents[2]
PROVIDER = REPO / "node_modules" / "@ai-sdk" / "openai-compatible" / "dist" / "index.mjs"
LOOKUP = ("export default { description: 'Look a word up.', args: {}, "
          "async execute() { return 'found' } }\n")
CALLS = {"ok": {"a": 2, "b": 3}, "fail": {"a": -1, "b": 3}}


def _sse(delta: dict, finish: str) -> bytes:
    frame = {"id": "scripted", "object": "chat.completion.chunk", "model": "m",
             "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
    first = "data: " + json.dumps(frame) + "\n\n"
    frame["choices"] = [{"index": 0, "delta": {}, "finish_reason": finish}]
    return (first + "data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n").encode()


@pytest.mark.opencode
@pytest.mark.skipif(not BINARY.exists(), reason="the pinned OpenCode binary is not installed")
def test_a_typescript_and_a_python_tool_reach_the_model_and_a_python_error_is_the_tools(tmp_path):
    seen: list[dict] = []
    failures: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                messages = body.get("messages", [])
                if "title generator" in str(messages[0].get("content", "")).lower():
                    self.wfile.write(_sse({"content": "Tools"}, "stop"))
                    return
                seen.append(body)
                case = next((c for c in CALLS if f"CASE:{c}" in json.dumps(messages)), None)
                if case is None or any(m.get("role") == "tool" for m in messages):
                    self.wfile.write(_sse({"content": "Done."}, "stop"))
                    return
                self.wfile.write(_sse({"tool_calls": [{
                    "index": 0, "id": f"call-{case}", "type": "function",
                    "function": {"name": "adder", "arguments": json.dumps(CALLS[case])}}]},
                    "tool_calls"))
            except Exception as error:  # reported by the test, not swallowed
                failures.append(repr(error))

        def log_message(self, *ignored):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = tmp_path / "project"
    chat, build = root / ".sage" / "chat-work", root / "apps" / "app1"
    chat.mkdir(parents=True)
    build.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    extensions.add(root, {"kind": "tool", "name": "lookup", "code": LOOKUP})
    extensions.add(root, {"kind": "tool", "python": ADDER})
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "opencode.json").write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json", "model": "cap/m",
        "provider": {"cap": {"npm": PROVIDER.as_uri(),
                             "options": {"baseURL": f"http://127.0.0.1:{server.server_port}/v1",
                                         "apiKey": "x"},
                             "models": {"m": {"name": "m", "tool_call": True}}}},
        "permission": {"*": "allow"}}))
    env = dict(os.environ)
    env.update(OPENCODE_CONFIG=str(runtime / "opencode.json"), OPENCODE_DISABLE_AUTOUPDATE="true",
               XDG_CONFIG_HOME=str(runtime / "config"), XDG_DATA_HOME=str(runtime / "data"),
               XDG_CACHE_HOME=str(runtime / "cache"), XDG_STATE_HOME=str(runtime / "state"),
               OPENCODE_CONFIG_DIR=str(runtime / "config" / "opencode"))

    def turn(client: OpenCodeClient, directory: Path, text: str) -> tuple[str, int]:
        before = len(seen)
        sid = client.create_session(str(directory))
        client.send_prompt(sid, text, model={"providerID": "cap", "modelID": "m"}, agent="build")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            assert not failures, failures
            if len(seen) > before and not client.is_running(sid, directory=str(directory)):
                return sid, before
            time.sleep(0.1)
        pytest.fail(f"the turn did not finish; {len(seen)} model calls; {failures}")

    def adder_part(client: OpenCodeClient, sid: str) -> dict:
        parts = [p for m in client.messages(sid) if m.get("type") == "assistant"
                 for p in m.get("content", []) if isinstance(p, dict) and p.get("tool") == "adder"]
        (runtime / f"{sid}-parts.json").write_text(json.dumps(parts, indent=2))
        assert len(parts) == 1, parts
        return parts[0]

    try:
        with _opencode_server(runtime, env) as url:
            client = OpenCodeClient(url)
            for directory in (chat, build):
                _, before = turn(client, directory, "Say hello.")
                offered = {t["function"]["name"] for t in seen[before].get("tools", [])}
                assert {"lookup", "adder"} <= offered, (directory, sorted(offered))
                schema = next(t["function"]["parameters"] for t in seen[before]["tools"]
                              if t["function"]["name"] == "adder")
                assert set(schema["properties"]) == {"a", "b"}, schema

            sid, _ = turn(client, chat, "CASE:ok")
            ran = adder_part(client, sid)
            assert ran["state"]["status"] == "completed", ran
            assert ran["state"]["output"] == '{"sum": 5}', ran

            sid, _ = turn(client, chat, "CASE:fail")
            failed = adder_part(client, sid)
            assert failed["state"]["status"] == "error", failed
            assert "ValueError: adder refuses negative numbers" in failed["state"]["error"], failed
            told = [m for m in seen[-1]["messages"] if m.get("role") == "tool"]
            assert len(told) == 1 and "adder refuses negative numbers" in str(told[0]["content"])
    finally:
        server.shutdown()
        server.server_close()
