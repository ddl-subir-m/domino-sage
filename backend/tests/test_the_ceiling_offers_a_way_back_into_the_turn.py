"""Continue, as the Thread draws it and as the click actually behaves (#454).

The server writes a `continue-offer` row when the reserved slice left measurements behind. Both
ends of getting that row onto the screen are silent when they break: `historyToMessages` is a chain
of `ev.type === ...` branches and a row with no branch simply disappears, and a card that renders
without its button is a sentence about work nobody can reach. Ten minutes are behind this one
click, so neither failure is cheap.

The click is an ORDINARY turn: the original question, on the same Thread, on a full clock, with no
grant. It reads what the last turn measured because `_findings_note` names this Thread's
`findings.md` into every prompt that has one — so nothing is attached to the question here, and a
test that found something attached would be reading a second mechanism nobody decided to build.
"""
from __future__ import annotations

from .test_a_shape_only_table_artifact_renders_as_a_receipt import _node, needs_node

QUESTION = "which accounts are at risk, and why"
FINDINGS = ".sage/threads/thr_1/findings.md"

A_CEILING_THAT_KEPT_SOMETHING = [
    {"type": "user", "text": QUESTION},
    {"type": "error", "message": f"This ran out of time before Sage had an answer. What it "
                                 f"measured is written down in {FINDINGS}."},
    {"type": "done", "ok": False, "decision": "timeout"},
    {"type": "continue-offer", "prompt": QUESTION, "threadId": "thr_1",
     "message": f"Continue from what this turn measured — Sage reads {FINDINGS} first, so the "
                "next turn starts where this one stopped."},
]


def _run(history: list[dict]) -> dict:
    return _node("chat_continue_offer_harness.mjs", {"history": history})


@needs_node
def test_the_ceilings_card_survives_the_reload():
    """The row has to reach the Thread at all. Without a branch for its type it is dropped on the
    floor, and the only thing on screen is the sentence saying the turn ran out of time."""
    result = _run(A_CEILING_THAT_KEPT_SOMETHING)

    assert len(result["cards"]) == 1
    card = result["cards"][0]
    assert card["prompt"] == QUESTION
    assert card["threadId"] == "thr_1"
    assert FINDINGS in card["message"]


@needs_node
def test_a_replayed_ceiling_is_a_record_and_not_a_button():
    """`live` is minted where the frame arrives and nowhere else. A transcript opened a week later
    must not be able to start ten minutes of work on a page load nobody connected to it."""
    result = _run(A_CEILING_THAT_KEPT_SOMETHING)

    assert result["cards"][0]["live"] is False
    assert result["replayedButtons"] == 0


@needs_node
def test_the_card_offers_one_act_and_it_is_continue():
    """One primary act, and no decline beside it. A ceiling records no decision either way, so
    "leave it then" is a click that does nothing the person is not already doing."""
    result = _run(A_CEILING_THAT_KEPT_SOMETHING)

    assert result["buttons"] == ["Continue"]


@needs_node
def test_pressing_continue_re_reads_the_thread_before_it_posts():
    """The failure every card beside this one is commented against: `sendMessage` reads
    `state.thread`, so posting without re-reading lands the question in whichever conversation the
    person moved to after the ceiling drew this — with `echo` off, where they would never see it."""
    result = _run(A_CEILING_THAT_KEPT_SOMETHING)

    routes = result["routes"]
    assert "api/threads/thr_1" in routes
    assert routes.index("api/threads/thr_1") < next(
        i for i, r in enumerate(routes) if "chat/stream" in r)


@needs_node
def test_continue_sends_the_original_question_and_nothing_else():
    """An ordinary turn. The findings reach it through the prompt the server renders, so a client
    that attached them here would be a second mechanism for the same job — and the one that can
    drift, because it cannot see whether the file is still there."""
    result = _run(A_CEILING_THAT_KEPT_SOMETHING)

    assert result["replay"]["prompt"] == QUESTION
    # The server's own copy of "do not write this down again". `echo: false` suppresses only this
    # tab's bubble; without this flag `asking` at `service.py:11692` appends a second `user` row
    # and the reload reads question, ceiling, card, question. Every sibling card pairs the two.
    assert result["replay"]["alreadyAsked"] is True
    # No gate is walked past and no capability is spent. `otherLaneGrant` is `sendMessage`'s own
    # default and is empty here, which is the claim: Continue mints nothing and redeems nothing.
    assert result["replay"]["otherLaneGrant"] == ""
    assert result["replay"]["investigationAnswered"] is False
    assert result["replay"]["skipTableGate"] is False
    assert result["replay"]["skipDatasetGate"] is False
    # The question is already on screen directly above this card. Echoing it would read as a
    # second question having been asked rather than the first one being picked back up.
    assert result["asked"] == [QUESTION]
