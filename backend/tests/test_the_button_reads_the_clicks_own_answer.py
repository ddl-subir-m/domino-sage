"""The card's button promises what the click does, because it asks the click.

`store.withholdRerunPrompt` decides whether pressing "Continue without…" re-runs the failed turn,
and `withholdContent` acts on the same call. The card derived that answer for itself once, from
`surviving` and `prompt`, and the copy was a clause short: the store also needs a QUESTION to
re-send, and `lastUserPrompt` returns `''` when the drawn transcript has none. Two of three
conditions, so a card that was right about both of them still promised a turn that never ran
(#311).

"None" means none ANYWHERE, not an empty last row. `lastUserPrompt` skips a user row whose text
block is empty and keeps walking backwards, so a transcript that holds an empty row over an older
question re-runs that older question — and the card promises it, because the card and the click are
now the same call. `test_an_empty_row_over_an_older_question_still_has_one` is the difference
between the rule and the sentence people reach for when they describe it.

That third condition is not reachable from the UI — `sendMessage` and `sendBuildPrompt` both refuse
text that trims to nothing, so no live transcript runs out of questions. It is constructed here
directly. An unreachable state is exactly what a second copy of a rule goes wrong about without
anything saying so, and this file is the pin that makes one copy worth having.

The two conditions the card was already right about are armed in
`test_the_card_for_a_blocked_file_reads.py` — `test_when_nothing_survives_the_button_does_not_promise_to_carry_on`
and `test_the_button_does_not_promise_to_carry_on_when_the_question_is_what_went`. Three clauses,
three tests, each red on its own.
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

# What the send paths can produce, and what the harness draws over unless a test says otherwise.
ASKED = [{"role": "user", "blocks": [{"type": "text", "value": "chart the weekly panel spend"}]}]
EMPTY_ROW = {"role": "user", "blocks": [{"type": "text", "value": ""}]}
# What they cannot: a transcript with no question anywhere in it. `lastUserPrompt` walks the whole
# of this and comes out with nothing, and the click returns without starting a turn.
UNASKED = [EMPTY_ROW]


def _button(surface: str = "chat", **spec) -> str:
    """The label on the card's primary button, off the rendered tree.

    Rendered rather than reasoned about. Reading the branch finds a missing `else`; only rendering
    both states finds an input the branch never receives, which is the shape of this defect — the
    card had no transcript in front of it at all.
    """
    block = {"type": "withhold", "searching": False, "carriers": [FILE], "complete": True,
             "surviving": 2, "prompt": False, "stopped": "", "surface": surface, "live": True}
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"block": block, **spec}),
                         capture_output=True, text=True, check=True)
    buttons = [n for n in json.loads(out.stdout)["nodes"] if n["tag"] == "Button"]
    assert buttons, "a label only means anything if the button is drawn at all"
    return buttons[0]["text"]


def test_a_transcript_with_no_question_left_to_re_send_promises_nothing():
    """The third clause. Everything else holds — two survivors, the question is not one of the
    things going — and the click still returns without running a turn, because there is nothing to
    re-send. A button reading "Continue without" over that is #297's exact defect one condition
    further out.
    """
    assert _button(messages=UNASKED) == "Stop sending this file"


def test_an_empty_row_over_an_older_question_still_has_one():
    """The rule is "no question anywhere", not "the last row is empty", and only a transcript
    holding both tells the two apart. `lastUserPrompt` skips the empty row and comes out with the
    older question, so the click re-runs that and the button may say so.

    Pinned because the sentence people reach for when they describe this condition is the wrong
    one, and the single-row transcript above passes under either reading. Whether reaching back
    past the refused turn is the right question to re-send is `lastUserPrompt`'s business; what
    this file is for is that the card and the click give the same answer either way.
    """
    assert _button(messages=[*ASKED, EMPTY_ROW]) == "Continue without this file"


def test_the_same_card_over_a_question_does_promise_to_carry_on():
    """The control the clause above is only a fact against. Same block, same two survivors, one
    non-empty user row — and byte-identical output at these two states would mean the card never
    received the transcript at all.
    """
    assert _button(messages=ASKED) == "Continue without this file"


def test_a_build_card_reads_the_build_transcript():
    """The surface split, which moved into the predicate with the rule. `state.messages` is not
    what Build draws, and the first version of the click named Chat's list on both surfaces: the
    door was called, the row was written, and the person watched a card that did not move (#297).

    Read on the card now too, so it can be wrong in the same way. Chat's transcript is empty here,
    so a card reading the wrong one refuses to promise a turn that will run.
    """
    assert _button("build", messages=[], buildMessages=ASKED) == "Continue without this file"
    assert _button("build", messages=ASKED, buildMessages=UNASKED) == "Stop sending this file"
