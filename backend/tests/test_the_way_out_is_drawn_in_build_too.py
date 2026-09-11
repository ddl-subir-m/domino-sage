"""Build draws the offer to start the model over, and stops drawing it (ADR-0022).

The backend half has its own tests. This is the half that decides whether anybody ever sees it:
`buildHistoryToMessages` is a chain of `ev.type === ...` branches, and a row whose type has no
branch reaches the transcript and disappears — the defect `data-leak` shipped with (#94). A
hand-built block would pass whether or not the branch exists, so the harness feeds a history in and
reads `buildMessages` back out of a real `store.loadBuild()`.

The retirement rule is shared with Chat and is tested here because this is where it was wrong for
both: the reduce picked the newest suggestion whatever followed it, so the re-read that runs
straight after a clear drew the card again — asking someone to start over from a session they had
just started over.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_recall_offer_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

KEY = "guardrail:Block phone numbers"
REFUSED = 'Sage couldn\'t finish — the gateway refused it: "Blocked by guardrail: Block phone numbers".'


def _run(history: list[dict], dismiss: object = None) -> dict:
    body = {"history": history}
    if dismiss is not None:
        body["dismiss"] = dismiss
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(body),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _refused(order: int) -> list[dict]:
    return [
        {"type": "user", "text": "build me a dashboard", "order": order},
        {"type": "error", "reason": KEY, "message": REFUSED, "order": order + 1},
        {"type": "done", "ok": False, "decision": "gateway error", "order": order + 2},
    ]


def test_a_refused_build_turn_says_why_on_a_reload():
    """`error` was not persisted at all, so a reload got back the decision and nothing else. The
    branch that draws these rows has been here since it shipped; only the write was missing."""
    drawn = _run(_refused(0))

    assert REFUSED in drawn["statuses"]


def test_the_offer_is_drawn():
    drawn = _run(_refused(0) + [{"type": "recall-suggest", "scope": "summary", "order": 3}])

    assert drawn["offers"] == [{"scope": "summary", "surface": "build", "offerKey": "build:3"}]


def test_the_offer_knows_which_transcript_drew_it():
    """One card, two sessions. Build's Recall is filed per (Conversation, app) and Chat's per
    Thread, so the button has to reach a different clear — `surface` is what sends it there."""
    drawn = _run(_refused(0) + [{"type": "recall-suggest", "scope": "empty", "order": 3}])

    assert drawn["offers"][0]["surface"] == "build"
    assert drawn["offers"][0]["scope"] == "empty"


def test_a_clear_retires_the_offer_above_it():
    """The defect this shares with Chat. `clearBuildRecall` re-reads the transcript the moment it
    returns, and the reduce still pointed at the suggestion — so the card came back, buttons and
    all, one render after it had been used."""
    drawn = _run(_refused(0)
                 + [{"type": "recall-suggest", "scope": "summary", "order": 3},
                    {"type": "recall-cleared", "scope": "summary", "order": 4}])

    assert drawn["offers"] == []
    assert drawn["cleared"] == ["summary"]


def test_a_refusal_after_a_clear_offers_again():
    """Retiring the old card must not close the ladder. A Conversation that recovers and is refused
    again starts over, and the rung it reaches next is the server's to decide."""
    drawn = _run(_refused(0)
                 + [{"type": "recall-suggest", "scope": "summary", "order": 3},
                    {"type": "recall-cleared", "scope": "summary", "order": 4}]
                 + _refused(5)
                 + [{"type": "recall-suggest", "scope": "empty", "order": 8}])

    assert drawn["offers"] == [{"scope": "empty", "surface": "build", "offerKey": "build:8"}]


def test_only_the_newest_offer_keeps_its_buttons():
    """An older card is a record that the ladder was climbed here once. Clicking it would clear a
    session that has been replaced since."""
    drawn = _run(_refused(0)
                 + [{"type": "recall-suggest", "scope": "summary", "order": 3}]
                 + _refused(4)
                 + [{"type": "recall-suggest", "scope": "summary", "order": 7}])

    assert [o["offerKey"] for o in drawn["offers"]] == ["build:7"]


def test_not_now_survives_the_poll():
    """Build rebuilds this transcript every two seconds. Filtering the drawn messages was how Chat
    dismissed the card, and in Build that lasted until the next tick brought it back."""
    history = _refused(0) + [{"type": "recall-suggest", "scope": "summary", "order": 3}]

    assert _run(history, dismiss="build:3")["offers"] == []


def test_dismissing_one_offer_does_not_dismiss_the_next():
    """"Not now" is an answer about the offer in front of someone, not a preference about being
    offered. A later refusal is a new question."""
    history = (_refused(0)
               + [{"type": "recall-suggest", "scope": "summary", "order": 3}]
               + _refused(4)
               + [{"type": "recall-suggest", "scope": "empty", "order": 7}])

    assert [o["offerKey"] for o in _run(history, dismiss="build:3")["offers"]] == ["build:7"]


def test_a_transcript_with_no_refusal_draws_nothing():
    drawn = _run([{"type": "user", "text": "build me a dashboard", "order": 0},
                  {"type": "done", "ok": True, "decision": "build clean", "order": 1}])

    assert drawn["offers"] == [] and drawn["cleared"] == []
