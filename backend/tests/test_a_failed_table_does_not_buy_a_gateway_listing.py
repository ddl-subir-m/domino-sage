"""#435: a turn that answered and left one bad table raised a platform alarm about the gateway.

`readSSE` fires `store.refreshProblems()` once per failed stream, on ADR-0027's rule that a turn
which has just failed is the one moment worth paying a gateway listing for. `NO_PLATFORM_FAULT`
withdraws that flag for endings the platform had nothing to do with — it already held
`no app described` (#150) and `queries failed` (#203), both because the turn ran, the model
answered, and a listing of models can say nothing about what went wrong.

A table that failed validation is the same category, and was missing from the list. So the #435
turn — correct card, correct number, one malformed `.table.json` — spent a `/health` read and could
raise "1 problem needs attention" at a person whose question had just been answered correctly.

Driven through the real reducer rather than grepped out of the source, for the reason the Build
harness gives for counting the same thing: whether an ending pays for a listing is a fact about the
SEQUENCE of frames, and the source says only what the list contains.
"""
from __future__ import annotations

from .test_a_shape_only_table_artifact_renders_as_a_receipt import _node, needs_node

# The #435 shape, in the order the server sends it: the answer, the card, the table failure, and a
# `done` that carries the verdict. The `error` frame is what makes `endedBadly` true by frame type,
# which is why `ok` alone could never have settled this — the flag is already up when `done` lands.
ANSWERED_BUT_A_TABLE_FAILED = [
    {"type": "user", "text": "count the distinct users"},
    {"type": "agent", "kind": "text", "text": "There are 1,373,861 distinct users."},
    {"type": "artifacts", "items": [
        {"kind": "table", "path": "examples/t1/distinct-users.table.json"}]},
    {"type": "error", "reason": "table generation failed",
     "message": "I could not generate the table: sample."},
    {"type": "done", "ok": False, "decision": "table generation failed"},
]


@needs_node
def test_a_turn_that_answered_does_not_raise_a_platform_alarm():
    result = _node("chat_stream_harness.mjs", ANSWERED_BUT_A_TABLE_FAILED)

    assert result["healthCalls"] == 0, (
        "a malformed table is not a fault of the platform, and a listing of models cannot say "
        "anything about it")


@needs_node
def test_the_person_is_still_told_which_table_failed():
    """The half that must NOT go quiet. #436 is a turn that reported success over a refusal, and
    the fix for this one is not allowed to buy silence anywhere near it."""
    result = _node("chat_stream_harness.mjs", ANSWERED_BUT_A_TABLE_FAILED)

    said = " ".join(str(b.get("value", "")) for b in result["final"])
    assert "I could not generate the table: sample." in said
    assert any(b.get("ok") is False for b in result["final"]), "and it is still shown as a failure"


@needs_node
def test_an_ending_the_platform_really_did_cause_still_buys_one():
    """The discriminator. Without this, a fix that simply stopped calling `refreshProblems` at all
    would pass the test above — and ADR-0027's whole point is that this is the moment to ask."""
    result = _node("chat_stream_harness.mjs", [
        {"type": "user", "text": "count the distinct users"},
        {"type": "error", "reason": "gateway error", "message": "The model refused the call."},
        {"type": "done", "ok": False, "decision": "gateway error"},
    ])

    assert result["healthCalls"] == 1, "a gateway failure is exactly what the listing is for"


@needs_node
def test_an_empty_answer_is_not_reported_as_a_gateway_fault():
    result = _node("chat_stream_harness.mjs", [
        {"type": "user", "text": "summarize the file"},
        {"type": "error", "reason": "empty answer", "message": "No answer was produced."},
        {"type": "done", "ok": False, "decision": "empty answer", "advanced": False},
    ])
    assert result["healthCalls"] == 0
    assert any(b.get("ok") is False for b in result["final"])


@needs_node
def test_a_stale_question_is_not_reported_as_a_gateway_fault():
    result = _node("chat_stream_harness.mjs", [
        {"type": "user", "text": "summarize the file"},
        {"type": "error", "message": "That question has changed. Use the current question."},
        {"type": "done", "ok": False, "decision": "stale question"},
    ])
    assert result["healthCalls"] == 0


@needs_node
def test_table_failure_replaces_provisional_success_in_the_live_view():
    result = _node("chat_stream_harness.mjs", [
        {"type": "user", "text": "summarize the file"},
        {"type": "delta", "text": "Saved all 36 customers successfully.", "final": True},
        {"type": "agent", "kind": "text", "text": ""},
        {"type": "artifacts", "items": [{"kind": "chart", "path": "examples/t1/trend.png"}]},
        {"type": "error", "reason": "table generation failed",
         "message": "The chart is ready, but I could not generate the table: customers."},
        {"type": "done", "ok": False, "decision": "table generation failed"},
    ])
    said = " ".join(str(b.get("value", "")) for b in result["final"])
    assert "36 customers" not in said
    assert "could not generate the table: customers" in said
    assert any(b.get("type") == "image" and b.get("path") == "examples/t1/trend.png"
               for b in result["final"])
