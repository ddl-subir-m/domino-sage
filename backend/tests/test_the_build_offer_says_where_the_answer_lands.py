"""What the two Build offers promise, in the only place anyone reads it: the button.

The card arrives by two routes that do two different things on decline. The explicit-build regex
raises it INSTEAD of a turn, so declining runs the question in Chat and the button says `Answer
here`. The classifier raises it after a turn that already answered, so declining runs nothing —
and a second button there read as an act that then did nothing visible (#588). It declines with a
corner × and leaves a note saying what changed.

One handler, two shapes. A source assertion on `dismissPlanSuggestion` cannot see which the
person got, and that is the whole difference.
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
    """Declining this one runs the question in Chat, so the button says so — and asks for it."""
    buttons = _offer("explicit")
    assert [b["text"] for b in buttons] == ["Write a plan", "Answer here"]
    assert buttons[1]["act"] == "dismiss-plan:answer"


def test_the_classifier_offer_declines_with_a_corner_x():
    """Nothing runs under this one — the turn beneath it already answered — so a second button
    beside `Write a plan` read as an act and then did nothing visible (#588). The decline is a ×
    that says what it is to a screen reader, and it does not ask for an answer."""
    buttons = _offer("classifier")
    assert [b["text"] for b in buttons] == ["", "Write a plan"]
    assert buttons[0]["label"] == "Dismiss"
    assert buttons[0]["act"] == "dismiss-plan"


def test_write_a_plan_is_the_one_primary_on_either_arm():
    for reason in ("explicit", "classifier"):
        buttons = _offer(reason)
        assert [b["text"] for b in buttons if b["kind"] == "primary"] == ["Write a plan"]
        assert next(b for b in buttons if b["text"] == "Write a plan")["act"] == "plan"


def test_the_declined_note_says_what_changed_and_the_way_back():
    """What the × leaves in the card's place. The decline is permanent for this chat, and nothing
    else on screen moves, so the note is the only way the person learns either."""
    rendered = _render({"type": "plan_suggestion_declined"})
    said = " ".join(n["text"] for n in rendered["nodes"] if n["text"])
    assert "won't suggest this again in this chat" in said
    assert "Open in Build" in said
    assert _buttons(rendered) == []
