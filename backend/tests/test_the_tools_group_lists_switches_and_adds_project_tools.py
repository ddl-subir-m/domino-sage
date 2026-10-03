"""The resources panel's Tools group: the Project's custom tools, a switch each, and a way in (#622).

ADR-0071. A switch answers for the Conversation open in Chat or the app selected in Build. A row's
menu changes the tool's read-only flag, which decides whether Ask and plan turns are offered it.
Drawn through `tests/js/project_tools_harness.mjs`, which records every request the panel sends.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "project_tools_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

ITEMS = [
    {"id": "tool:adder", "kind": "tool", "name": "adder", "enabled": False, "readOnly": True,
     "files": [".opencode/tools/adder.py", ".opencode/tools/adder.ts"],
     "source": {"type": "upload"}},
    {"id": "tool:lookup", "kind": "tool", "name": "lookup", "enabled": True, "readOnly": False,
     "files": [".opencode/tools/lookup.ts"], "source": {"type": "git", "url": "https://x"}},
    {"id": "skill:tables", "kind": "skill", "name": "tables", "enabled": True,
     "source": {"type": "upload"}},
]


def _run(act: str, **kw) -> dict:
    payload = {"act": act, "items": ITEMS, **kw}
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(payload), capture_output=True,
                         text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_each_project_tool_is_a_row_in_its_own_group_and_says_what_it_is():
    drawn = _run("drawn", hash="#/chat", thread="t1")
    assert "Tools (2)" in drawn["heads"] and "Skills (1)" in drawn["heads"]
    rows = drawn["rows"]
    assert [r["name"] for r in rows] == ["adder", "lookup"]
    assert rows[0]["subtitle"] == "Python · Read-only · Uploaded"
    assert rows[1]["subtitle"] == "TypeScript · From git"
    assert rows[0]["checked"] is False and rows[0]["disabled"] is False
    assert rows[0]["label"] == "Use adder in this conversation"
    assert rows[0]["menu"][0] == "Mark not read-only" and rows[1]["menu"][0] == "Mark read-only"


def test_only_build_with_no_app_picked_keeps_the_switch_off_limits():
    """#627: a new Chat conversation opens on the first switch, so only Build waits for an app."""
    assert _run("drawn", hash="#/build")["rows"][0]["disabled"] is True
    assert _run("drawn", hash="#/chat")["rows"][0]["disabled"] is False


def test_a_switch_in_chat_writes_to_the_conversation_and_in_build_to_the_app():
    chat = _run("toggle", hash="#/chat", thread="t1")["calls"]
    put = next(c for c in chat if c["method"] == "PUT")
    assert put["url"] == "./api/project/extensions/tool%3Aadder/enabled"
    assert put["body"] == {"enabled": False, "thread": "t1", "app": ""}
    build = _run("toggle", hash="#/build", app="app-1")["calls"]
    assert next(c for c in build if c["method"] == "PUT")["body"] == {
        "enabled": False, "thread": "", "app": "app-1"}


def test_the_menu_flips_read_only_for_the_project_and_reads_the_list_again():
    calls = _run("read-only", hash="#/chat", thread="t1")["calls"]
    assert calls[0] == {"url": "./api/project/extensions/tool%3Aadder/readOnly", "method": "PUT",
                        "body": {"readOnly": False}}
    assert calls[-1]["url"].endswith("/project/extensions?thread=t1&app=")


def test_remove_asks_first_then_deletes():
    out = _run("remove", hash="#/chat", thread="t1")
    assert out["confirmTitle"] == "Remove adder?"
    assert out["calls"][0] == {"url": "./api/project/extensions/tool%3Aadder",
                               "method": "DELETE", "body": None}


def test_the_add_menu_and_the_group_door_open_the_tool_dialog_not_the_skill_one():
    drawn = _run("drawn", hash="#/chat", thread="t1")
    assert "tool" in drawn["menuKeys"] and drawn["dialogOpen"] is False
    pressed = _run("press-door", hash="#/chat", thread="t1")
    assert pressed["dialogOpen"] is True and pressed["skillDialogOpen"] is False
    assert pressed["box"] == {"checked": False, "disabled": False}


def test_a_python_tool_says_its_own_read_only_so_the_checkbox_stands_down():
    assert _run("dialog-py")["box"] == {"checked": False, "disabled": True}


def test_each_way_in_reaches_its_route():
    upload = _run("add-file", hash="#/chat", filename="lookup.ts")
    assert upload["calls"][0]["url"] == \
        "./api/project/extensions/tools?filename=lookup.ts&readOnly=true"
    assert upload["calls"][0]["method"] == "POST"
    assert upload["calls"][0]["body"].startswith("<blob")
    git = _run("add-git", hash="#/chat")["calls"][0]
    assert git == {"url": "./api/project/extensions/tools/git", "method": "POST",
                   "body": {"url": "https://example.com/tools.git", "path": "tools",
                            "readOnly": True}}
