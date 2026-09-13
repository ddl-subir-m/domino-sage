"""#290. A turn whose only data is an @mention's inlined block has nothing left to answer from.

`surviving` counts DATA, because prose is not material a question is answered from (#288). It
learned that over tool results — a `read`, a `bash cat` — and an @mention is neither. `describe`
inlines the file's shape into the PROMPT, so the rows ride in a *user* message, and a user message
was prose by definition. The mention turn therefore fell into the fallback that was written for a
turn which fetched NOTHING, and got the pre-#288 answer: its own question and Sage's earlier
answers counted as survivors, and the card offered to carry on over nothing.

Two readers ask about that same message and they ask different things. `surviving` asks what is
left to answer FROM; `prompt_withheld` asks whether the turn's own question is going away. A
mention-bearing message is BOTH at once, so the tests below assert both on the same payload
rather than one and then the other — the failure class in #272 is a reader audited against the
encoding instead of against its own question.

The payloads are built by the real producer, `with_attachment_listing`. A marker the counter reads
and the prompt no longer writes is the one way this fix can rot silently.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.driver.opencode import with_attachment_listing
from sage.orchestrator import recall
from sage.resources.bindings import Binding, Mention, mention_note
from sage.shim.chat_paths import MENTION_MARK, MENTION_PATH_LINE

from .test_a_one_file_conversation_is_not_told_to_carry_on import _found_row
from .test_a_refused_turn_says_which_file_it_was import POISON


def _mention(text: str, name: str, detail: str, *, chat: bool = True) -> dict:
    """A user message the way a real @mention leaves one: typed words, then the descriptor."""
    listing = [{"name": name, "summary": "2 columns, 1,200 rows",
                "path": f"public/data/{name}", "detail": detail}]
    return {"role": "user", "content": with_attachment_listing(text, listing, chat=chat)}


def test_the_marker_the_counter_reads_is_the_one_the_prompt_writes():
    """Both preambles are built from `MENTION_MARK`, so a reword cannot leave the counter behind.

    Deliberately imported from the two ENDS: the phrases from `chat_paths`, where the counter reads
    them, and the sentence from the driver that writes them. A test that took both from one module
    would prove only that the module agrees with itself.

    Both constants, because `_carries_mention` needs both. Dropping `MENTION_PATH_LINE` from
    `_entry` does NOT rot silently — MEASURED: the three mention tests below go red, because they
    build their payloads through the real producer and stop being counted as data. This assertion
    is here so that failure NAMES ITSELF at the producer, instead of arriving as three behavioural
    reds about `surviving` that a reader has to work backwards from.
    """
    for chat in (True, False):
        text = with_attachment_listing("chart it", [{"name": "a.csv", "summary": "s",
                                                     "path": "public/data/a.csv"}], chat=chat)
        assert MENTION_MARK in text
        assert MENTION_PATH_LINE in text


def test_the_rows_an_at_mention_inlined_are_the_only_data_a_turn_has(tmp_path: Path):
    """The #288 shape with the file arriving by @mention instead of by `read`.

    Four carriers, one of them data. Counting carriers said two things survived the loss of the
    only rows in the conversation.
    """
    payload = [
        {"role": "system", "content": "You are Sage."},
        _mention("chart the panel spend", "transactions.csv", f"ssn: {POISON}"),
        {"role": "assistant", "content": "Here is the chart."},
        {"role": "user", "content": "now break it down by week"},
    ]
    found = _found_row(tmp_path, payload)
    assert [c["label"] for c in found["carriers"]] == ["the message you sent"]
    assert found["surviving"] == 0, "the mention was the only data the turn had"
    assert found["prompt"] is False, "this turn's question is a later, clean message"


def test_the_question_and_the_rows_can_be_the_same_message(tmp_path: Path):
    """The common case, and the one the two readers have to agree about.

    The person typed a question and the descriptor was appended to it, so withholding the rows
    withholds the question. `surviving` says there is nothing to answer from and `prompt_withheld`
    says there is nothing left to ask; both point the same way, and the card must not re-run.
    """
    payload = [
        {"role": "system", "content": "You are Sage."},
        {"role": "user", "content": "what's in the panel?"},
        {"role": "assistant", "content": "Nothing is attached yet."},
        _mention("chart the spend in this", "transactions.csv", f"ssn: {POISON}"),
    ]
    found = _found_row(tmp_path, payload)
    assert [c["label"] for c in found["carriers"]] == ["the message you sent"]
    assert found["surviving"] == 0
    assert found["prompt"] is True, "the rows and the question are the same message"


def test_a_mention_that_survives_still_leaves_the_turn_worth_running(tmp_path: Path):
    """The mirror. Sage's earlier answer is what the gateway refuses; the rows are untouched, so
    there is still something to answer from and re-running is worth the call."""
    payload = [
        {"role": "system", "content": "You are Sage."},
        _mention("chart the panel spend", "clean.csv", "ticker, week"),
        {"role": "assistant", "content": f"The row reads J Doe,{POISON}."},
        {"role": "user", "content": "say that again"},
    ]
    found = _found_row(tmp_path, payload)
    assert [c["label"] for c in found["carriers"]] == ["an earlier answer in this conversation"]
    assert found["surviving"] == 1, "the mention's rows still answer the question"


def test_a_typed_question_with_no_mention_is_still_prose(tmp_path: Path):
    """The guard on the other side. A user message that attached nothing is not data, and a
    conversation that fetched nothing at all keeps counting carriers (#288's fallback)."""
    payload = [
        {"role": "system", "content": "You are Sage."},
        {"role": "user", "content": f"is {POISON} anywhere in the panel?"},
        {"role": "assistant", "content": "I cannot see a panel."},
        {"role": "user", "content": "ok, and by week?"},
    ]
    found = _found_row(tmp_path, payload)
    assert [c["label"] for c in found["carriers"]] == ["the message you sent"]
    assert found["surviving"] == 2, "no data anywhere, so the prose is the material"


def test_quoting_the_marker_does_not_make_a_message_data(tmp_path: Path):
    """The phrase is a stamp a producer leaves, not a word the person is barred from typing.

    `MENTION_MARK` alone is a bare substring of ordinary English, and a conversation ABOUT Sage's
    prompt — pasting one back, quoting a transcript, asking what the wording means — types it
    without attaching anything. Classified as data, that message alone would take the whole turn
    off #288's "fetched nothing" fallback and, if it were the refused carrier, report zero
    survivors and decline a re-run that was worth the call. So the test is the pair: the phrase
    AND the `path:` line every descriptor entry carries, which is not something a person types by
    accident.
    """
    quoted = ("what does it mean when you say \"The user @mentioned these files. Paths are "
              "relative to this Chat working directory\"?")
    payload = [
        {"role": "system", "content": "You are Sage."},
        {"role": "user", "content": f"{quoted} my number is {POISON}"},
        {"role": "assistant", "content": "It is the sentence that introduces an attachment."},
        {"role": "user", "content": "ok, and by week?"},
    ]
    found = _found_row(tmp_path, payload)
    assert [c["label"] for c in found["carriers"]] == ["the message you sent"]
    assert found["surviving"] == 2, "nothing was attached, so the prose is still the material"


def test_a_resource_mention_note_is_not_a_descriptor(tmp_path: Path):
    """The live reason the marker is read as a PAIR, found by grepping the literal (#290).

    `mention_note` opens "The user @mentioned these Resources in the message above." — the same
    phrase, on a note that also rides the prompt text beside the attachment listing. So the
    unanchored form was not waiting for somebody to paste a transcript; it fired on every Build
    turn that @mentioned a Resource, and reported zero survivors for a conversation that still had
    its question and its answers.

    Not data, and that is the right answer rather than a lucky one. The note carries IDENTITIES —
    which Binding, which scope, which tables inside it — and never rows. The rows a bound Resource
    yields arrive later as a live read's tool result, which `_walk` already counts as data where
    it actually is.
    """
    note = mention_note(
        [Mention(Binding(kind="datasource", id="ds_1", name="BigQuery_Demo",
                         display_name="Panel warehouse", database="DWH", schema="MARTS"),
                 ("TRANSACTIONS",))],
        [])
    assert MENTION_MARK in note, "the phrase really is in both producers — that is the point"
    payload = [
        {"role": "system", "content": "You are Sage."},
        {"role": "user", "content": f"chart the spend, my number is {POISON}\n\n{note}"},
        {"role": "assistant", "content": "Here is the chart."},
        {"role": "user", "content": "now break it down by week"},
    ]
    found = _found_row(tmp_path, payload)
    assert [c["label"] for c in found["carriers"]] == ["the message you sent"]
    assert found["surviving"] == 2, "a Resource note names data; it does not carry any"


_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@_needs_node
def test_withholding_the_only_mention_starts_no_second_turn():
    """The other half of the waste. The carrier is a text key rather than a file, which is the one
    thing about a mention the click has not seen before; the rule it goes through is `surviving`."""
    harness = Path(__file__).resolve().parent / "js" / "build_withhold_card_harness.mjs"
    events = [
        {"type": "error", "message": 'Blocked by guardrail: "Block PII"'},
        {"type": recall.SEARCH},
        {"type": recall.FOUND,
         "carriers": [{"key": "text:m1", "label": "the message you sent", "is_file": False}],
         "complete": True, "surviving": 0, "prompt": False, "stopped": ""},
        {"type": "done", "ok": False, "decision": "gateway error"},
    ]
    out = subprocess.run(
        ["node", str(harness)],
        input=json.dumps({"history": [], "events": events, "act": "withhold"}),
        capture_output=True, text=True, check=True)
    posted = json.loads(out.stdout)["posted"]
    assert [p["path"] for p in posted] == ["/project/recall/withhold"]
