"""Closing is not deleting (#386, ADR-0056).

Until this ticket the two were one act, because the state WAS the file: the only way to stop a
Thread being unbounded was to remove `findings.md`, and that threw away every measurement with it.
They are different things. The grant is a capability the person gave and can take back; the
findings are the work, and nobody asked to lose the work.

So closing clears the flag, puts the bounding back on the very next turn, and leaves the file
exactly where it is. The one door that takes both is a complete Recall clear, which is the person
saying start over and meaning it (ADR-0055) — and it has to take the grant too, or a conversation
that started over still has a shell for a reason nothing on the record explains.
"""
from __future__ import annotations

from pathlib import Path

from sage.orchestrator import recall
from sage.workspace.threads import findings_file

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, ObservedControlOpenCode, _orch

MEASURED = "- 2026-09-16T11:04Z — SFDC_CONTACT_ID populated 16,756/89,399 (18.7%)\n"


def _open_one(tmp_path: Path):
    turns = [Turn(text="Answered.")]
    orch, oc = _orch(
        tmp_path, turns,
        gateway=IntentGateway({"label": "data_answer", "confidence": 0.93}),
        client=lambda ws: ObservedControlOpenCode(ws, list(turns)),
    )
    project = orch.project(start_preview=False)
    oc.control = project.control
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1",
                                  "name": "Snowflake-Data-Warehouse"})
    orch.decide_thread_investigation(tid, "open")
    findings = findings_file(project.record.path, tid)
    findings.parent.mkdir(parents=True, exist_ok=True)
    findings.write_text(MEASURED)
    return orch, oc, tid, findings


def test_closing_clears_the_grant_and_leaves_the_measurements_on_disk(tmp_path: Path):
    orch, oc, tid, findings = _open_one(tmp_path)

    assert orch.decide_thread_investigation(tid, "close")["investigation"]["state"] == "closed"

    assert findings.exists() and findings.read_text() == MEASURED
    assert orch.thread_context(tid)["investigation"]["state"] == "closed"
    # The bounding is back on the next turn, which is what "taking it back" has to mean.
    list(orch.chat_stream(tid, "so which of those accounts score highest?"))
    assert oc.snapshots and oc.snapshots[0].read_only_turn
    # And the conversation says it ended, the way it said it began.
    rows = [r for r in orch.thread_history(tid) if r.get("type") == "investigation-state"]
    assert [r["state"] for r in rows] == ["open", "closed"]
    # No `reason`: an ordinary close keeps the measurements, and the sentence the transcript draws
    # for it is the one that says so.
    assert "reason" not in rows[-1]


def test_closing_twice_is_not_an_error_and_writes_nothing_the_second_time(tmp_path: Path):
    """Two tabs on one conversation both show the bar. The second press must not be an error about
    a state the first one already reached."""
    orch, _, tid, _ = _open_one(tmp_path)
    first = orch.decide_thread_investigation(tid, "close")["investigation"]

    assert orch.decide_thread_investigation(tid, "close")["investigation"] == first
    assert [r["state"] for r in orch.thread_history(tid) if r.get("type") == "investigation-state"] \
        == ["open", "closed"]


def test_a_closed_thread_can_be_offered_again(tmp_path: Path):
    """Closing returns the Thread to where it started — which is the only way back in. A decline is
    what retires the card for good; closing is not a decline."""
    orch, _, tid, _ = _open_one(tmp_path)
    orch.decide_thread_investigation(tid, "close")

    events = list(orch.chat_stream(
        tid, "Investigate which accounts look like adopters and score them."))

    assert any(e.get("type") == "investigation-offer" for e in events)


def test_a_complete_recall_clear_takes_the_grant_with_the_findings(tmp_path: Path):
    orch, oc, tid, findings = _open_one(tmp_path)

    orch.clear_recall(tid, recall.EMPTY)

    assert not findings.exists()
    # Back where the Thread started, not merely closed: a complete clear is START OVER, and it is
    # the one door that has to leave nothing behind for a later question to trip over.
    assert orch.thread_context(tid).get("investigation") == {}
    list(orch.chat_stream(tid, "so which of those accounts score highest?"))
    assert oc.snapshots and oc.snapshots[0].read_only_turn
    # And the row that says the grant ended does not also promise the measurements survived it —
    # they were deleted two lines above.
    row = [r for r in orch.thread_history(tid) if r.get("type") == "investigation-state"][-1]
    assert row["state"] == "closed" and row["reason"] == "clear"


def test_a_complete_recall_clear_takes_a_decline_with_it_too(tmp_path: Path):
    """A decline is remembered so the card does not come back — the right answer to "I already said
    no" and the wrong one to "start over". After a complete clear the conversation is back where it
    began, which has to include being offerable again."""
    orch, _oc, tid, _ = _open_one(tmp_path)
    orch.decide_thread_investigation(tid, "decline")

    orch.clear_recall(tid, recall.EMPTY)

    assert orch.thread_context(tid).get("investigation") == {}
    events = list(orch.chat_stream(
        tid, "Investigate which accounts look like adopters and score them."))
    assert any(e.get("type") == "investigation-offer" for e in events)
    # A decline says nothing in the transcript, so the clear that drops it says nothing either.
    assert [r["state"] for r in orch.thread_history(tid)
            if r.get("type") == "investigation-state"] == ["open"]


def test_a_summary_clear_takes_neither(tmp_path: Path):
    """The softer rung trims talk. A measurement log is not talk, and neither is a grant the person
    made deliberately — taking either there would throw away work nobody asked to lose."""
    orch, oc, tid, findings = _open_one(tmp_path)

    orch.clear_recall(tid, recall.SUMMARY)

    assert findings.read_text() == MEASURED
    assert orch.thread_context(tid)["investigation"]["state"] == "open"
    assert not [r for r in orch.thread_history(tid) if r.get("type") == "investigation-state"
                and r["state"] == "closed"]
    list(orch.chat_stream(tid, "so which of those accounts score highest?"))
    assert oc.snapshots and not oc.snapshots[0].read_only_turn
