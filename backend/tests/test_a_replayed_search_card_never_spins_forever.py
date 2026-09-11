"""What the store makes of the search's rows when a Conversation is read back off disk.

The component's own test covers what the card says. This covers what the store decides it IS —
and the two rules here are only visible from this side.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.orchestrator import recall

_HARNESS = Path(__file__).resolve().parent / "js" / "chat_withhold_card_harness.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

FOUND = {"type": recall.FOUND, "carriers": [{"key": "file:raw.csv", "label": "raw.csv",
                                             "is_file": True}],
         "complete": True, "surviving": 2, "stopped": ""}


def _read(history: list[dict]) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"history": history}),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _cards(history: list[dict]) -> list[dict]:
    return _read(history)["cards"]


def test_a_replayed_search_row_draws_nothing():
    """A spinner read back off disk is one that never stops. The row it resolves into is already on
    the transcript, so there is nothing lost by skipping it."""
    assert _cards([{"type": "user", "text": "chart it"}, {"type": recall.SEARCH}]) == []


def test_a_replayed_answer_keeps_its_sentence_and_loses_its_buttons():
    cards = _cards([{"type": "user", "text": "chart it"}, {"type": recall.SEARCH}, FOUND])
    assert len(cards) == 1
    assert cards[0]["searching"] is False
    assert cards[0]["live"] is False, "a replayed row must not run a turn out of the scrollback"
    assert cards[0]["labels"] == ["raw.csv"]
    assert cards[0]["surface"] == "chat"


def test_the_surviving_count_survives_the_reload():
    """It is what decides whether the button offers to carry on or only to stop sending."""
    assert _cards([{"type": "user", "text": "chart it"}, FOUND])[0]["surviving"] == 2


WITHHELD = {"type": recall.WITHHELD, "keys": ["file:raw.csv"], "labels": ["raw.csv"]}


def test_a_withheld_row_draws_the_receipt_that_says_it_worked():
    """It was written from the day this shipped and drawn by nobody, so the only sign a click had
    landed was the buttons going quiet — and on Build not even that. Chat and Build render the same
    row for the same reason they share every other part of this: it says the same thing."""
    assert _read([{"type": "user", "text": "chart it"}, FOUND, WITHHELD])["withheld"] == [
        {"labels": ["raw.csv"], "surface": "chat"}]


def test_the_receipt_retires_the_card_that_asked_the_question():
    """Redrawing an answered offer leaves a person looking at an invitation to do the thing they
    have just done. The receipt names the same file, so the sentence is not lost with it."""
    assert _cards([{"type": "user", "text": "chart it"}, FOUND, WITHHELD]) == []


def test_a_later_refusal_naming_something_else_still_gets_its_card():
    """Retiring is per carrier, never per Conversation. A second offender is a second question."""
    other = {**FOUND, "carriers": [{"key": "file:export.csv", "label": "export.csv",
                                    "is_file": True}]}
    cards = _cards([{"type": "user", "text": "chart it"}, FOUND, WITHHELD, other])
    assert [c["labels"] for c in cards] == [["export.csv"]]
