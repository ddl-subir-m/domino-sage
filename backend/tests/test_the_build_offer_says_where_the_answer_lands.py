"""What the two Build offers promise, in the only place anyone reads it: the button.

The card arrives by two routes that do two different things on decline. The classifier raises it
after a turn that already answered, so declining runs nothing and `Not now` is the truth. The
explicit-build regex raises it INSTEAD of a turn, so declining runs the question in Chat — and
`Not now` reads as later, promises nothing, and leaves that answer arriving unannounced.

One handler, two labels. A source assertion on `dismissPlanSuggestion` cannot see which word the
person got, and the word is the whole difference.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "recall_offer_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _render(block: dict) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"block": block}),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _buttons(rendered: dict) -> list[dict]:
    return [n for n in rendered["nodes"] if n["tag"] == "Button"]


def _offer(reason: str) -> list[dict]:
    return _buttons(_render({"type": "plan_suggestion", "reason": reason}))


def test_the_explicit_offer_says_the_answer_lands_here():
    """Declining this one runs the question in Chat, so the button says so."""
    assert _offer("explicit")[1]["text"] == "Answer it here"


def test_the_classifier_offer_still_says_not_now():
    """Nothing runs under this one — the turn beneath it already answered."""
    assert _offer("classifier")[1]["text"] == "Not now"


def test_both_labels_are_the_same_button():
    """Two words, one act. The route is chosen by the store from what is on the Thread, not by
    which label was clicked, so a card that wired the new label to a new handler would be wrong."""
    for reason in ("explicit", "classifier"):
        buttons = _offer(reason)
        assert buttons[0]["text"] == "Write a plan"
        assert buttons[0]["act"] == "plan"
        assert buttons[1]["act"] == "dismiss-plan"


def test_declining_is_never_the_primary():
    """Writing a plan stays the one primary action on the card, under either label."""
    for reason in ("explicit", "classifier"):
        assert [b["kind"] for b in _offer(reason)] == ["primary", ""]
