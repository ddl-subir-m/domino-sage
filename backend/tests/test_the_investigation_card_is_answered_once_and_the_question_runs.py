"""What the click does, on both buttons (#386, ADR-0056).

The card ends the turn, so the answer to it has to run the question — otherwise the person pays a
round trip for a card they did not ask for, types their question again, and the offer was a tax.
`chooseTableAndAsk` already settles the shape: record the decision, then re-ask the original prompt
with the echo off, because the sentence is already in the transcript above the card.

The two buttons differ in one thing only, and it is the thing the whole ticket is about: `Yes` runs
the question with a shell, `No` runs exactly the turn that would have run anyway.
"""
from __future__ import annotations

from pathlib import Path

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, ObservedControlOpenCode, _orch

ASK = "Which customers actively use Model Monitor, based on Mixpanel, Gong and Salesforce?"


def _orch_with_a_store(tmp_path: Path, label: str = "data_answer"):
    turns = [Turn(text="Answered.")]
    orch, oc = _orch(
        tmp_path, turns,
        gateway=IntentGateway({"label": label, "confidence": 0.93}),
        client=lambda ws: ObservedControlOpenCode(ws, list(turns)),
    )
    oc.control = orch.project(start_preview=False).control
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1",
                                  "name": "Snowflake-Data-Warehouse"})
    return orch, oc, tid


def test_accepting_records_the_grant_and_the_replayed_question_runs_unbounded(tmp_path: Path):
    orch, oc, tid = _orch_with_a_store(tmp_path)
    list(orch.chat_stream(tid, ASK))
    assert oc.prompts == []

    answer = orch.decide_thread_investigation(tid, "open")
    assert answer["investigation"]["state"] == "open"
    assert answer["investigation"]["openedAt"]
    assert orch.thread_context(tid)["investigation"]["state"] == "open"

    list(orch.chat_stream(tid, ASK, skip_investigation_gate=True))

    assert oc.snapshots
    assert not oc.snapshots[0].read_only_turn
    # Once in the transcript, not twice. The turn that drew the card wrote the question down.
    rows = orch.thread_history(tid)
    assert len([r for r in rows if r.get("type") == "user" and r.get("text") == ASK]) == 1
    # And the conversation says what happened, which is the review's sharpest objection answered:
    # nothing used to tell the person their Thread was now unbounded.
    assert [r["state"] for r in rows if r.get("type") == "investigation-state"] == ["open"]


def test_declining_is_recorded_the_question_still_runs_and_the_card_does_not_come_back(
    tmp_path: Path,
):
    orch, oc, tid = _orch_with_a_store(tmp_path)
    list(orch.chat_stream(tid, ASK))

    assert orch.decide_thread_investigation(tid, "decline")["investigation"]["state"] == "declined"

    list(orch.chat_stream(tid, ASK, skip_investigation_gate=True))

    assert oc.snapshots
    assert oc.snapshots[0].read_only_turn, "declining answers the question as it would have"
    assert oc.snapshots[0].read_only_reason == "question"

    # A second investigative question in the same conversation meets no card. An offer that came
    # back would be the same question asked until it got the answer it wanted.
    # Appended, not replaced: the fake's cursor is past the first script, and a turn it leaves
    # unanswered is asked again (#557 P3), which this counts.
    oc.turns.append(Turn(text="Answered again."))
    events = list(orch.chat_stream(tid, "Investigate which accounts look like adopters."))
    assert not any(e.get("type") == "investigation-offer" for e in events)
    assert len(oc.prompts) == 2


def test_the_decision_survives_a_chip_being_added_and_removed_beside_it(tmp_path: Path):
    """One record holds the chips and the decision, and every writer of it writes the whole row.

    A writer that rebuilt the row from `items` alone would drop the flag on the next chip — an open
    investigation closed by a click about something else, with nothing on screen saying so.
    """
    orch, _, tid = _orch_with_a_store(tmp_path)
    orch.decide_thread_investigation(tid, "open")

    orch.add_thread_context(tid, {"kind": "file", "name": "desk.csv",
                                  "path": ".sage/scratch/desk.csv"})
    assert orch.thread_context(tid)["investigation"]["state"] == "open"

    row = next(i for i in orch.thread_context(tid)["items"] if i.get("kind") == "file")
    orch.remove_thread_context(tid, row["id"])
    assert orch.thread_context(tid)["investigation"]["state"] == "open"


def test_the_decision_survives_a_table_being_picked_beside_it(tmp_path: Path):
    """The writer with the sharpest reach: `confirm_thread_table_candidate` reads the chips, edits
    one and writes the record back. It is the door that runs most often in an investigating Thread,
    because picking a table is what a question about a warehouse starts with."""
    from .test_a_chat_build_request_is_asked_which_table import _gong_warehouse, _thread_with_source
    from .test_a_chat_build_request_is_asked_which_table import _orch as _table_orch

    orch, _oc = _table_orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)
    orch.decide_thread_investigation(tid, "open")

    orch.confirm_thread_table_candidate(tid, "ds-dwh", "DWH", "MARTS", "GONG__CALLS")

    context = orch.thread_context(tid)
    assert context["investigation"]["state"] == "open"
    row = next(i for i in context["items"] if i.get("kind") == "data_source")
    assert row["scope"]["table"] == "GONG__CALLS"
