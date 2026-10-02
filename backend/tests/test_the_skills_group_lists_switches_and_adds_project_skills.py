"""The resources panel's Skills group: the Project's own skills, a switch each, and a way in (#620).

ADR-0071. A switch answers for the Conversation open in Chat or the app selected in Build, and is
off-limits before either exists. Drawn through `tests/js/project_skills_harness.mjs`, which records
every request the panel sends, so a switch or a removal is proved by what reached the server.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "project_skills_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

ITEMS = [
    {"id": "skill:tables", "kind": "skill", "name": "tables", "enabled": False,
     "replaces": "data-table", "source": {"type": "upload"}},
    {"id": "skill:charts", "kind": "skill", "name": "charts", "enabled": True, "shadowed": True,
     "source": {"type": "git", "url": "https://x"}},
    {"id": "tool:lookup", "kind": "tool", "name": "lookup", "enabled": True},
]
BUILTINS = [{"name": "data-table", "description": "Tables."},
            {"name": "investigate-weak-signals", "description": "Investigate."}]


def _run(act: str, **kw) -> dict:
    payload = {"act": act, "items": ITEMS, "builtinSkills": BUILTINS, **kw}
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(payload), capture_output=True,
                         text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_each_project_skill_is_a_row_and_only_skills_are():
    rows = _run("drawn", hash="#/chat", thread="t1")["rows"]
    assert [r["name"] for r in rows] == ["tables", "charts"]
    assert rows[0]["subtitle"] == "Replaces data-table · Uploaded"
    assert rows[0]["checked"] is False and rows[0]["disabled"] is False
    assert rows[0]["label"] == "Use tables in this conversation"


def test_a_shadowed_skill_says_to_rename_it_and_cannot_be_switched_on():
    charts = _run("drawn", hash="#/chat", thread="t1")["rows"][1]
    assert "Rename yours" in charts["subtitle"]
    assert charts["checked"] is False and charts["disabled"] is True


def test_with_no_conversation_or_app_yet_the_switch_is_off_limits_and_says_why():
    for hash_ in ("#/chat", "#/build"):
        row = _run("drawn", hash=hash_)["rows"][0]
        assert row["disabled"] is True and "Start a conversation" in row["tip"]


def test_a_switch_in_chat_writes_to_the_conversation_and_in_build_to_the_app():
    chat = _run("toggle", hash="#/chat", thread="t1")["calls"]
    put = next(c for c in chat if c["method"] == "PUT")
    assert put["url"] == "./api/project/extensions/skill%3Atables/enabled"
    assert put["body"] == {"enabled": False, "thread": "t1", "app": ""}
    # And the list is read again for the same Conversation, so the switch shows what was stored.
    assert chat[-1]["url"].endswith("/project/extensions?thread=t1&app=")

    build = _run("toggle", hash="#/build", app="app-1")["calls"]
    assert next(c for c in build if c["method"] == "PUT")["body"] == {
        "enabled": False, "thread": "", "app": "app-1"}


def test_remove_asks_first_then_deletes():
    out = _run("remove", hash="#/chat", thread="t1")
    assert out["confirmTitle"] == "Remove tables?"
    assert out["calls"][0] == {"url": "./api/project/extensions/skill%3Atables",
                               "method": "DELETE", "body": None}


def test_the_add_menu_and_the_group_door_both_open_the_dialog():
    drawn = _run("drawn", hash="#/chat", thread="t1")
    assert "skill" in drawn["menuKeys"] and drawn["dialogOpen"] is False
    pressed = _run("press-door", hash="#/chat", thread="t1")
    assert pressed["dialogOpen"] is True
    assert pressed["replacesOptions"] == ["", "data-table", "investigate-weak-signals"]


def test_each_way_in_reaches_its_route():
    git = _run("add-git", hash="#/chat")
    assert git["calls"][0] == {"url": "./api/project/extensions/skills/git", "method": "POST",
                               "body": {"url": "https://example.com/skills.git",
                                        "replaces": "data-table"}}
    upload = _run("add-file", hash="#/chat")["calls"][0]
    assert upload["url"] == "./api/project/extensions/skills?filename=house.zip&replaces="
    assert upload["method"] == "POST" and upload["body"].startswith("<blob")
    dataset = _run("add-dataset", hash="#/chat")["calls"][0]
    assert dataset["url"] == "./api/project/extensions/skills/dataset"
    assert dataset["body"] == {"dataset": "dataset:ds1", "path": "skills/house", "replaces": ""}


def test_a_dataset_offers_each_skill_folder_and_each_zip():
    files = [{"path": p} for p in ("SKILL.md", "skills/house/SKILL.md", "skills/house/ref.md",
                                   "packs/brand.ZIP", "data.csv", "notes/SKILL.md.bak")]
    assert _run("candidates", files=files)["candidates"] == [
        {"value": "", "label": "The whole dataset"},
        {"value": "packs/brand.ZIP", "label": "packs/brand.ZIP"},
        {"value": "skills/house", "label": "skills/house/"},
    ]
