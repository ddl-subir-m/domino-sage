"""What a person actually reads when a guardrail blocks their turn.

Source assertions cannot see copy, and copy is where this feature succeeds or fails. The distinction
these tests exist to hold: Sage STOPS SENDING content, it never alters it. A card that read as
"Sage removed the values from your file" would describe a thing Sage refuses to do, and ADR-0022
says so in as many words.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "withhold_card_harness.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

FILE = {"key": "file:raw.csv", "label": "card_panel_transactions_RAW.csv", "is_file": True}
TEXT = {"key": "text:abc123", "label": "the message you sent", "is_file": False}
EXPORT = {"key": "file:export.csv", "label": "export.csv", "is_file": True}
OLDER = {"key": "text:xyz789", "label": "an earlier answer in this conversation",
         "is_file": False}
# A turn that fetched rows through `bash cat` or `grep` leaves a carrier with no path, so no
# `is_file` — and it is not a message either. The label population that rules out "messages"
# as the word for a set holding no file.
TOOL = {"key": "text:def456", "label": "something a tool read", "is_file": False}


def _render(block: dict) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"block": block}),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _card(**over) -> dict:
    block = {"type": "withhold", "searching": False, "carriers": [FILE], "complete": True,
             "surviving": 2, "stopped": "", "surface": "chat", "live": True}
    block.update(over)
    return block


def _text(r: dict) -> str:
    return " ".join(n["text"] for n in r["nodes"] if n["text"])


def _buttons(r: dict) -> list[dict]:
    return [n for n in r["nodes"] if n["tag"] == "Button"]


def test_while_searching_it_spins_and_offers_nothing():
    r = _render(_card(searching=True))
    assert [n["tag"] for n in r["nodes"] if n["tag"] == "Spin"] == ["Spin"]
    assert _buttons(r) == [], "nothing to offer until the search has an answer"
    assert "Finding" in _text(r)


def test_a_found_file_is_named_and_offered():
    r = _render(_card())
    said = _text(r)
    assert "card_panel_transactions_RAW.csv" in said
    assert "Nothing else this turn read is affected." in said
    buttons = _buttons(r)
    assert [b["kind"] for b in buttons] == ["primary", "text"], "exactly one primary action"
    assert buttons[0]["text"] == "Continue without this file"
    assert buttons[0]["act"] == "withhold:chat:file:raw.csv"
    assert buttons[1]["act"] == "dismiss"


def test_when_nothing_survives_the_button_does_not_promise_to_carry_on():
    r = _render(_card(surviving=0))
    said = _text(r)
    assert "That was everything this turn read" in said
    assert "can't be answered from what's left" in said
    assert _buttons(r)[0]["text"] == "Stop sending this file"


def test_a_pasted_message_says_sage_will_not_change_what_you_wrote():
    """The line that separates this from redaction, which ADR-0022 forbids outright."""
    r = _render(_card(carriers=[TEXT], surviving=0))
    said = _text(r)
    assert "not in a file" in said
    assert "won't change what you wrote" in said
    assert _buttons(r)[0]["text"] == "Stop sending that message"


def test_it_says_how_long_the_withhold_lasts():
    """There is no undo, so the reset has to be discoverable from the card itself."""
    assert "A new conversation starts fresh." in _text(_render(_card()))


def test_an_incomplete_search_does_not_offer_a_fix_it_cannot_deliver():
    """Withholding these would not clear the refusal, so offering it would promise a fix the next
    turn disproves. The clear-Recall offer renders underneath and is the honest next rung."""
    r = _render(_card(complete=False))
    assert "something else as well" in _text(r)
    assert _buttons(r) == []


def test_finding_nothing_says_so_rather_than_going_quiet():
    r = _render(_card(carriers=[], complete=False, stopped="not in this conversation's content"))
    assert "isn't in anything this conversation can stop sending" in _text(r)
    assert _buttons(r) == []


def test_a_replayed_card_keeps_the_sentence_and_loses_the_buttons():
    """The house rule for every card that can run a turn: a row read back off the transcript must
    not run one out of a message somebody is only scrolling back through."""
    r = _render(_card(live=False))
    assert "card_panel_transactions_RAW.csv" in _text(r)
    assert _buttons(r) == []


def test_the_build_card_writes_through_the_build_door():
    """Same component, same copy — the surface only decides which door the click reaches. Sending a
    Build withhold to the Chat door would write it to the wrong transcript and silently do nothing."""
    chat, build = _render(_card()), _render(_card(surface="build"))
    assert _text(chat) == _text(build)
    assert _buttons(build)[0]["act"] == "withhold:build:file:raw.csv"


def test_several_carriers_are_all_named():
    r = _render(_card(carriers=[FILE, {"key": "file:export.csv", "label": "export.csv",
                                       "is_file": True}]))
    said = _text(r)
    assert "card_panel_transactions_RAW.csv" in said and "export.csv" in said
    assert _buttons(r)[0]["act"] == "withhold:chat:file:raw.csv,file:export.csv"


def _button_label(carriers: list[dict], surviving: int) -> str:
    buttons = _buttons(_render(_card(carriers=carriers, surviving=surviving)))
    assert buttons, "a label only means anything if the button is drawn at all"
    return buttons[0]["text"]


def test_the_button_takes_its_noun_from_the_carriers_not_from_how_many():
    """#292. The label counted carriers and called whatever it found "files", so a set holding no
    file at all read "Stop sending these files" — and "Continue without these files" in the other
    arm, which counts the same way.

    All three sets through the one button, in both arms, in one test. Three tests each asserting a
    single label would all pass with the button never drawn, and that is how this breaks.
    """
    files, texts, mixed = [FILE, EXPORT], [TEXT, OLDER], [FILE, TEXT]

    assert _button_label(files, 0) == "Stop sending these files"
    assert _button_label(texts, 0) == "Stop sending them"
    assert _button_label(mixed, 0) == "Stop sending them"

    assert _button_label(files, 2) == "Continue without these files"
    assert _button_label(texts, 2) == "Continue without them"
    assert _button_label(mixed, 2) == "Continue without them"

    for carriers in (texts, mixed):
        for surviving in (0, 2):
            assert "file" not in _button_label(carriers, surviving)


def test_a_tool_s_rows_are_not_messages_either():
    """Why the word is the bare "them" and not "these messages". `withhold.py`'s `_text_label`
    names a non-file carrier three ways, and "something a tool read" is neither a file nor anything
    a person would call a message. Naming this set "messages" would be #292 one word over.

    The card cannot tell the two apart on its own: the row carries `is_file`, and the distinction
    lives in `Carrier.is_data`, which is not sent to it.
    """
    assert _button_label([TOOL, OLDER], 0) == "Stop sending them"
    assert _button_label([TOOL, FILE], 2) == "Continue without them"


def test_the_button_and_the_sentence_above_it_never_disagree():
    """The note above the pronoun asks the two to count the same way. On a plural set they now go
    further and match word for word, so nobody is handed a second name for one set of things.

    The singular pair agrees on number only — prose "it", button "this file" — and that is the
    deliberate half: a lone carrier is worth naming, and the plural set has its names listed
    immediately above the button instead.
    """
    said = _text(_render(_card(carriers=[FILE, TEXT], surviving=0)))
    assert "Stop sending them and this conversation will work again" in said
    assert _button_label([FILE, TEXT], 0) == "Stop sending them"

    # And the singular arm, which nobody looks at: the prose drops to "it", so the button has to
    # drop with it rather than keep a plural the sentence has already let go of.
    one = _text(_render(_card(carriers=[FILE], surviving=0)))
    assert "Stop sending it and this conversation will work again" in one
    assert _button_label([FILE], 0) == "Stop sending this file"


def _receipt(**over) -> dict:
    block = {"type": "recall_withheld", "labels": ["card_panel_transactions_RAW.csv"],
             "surface": "chat"}
    block.update(over)
    return _render(block)


def test_the_receipt_names_what_stopped_and_swears_nothing_was_altered():
    """The sentence that keeps this on the right side of ADR-0022. A person who reads "Sage removed
    the values from your file" will go looking for a file that has been edited, and none has been —
    Sage withheld it, and withholding and redacting are not the same act."""
    said = _text(_receipt())
    assert "card_panel_transactions_RAW.csv" in said
    assert "Nothing was changed or deleted" in said
    assert _buttons(_receipt()) == [], "a receipt records; it does not offer"


def test_the_receipt_says_how_far_the_withhold_reaches():
    """Same scope the card promised. There is no undo, so it has to keep saying where the reset is."""
    assert "this conversation" in _text(_receipt())


def test_several_withheld_things_read_as_plural():
    said = _text(_receipt(labels=["raw.csv", "export.csv"]))
    assert "raw.csv" in said and "export.csv" in said
    assert "them" in said


def test_the_receipt_says_to_ask_again_in_different_words():
    """The non-obvious thing, and the only place a person can be told it. Retyping the question is
    the obvious move after a refusal, and it is the one that silently fails: the same words hash to
    the same key, so they are stopped before they are sent and nothing on screen says why.

    Only when their own question was the thing withheld. On a withheld FILE the words are fine and
    this would send a person rewriting a question that was never the problem.
    """
    said = _text(_receipt(prompt=True, labels=["the message you sent"]))
    assert "different words" in said, "a person who retypes the same question hits the same wall"
    assert "Nothing was changed or deleted" in said, "still a receipt, still bound by ADR-0022"


def test_a_withheld_file_is_not_a_reason_to_rewrite_the_question():
    assert "different words" not in _text(_receipt())
