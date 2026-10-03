"""A local and a remote MCP server added from the panel connect, and their tools reach the model (#621).

ADR-0071. Against the pinned OpenCode: both servers read `connected` in the panel's status, asked
of Chat's directory and of a Built App's, and their tools are in the request OpenCode sends the
model from each. A third server naming an unset variable reads as failed in the panel, whatever
OpenCode says of it. The model request is captured by a local stand-in for the gateway.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from sage.driver.opencode import OpenCodeClient

from . import mcp_stub
from .opencode_server import BINARY, _opencode_server
from .test_an_added_skill_reaches_real_opencode_without_a_restart import _Capture, _system
from .test_no_edit_recovery_uses_the_active_stack import _no_waiting  # noqa: F401  (autouse)
from .test_turn_path import _build

REPO = Path(__file__).resolve().parents[2]
PROVIDER = REPO / "node_modules" / "@ai-sdk" / "openai-compatible" / "dist" / "index.mjs"


def _tools_sent(client: OpenCodeClient, capture: _Capture, directory: Path) -> set[str]:
    _system(client, capture, directory)
    return {t["function"]["name"] for t in capture.seen[-1].get("tools") or []}


@pytest.mark.opencode
@pytest.mark.skipif(not BINARY.exists(), reason="the pinned OpenCode binary is not installed")
def test_a_local_and_a_remote_server_connect_and_reach_chat_and_build(tmp_path, monkeypatch):
    remote = ThreadingHTTPServer(("127.0.0.1", 0), mcp_stub.Handler)
    remote.token = "s3cret"
    threading.Thread(target=remote.serve_forever, daemon=True).start()
    capture = _Capture()
    monkeypatch.setenv("STUB_TOKEN_621", "s3cret")
    monkeypatch.delenv("UNSET_621", raising=False)

    orch, _, _ = _build(tmp_path / "w", [])
    app = orch.create_app(stack="react-vite")["id"]
    root = Path(orch.project(start_preview=False).record.path)
    chat = root / ".sage" / "chat-work"
    build = orch._wm.app_workspace(orch._project_id, app).path
    chat.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q", str(root)], check=True)

    stub = [sys.executable, str(Path(mcp_stub.__file__).resolve())]
    url = f"http://127.0.0.1:{remote.server_address[1]}/mcp"
    added = [orch.add_mcp({"name": "loc", "config": {"type": "local", "command": stub}}),
             orch.add_mcp({"name": "rem", "config": {
                 "type": "remote", "url": url,
                 "headers": {"Authorization": "Bearer {env:STUB_TOKEN_621}"}}}),
             orch.add_mcp({"name": "bad", "config": {"type": "local", "command": stub,
                                                     "environment": {"K": "{env:UNSET_621}"}}})]
    assert ["echo" in e["tools"] for e in added] == [True, True, False]

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "opencode.json").write_text(
        '{"$schema": "https://opencode.ai/config.json", "model": "cap/m", "provider": {"cap": {'
        f'"npm": "{PROVIDER.as_uri()}", "options": {{"baseURL": "{capture.url}/v1", '
        '"apiKey": "x"}, "models": {"m": {"name": "m", "tool_call": true}}}}, '
        '"permission": {"*": "allow"}}')
    env = dict(os.environ)
    env.update(OPENCODE_CONFIG=str(runtime / "opencode.json"), OPENCODE_DISABLE_AUTOUPDATE="true",
               XDG_CONFIG_HOME=str(runtime / "config"), XDG_DATA_HOME=str(runtime / "data"),
               XDG_CACHE_HOME=str(runtime / "cache"), XDG_STATE_HOME=str(runtime / "state"),
               OPENCODE_CONFIG_DIR=str(runtime / "config" / "opencode"))
    try:
        with _opencode_server(runtime, env) as server_url:
            orch._oc_server = type("Server", (), {"url": lambda self: server_url})()
            for listed in (orch.list_extensions(), orch.list_extensions(app=app)):
                status = {e["name"]: e["status"] for e in listed}
                assert status["loc"] == {"status": "connected"}, status
                assert status["rem"] == {"status": "connected"}, status
                assert status["bad"]["status"] == "failed"
                assert "UNSET_621 is not set" in status["bad"]["error"]

            client = OpenCodeClient(server_url)
            for directory in (chat, build):
                sent = _tools_sent(client, capture, directory)
                assert {"loc_echo", "loc_write_note", "rem_echo", "rem_ping"} <= sent, sent
    finally:
        capture.server.shutdown()
        capture.server.server_close()
        remote.shutdown()
        remote.server_close()
