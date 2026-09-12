"""The 409 that blocks removing a Resource from the Project, as a person reads it.

`remove_project_resource` already names the Built Apps, the files, and the conversation titles.
Dumping that sentence into one Modal.info body reprints the titles — the server line AND
"Held in" — and untitled chats use the first message as the title, so @mentions wrap into a wall.
The payload has lists; the notice has to draw lists.

Nothing is mounted. `createElement` is stubbed, so this walks a tree of data. See
`js/still_bound_notice_harness.mjs`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "still_bound_notice_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

# The screenshot: a Dataset id in the heading, one Built App, three untitled chats whose titles
# are the first user message, all concatenated into one paragraph.
_DASHBOARD = (
    "build me a support dashboard using the data in @sage-subir-mansukhani-66a821b1-2"
)
_WHAT = "what data is there in @sage-subir-mansukhani-66a821b1-2"
_VIZ = "visualize the support data thats there in @sage-subir-mansukhani-66a821b1-2"
_SCREENSHOT = {
    "apps": ["Support Pulse"],
    "refs": ["src/App.tsx"],
    "conversations": [_DASHBOARD, _WHAT, _VIZ],
    "scopeName": "Default",
}


def _notice(spec: dict) -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(spec),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def _text(rendered: dict) -> str:
    return " ".join(n["text"] for n in rendered["nodes"] if n["text"])


def _rows(rendered: dict) -> list[dict]:
    return [n for n in rendered["nodes"] if n["className"] == "sw-still-bound-row"]


def test_the_heading_does_not_carry_the_dataset_id():
    """The confirm they just answered already named the Resource. Repeating the id as the
    heading wraps the title and crowds the body that has to name the holders."""
    rendered = _notice(_SCREENSHOT)
    assert rendered["title"] == "Can't remove this yet"
    assert "sage-subir-mansukhani-66a821b1-2" not in rendered["title"]


def test_apps_and_conversations_are_separate_rows_not_one_sentence():
    """The bug: app name, three titles, the dataset id and both how-tos in one paragraph."""
    rendered = _notice(_SCREENSHOT)
    said = _text(rendered)
    assert [row["text"] for row in _rows(rendered)] == [
        "Support Pulse", _DASHBOARD, _WHAT, _VIZ,
    ]
    assert "Support Pulse, build me a support dashboard" not in said
    assert "Held in:" not in said
    assert "still need" not in said


def test_each_long_title_is_kept_in_full_on_the_row():
    """The row ellipsises in CSS. The title attribute is how hover still has the whole sentence."""
    rendered = _notice(_SCREENSHOT)
    chats = [row for row in _rows(rendered) if row["text"] != "Support Pulse"]
    assert [row["title"] for row in chats] == [_DASHBOARD, _WHAT, _VIZ]


def test_each_group_says_what_to_do_under_its_own_list():
    rendered = _notice(_SCREENSHOT)
    said = _text(rendered)
    assert "Still needed in Default" in said
    assert "1 Built App" in said
    assert "3 conversations" in said
    assert "src/App.tsx" in said
    assert "Remove those uses in Build." in said


def test_an_app_only_refusal_does_not_draw_a_conversation_group():
    rendered = _notice({
        "apps": ["Churn model"],
        "refs": [".sage/queries.json", "src/Ads.tsx"],
        "conversations": [],
        "scopeName": "Default",
    })
    said = _text(rendered)
    assert "conversation" not in said.lower()
    assert [row["text"] for row in _rows(rendered)] == ["Churn model"]
    assert "1 Built App" in said


def test_a_chip_only_refusal_does_not_draw_an_app_group():
    rendered = _notice({
        "apps": [],
        "refs": [],
        "conversations": ["Positions review"],
        "scopeName": "Default",
    })
    said = _text(rendered)
    assert "Built App" not in said
    assert [row["text"] for row in _rows(rendered)] == ["Positions review"]
    assert "1 conversation" in said


def test_an_app_that_carries_files_says_what_it_holds_and_not_only_that_it_refused():
    """The third holder (ADR-0048). Its app name is already a row — one app is one place to go —
    so what the notice owes is the other half: which folders it carries.

    And the how-to underneath has to match, which is where this first went wrong. A carrying app
    holds no Binding, so it has no "uses" to remove; sending the reader to Build to remove one is
    the dead end the whole change exists to close."""
    rendered = _notice({
        "apps": ["Support Pulse"],
        "refs": [],
        "conversations": [],
        "carriers": ["sales_2026"],
        "scopeName": "Default",
    })
    said = _text(rendered)
    assert [row["text"] for row in _rows(rendered)] == ["Support Pulse"]
    assert "Carries data in sales_2026" in said
    assert "Remove those files in Build." in said
    assert "Remove those uses in Build." not in said


def test_an_app_that_both_binds_and_carries_is_told_to_do_both():
    """`refs` and `carriers` are not alternatives. One app can bind a Dataset and carry its files,
    and `_resource_usage` matches a Dataset binding on its NAME — which the served path of its own
    attachment contains — so this is the ordinary case rather than a corner. Naming only the uses
    sends the reader to remove them and straight back into the same 409."""
    rendered = _notice({
        "apps": ["Support Pulse"],
        "refs": ["src/App.tsx"],
        "conversations": [],
        "carriers": ["sales_2026"],
        "scopeName": "Default",
    })
    said = _text(rendered)
    assert "Used in src/App.tsx" in said
    assert "Carries data in sales_2026" in said
    assert "Remove those uses and those files in Build." in said


def test_two_carrying_apps_keep_their_folders_apart():
    """Each entry already joins one app's folders with ", ". Joining the entries at the same
    separator makes one flat list, and nobody can then say which folder belongs to which app."""
    rendered = _notice({
        "apps": ["Support Pulse", "Churn model"],
        "refs": [],
        "conversations": [],
        "carriers": ["Support Pulse — sales_2026/raw, sales_2026/curated",
                     "Churn model — sales_2026"],
        "scopeName": "Default",
    })
    lines = [n["text"] for n in rendered["nodes"] if n["className"] == "sw-still-bound-meta"]

    assert lines == [
        "Carries data in Support Pulse — sales_2026/raw, sales_2026/curated",
        "Carries data in Churn model — sales_2026",
    ]


def test_two_apps_take_the_plural():
    rendered = _notice({
        "apps": ["Support Pulse", "Churn model"],
        "refs": [],
        "conversations": [],
        "scopeName": "Default",
    })
    said = _text(rendered)
    assert "2 Built Apps" in said
    assert "Remove it from those apps in Build." in said
