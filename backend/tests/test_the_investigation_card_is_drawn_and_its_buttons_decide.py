"""The Workbench half of the investigation offer (#386, ADR-0056).

`historyToMessages` is its own chain of `ev.type === ...` branches, so a row whose type has no
branch reaches the Thread and vanishes — and this card is drawn INSTEAD of an answer, so a card that
vanishes is a turn that produced nothing at all.

The click is two acts, record then re-ask. Drop the second and the answer never comes. Drop
`investigationAnswered` and the person's question is drawn twice, once from the Thread and once
optimistically under the card that already carries it. Swap the two buttons' words and `Just answer
this` grants a shell for the rest of the conversation.

None of that throws, and none of it crosses into Python — which is why this runs the real
`store.js` instead of reading it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "chat_investigation_offer_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

PROMPT = ("Which customers actively use Model Monitor, based on Mixpanel, Gong and Salesforce?")

# One Chat turn, exactly as the orchestrator wrote it to the Thread: the person's sentence, the
# card, and the `done` that says the turn stopped here without answering.
HISTORY = [
    {"type": "user", "text": PROMPT},
    {"type": "investigation-offer", "prompt": PROMPT, "threadId": "thr_1",
     "message": "This question looks like it needs more than one answer."},
    {"type": "done", "ok": False, "decision": "investigation offer"},
]


def _run(press: int | str = 0, history: list | None = None) -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"history": HISTORY if history is None else history,
                          "prompt": PROMPT, "press": press}),
        check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_card_is_drawn_from_the_thread_and_carries_the_question_back():
    """The card has to carry the prompt and the Thread, because both buttons replay the question
    and the record goes on the conversation rather than on a Built App it may not have."""
    cards = _run()["cards"]
    assert len(cards) == 1
    assert cards[0]["prompt"] == PROMPT
    assert cards[0]["threadId"] == "thr_1"
    assert cards[0]["message"]


@needs_node
def test_a_card_read_back_off_the_thread_carries_no_buttons():
    """`live` is set only on a frame that arrived over SSE this session. These buttons grant a
    capability for the rest of the conversation and then re-run a question; a replayed card would
    do both out of a message somebody is only scrolling back through."""
    assert _run()["cards"][0]["live"] is False
    assert _run()["replayedButtons"] == 0


@needs_node
def test_the_card_offers_one_act_and_one_way_past_it():
    """Two buttons, in this order: the grant, then the turn that would have run anyway."""
    assert _run()["buttons"] == ["Investigate", "Just answer this"]


@needs_node
@pytest.mark.parametrize("press,decision", [(0, "open"), (1, "decline")])
def test_each_button_records_its_own_word_and_then_asks_the_question_again(
    press: int, decision: str,
):
    out = _run(press)

    assert out["routes"][0] == "api/threads/thr_1/investigation"
    assert out["click"] == {"decision": decision}
    assert "api/threads/thr_1/chat/stream" in out["routes"]
    assert out["routes"].index("api/threads/thr_1/chat/stream") > 0
    # The other cards' gate flags ride along at their defaults: a Chat turn carries every one the
    # route reads, and this click answers only the investigation.
    assert out["replay"] == {"prompt": PROMPT, "skipTableGate": False, "skipDatasetGate": False,
                             "datasetDismissed": "", "investigationAnswered": True,
                             "otherLaneGrant": ""}
    # And the question stays a single bubble. It comes back with the reload, above the card that
    # quotes it, so the replay draws none of its own.
    assert out["asked"] == [PROMPT]


@needs_node
def test_accepting_leaves_the_bar_up_and_declining_does_not():
    """The bar over the composer is the visible half of the grant, and it reads the Thread's own
    record — so it is true after the reload the click does, not only in the tab that clicked."""
    assert _run(0)["barOpen"] is True
    assert _run(1)["barOpen"] is False


@needs_node
def test_closing_records_the_close_and_asks_nothing():
    """Nothing was asked, so nothing is owed an answer. A close that replayed would re-run whatever
    question happened to be last in the conversation."""
    out = _run("close")

    assert out["click"] == {"decision": "close"}
    assert not [r for r in out["routes"] if "chat/stream" in r]
    assert out["barOpen"] is False


@needs_node
def test_the_transcript_says_when_the_grant_began_and_when_it_ended():
    """A bar says what is true now. A transcript read a week later is where "when did this
    conversation get a shell" gets asked, and only these rows can answer it."""
    history = HISTORY + [{"type": "investigation-state", "state": "open"},
                         {"type": "investigation-state", "state": "closed"}]

    lines = _run("close", history)["lines"]

    assert len(lines) == 2
    assert "opened" in lines[0] and "closed" in lines[1]
    # Closing is not deleting, and the line that reports it says so.
    assert "kept" in lines[1]
    # And it says which turns it reaches. The flag is read once at the start of a turn, so a turn
    # already streaming keeps the shell it was armed with — "turns are bounded again" would be a
    # sentence the close cannot make true.
    assert "Later" in lines[1]


@needs_node
def test_a_close_that_came_with_a_clear_promises_nothing_about_the_measurements():
    """The one close where the findings did NOT survive: a complete Recall clear deletes them and
    then takes the grant (ADR-0055, ADR-0056). The ordinary line would be a promise about a file
    that is gone, so `reason` picks a different one."""
    history = HISTORY + [{"type": "investigation-state", "state": "closed", "reason": "clear"}]

    lines = _run("close", history)["lines"]

    assert len(lines) == 1
    assert "closed" in lines[0]
    assert "kept" not in lines[0] and "measured" not in lines[0]
