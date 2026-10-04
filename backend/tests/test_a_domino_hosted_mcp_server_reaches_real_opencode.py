"""A Domino-hosted MCP server reaches Chat through Sage's control port (#645).

Against the pinned OpenCode: the server is written as `http://127.0.0.1:<control port>/mcp/domino/
<name>`, OpenCode connects there, the control app forwards each request to a stub standing in for the
Domino App with a token fetched for that request, and the stub's tools are in the request OpenCode
sends the model from Chat's directory.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from sage import extension_mcp
from sage.driver.opencode import OpenCodeClient

from . import mcp_stub
from .opencode_server import BINARY, _opencode_server
from .test_a_domino_hosted_mcp_server_is_signed_in_on_every_call import (
    APPS,
    _on_domino,
    _point_at,
    _serve,
    _stub_url,
)
from .test_an_added_remote_mcp_server_reaches_real_opencode import PROVIDER, _tools_sent
from .test_an_added_skill_reaches_real_opencode_without_a_restart import _Capture
from .test_no_edit_recovery_uses_the_active_stack import _no_waiting  # noqa: F401  (autouse)
from .test_turn_path import _build


def _control_port(app):
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error",
                                           lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "the control app did not come up"
    return server, thread, server.servers[0].sockets[0].getsockname()[1]


@pytest.mark.opencode
@pytest.mark.skipif(not BINARY.exists(), reason="the pinned OpenCode binary is not installed")
def test_a_domino_server_connects_through_the_control_port_and_reaches_chat(tmp_path, monkeypatch):
    import sage.orchestrator.app as app_module

    stub = _serve(mcp_stub.Handler, seen=[])
    sidecar, domino = _on_domino(monkeypatch)
    capture = _Capture()

    orch, _, _ = _build(tmp_path / "w", [])
    monkeypatch.setattr(app_module, "orchestrator", orch)
    control, control_thread, port = _control_port(app_module.control_app)
    monkeypatch.setenv("SAGE_CONTROL_PORT", str(port))
    root = Path(orch.project(start_preview=False).record.path)
    chat = root / ".sage" / "chat-work"
    chat.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q", str(root)], check=True)

    with monkeypatch.context() as m:
        m.setattr(extension_mcp, "read_tools", lambda config, env: [])
        orch.add_mcp({"name": "dom", "kind": "domino", "url": f"https://{APPS}/dom/mcp"})
    _point_at(root, "dom", _stub_url(stub))
    assert orch.read_mcp_tools("dom")["tools"] == ["echo", "ping", "write_note"]
    stub.seen.clear()

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
            [dom] = orch.list_mcp_servers()
            assert (dom["kind"], dom["status"]) == ("domino", "connected"), dom

            sent = _tools_sent(OpenCodeClient(server_url), capture, chat)
            assert {"dom_echo", "dom_ping", "dom_write_note"} <= sent, sent

            tokens = [h.get("authorization") for h in stub.seen]
            assert len(tokens) >= 2 and len(set(tokens)) == len(tokens), tokens
            assert all(t.startswith("Bearer tok-645-") for t in tokens), tokens
    finally:
        control.should_exit = True
        control_thread.join(timeout=5)
        for server in (capture.server, stub, sidecar, domino):
            server.shutdown()
            server.server_close()
