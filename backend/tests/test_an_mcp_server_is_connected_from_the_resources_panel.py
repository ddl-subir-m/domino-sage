"""An MCP server is connected from the resources panel, and the panel says whether it is (#621).

ADR-0071. The form takes a remote URL plus headers or a local command plus environment; a secret is
a variable name written as `{env:VAR}`, never a value. A git URL works too, for a repo that declares
its own server. When a server is added Sage reads `tools/list` itself, storing each tool's
`readOnlyHint` as `readOnly`, which the person can override. Each row carries OpenCode's own status
for the directory the switch answers for, and a server naming an unset variable reads as failed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage import extension_mcp, extensions
from sage.router.model_control import ModelControl
from sage.router.models import Mode

from . import mcp_stub
from .test_a_projects_own_extensions_reach_the_next_turn import _sent
from .test_no_edit_recovery_uses_the_active_stack import _no_waiting  # noqa: F401  (autouse)
from .test_turn_path import _build

STUB = str(Path(mcp_stub.__file__).resolve())
LOCAL = {"type": "local", "command": [sys.executable, STUB]}


@pytest.fixture
def remote():
    server = ThreadingHTTPServer(("127.0.0.1", 0), mcp_stub.Handler)
    server.token = "s3cret"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/mcp"
    server.shutdown()
    server.server_close()


# ---- reading tools/list ------------------------------------------------------------------------

def test_a_local_servers_read_only_hints_are_read_and_a_missing_one_is_false(monkeypatch):
    monkeypatch.setenv("STUB_PAGES", "1")
    assert extension_mcp.read_tools(LOCAL) == {"echo": True, "write_note": False, "ping": False}


def test_a_local_servers_environment_resolves_its_variables(monkeypatch):
    monkeypatch.setenv("MY_TOOL_621", "lookup")
    tools = extension_mcp.read_tools({**LOCAL, "environment": {"STUB_EXTRA": "{env:MY_TOOL_621}"}})
    assert tools["lookup"] is True


def test_a_remote_server_is_read_with_its_headers_resolved(remote, monkeypatch):
    monkeypatch.setenv("STUB_TOKEN_621", "s3cret")
    config = {"type": "remote", "url": remote,
              "headers": {"Authorization": "Bearer {env:STUB_TOKEN_621}"}}
    assert extension_mcp.read_tools(config) == {"echo": True, "write_note": False, "ping": False}


def test_a_remote_server_holding_its_stream_open_is_read_without_waiting_for_it(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), mcp_stub.Handler)
    server.hold = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        tools = extension_mcp.read_tools(
            {"type": "remote", "url": f"http://127.0.0.1:{server.server_address[1]}/mcp"})
        assert tools["echo"] is True and time.monotonic() - started < 5
    finally:
        server.hold.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_remote_server_that_refuses_says_so(remote):
    with pytest.raises(extensions.ExtensionError, match="401"):
        extension_mcp.read_tools({"type": "remote", "url": remote})


def test_an_unset_variable_is_named_before_anything_is_started(monkeypatch):
    monkeypatch.delenv("NOPE_621", raising=False)
    config = {**LOCAL, "environment": {"KEY": "{env:NOPE_621}"}}
    assert extensions.unset_variables(config) == ["NOPE_621"]
    with pytest.raises(extensions.ExtensionError, match="NOPE_621 is not set"):
        extension_mcp.read_tools(config)


def test_a_command_that_is_not_a_server_says_so():
    with pytest.raises(extensions.ExtensionError, match="exited"):
        extension_mcp.read_tools({"type": "local", "command": [sys.executable, "-c", "pass"]})


# ---- the door ----------------------------------------------------------------------------------

@pytest.mark.parametrize(("config", "said"), [
    ({"type": "remote", "url": "u", "headers": {"Authorization": 7}}, "headers"),
    ({"type": "local", "command": ["x"], "environment": ["A=1"]}, "environment"),
    ({"type": "local", "command": ["x"], "environment": {"A": "{env:not a name}"}}, "variable"),
])
def test_headers_and_environment_are_text_and_variables_are_names(tmp_path, config, said):
    with pytest.raises(extensions.ExtensionError, match=said):
        extensions.add(tmp_path, {"kind": "mcp", "name": "crm", "config": config})
    assert extensions.read_manifest(tmp_path) == []


# ---- the orchestrator --------------------------------------------------------------------------

def test_adding_from_the_form_stores_each_tools_mark_and_reloads(tmp_path):
    orch, oc, _ = _build(tmp_path, [])
    project = orch.project(start_preview=False)
    item = orch.add_mcp({"name": "notes", "config": LOCAL})
    assert item["tools"] == {"echo": True, "write_note": False, "ping": False}
    assert item["source"] == {"type": "form"} and "warning" not in item
    assert oc.disposed
    owner = project.control.snapshot().extensions.owner
    assert owner("notes_echo") == ("mcp:notes", True) and owner("notes_ping") == ("mcp:notes", False)


def test_a_server_naming_an_unset_variable_is_still_added_and_says_why(tmp_path, monkeypatch):
    monkeypatch.delenv("NOPE_621", raising=False)
    orch, _, _ = _build(tmp_path, [])
    item = orch.add_mcp({"name": "crm", "config": {"type": "remote", "url": "https://x/mcp",
                                                   "headers": {"Authorization": "{env:NOPE_621}"}}})
    assert item["tools"] == {} and "NOPE_621 is not set" in item["warning"]
    assert [e["id"] for e in orch.list_extensions()] == ["mcp:crm"]


def test_an_override_changes_the_mark_and_reading_again_keeps_it(tmp_path, monkeypatch):
    orch, _, _ = _build(tmp_path, [])
    project = orch.project(start_preview=False)
    orch.add_mcp({"name": "notes", "config": LOCAL})
    orch.set_mcp_tool_read_only("mcp:notes", "write_note", True)
    assert project.control.snapshot().extensions.owner("notes_write_note").read_only is True

    monkeypatch.setenv("STUB_EXTRA", "fresh")
    tools = orch.read_mcp_tools("mcp:notes")["tools"]
    assert tools == {"echo": True, "write_note": True, "ping": False, "fresh": True}
    with pytest.raises(KeyError):
        orch.set_mcp_tool_read_only("mcp:notes", "nope", True)
    with pytest.raises(KeyError):
        orch.set_mcp_tool_read_only("mcp:other", "echo", True)


def test_an_overridden_tool_is_offered_on_an_ask_turn(tmp_path):
    extensions.add(tmp_path, {"kind": "mcp", "name": "crm", "config": {"type": "remote", "url": "u"},
                              "tools": {"search": True, "update": False}})
    extensions.set_tool_read_only(tmp_path, "mcp:crm", "update", True)
    control = ModelControl(mode=Mode.ASK)
    control.set_extensions(extensions.load_catalog(tmp_path))
    names = _sent(control)[0]
    assert {"crm_search", "crm_update"} <= set(names) and "crm_other" not in names


# ---- the status OpenCode reports ---------------------------------------------------------------

def _status_orch(tmp_path, said: dict):
    orch, _, _ = _build(tmp_path, [])
    asked: list[str] = []
    orch.opencode_mcp_status = lambda directory=None: (asked.append(directory), said)[1]
    return orch, asked


def test_each_server_carries_opencodes_status_for_the_directory_it_answers_for(tmp_path):
    orch, asked = _status_orch(tmp_path, {"asked": True, "ok": True, "servers": {
        "up": {"status": "connected"}, "down": {"status": "failed", "error": "refused"}}})
    for name in ("up", "down", "gone"):
        extensions.add(Path(orch.project(start_preview=False).record.path),
                       {"kind": "mcp", "name": name, "config": {"type": "remote", "url": "u"}})
    status = {e["name"]: e["status"] for e in orch.list_extensions()}
    assert status["up"] == {"status": "connected"}
    assert status["down"] == {"status": "failed", "error": "refused"}
    # OpenCode drops a server it cannot load, silently. Here that is a failure, not an absence.
    assert status["gone"]["status"] == "failed" and "did not load" in status["gone"]["error"]
    root = Path(orch.project(start_preview=False).record.path)
    assert asked == [str(root / ".sage" / "chat-work")]

    app = orch.create_app(stack="react-vite")["id"]
    orch.list_extensions(app=app)
    assert asked[-1] == str(orch._wm.app_workspace(orch._project_id, app).path)


def test_a_server_naming_an_unset_variable_reads_as_failed_even_if_opencode_connected(
        tmp_path, monkeypatch):
    """OpenCode substitutes an unset variable with nothing and may well connect anyway."""
    monkeypatch.delenv("NOPE_621", raising=False)
    orch, _ = _status_orch(tmp_path, {"asked": True, "ok": True,
                                      "servers": {"crm": {"status": "connected"}}})
    extensions.add(Path(orch.project(start_preview=False).record.path), {
        "kind": "mcp", "name": "crm",
        "config": {"type": "remote", "url": "u", "headers": {"X-Key": "{env:NOPE_621}"}}})
    [crm] = orch.list_extensions()
    assert crm["status"]["status"] == "failed" and "NOPE_621 is not set" in crm["status"]["error"]


def test_when_opencode_cannot_be_asked_the_status_says_so(tmp_path):
    orch, _ = _status_orch(tmp_path, {"asked": False, "why": "the OpenCode server is not running"})
    extensions.add(Path(orch.project(start_preview=False).record.path),
                   {"kind": "mcp", "name": "crm", "config": {"type": "remote", "url": "u"}})
    [crm] = orch.list_extensions()
    assert crm["status"] == {"status": "unknown", "error": "the OpenCode server is not running"}


def test_a_project_with_no_servers_never_asks_opencode(tmp_path):
    orch, asked = _status_orch(tmp_path, {"asked": True, "ok": True, "servers": {}})
    orch.list_extensions()
    assert asked == []


# ---- a git URL ---------------------------------------------------------------------------------

def _origin(tmp_path: Path, files: dict[str, dict]) -> str:
    origin = tmp_path / "origin"
    origin.mkdir()
    for rel, body in files.items():
        (origin / rel).parent.mkdir(parents=True, exist_ok=True)
        (origin / rel).write_text(json.dumps(body))
    git = ["git", "-C", str(origin), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(origin)], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "mcp"], check=True)
    return origin.as_uri()


@pytest.fixture
def git_origin(tmp_path, monkeypatch):
    def make(files: dict[str, dict]) -> str:
        uri = _origin(tmp_path, files)
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{uri}.insteadOf")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://git.example/team/mcp")
        return "https://git.example/team/mcp"
    return make


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_a_repo_declaring_one_server_in_mcp_json_is_read_as_opencodes_shape(git_origin):
    url = git_origin({".mcp.json": {"mcpServers": {"crm": {
        "command": "npx", "args": ["-y", "crm-mcp"], "env": {"CRM_TOKEN": "${CRM_TOKEN}"}}}}})
    config, commit = extension_mcp.server_from_git(url)
    assert config == {"type": "local", "command": ["npx", "-y", "crm-mcp"],
                      "environment": {"CRM_TOKEN": "{env:CRM_TOKEN}"}}
    assert len(commit) == 40


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_a_repo_declaring_several_servers_needs_one_named(git_origin):
    url = git_origin({"opencode.json": {"mcp": {
        "a": {"type": "remote", "url": "https://a/mcp"},
        "b": {"type": "local", "command": ["b-server"]}}}})
    with pytest.raises(extensions.ExtensionError, match="a, b"):
        extension_mcp.server_from_git(url)
    assert extension_mcp.server_from_git(url, "b")[0] == {"type": "local", "command": ["b-server"]}


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_a_repo_declaring_no_server_says_where_it_looked(git_origin):
    url = git_origin({"package.json": {"name": "x"}})
    with pytest.raises(extensions.ExtensionError, match=".mcp.json"):
        extension_mcp.server_from_git(url)


def test_a_git_url_that_is_not_https_is_refused():
    with pytest.raises(extensions.ExtensionError, match="https://"):
        extension_mcp.server_from_git("file:///etc")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_adding_from_git_records_the_url_and_commit(tmp_path, git_origin):
    url = git_origin({".mcp.json": {"mcpServers": {"notes": {"command": sys.executable,
                                                             "args": [STUB]}}}})
    orch, _, _ = _build(tmp_path / "w", [])
    item = orch.add_mcp({"name": "notes", "git": url})
    assert item["source"]["type"] == "git" and item["source"]["url"] == url
    assert len(item["source"]["commit"]) == 40
    assert item["tools"]["echo"] is True


# ---- the routes --------------------------------------------------------------------------------

def test_the_routes_add_override_and_read_again(tmp_path, monkeypatch):
    import sage.orchestrator.app as app_module

    orch, _, _ = _build(tmp_path, [])
    monkeypatch.setattr(app_module, "orchestrator", orch)
    client = TestClient(app_module.control_app)

    added = client.post("/api/project/extensions/mcp", json={"name": "notes", "config": LOCAL})
    assert added.json()["item"]["tools"]["write_note"] is False
    assert client.post("/api/project/extensions/mcp",
                       json={"name": "notes", "config": LOCAL}).status_code == 400
    put = client.put("/api/project/extensions/mcp:notes/tools/write_note", json={"readOnly": True})
    assert put.json()["item"]["tools"]["write_note"] is True
    assert client.put("/api/project/extensions/mcp:notes/tools/nope",
                      json={"readOnly": True}).status_code == 404
    again = client.post("/api/project/extensions/mcp:notes/tools")
    assert again.json()["item"]["tools"]["write_note"] is True
    assert client.post("/api/project/extensions/mcp:nope/tools").status_code == 404
