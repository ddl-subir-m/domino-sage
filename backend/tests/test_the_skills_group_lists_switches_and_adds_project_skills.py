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
SECTIONS = [{"name": "design"}, {"name": "platform"}]


def _run(act: str, **kw) -> dict:
    payload = {"act": act, "items": ITEMS, "builtinSkills": BUILTINS,
               "builtinSections": SECTIONS, **kw}
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
    assert _run("press-door", hash="#/chat", thread="t1")["dialogOpen"] is True


def test_a_skill_from_a_dataset_says_which_one():
    items = [{"id": "skill:house", "kind": "skill", "name": "house", "enabled": True,
              "source": {"type": "dataset", "dataset": "ds1", "name": "team-skills", "path": "s"}}]
    [row] = _run("drawn", hash="#/chat", thread="t1", items=items)["rows"]
    assert row["subtitle"] == "From team-skills"


def test_a_skill_row_opens_its_drawer_where_replaces_is_chosen():
    opened = _run("open-skill", hash="#/chat", thread="t1")
    assert opened["drawerOpen"] is True and opened["title"] == "tables"
    # An uploaded skill has nowhere to be read from again, so only Remove is offered.
    assert opened["buttons"] == ["Remove"]
    assert opened["replacesOptions"] == ["", "data-table", "investigate-weak-signals",
                                         "design", "platform"]
    assert "stops offering data-table" in opened["replacesCaption"]
    sent = _run("open-skill", hash="#/chat", thread="t1", replaces="design")["calls"]
    assert sent[0] == {"url": "./api/project/extensions/skill%3Atables/replaces", "method": "PUT",
                       "body": {"replaces": "design"}}


def test_a_git_skill_offers_update_from_source_and_it_reaches_its_route():
    items = [ITEMS[1] | {"shadowed": False}]
    assert _run("open-skill", hash="#/chat", thread="t1", items=items)["buttons"] == [
        "Update from source", "Remove"]
    out = _run("update", hash="#/chat", thread="t1", items=items)
    assert out["calls"][0] == {"url": "./api/project/extensions/skill%3Acharts/update",
                               "method": "POST", "body": None}
    assert out["toasts"] == ["Updated charts. It reaches the next turn."]


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


def test_several_md_files_are_sent_as_their_text_for_a_preview():
    [call] = _run("preview-md", hash="#/chat")["calls"]
    assert call["url"] == "./api/project/extensions/skills/files"
    assert call["body"] == {"files": {"house.md": "---\nname: house\n---\n", "colors.md": "Teal."},
                            "replaces": "", "preview": True}


def test_adding_sends_only_the_picked_skill_folders():
    call = _run("add-pick", hash="#/chat")["calls"][0]
    assert call["url"] == ("./api/project/extensions/skills?filename=pack.zip&replaces="
                           "&pick=skills%2Fa&pick=skills%2Fb")


def test_a_dataset_offers_each_skill_folder_and_each_zip():
    files = [{"path": p} for p in ("SKILL.md", "skills/house/SKILL.md", "skills/house/ref.md",
                                   "packs/brand.ZIP", "data.csv", "notes/SKILL.md.bak",
                                   "lower/skill.md")]
    assert _run("candidates", files=files)["candidates"] == [
        {"value": "", "label": "The whole dataset"},
        {"value": "lower", "label": "lower/"},
        {"value": "packs/brand.ZIP", "label": "packs/brand.ZIP"},
        {"value": "skills/house", "label": "skills/house/"},
    ]
