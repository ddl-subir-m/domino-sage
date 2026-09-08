"""`live` is this tab's memory of watching a card arrive, and a re-read must not erase it (#209).

Seven Build cards are answered by clicking them, and every one of those buttons is drawn from a
`live` flag the browser stamps on the SSE frame as it lands. Nothing on the server carries it, on
purpose: a card replayed on a page load is a record of a decision somebody already made, and its
buttons would write a Binding and start a build out of a message they are only scrolling back
through. `test_a_card_read_back_off_the_server_carries_no_buttons` is where that rule is pinned and
it stays pinned.

What broke is the OTHER reading of the same flag. `applyBuildRead` replaces `state.buildHistory`
wholesale with rows read back from the server, and three callers can fire it while a turn is still
running — the 2s poll, the app rail, a route change. Any of them landing between the card arriving
and the person clicking took the buttons off a card this tab had watched arrive, leaving a sentence
still asking them to pick with no way to answer it. The way forward IS the click, so there was none.

The distinction is "this tab watched this card arrive" against "this tab is reading it back", which
is not the same as "live against history": the rows are identical either way, and the difference
lives only in what this tab saw happen. So this drives the real store through both hops rather than
asserting on a mock. Both ENDS are already covered elsewhere — a card off the server has no buttons,
a card off the stream has them — and the middle hop, where the re-read lands on an already-live
card, is invisible from either one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "table_candidate_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

# The request from the report: an @mentioned store with nothing bound, which draws #206's merged
# card at the end of a table walk that runs for several seconds. That wait is what made the window
# wide enough to see — the same race was milliseconds wide before it.
PROMPT = "build me a dashboard from the gong table in @Snowflake-Data-Warehouse"

# The card, as the server both yields it and writes it. One dict for both, because that is literally
# what the orchestrator does (see `_table_offer`) — which is why a key taken off the frame can still
# recognise the row that comes back.
TABLE_CARD = {
    "type": "table-candidates", "prompt": PROMPT,
    "message": "Sage read what **Snowflake-Data-Warehouse** holds.",
    "sourceId": "ds-dwh", "sourceName": "Snowflake-Data-Warehouse", "bindFirst": True,
    "groups": [{"database": "DWH", "schema": "MARTS", "tables": ["GONG__CALLS"]}],
    "allGroups": [{"database": "DWH", "schema": "MARTS", "tables": ["GONG__CALLS"]}],
    "total": 1, "matched": 1,
}

# The four offers with no `sourceId` — which is the half of this that needed thinking about, because
# the obvious key for a card is the store it is about and these name no store. `order` is no help
# either: `appendBuildRow` only stamps one when Build is reading the merged Conversation.
RESET_OFFER = {
    "type": "reset-offer", "prompt": PROMPT,
    "message": "Starting over is its own action, not a build.",
    "mentions": [], "resources": [],
}
INCOMING_CHANGES = {
    "type": "incoming-changes", "prompt": PROMPT,
    "message": "Somebody else changed this app since you last built it.",
    "files": ["app.py"], "count": 1,
}
BUILD_STALLED = {
    "type": "build-stalled", "prompt": PROMPT,
    "message": "That turn stopped without saying anything. Nothing was lost.",
}
MENTIONS_UNRESOLVED = {
    "type": "mentions-unresolved",
    "message": "Sage could not use **Snowflake-Data-Warehouse**: this app has no record of it.",
    "entries": [{"kind": "data_source", "id": "ds-dwh", "name": "Snowflake-Data-Warehouse",
                 "app": "Demo app", "appId": "app_a"}],
}

DONE = {"type": "done", "ok": False, "decision": "offered"}


def _live(card: dict) -> dict:
    """One typed turn that ends in `card`, then a re-read on top of it.

    `stream` is what the turn yields; `history` is what the server has on disk once it has yielded
    it, and the harness only starts serving that after the stream opens. So the re-read finds the
    same rows the frames carried, minus the `live` this tab stamped on them — which is the exact
    shape of the read that was stripping the buttons.
    """
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({
            "prompt": PROMPT, "answered": {}, "stream": [card, DONE],
            "history": [{"type": "user", "text": PROMPT}, card, DONE],
        }),
        check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_a_card_this_tab_watched_arrive_keeps_its_buttons_through_a_re_read():
    """The report, reproduced: the merged card arrives with its tables answerable, a transcript
    re-read lands, and every field is still right except the one that decides whether the person can
    act. `live: false` on a card nobody reloaded past is the confirmation.

    Both moments are read, not just the second: a run where the card was never answerable to begin
    with would pass the second assertion for the wrong reason entirely.
    """
    out = _live(TABLE_CARD)

    assert out["arrived"]["blocks"] == [
        {"type": "table_candidates", "live": True, "buttons": ["GONG__CALLS"]}]
    # A re-read really landed between the two reads. Without this the test below could pass on a run
    # where nothing ever re-read anything, which is the one way it could certify nothing at all.
    assert out["reread"]["reads"] > out["arrived"]["reads"]
    assert out["reread"]["blocks"] == [
        {"type": "table_candidates", "live": True, "buttons": ["GONG__CALLS"]}]


@needs_node
@pytest.mark.parametrize("card,expected", [
    (RESET_OFFER, ["Reset and build this", "Just reset", "Build without resetting"]),
    (INCOMING_CHANGES, ["Pull and build this", "Keep building"]),
    (BUILD_STALLED, ["Try again"]),
    (MENTIONS_UNRESOLVED, ["Use in Demo app"]),
])
def test_an_offer_that_names_no_store_survives_the_same_re_read(card, expected):
    """The four cards a `type` + `sourceId` key cannot tell apart, which is why the key is not that.

    They are also the four where losing the buttons is quietest: a table card that goes dead still
    shows a warehouse of names somebody can see they cannot click, while these collapse to the
    sentence they were already showing. "Somebody else changed this app" with nothing under it reads
    as a note rather than a question that was withdrawn while it was being read.
    """
    out = _live(card)

    assert [b["buttons"] for b in out["arrived"]["blocks"]] == [expected]
    assert out["reread"]["reads"] > out["arrived"]["reads"]
    assert [b["buttons"] for b in out["reread"]["blocks"]] == [expected]


@needs_node
def test_the_rail_moving_to_another_conversation_takes_the_buttons_back():
    """What this tab watched arrive is a memory of THIS conversation, and it does not follow the
    reader out of it and back in.

    The card standing there after a round trip through another conversation is one being read back,
    and it is the same card a page reload leaves — so it has to read the same way. Without this the
    memory would only ever grow, and every offer the session had ever seen would still be answerable
    from wherever the reader ended up, against an app and a catalog that had moved on.
    """
    out = _live(TABLE_CARD)

    assert out["afterSwitch"]["blocks"] == [
        {"type": "table_candidates", "live": False, "buttons": []}]
