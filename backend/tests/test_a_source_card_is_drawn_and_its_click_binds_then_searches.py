"""The Workbench half of the Data Source question (#185, ADR-0038).

Two things break here without throwing anything, which is why this runs the real `store.js` rather
than reading it. `buildHistoryToMessages` is a chain of `ev.type === ...` branches, and a turn event
with no branch reaches the transcript and vanishes — the defect this repo has already shipped once,
and here it would make the card a turn that asked nothing. And the click is TWO acts, deliberately:
it records the Binding, then sends the request the person already made against it. Drop the second
and answering the question bought them nothing; drop `chosenSource` from it and the search that
follows runs against whatever the prose happened to name. Neither shows up in Python.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "source_candidate_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

PROMPT = "build me a dashboard of daily gong calls from Snowflake"

# One turn, exactly as the orchestrator wrote it to `.sage/history.jsonl`: the person's sentence,
# the card, and the `done` that says the turn stopped here without building.
HISTORY = [
    {"type": "user", "text": PROMPT},
    {"type": "source-candidates", "prompt": PROMPT,
     "message": "Which Data Source should this Built App read?",
     "answered": {"skipResetGate": False, "skipIncomingGate": False, "skipSourceGate": False},
     "named": 1,
     "sources": [{"id": "ds-dwh", "name": "Snowflake-Data-Warehouse", "connector": "Snowflake"},
                 {"id": "ds-reporting", "name": "reporting-replica", "connector": "PostgreSQL"}]},
    {"type": "done", "ok": False, "decision": "data source candidates"},
]


def _run(answered: dict | None = None) -> dict:
    """The turn above, with the gates it had already answered set on its card.

    On the card rather than passed alongside it, because the card is how they reach the click: the
    harness answers whatever it drew, so a card that dropped them on the way through the transcript
    fails here rather than showing up as a reset offer somebody sees twice.
    """
    history = [dict(row) for row in HISTORY]
    if answered:
        history[1]["answered"] = {**history[1]["answered"], **answered}
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"history": history, "prompt": PROMPT}),
        check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_card_is_drawn_from_the_turn_row_with_every_store_on_it():
    """The whole card survives the transcript, every reachable store included.

    Every one of them, because the card is the list: a person whose store was left off it is back
    where this ticket started, with a question they cannot answer from where they are standing.
    """
    cards = _run()["cards"]

    assert len(cards) == 1
    assert cards[0]["prompt"] == PROMPT
    assert [s["name"] for s in cards[0]["sources"]] == [
        "Snowflake-Data-Warehouse", "reporting-replica"]
    # And the gates the turn was already past, which the click reads off the card and sends back.
    assert cards[0]["answered"] == HISTORY[1]["answered"]
    # And whether any of these was named, which is what decides that the first row is drawn as the
    # answer rather than as the head of a list.
    assert cards[0]["named"] == 1


@needs_node
def test_a_card_read_back_off_the_server_carries_no_buttons():
    """`live` is set only on a frame that arrived over SSE in this session.

    A card replayed on a page load is a record of a decision somebody already made, and its buttons
    would record a Binding and start a build out of a message being scrolled back through. The
    buttons GO rather than grey out, which is what the offers beside this one do.
    """
    assert _run()["cards"][0]["live"] is False
    ui = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js" / "components"
          / "message-blocks.js").read_text()
    card = ui[ui.index("function SourceCandidates("):ui.index("// The tables a search found")]
    assert "block.live && block.prompt" in card
    assert "!block.live" not in card, "a replayed card renders its buttons disabled, not gone"


@needs_node
def test_the_click_records_the_binding_first_and_then_replays_the_request():
    """Two calls, in order, and the second is what makes the first worth clicking.

    The record stands whether or not the turn after it succeeds, and that turn takes the turn lock
    like any other — which is what makes this two requests rather than one route doing both. The
    replay carries the store that was picked, because the pick is the answer and the prose is not.
    """
    out = _run()

    assert out["routes"][0] == "POST api/bindings"
    assert out["bind"] == {"kind": "data_source", "id": "ds-dwh"}
    assert "POST api/project/build/stream" in out["routes"]
    assert out["routes"].index("POST api/project/build/stream") > 0
    assert out["replay"]["chosenSource"] == "ds-dwh"
    assert out["replay"]["prompt"] == PROMPT
    # The card that follows is the table card, and the request behind it is not asked for again.
    assert out["replay"]["skipTableGate"] is False


@needs_node
def test_the_answered_card_is_retired_and_says_which_store_was_picked():
    """The reload the click performs is what takes the buttons back.

    A card that stayed answerable would let a second click months into a conversation record a
    different store and start another build. And the bubble it leaves says which store was picked:
    the request is already a bubble above the card, so repeating it there would say the person
    asked for the same thing twice.
    """
    out = _run()

    assert all(card["live"] is False for card in out["cardsAfter"])
    assert out["bubbles"] == [PROMPT, "Use Snowflake-Data-Warehouse."]


@needs_node
def test_the_replay_carries_the_gates_this_turn_had_already_answered():
    """"Start over and build a gong dashboard from Snowflake" answers the reset offer and then
    reaches this card. The reset gate is a prompt match with nothing remembered, so a replay that
    dropped `skipResetGate` would offer to throw the app away a second time — for a request the
    person already answered, with the destructive button back under it."""
    out = _run({"skipResetGate": True, "skipIncomingGate": False})

    assert out["replay"]["skipResetGate"] is True
    assert out["replay"]["chosenSource"] == "ds-dwh"
    # Both buttons, and the second is the one that would loop: a "build without one" that dropped
    # `skipResetGate` meets the reset offer again, and answering THAT drops `skipSourceGate` and
    # meets this card again. The two cards would trade the turn back and forth for as long as the
    # person kept answering them.
    assert out["withoutOne"]["skipResetGate"] is True
    assert out["withoutOne"]["skipSourceGate"] is True


@needs_node
def test_building_without_one_answers_the_card_rather_than_recording_anything():
    """The other button. Nothing is recorded, and the gate is answered rather than skipped —
    without that flag the same words meet the same card on the next turn, forever."""
    out = _run()

    assert out["withoutOne"]["skipSourceGate"] is True
    assert out["withoutOne"]["chosenSource"] == ""
    assert out["withoutOne"]["prompt"] == PROMPT
