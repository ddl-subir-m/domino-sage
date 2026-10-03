"""The resources panel's MCPs group: each server with OpenCode's status, a switch, and a way in (#621).

ADR-0071. Drawn through `tests/js/project_mcps_harness.mjs`, which records every request the panel
sends, so a switch or an add is proved by what reached the server.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "project_mcps_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

ITEMS = [
    {"id": "mcp:crm", "kind": "mcp", "name": "crm", "enabled": False, "source": {"type": "form"},
     "tools": ["search", "update"],
     "status": {"status": "failed", "error": "CRM_TOKEN is not set."}},
    {"id": "mcp:notes", "kind": "mcp", "name": "notes", "enabled": True,
     "source": {"type": "git", "url": "https://x"}, "tools": [], "status": {"status": "connected"}},
    {"id": "skill:tables", "kind": "skill", "name": "tables", "enabled": True,
     "source": {"type": "upload"}},
]


def _run(act: str, **kw) -> dict:
    payload = {"act": act, "items": ITEMS, **kw}
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(payload), capture_output=True,
                         text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_each_server_is_a_row_with_opencodes_status():
    rows = _run("drawn", hash="#/chat", thread="t1")["rows"]
    assert [r["name"] for r in rows] == ["crm", "notes"]
    assert rows[0]["subtitle"].startswith("Failed: CRM_TOKEN is not set.")
    assert rows[0]["failed"] is True
    assert rows[1]["subtitle"].startswith("Connected") and rows[1]["failed"] is False
    assert "From git" in rows[1]["subtitle"]
    assert rows[0]["checked"] is False and rows[0]["disabled"] is False
    assert rows[0]["label"] == "Use crm in this conversation"


def test_the_menu_names_each_tool_and_marks_none_of_them():
    """#636: the names only. The menu used to flip a read-only mark per tool."""
    menu = _run("drawn", hash="#/chat", thread="t1")["menu"]
    assert [m["key"] for m in menu] == ["tool:search", "tool:update", "reread", "remove"]
    assert [m["label"] for m in menu[:2]] == ["search", "update"]


def test_pressing_a_tools_name_sends_nothing():
    assert _run("menu", key="tool:update", hash="#/chat", thread="t1")["calls"] == []


def test_reading_again_asks_the_server():
    calls = _run("menu", key="reread", hash="#/chat", thread="t1")["calls"]
    assert calls[0]["url"] == "./api/project/extensions/mcp%3Acrm/tools"
    assert calls[0]["method"] == "POST"


def test_a_switch_in_build_writes_to_the_app():
    calls = _run("toggle", hash="#/build", app="app-1")["calls"]
    put = next(c for c in calls if c["method"] == "PUT")
    assert put["url"] == "./api/project/extensions/mcp%3Acrm/enabled"
    assert put["body"] == {"enabled": False, "thread": "", "app": "app-1"}


def test_the_add_menu_and_the_group_door_open_the_mcp_dialog():
    drawn = _run("drawn", hash="#/chat", thread="t1")
    assert "mcp" in drawn["menuKeys"] and drawn["mcpDialogOpen"] is False
    pressed = _run("press-door", hash="#/chat", thread="t1")
    assert pressed["doorLabel"] == "Add MCP server"
    assert pressed["mcpDialogOpen"] is True and pressed["skillDialogOpen"] is False
    assert "credentials" in pressed["warning"] and "switched on" in pressed["warning"]


def test_a_remote_form_writes_secrets_as_variable_references():
    body = _run("body", form={"name": "crm", "how": "remote", "url": " https://crm/mcp ", "pairs": [
        {"key": "Authorization", "value": "CRM_TOKEN", "secret": True},
        {"key": "X-Team", "value": "data", "secret": False},
        {"key": "", "value": "", "secret": True}]})["body"]
    assert body == {"name": "crm", "config": {"type": "remote", "url": "https://crm/mcp", "headers": {
        "Authorization": "{env:CRM_TOKEN}", "X-Team": "data"}}}


def test_a_local_form_splits_its_command_and_names_its_environment():
    body = _run("body", form={"name": "fs", "how": "local", "command": "npx -y  fs-mcp /data",
                              "pairs": [{"key": "API_KEY", "value": "FS_KEY", "secret": True}]})["body"]
    assert body == {"name": "fs", "config": {"type": "local",
                                             "command": ["npx", "-y", "fs-mcp", "/data"],
                                             "environment": {"API_KEY": "{env:FS_KEY}"}}}


def test_a_secret_that_is_not_a_variable_name_is_refused_before_it_is_sent():
    out = _run("body", form={"name": "crm", "how": "remote", "url": "https://crm/mcp", "pairs": [
        {"key": "Authorization", "value": "Bearer abc123", "secret": True}]})
    assert "body" not in out and "variable name" in out["error"]


def test_a_git_form_names_the_repo_and_optionally_its_server():
    body = _run("body", form={"name": "crm", "how": "git", "git": "https://g/x.git",
                              "server": ""})["body"]
    assert body == {"name": "crm", "git": "https://g/x.git", "server": ""}


def test_adding_posts_to_the_mcp_route_and_reports_a_warning():
    out = _run("add", hash="#/chat", body={"name": "crm", "config": {"type": "remote", "url": "u"}},
               reply={"name": "crm", "warning": "Added, but CRM_TOKEN is not set."})
    assert out["calls"][0] == {"url": "./api/project/extensions/mcp", "method": "POST",
                               "body": {"name": "crm", "config": {"type": "remote", "url": "u"}}}
    assert out["toasts"] == ["Added, but CRM_TOKEN is not set."]
