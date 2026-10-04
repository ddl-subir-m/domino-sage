"""A remote MCP server is added from the resources panel, with secret headers and an on/off (#642).

Part of #640. Remote only: a URL and headers, where a credential is written `{env:NAME}` and never as
a value. Sage reads `tools/list` itself when the server is added and stores the names. The server
lives in the Project's `.opencode/opencode.json` `mcp` block, switched by OpenCode's own `enabled`,
and each row carries OpenCode's status for Chat's directory.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage import extension_mcp
from sage.extensions import ExtensionError

from . import mcp_stub
from .test_no_edit_recovery_uses_the_active_stack import _no_waiting  # noqa: F401  (autouse)
from .test_turn_path import _build

TOKEN = "s3cret-642"


@pytest.fixture
def remote():
    server = ThreadingHTTPServer(("127.0.0.1", 0), mcp_stub.Handler)
    server.token = TOKEN
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/mcp"
    server.shutdown()
    server.server_close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import sage.orchestrator.app as app_module

    orch, oc, _ = _build(tmp_path, [])
    monkeypatch.setattr(app_module, "orchestrator", orch)
    http = TestClient(app_module.control_app)
    http.orch, http.oc = orch, oc
    http.root = Path(orch.project(start_preview=False).record.path)
    return http


def _stored(root: Path) -> dict:
    return json.loads((root / ".opencode" / "opencode.json").read_text())["mcp"]


# ---- reading tools/list ------------------------------------------------------------------------

def test_tools_are_read_across_pages_with_the_header_resolved(remote, monkeypatch):
    monkeypatch.setenv("STUB_PAGES", "1")
    config = {"type": "remote", "url": remote,
              "headers": {"Authorization": "Bearer {env:STUB_TOKEN_642}"}}
    assert extension_mcp.read_tools(config, {"STUB_TOKEN_642": TOKEN}) == \
        ["echo", "write_note", "ping"]


def test_a_server_holding_its_stream_open_is_read_without_waiting_for_it():
    server = ThreadingHTTPServer(("127.0.0.1", 0), mcp_stub.Handler)
    server.hold = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        tools = extension_mcp.read_tools(
            {"type": "remote", "url": f"http://127.0.0.1:{server.server_address[1]}/mcp"}, {})
        assert "echo" in tools and time.monotonic() - started < 5
    finally:
        server.hold.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_server_that_refuses_says_so(remote):
    with pytest.raises(ExtensionError, match="401"):
        extension_mcp.read_tools({"type": "remote", "url": remote}, {})


def test_an_unset_secret_is_named_before_anything_is_asked():
    config = {"type": "remote", "url": "http://127.0.0.1:1/mcp",
              "headers": {"Authorization": "Bearer {env:NOPE_642}"}}
    with pytest.raises(ExtensionError, match="NOPE_642 is not set"):
        extension_mcp.read_tools(config, {})


# ---- the door ----------------------------------------------------------------------------------

@pytest.mark.parametrize(("body", "said"), [
    ({"name": "crm", "url": "ftp://x/mcp"}, "http"),
    ({"name": "crm", "url": "x/mcp"}, "http"),
    ({"name": "crm", "command": ["npx", "crm-mcp"]}, "http"),
    ({"name": "crm", "url": "https://x/mcp", "headers": {"X-Region": 7}}, "headers"),
    ({"name": "crm", "url": "https://x/mcp", "headers": {"X-Region": "{env:not a name}"}},
     "not a secret's name"),
    ({"name": "CRM", "url": "https://x/mcp"}, "lowercase"),
    ({"name": "sage-crm", "url": "https://x/mcp"}, "sage-"),
    ({"name": "sage_crm", "url": "https://x/mcp"}, "sage-"),
    ({"name": "bash", "url": "https://x/mcp"}, "built in"),
    ({"name": "live_read", "url": "https://x/mcp"}, "built in"),
])
def test_only_a_named_remote_server_is_taken(client, body, said):
    r = client.post("/api/project/mcp", json=body)
    assert r.status_code == 400 and said in r.json()["error"]
    assert not (client.root / ".opencode" / "opencode.json").exists()


@pytest.mark.parametrize("header", ["Authorization", "authorization", "X-Api-Key", "X-Token",
                                    "Client-Secret", "apikey"])
def test_a_literal_credential_is_refused_and_the_builder_told_to_use_a_secret(client, header):
    r = client.post("/api/project/mcp", json={"name": "crm", "url": "https://x/mcp",
                                              "headers": {header: "Bearer abc123"}})
    assert r.status_code == 400 and "secret" in r.json()["error"]
    assert "abc123" not in r.json()["error"]
    assert not (client.root / ".opencode" / "opencode.json").exists()


def test_a_credential_header_by_reference_and_a_plain_literal_are_both_taken(client, monkeypatch):
    monkeypatch.delenv("CRM_TOKEN_642", raising=False)
    r = client.post("/api/project/mcp", json={
        "name": "crm", "url": "https://x/mcp",
        "headers": {"Authorization": "Bearer {env:CRM_TOKEN_642}", "X-Region": "eu"}})
    assert r.status_code == 200
    assert _stored(client.root)["crm"]["headers"] == {
        "Authorization": "Bearer {env:CRM_TOKEN_642}", "X-Region": "eu"}


def test_a_key_sage_did_not_register_is_never_overwritten(client):
    (client.root / ".opencode").mkdir(parents=True, exist_ok=True)
    theirs = {"type": "remote", "url": "https://theirs/mcp"}
    (client.root / ".opencode" / "opencode.json").write_text(json.dumps({"mcp": {"crm": theirs}}))

    r = client.post("/api/project/mcp", json={"name": "crm", "url": "https://x/mcp"})
    assert r.status_code == 400 and "not Sage's" in r.json()["error"]
    assert _stored(client.root) == {"crm": theirs}
    assert client.get("/api/project/mcp").json() == {"servers": []}
    assert client.delete("/api/project/mcp/crm").status_code == 404
    assert client.put("/api/project/mcp/crm/enabled", json={"enabled": False}).status_code == 404
    assert client.post("/api/project/mcp/crm/tools").status_code == 404
    assert _stored(client.root) == {"crm": theirs}


def test_two_servers_whose_tool_names_would_blur_are_refused(client):
    assert client.post("/api/project/mcp",
                       json={"name": "crm", "url": "https://x/mcp"}).status_code == 200
    for name in ("crm", "crm_eu"):
        r = client.post("/api/project/mcp", json={"name": name, "url": "https://y/mcp"})
        assert r.status_code == 400 and "clashes" in r.json()["error"]


# ---- the contract ------------------------------------------------------------------------------

def test_add_lists_tools_and_every_route_answers_in_the_contracts_shape(client, remote,
                                                                         monkeypatch):
    monkeypatch.setenv("STUB_TOKEN_642", TOKEN)
    added = client.post("/api/project/mcp", json={
        "name": "notes", "url": remote, "headers": {"Authorization": "Bearer {env:STUB_TOKEN_642}"}})
    assert added.status_code == 200
    row = added.json()
    assert set(row) == {"name", "kind", "url", "headers", "enabled", "tools", "status", "warning"}
    assert row["kind"] == "remote"
    assert row["tools"] == ["echo", "ping", "write_note"]
    assert extension_mcp.server(client.root, "notes")["warning"] is None
    assert row["headers"] == {"Authorization": "Bearer {env:STUB_TOKEN_642}"}
    assert row["enabled"] is True
    # The fake wiring runs no OpenCode server, so its status cannot be known.
    assert (row["status"], row["warning"]) == ("unknown", "the OpenCode server is not running")
    assert client.oc.disposed, "OpenCode reloads after an add"

    [listed] = client.get("/api/project/mcp").json()["servers"]
    assert {k: listed[k] for k in ("name", "url", "headers", "enabled", "tools")} == \
        {k: row[k] for k in ("name", "url", "headers", "enabled", "tools")}

    again = client.post("/api/project/mcp/notes/tools")
    assert again.status_code == 200 and again.json()["tools"] == ["echo", "ping", "write_note"]

    assert client.delete("/api/project/mcp/notes").json() == {"removed": True}
    assert client.get("/api/project/mcp").json() == {"servers": []}
    assert not (client.root / ".opencode" / "opencode.json").exists()
    assert client.delete("/api/project/mcp/notes").status_code == 404


def test_a_server_that_cannot_be_read_yet_is_kept_with_a_warning(client, monkeypatch):
    monkeypatch.delenv("NOPE_642", raising=False)
    r = client.post("/api/project/mcp", json={
        "name": "crm", "url": "https://x/mcp", "headers": {"X-Api-Key": "{env:NOPE_642}"}})
    assert r.status_code == 200
    row = r.json()
    assert row["tools"] == [] and row["status"] == "failed" and "NOPE_642" in row["warning"]
    assert [s["name"] for s in client.get("/api/project/mcp").json()["servers"]] == ["crm"]


def test_reading_again_replaces_the_tools_and_clears_the_warning(client, remote, monkeypatch):
    monkeypatch.delenv("STUB_TOKEN_642", raising=False)
    client.post("/api/project/mcp", json={
        "name": "notes", "url": remote, "headers": {"Authorization": "Bearer {env:STUB_TOKEN_642}"}})
    assert extension_mcp.server(client.root, "notes")["warning"]

    monkeypatch.setenv("STUB_TOKEN_642", TOKEN)
    row = client.post("/api/project/mcp/notes/tools").json()
    assert row["tools"] == ["echo", "ping", "write_note"]
    assert extension_mcp.server(client.root, "notes")["warning"] is None


def test_the_enabled_flag_reaches_the_written_config_and_reloads(client):
    client.post("/api/project/mcp", json={"name": "crm", "url": "https://x/mcp"})
    assert _stored(client.root)["crm"] == {"type": "remote", "url": "https://x/mcp",
                                           "headers": {}, "enabled": True}
    client.oc.disposed.clear()

    off = client.put("/api/project/mcp/crm/enabled", json={"enabled": False})
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert off.json()["status"] == "disabled"
    assert _stored(client.root)["crm"]["enabled"] is False
    assert client.oc.disposed, "OpenCode reloads after a switch"

    assert client.put("/api/project/mcp/crm/enabled", json={"enabled": True}).json()["enabled"]
    assert _stored(client.root)["crm"]["enabled"] is True
    assert client.put("/api/project/mcp/crm/enabled", json={"enabled": "no"}).status_code == 400


def test_the_stored_config_never_holds_a_resolved_value(client, remote, monkeypatch):
    monkeypatch.setenv("STUB_TOKEN_642", TOKEN)
    client.post("/api/project/mcp", json={
        "name": "notes", "url": remote, "headers": {"Authorization": "Bearer {env:STUB_TOKEN_642}"}})
    client.post("/api/project/mcp/notes/tools")
    client.put("/api/project/mcp/notes/enabled", json={"enabled": False})

    for path in (client.root / ".opencode").rglob("*"):
        if path.is_file():
            assert TOKEN not in path.read_text(), path
    assert TOKEN not in client.get("/api/project/mcp").text


def test_a_remove_leaves_what_sage_did_not_write(client):
    (client.root / ".opencode").mkdir(parents=True, exist_ok=True)
    (client.root / ".opencode" / "opencode.json").write_text(
        json.dumps({"mcp": {"theirs": {"type": "remote", "url": "https://t/mcp"}}}))
    client.post("/api/project/mcp", json={"name": "crm", "url": "https://x/mcp"})
    client.delete("/api/project/mcp/crm")
    assert set(_stored(client.root)) == {"theirs"}


# ---- the status --------------------------------------------------------------------------------

def _status_orch(tmp_path, said: dict):
    orch, _, _ = _build(tmp_path, [])
    asked: list[str] = []
    orch.opencode_mcp_status = lambda directory=None: (asked.append(directory), said)[1]
    root = Path(orch.project(start_preview=False).record.path)
    return orch, root, asked


def test_each_server_carries_opencodes_status_for_chats_directory(tmp_path):
    orch, root, asked = _status_orch(tmp_path, {"asked": True, "ok": True, "servers": {
        "up": {"status": "connected"}, "down": {"status": "failed", "error": "refused"},
        "auth": {"status": "needs_auth"}}})
    for name in ("up", "down", "auth", "gone"):
        extension_mcp.add(root, name, "https://x/mcp")
    orch._extensions_pending = False
    rows = {r["name"]: r for r in orch.list_mcp_servers()}
    assert (rows["up"]["status"], rows["up"]["warning"]) == ("connected", None)
    assert (rows["down"]["status"], rows["down"]["warning"]) == ("failed", "refused")
    assert rows["auth"]["status"] == "failed" and "needs_auth" in rows["auth"]["warning"]
    # OpenCode drops a server it cannot load, silently. Here that is a failure, not an absence.
    assert rows["gone"]["status"] == "failed" and "did not load" in rows["gone"]["warning"]
    assert asked == [str(root / ".sage" / "chat-work")]

    orch._extensions_pending = True
    assert {r["name"]: r for r in orch.list_mcp_servers()}["gone"]["status"] == "pending"


def test_an_unset_secret_reads_as_failed_even_if_opencode_connected(tmp_path, monkeypatch):
    """OpenCode substitutes an unset variable with nothing and may well connect anyway."""
    monkeypatch.delenv("NOPE_642", raising=False)
    orch, root, _ = _status_orch(tmp_path, {"asked": True, "ok": True,
                                            "servers": {"crm": {"status": "connected"}}})
    extension_mcp.add(root, "crm", "https://x/mcp", {"X-Key": "{env:NOPE_642}"})
    [crm] = orch.list_mcp_servers()
    assert crm["status"] == "failed" and "NOPE_642 is not set" in crm["warning"]


def test_secrets_resolve_through_one_seam(tmp_path, monkeypatch):
    """#641 overlays the Project's variables on `os.environ` in `_mcp_env`; status follows it."""
    monkeypatch.delenv("PROJ_ONLY_642", raising=False)
    orch, root, _ = _status_orch(tmp_path, {"asked": True, "ok": True,
                                            "servers": {"crm": {"status": "connected"}}})
    extension_mcp.add(root, "crm", "https://x/mcp", {"X-Key": "{env:PROJ_ONLY_642}"})
    orch._mcp_env = lambda: {"PROJ_ONLY_642": "v"}
    [crm] = orch.list_mcp_servers()
    assert crm["status"] == "connected"


def test_when_opencode_cannot_be_asked_the_status_says_so(tmp_path):
    orch, root, _ = _status_orch(tmp_path, {"asked": False,
                                            "why": "the OpenCode server is not running"})
    extension_mcp.add(root, "crm", "https://x/mcp")
    [crm] = orch.list_mcp_servers()
    assert (crm["status"], crm["warning"]) == ("unknown", "the OpenCode server is not running")


def test_a_project_with_no_servers_never_asks_opencode(tmp_path):
    orch, _, asked = _status_orch(tmp_path, {"asked": True, "ok": True, "servers": {}})
    assert orch.list_mcp_servers() == []
    assert asked == []


# ---- diag --------------------------------------------------------------------------------------

def test_diag_reads_the_project_config_as_sages_only_while_sage_registered_every_server(
        tmp_path, monkeypatch):
    import sage.orchestrator.app as app_module

    workspace = tmp_path / "mnt" / "code"
    extension_mcp.add(workspace, "crm", "https://x/mcp")
    monkeypatch.setattr(app_module.orchestrator, "_wm",
                        type("W", (), {"_dir": workspace})(), raising=False)
    monkeypatch.setattr(app_module.orchestrator, "_opencode_log_tail", lambda n=30: [], raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config" / "opencode").mkdir(parents=True)
    http = TestClient(app_module.control_app)

    assert http.get("/api/diag").json()["opencode_config"]["shadowing_mcp"] == []

    config = workspace / ".opencode" / "opencode.json"
    body = json.loads(config.read_text())
    body["mcp"]["theirs"] = {"type": "remote", "url": "https://t/mcp"}
    config.write_text(json.dumps(body))
    assert http.get("/api/diag").json()["opencode_config"]["shadowing_mcp"] == [str(config)]
