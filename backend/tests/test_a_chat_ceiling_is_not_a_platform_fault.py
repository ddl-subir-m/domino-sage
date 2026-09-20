"""A turn stopped by Sage's own ten-minute cap must not buy a gateway listing (#454).

`readSSE` fires `store.refreshProblems()` once per failed stream, on ADR-0027's rule that a turn
which has just failed is the one moment worth paying a listing of models for — very often the
listing IS the answer. `NO_PLATFORM_FAULT` withdraws the flag for endings the platform had nothing
to do with: `no app described` (#150), `queries failed` (#203), `table generation failed` (#435).

`timeout` belongs in that list for the sharpest version of the same reason. The cap that fired is
one Sage set itself — `_CHAT_TURN_MAX_S`, 600 seconds — so a listing of models cannot say a word
about it, and the gateway it sends the person to look at was answering fine the whole ten minutes.

Driven through the real reducer rather than grepped out of the source, for the reason the two
harness tests beside this one give: whether an ending pays for a listing is a fact about the
SEQUENCE of frames, and the source says only that a list contains a string. The comment above
`endedBadly` records that exact drift — `queries failed` sat in the list for two releases without
working, and its test passed the whole time because it read store.js instead of running it.
"""
from __future__ import annotations

from .test_a_shape_only_table_artifact_renders_as_a_receipt import _node, needs_node

# The ceiling as the server sends it (#454): the block that says what happened, the `done` that
# carries the decision, and the card offering the way back in.
A_TURN_THAT_RAN_OUT_OF_TIME = [
    {"type": "user", "text": "which accounts are at risk, and why"},
    {"type": "error", "message": "This ran out of time before Sage had an answer. What it "
                                 "measured is written down in .sage/threads/thr_1/findings.md."},
    {"type": "done", "ok": False, "decision": "timeout"},
    {"type": "continue-offer", "prompt": "which accounts are at risk, and why",
     "threadId": "thr_1",
     "message": "Continue from what this turn measured — Sage reads "
                ".sage/threads/thr_1/findings.md first."},
]


@needs_node
def test_the_ceiling_does_not_buy_a_listing_of_models():
    result = _node("chat_stream_harness.mjs", A_TURN_THAT_RAN_OUT_OF_TIME)
    assert result["healthCalls"] == 0, (
        "the 600s cap is Sage's own; a listing of models cannot say anything about it")


@needs_node
def test_the_person_is_still_told_the_turn_ran_out_of_time():
    """The half that must not go quiet. Withdrawing the platform flag withdraws the flag and
    nothing else: the `error` frame still goes up and `done.ok` is untouched, so a fix that bought
    silence here would have traded one wrong answer for a worse one."""
    result = _node("chat_stream_harness.mjs", A_TURN_THAT_RAN_OUT_OF_TIME)
    said = " ".join(str(b.get("value", "")) for b in result["final"])
    assert "ran out of time" in said
    assert any(b.get("ok") is False for b in result["final"]), "and it is still shown as a failure"


@needs_node
def test_an_ending_the_platform_really_did_cause_still_buys_one():
    """The discriminator. Without it, a change that simply stopped calling `refreshProblems` at all
    would pass the first test — and ADR-0027's whole point is the moment it does ask."""
    result = _node("chat_stream_harness.mjs", [
        {"type": "user", "text": "which accounts are at risk, and why"},
        {"type": "error", "reason": "gateway error", "message": "The model refused the call."},
        {"type": "done", "ok": False, "decision": "gateway error"},
    ])
    assert result["healthCalls"] == 1, "a gateway failure is exactly what a listing is for"
