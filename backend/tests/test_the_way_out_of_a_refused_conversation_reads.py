"""What a person actually sees when the gateway keeps refusing their Conversation (ADR-0022).

The backend decides which rung the ladder is on; these strings are the only part of that anyone
reads. Every decision the design made and a person can perceive is here: which rung they are on,
what clearing costs, what survives it, and that the transcript is not the thing being emptied.

Nothing is mounted — `createElement` is stubbed, so this walks a tree of data. See
`js/recall_offer_harness.mjs`.
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


def _text(rendered: dict) -> str:
    return " ".join(n["text"] for n in rendered["nodes"] if n["text"])


def _buttons(rendered: dict) -> list[dict]:
    return [n for n in rendered["nodes"] if n["tag"] == "Button"]


def test_the_first_rung_says_what_it_costs_and_what_survives():
    said = _text(_render({"type": "recall_offer", "scope": "summary"}))
    assert "refused the same way twice" in said       # why it is being offered at all
    assert "Recall" in said                           # the thing being cleared, by its name
    assert "transcript stays" in said                 # and the thing that is not
    assert "summary of what was said" in said         # the promise `recall.seed` then keeps


def test_the_second_rung_says_why_the_first_one_failed():
    """Otherwise clicking again looks like the same button doing the same thing twice."""
    said = _text(_render({"type": "recall_offer", "scope": "empty"}))
    assert "still being refused" in said
    assert "summary carried over must hold the value too" in said
    assert "nothing from this conversation" in said
    assert "transcript stays" in said


def test_each_rung_asks_for_the_clear_it_advertised():
    """A card offering a complete clear while asking for a seeded one would read correctly and do
    the wrong thing, and the person would have no way to tell."""
    seeded = _render({"type": "recall_offer", "scope": "summary"})
    complete = _render({"type": "recall_offer", "scope": "empty"})
    assert _buttons(seeded)[0]["act"] == "clear:summary"
    assert _buttons(complete)[0]["act"] == "clear:empty"
    assert _buttons(seeded)[0]["text"] == "Clear recall"
    assert _buttons(complete)[0]["text"] == "Clear recall completely"


def test_the_destructive_act_is_the_primary_and_the_only_primary():
    for scope in ("summary", "empty"):
        buttons = _buttons(_render({"type": "recall_offer", "scope": scope}))
        assert [b["kind"] for b in buttons] == ["primary", ""]


def test_declining_is_offered_but_only_dismisses():
    """`Not now` is local here. Declining is a judgment about a moment, not a preference about a
    want, so nothing is written down and the next refusal offers again."""
    buttons = _buttons(_render({"type": "recall_offer", "scope": "summary"}))
    assert buttons[1]["text"] == "Not now"
    assert buttons[1]["act"] == "dismiss"


def test_the_divider_says_which_clear_happened():
    """The transcript would otherwise lie about why the model forgot what is written above it."""
    seeded = _text(_render({"type": "recall_cleared", "scope": "summary"}))
    complete = _text(_render({"type": "recall_cleared", "scope": "empty"}))
    assert "with a summary of what was said above" in seeded
    assert "nothing from above" in complete
    assert seeded != complete


def test_the_divider_is_not_a_message_with_buttons():
    rendered = _render({"type": "recall_cleared", "scope": "summary"})
    assert _buttons(rendered) == []
    assert rendered["nodes"][0]["className"] == "sw-recall-cleared"
