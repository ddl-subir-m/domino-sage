"""`Use in this chat` / `Stop using here` is a Chat-only act (#147, ADR-0023).

WHAT #147 CHANGED. Both labels write or drop a chip on THIS Conversation
(`SW.store.addToContext` / `SW.store.removeResourceFromConversation`). Build has no Conversation on
screen for either verb to name, so a Project-list row offering them there was the resource-panel.js
bug #147 fixed: no mode check on the `mention` / `remove-resource-from-conversation` menu item,
so a Build reader saw "chat" while standing in Build.

THE BUILD-MODE HALF OF THIS PAIR is `test_the_resource_browser_stops_offering_use_in_app.py`, which
asserts the now-empty menu on the same two rows this file exercises in Chat. Read together, the two
files are one claim: the act rides the mode, not the kind or the row.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

APP_ID = "app_c"


def _run(steps: list[dict]) -> list[dict]:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(steps),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _row(rows: list[dict], name: str, section: str) -> dict:
    found = [r for r in rows if r["section"] == section and name in r["texts"]]
    assert len(found) == 1, f"{name} appears {len(found)} times under {section}: {rows}"
    return found[0]


def _keys(row: dict) -> list[str]:
    return [i["key"] for i in row["items"] if i.get("key")]


@needs_node
def test_use_in_this_chat_still_offers_the_act_it_owns():
    """The positive claim the mode gate exists to protect: Chat keeps the act, unchanged, for a
    Resource nothing here has attached yet."""
    rows = _run([{"panel": "thr_many", "select": APP_ID, "mode": "chat"}])[-1]["rows"]
    row = _row(rows, "Claude Sonnet 4", "In this project")
    assert _keys(row) == ["mention"]
    assert [i["label"] for i in row["items"]] == ["Use in this chat"]


@needs_node
def test_stop_using_here_still_offers_the_way_back_out_in_chat():
    """The other half of the pair ADR-0015 named, on the same surface it was always meant for."""
    rows = _run([{"panel": "thr_many", "select": APP_ID, "mode": "chat"}])[-1]["rows"]
    row = _row(rows, "Market data EOD", "In this project")
    assert _keys(row) == ["remove-resource-from-conversation"]
    assert [i["label"] for i in row["items"]] == ["Stop using here"]
