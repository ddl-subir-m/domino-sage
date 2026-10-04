"""A remote MCP server added from the panel connects, and its tools reach the model (#642).

Against the pinned OpenCode: the server reads `connected` in the panel's status, and its tools are in
the request OpenCode sends the model from Chat's directory and from a Built App's. Its credential
header is a `{env:NAME}` reference OpenCode resolves itself. A second server naming an unset secret
reads as failed whatever OpenCode says of it, and one switched off reads as disabled with its tools
gone. The model request is captured by a local stand-in for the gateway.
"""
from __future__ import annotations

import os
import subprocess
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
def test_a_remote_server_connects_and_reaches_chat_and_build(tmp_path, monkeypatch):
    remote = ThreadingHTTPServer(("127.0.0.1", 0), mcp_stub.Handler)
    remote.token = "s3cret"
    threading.Thread(target=remote.serve_forever, daemon=True).start()
    capture = _Capture()
    monkeypatch.setenv("STUB_TOKEN_642", "s3cret")
    monkeypatch.delenv("UNSET_642", raising=False)

    orch, _, _ = _build(tmp_path / "w", [])
    app = orch.create_app(stack="react-vite")["id"]
    root = Path(orch.project(start_preview=False).record.path)
    chat = root / ".sage" / "chat-work"
    build = orch._wm.app_workspace(orch._project_id, app).path
    chat.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q", str(root)], check=True)

    url = f"http://127.0.0.1:{remote.server_address[1]}/mcp"
    added = [orch.add_mcp({"name": "rem", "url": url,
                           "headers": {"Authorization": "Bearer {env:STUB_TOKEN_642}"}}),
             orch.add_mcp({"name": "bad", "url": url,
                           "headers": {"Authorization": "Bearer {env:UNSET_642}"}})]
    assert [a["tools"] for a in added] == [["echo", "ping", "write_note"], []]

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
            status = {r["name"]: r for r in orch.list_mcp_servers()}
            assert status["rem"]["status"] == "connected", status
            assert status["bad"]["status"] == "failed"
            assert "UNSET_642 is not set" in status["bad"]["warning"]

            client = OpenCodeClient(server_url)
            for directory in (chat, build):
                sent = _tools_sent(client, capture, directory)
                assert {"rem_echo", "rem_ping", "rem_write_note"} <= sent, sent

            orch._oc_client = client
            assert orch.set_mcp_enabled("rem", False)["status"] == "disabled"
            assert not any(t.startswith("rem_") for t in _tools_sent(client, capture, chat))
    finally:
        capture.server.shutdown()
        capture.server.server_close()
        remote.shutdown()
        remote.server_close()
