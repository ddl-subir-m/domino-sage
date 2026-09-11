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


def _cards(history: list[dict]) -> list[dict]:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"history": history}),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)["cards"]


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
