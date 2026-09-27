"""`Use in this conversation` / `Stop using here` writes a chip on this Conversation.

WHAT #147 CHANGED, AND WHAT FOLLOWED IT. Both labels write or drop a chip
(`SW.store.addToContext` / `SW.store.removeResourceFromConversation`). #147 hid them in Build on
the reading that Build had no Conversation to name. Build is a view of that Conversation now
(ADR-0009), the chips already sit over the Build composer, and the same menu is offered there.
The bind stays off this row either way — that is the header's door.

THE BUILD-MODE HALF OF THIS PAIR is `test_the_resource_browser_stops_offering_use_in_app.py`,
which asserts the same two rows in Build. Read together, the two files are one claim: the act
rides the Conversation, in both modes, and it is not a bind.
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


def _row(rows: list[dict], name: str) -> dict:
    """The one panel row for `name`. No section to name any more: the panel is the Project's one
    list (#151), so a Resource is in it once or not at all."""
    found = [r for r in rows if name in r["texts"]]
    assert len(found) == 1, f"{name} appears {len(found)} times in the panel: {rows}"
    return found[0]


def _keys(row: dict) -> list[str]:
    return [i["key"] for i in row["items"] if i.get("key")]


@needs_node
def test_use_in_this_chat_still_offers_the_act_it_owns():
    """The positive claim the mode gate exists to protect: Chat keeps the act, unchanged, for a
    Resource nothing here has attached yet."""
    rows = _run([{"panel": "thr_many", "select": APP_ID, "mode": "chat"}])[-1]["rows"]
    row = _row(rows, "Claude Sonnet 4")
    assert _keys(row) == ["mention"]
    # Ink and hover, as the pair #410 moved it to: the short form is what the menu draws, and the
    # glossary term the other two surfaces say out loud lives on the hover. Asserted together, so
    # that shortening the ink cannot quietly cost the long form as well.
    assert [i["label"] for i in row["items"]] == ["Use here"]
    assert [i["title"] for i in row["items"]] == ["Use in this conversation"]


@needs_node
def test_stop_using_here_still_offers_the_way_back_out_in_chat():
    """The other half of the pair ADR-0015 named, on the same surface it was always meant for."""
    rows = _run([{"panel": "thr_many", "select": APP_ID, "mode": "chat"}])[-1]["rows"]
    row = _row(rows, "Market data EOD")
    assert _keys(row) == ["remove-resource-from-conversation"]
    assert [i["label"] for i in row["items"]] == ["Stop using here"]
