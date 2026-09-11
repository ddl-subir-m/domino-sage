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


# ---- which offer a transcript still draws --------------------------------------------------------
#
# The strings above are what the card says. These are about which card is still ON, which is
# `historyToMessages` rather than the component — and it was wrong in a way no rendering test could
# catch: `clearRecall` re-opens the Thread the moment it returns, and the offer the person had just
# used was drawn straight back at them.

_CHAT_HARNESS = Path(__file__).resolve().parent / "js" / "chat_recall_offer_harness.mjs"

_KEY = "guardrail:Block phone numbers"


def _drawn(history: list[dict], dismiss: object = None) -> dict:
    body = {"history": history}
    if dismiss is not None:
        body["dismiss"] = dismiss
    out = subprocess.run(["node", str(_CHAT_HARNESS)], input=json.dumps(body),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _refusal(order: int) -> list[dict]:
    return [
        {"type": "user", "text": "summarize the claims file", "order": order},
        {"type": "error", "reason": _KEY, "message": "Sage couldn't finish — refused.",
         "order": order + 1},
        {"type": "done", "ok": False, "decision": "step failed", "order": order + 2},
    ]


def test_the_offer_is_drawn_while_it_is_the_newest_one():
    drawn = _drawn(_refusal(0) + [{"type": "recall-suggest", "scope": "summary", "order": 3}])

    assert [o["scope"] for o in drawn["offers"]] == ["summary"]
    # Namespaced by surface, not by position alone. Under the split view the two transcripts number
    # their own rows, so an undecorated 3 would let one "Not now" hide Build's offer as well.
    assert drawn["offers"][0]["offerKey"] == "chat:3"


def test_a_clear_retires_the_offer_above_it():
    """`clearRecall` re-opens the Thread as its last act, so this ran one render after the click."""
    drawn = _drawn(_refusal(0)
                   + [{"type": "recall-suggest", "scope": "summary", "order": 3},
                      {"type": "recall-cleared", "scope": "summary", "order": 4}])

    assert drawn["offers"] == []
    assert drawn["cleared"] == ["summary"]


def test_a_refusal_after_a_clear_offers_again():
    """Retiring the used card must not close the ladder: the seed carried the value too, and the
    second rung is the one that clears completely."""
    drawn = _drawn(_refusal(0)
                   + [{"type": "recall-suggest", "scope": "summary", "order": 3},
                      {"type": "recall-cleared", "scope": "summary", "order": 4}]
                   + _refusal(5)
                   + [{"type": "recall-suggest", "scope": "empty", "order": 8}])

    assert [o["scope"] for o in drawn["offers"]] == ["empty"]


def test_not_now_survives_reopening_the_thread():
    """The comment on `dismissRecallOffer` has said "lasts until the next refusal re-offers" since
    it shipped. Filtering the drawn messages made it last until the next read instead."""
    history = _refusal(0) + [{"type": "recall-suggest", "scope": "summary", "order": 3}]

    assert _drawn(history, dismiss="chat:3")["offers"] == []


def test_dismissing_one_offer_does_not_dismiss_the_next():
    history = (_refusal(0)
               + [{"type": "recall-suggest", "scope": "summary", "order": 3}]
               + _refusal(4)
               + [{"type": "recall-suggest", "scope": "empty", "order": 7}])

    assert [o["scope"] for o in _drawn(history, dismiss="chat:3")["offers"]] == ["empty"]


# ---- the same card, on Build's side --------------------------------------------------------------
#
# Build's Recall is filed per (Conversation, app) and the clear behind this button empties a
# different session. The words follow, because the PROMISE differs: Chat's first clear carries a
# written summary into the fresh session (`recall.seed`), and Build has no such thing — the agent
# opens the app's own directory, so the files and the plan come back by being read.


def test_builds_first_rung_promises_what_build_actually_keeps():
    said = _text(_render({"type": "recall_offer", "scope": "summary", "surface": "build"}))

    assert "refused the same way twice" in said
    assert "your app, its plan and this transcript stay" in said
    assert "the agent reads them back" in said
    # The mechanism that is not on this side. Promising it would describe something that does not
    # happen, and would have both halves saying the same words for different reasons.
    assert "short summary of what was said" not in said


def test_builds_last_rung_says_the_new_session_was_refused_too():
    said = _text(_render({"type": "recall_offer", "scope": "empty", "surface": "build"}))

    assert "Starting over was not enough" in said
    assert "Your app, its plan and this transcript all stay" in said
    assert "The summary carried over" not in said


def test_chats_wording_is_untouched():
    """The Build branch must not have been bought by changing what Chat says."""
    first = _text(_render({"type": "recall_offer", "scope": "summary"}))
    complete = _text(_render({"type": "recall_offer", "scope": "empty"}))

    assert "the model keeps a short summary of what was said" in first
    assert "The summary carried over must hold the value too" in complete


def test_builds_buttons_reach_builds_clear_and_not_chats():
    """The defect the shared card could have shipped quietly: one component, two sessions, and a
    button that empties the wrong one leaves the refused Conversation exactly where it was."""
    seeded = _render({"type": "recall_offer", "scope": "summary", "surface": "build"})
    complete = _render({"type": "recall_offer", "scope": "empty", "surface": "build"})

    assert _buttons(seeded)[0]["act"] == "clear-build:summary"
    assert _buttons(complete)[0]["act"] == "clear-build:empty"
    assert _buttons(seeded)[1]["act"] == "dismiss-build"
    # And Chat's card still reaches Chat's.
    assert _buttons(_render({"type": "recall_offer", "scope": "summary"}))[0]["act"] == "clear:summary"


def test_both_sides_keep_the_same_labels():
    """The words on the buttons are the rung the person is on, and the rung is the same on both
    sides. Only the session behind it differs."""
    for surface in (None, "build"):
        block = {"type": "recall_offer", "scope": "summary"}
        if surface:
            block["surface"] = surface
        assert [b["text"] for b in _buttons(_render(block))] == ["Clear recall", "Not now"]
