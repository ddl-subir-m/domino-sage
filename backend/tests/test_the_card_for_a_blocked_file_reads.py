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
#
# `is_fetched` is what the server actually sends beside it (#312), and it belongs in the fixture
# even though nothing in this file asserts on the clause it gates. Without it the row models a
# `bash cat` the way the server stopped sending it, and the next test written over `TOOL` would
# render a card missing an offer and read as though that were the answer.
TOOL = {"key": "text:def456", "label": "something a tool read", "is_file": False,
        "is_fetched": True}


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


def test_a_pasted_message_is_promised_it_will_not_be_altered():
    """The line that separates this from redaction, which ADR-0022 forbids outright.

    It says nothing about who wrote the thing. It used to say "what you wrote", and the same arm
    catches a `bash cat`'s rows, which the person did not write (#297). The label names the author
    one clause earlier, so the promise does not have to.
    """
    r = _render(_card(carriers=[TEXT], surviving=0))
    said = _text(r)
    # The whole clause, not "not in a file" — that is a substring of the qualified form, so it
    # would pass over the bare claim a `bash cat`'s rows were never in a file at all (#312).
    assert "not in a file Sage can name. Sage won't change what it matched" in said
    assert _buttons(r)[0]["text"] == "Stop sending it"


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


def _button_label(carriers: list[dict], surviving: int, **over) -> str:
    buttons = _buttons(_render(_card(carriers=carriers, surviving=surviving, **over)))
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

    The card cannot tell the three apart on its own, and `Carrier.is_data` is not the field that
    would let it: since #290 that field means "data, wherever it came from", so an @mention sets it
    on the message a person typed (#297).
    """
    assert _button_label([TOOL, OLDER], 0) == "Stop sending them"
    assert _button_label([TOOL, FILE], 2) == "Continue without them"


def test_a_lone_tool_read_is_not_credited_to_the_person_and_reaches_both_arms():
    """#297. A `bash cat` or a `grep` leaves one carrier with no path, so no `is_file`, and the
    arm that caught it said "Sage won't change what you wrote" over rows the person never wrote,
    called them a message on the button, and drew the same bytes whether anything survived or not.

    Both arms in one test, and the button read through `_button_label`, which fails if no button
    is drawn at all: three tests each pinning one string would all pass over an empty card (#292).
    """
    gone = _text(_render(_card(carriers=[TOOL], surviving=0)))
    left = _text(_render(_card(carriers=[TOOL], surviving=2)))
    assert gone != left, "the two arms rendered byte-identical cards before this"

    for said in (gone, left):
        assert "wrote" not in said
        assert "message" not in said
        # The promise this arm exists to make. It outlives the author it used to name, because
        # withholding is not redaction whoever wrote the thing (ADR-0022).
        #
        # "Sage can name" is not padding. A `bash cat transactions.csv` DID read a file and lands
        # here only because no path came with it, so the bare "not in a file" was a second false
        # claim about this exact carrier, in the same sentence as the first one. Asserting the
        # bare form would pin it — the shape where a test reads as verification of a decision
        # nobody checked.
        assert "not in a file Sage can name. Sage won't change what it matched" in said

    assert _button_label([TOOL], 0) == "Stop sending it"
    assert _button_label([TOOL], 2) == "Continue without it"

    # One act, one name, in the arm the older agreement test never rendered. Folding this arm into
    # the shared ending left the promise clause still offering to "stop sending" while the button
    # said "Continue without", which is the disagreement the note under the button forbids.
    assert "stop sending" not in left.lower(), left


def test_the_button_does_not_promise_to_carry_on_when_the_question_is_what_went():
    """The case the store's own comment calls the one a person meets most, and the one the card
    could not draw until the arms were folded together (#297).

    `store.withholdContent` re-runs the turn on `surviving > 0 && !prompt`. The card was reading
    `surviving` alone, so a matched question with every file surviving drew "Continue without it",
    the click withheld and stopped, and the receipt underneath told the person to ask again in
    different words. Rendered rather than reasoned about: this is the same field pair read from two
    places, and the two places disagreed.
    """
    assert _button_label([TEXT], 2, prompt=True) == "Stop sending it"
    assert _button_label([TEXT], 2, prompt=False) == "Continue without it"
    # Reaches every set that can arrive this way, not only the one #297 opened it on: the button
    # is one rule. No all-file row here — `withhold.prompt_withheld` returns `text_key(last user
    # message) in {withheld keys}`, so a set with no text carrier never arrives with `prompt`
    # set, and a row pinning one would read as coverage while standing for nothing.
    assert _button_label([FILE, TEXT], 2, prompt=True) == "Stop sending them"

    # And the button is given a reason to press it. #288's rule is about the button, not about
    # `surviving`: folding the arms together left this card saying what would NOT happen and
    # nothing about what the click buys, over the payload the store calls the commonest one.
    said = _text(_render(_card(carriers=[TEXT], surviving=2, prompt=True)))
    assert "asking it again in the same words would be stopped" in said
    assert "this conversation will work again" in said
    # Not the line for a turn with nothing left: the files here survive, and saying they do not
    # would send someone re-reading work that was never lost.
    assert "That was everything this turn read" not in said
    # And the survivors are still named. A branch picking the question dropped the reassurance
    # that the rest of the turn's reading is safe, on this same payload.
    assert "Nothing else this turn read is affected" in said

    # Both facts when both hold. They co-occur — a one-file conversation whose file AND typed
    # question both matched has nothing left to answer from and loses its question — and a branch
    # picking one of them says only the first. The person then met "Ask again in different words"
    # on the receipt, over a card that had not mentioned their question at all.
    both = _text(_render(_card(carriers=[FILE, TEXT], surviving=0, prompt=True)))
    assert "That was everything this turn read" in both
    assert "asking it again in the same words would be stopped" in both
    # Nothing survives here, so the reassurance above must NOT appear — the two payloads differ
    # in which facts are true, not in how many sentences they get.
    assert "Nothing else this turn read is affected" not in both


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


def test_the_promise_not_to_alter_anything_holds_at_every_size_of_a_set_with_no_file():
    """ADR-0022's one hard promise: Sage stops sending material and never changes what a person
    wrote. The clause carrying it hung off a gate that also COUNTED the carriers, so two matched
    messages drew the destructive-sounding button with the promise nowhere on the card (#309).

    Both sizes go through one render, asserting the SAME string. Two tests each pinning one size
    would both still pass with the promise dropped from the other — which is the width #297 left
    open and the reason it stayed open. The em dash is part of the assertion, not punctuation
    taste: the list above it is comma-joined, so a comma here reads as one more thing the policy
    matched rather than the break before the promise, and that misreading is only reachable once
    the set is allowed to be plural.
    """
    promise = " — not in a file Sage can name. Sage won't change what it matched"
    for carriers in ([TEXT], [TEXT, OLDER], [TOOL, TEXT, OLDER]):
        said = _text(_render(_card(carriers=carriers, surviving=0)))
        assert promise in said, carriers

    # An ALL-file set bounds the widening: every carrier there is nameable, so the clause's first
    # half is false of every one of them and its absence is the thing to hold.
    #
    # A MIXED set is deliberately not a member. The clause is false of the file and the promise is
    # true of the message, so asserting the whole clause absent would pin the promise's absence as
    # correct on the one payload where the person's own words are at stake — and the receipt for
    # that same click says "Nothing was changed or deleted" whatever was withheld. That gap is
    # #337, filed rather than fixed: separating the promise from the file clause rewrites prose
    # #292 landed, which this ticket is barred from re-deriving. Left unpinned so #337 has nothing
    # green to overturn.
    for carriers in ([FILE], [FILE, EXPORT]):
        said = _text(_render(_card(carriers=carriers, surviving=0)))
        assert "not in a file Sage can name" not in said, carriers
        assert "Sage won't change" not in said, carriers
