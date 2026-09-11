"""What pressing the guardrail card's button does on BUILD.

The Chat half shipped working and the Build half never did: `store.withholdContent` wrote through
the right door, then rearranged `state.messages` — the list CHAT draws. Build draws
`state.buildMessages`, so the row was written, the transcript came back identical, and both buttons
read as dead. Every test the feature had was blind to it: the component test asserts which act a
button calls against a stub store, the server tests assert the row, and the only store harness was
Chat's.

So these run the whole click on Build: a live refused turn, a press, and what changed on screen.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.orchestrator import recall

_HARNESS = Path(__file__).resolve().parent / "js" / "build_withhold_card_harness.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

RAW = {"key": "file:/mnt/data/raw.csv", "label": "card_panel_transactions_RAW.csv", "is_file": True}
PASTED = {"key": "text:abc123", "label": "the message you sent", "is_file": False}


def _refused(carrier: dict, surviving: int) -> list[dict]:
    """The frames a refused Build turn sends: the failure, the search, what it found, the end."""
    return [
        {"type": "error", "message": 'Blocked by guardrail: "Block PII"'},
        {"type": recall.SEARCH},
        {"type": recall.FOUND, "carriers": [carrier], "complete": True,
         "surviving": surviving, "stopped": ""},
        {"type": "done", "ok": False, "decision": "gateway error"},
    ]


def _click(act: str, carrier: dict = RAW, surviving: int = 2) -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"history": [], "events": _refused(carrier, surviving), "act": act}),
        capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_card_arrives_live_so_there_is_something_to_press():
    """The premise of every test below. If the card were never live the buttons would be absent and
    a broken click would be invisible for the wrong reason."""
    r = _click("")
    assert r["before"]["cards"] == [{
        "searching": False, "live": True, "labels": ["card_panel_transactions_RAW.csv"],
        "surviving": 2, "surface": "build"}]


def test_pressing_it_takes_the_card_away_and_leaves_a_receipt():
    """The defect, in one assertion. The door was always called — what never happened is anything a
    person could see. A card that stays put after a click reads as a button that does not work."""
    r = _click("withhold")
    assert [p["path"] for p in r["posted"]][:1] == ["/project/recall/withhold"]
    assert r["after"]["cards"] == [], "the question has been answered; the card must retire"
    assert r["after"]["withheld"] == [{"labels": ["card_panel_transactions_RAW.csv"]}]


def test_when_data_survives_the_failed_turn_runs_again():
    """The half of the promise the button makes. "Continue without this file" has to continue, and
    on Build it never did: the re-run read Chat's message list for the question and sent it to
    Chat's door, so a Build conversation with no Chat open re-ran nothing at all."""
    r = _click("withhold", surviving=2)
    assert [p["path"] for p in r["posted"]] == [
        "/project/recall/withhold", "/project/build/stream"], "the build turn, not Chat's"
    assert "chart weekly panel spend" in r["after"]["prompts"]


def test_when_nothing_survives_it_does_not_re_run():
    """Withhold the only thing a turn read and there is nothing left to answer from. Re-running
    would spend a whole turn arriving at "I cannot read that"."""
    r = _click("withhold", carrier=PASTED, surviving=0)
    assert [p["path"] for p in r["posted"]] == ["/project/recall/withhold"]


def test_dismiss_takes_the_card_away_and_it_stays_away():
    """Two failures in one press. It filtered `state.messages`, which Build does not draw, so
    nothing happened at all — and had it filtered the right list, the next two-second poll would
    have rebuilt the card anyway. Same shape, same fix, as `dismissBuildRecallOffer`."""
    r = _click("dismiss")
    assert r["after"]["cards"] == []
    assert r["posted"] == [], "hiding a card is not an answer worth writing to the transcript"
