"""ADR-0058's other half — the door, not the tool (#411).

#408 built `live_read_query`, so a Chat data turn can compute. This is what happens when one
SELECT is not enough: the turn offers the lane that already has Python instead of improvising its
way around the gap, which is what #400 measured at 400.2 seconds and #425 is the artifact-lane
version of.

Held here, in the order of how badly each one fails if it breaks:

- A CLAIM WITHOUT A STATEMENT DRAWS NOTHING. The model proposes and a cheap check verifies it, and
  the check is the only thing standing between this door and becoming the default. It is the
  condition most likely to rot, because every other test here passes without it.
- The marker is not readable mid-sentence. The prompt that teaches it necessarily names it, so a
  model quoting its own instruction is the expected first failure rather than a hypothetical one.
- The marker never reaches the person. It is addressed to Sage, and the prose it sits above is an
  answer someone is meant to trust.
- The count is of ATTEMPTS. A statement that timed out was still composed, and the slowest questions
  are the ones most likely to need the other lane.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator.service import (
    NEEDS_MORE_THAN_SQL_MARKER,
    _take_needs_more_than_sql_marker,
)
from sage.workspace.threads import ThreadStore

from .test_chat_turn import Turn, _orch

# ---- the marker itself -------------------------------------------------------------------------

def test_the_marker_is_read_on_its_own_line_and_nowhere_else():
    """The `NOTHING_TO_BUILD` rule, and it is inherited deliberately rather than re-argued.

    Mid-sentence is where a model QUOTES the marker while explaining itself. Reading that as a claim
    would hand every agent an accidental way to draw a card nobody asked for — and unlike the build
    marker, the prompt here has to name the token in order to teach it, so the model is being shown
    the exact sentence that would trip this."""
    body, claimed = _take_needs_more_than_sql_marker(
        f"The median needs a window function.\n{NEEDS_MORE_THAN_SQL_MARKER}")
    assert claimed
    assert body.strip() == "The median needs a window function."

    # The wrappers a model reaches for unprompted. A turn that ended correctly must not be read as
    # silent over a pair of backticks.
    for wrapped in (f"`{NEEDS_MORE_THAN_SQL_MARKER}`", f"**{NEEDS_MORE_THAN_SQL_MARKER}**",
                    f"  {NEEDS_MORE_THAN_SQL_MARKER}  "):
        _, claimed = _take_needs_more_than_sql_marker(f"Counted 41,234.\n{wrapped}\n")
        assert claimed, wrapped

    # And the quoting case, which is the one that matters.
    for quoted in (
        f"I would emit {NEEDS_MORE_THAN_SQL_MARKER} if this needed a join.",
        f"The instructions mention {NEEDS_MORE_THAN_SQL_MARKER} — it does not apply here.",
    ):
        body, claimed = _take_needs_more_than_sql_marker(quoted)
        assert not claimed, quoted
        assert body == quoted, "a sentence that only mentions the marker is left exactly as it was"


# ---- the cheap check ---------------------------------------------------------------------------

def test_a_turn_that_composed_no_statement_is_not_offered_the_door(tmp_path: Path):
    """THE CONDITION THIS WHOLE FEATURE RESTS ON.

    The door is for a question SQL could not express. A turn that never sent a statement has not
    established that — it has only said so — and an unchecked claim turns this door into the default
    that #400 measured. This test is the reason the check exists, and it is the one that goes quiet
    first: delete the check and every other test in this file still passes.
    """
    orch, _ = _orch(tmp_path, [Turn(
        text=f"That needs a correlation I can't express.\n{NEEDS_MORE_THAN_SQL_MARKER}")])
    tid = orch.create_thread()["id"]
    events = list(orch.chat_stream(tid, "is signup rate correlated with seat count?"))

    assert not [e for e in events if e.get("type") == "other-lane-offer"], (
        "the turn claimed the door without composing a statement, so no card is drawn")
    # And the claim leaves no trace on the person's screen either way.
    assert NEEDS_MORE_THAN_SQL_MARKER not in json.dumps(orch.thread_history(tid))


def test_the_marker_never_reaches_the_person(tmp_path: Path):
    """It is a signal addressed to Sage. A bare token under a friendly answer reads as a leaked
    error code — and here it sits directly above the prose that IS the answer, so the person is
    being asked to trust the sentence the token is sitting on."""
    orch, _ = _orch(tmp_path, [Turn(
        text=f"Signups rose 12% last quarter.\n{NEEDS_MORE_THAN_SQL_MARKER}")])
    tid = orch.create_thread()["id"]
    events = list(orch.chat_stream(tid, "how did signups move?"))

    texts = [e["text"] for e in events if e.get("type") == "agent" and e.get("kind") == "text"]
    assert [t.strip() for t in texts] == ["Signups rose 12% last quarter."]
    assert NEEDS_MORE_THAN_SQL_MARKER not in json.dumps(orch.thread_history(tid))


def test_the_door_opens_when_the_turn_tried_a_statement_first(tmp_path: Path):
    """The other arm. The count is set through the same attribute the wrapper writes, and the offer
    is asked for through the method `_chat_stream` calls, so what is exercised here is the decision
    rather than a copy of it."""
    orch, _ = _orch(tmp_path, [Turn(text="Counted 41,234 accounts.")])
    tid = orch.create_thread()["id"]
    store = ThreadStore(orch._chat_project().record.path)

    orch._statements_tried[tid] = 1
    events = list(orch._chat_other_lane_offer(store, tid, "correlate signups with seats",
                                              claimed=True) or [])
    assert [e["type"] for e in events] == ["other-lane-offer"]
    said = events[0]["message"]
    assert "can't run here" in said and "a few minutes" in said, said
    # No `done`. The turn's own terminal row follows this card a few lines below, and a second one
    # would settle the turn twice — the difference between this card and every other card in the
    # file, and the thing a later reader is most likely to "fix".
    assert not [e for e in events if e.get("type") == "done"]

    # Not claimed is not offered, whatever the count says.
    assert orch._chat_other_lane_offer(store, tid, "correlate signups with seats",
                                       claimed=False) is None


def test_the_count_is_of_attempts_not_of_answers(tmp_path: Path):
    """A statement that timed out was still composed and still sent.

    `StatementTimeout` is its own type precisely so a turn can tell "the store said no" from "I have
    no way to ask" (#399, #408). Counting only what came back would make the slowest questions — the
    ones most likely to need the other lane — the ones that can never reach the door.
    """
    from sage.resources.provider import StatementTimeout

    orch, _ = _orch(tmp_path, [Turn(text="…")])
    tid = orch.create_thread()["id"]

    def boom(source, sql, **kw):
        raise StatementTimeout("still running after 120 seconds")

    orch._resources.run_statement = boom
    turn = orch._live_read_turn_for(tid)
    assert orch._statements_tried.get(tid, 0) == 0

    with pytest.raises(StatementTimeout):
        turn.run_statement(object(), "SELECT CORR(a, b) FROM T", limit=10)
    assert orch._statements_tried.get(tid) == 1, (
        "the attempt counts even though nothing came back")


# ---- the copy that teaches it ------------------------------------------------------------------

def test_the_pack_and_its_mirror_both_teach_the_marker():
    """`template/chat/AGENTS.md` is hand-copied into `opencode.json`, so a rule added to one and not
    the other is taught to nobody in the lane that reads the mirror. Both are checked here because a
    grep of one proves nothing about the other."""
    root = Path(__file__).resolve().parents[2]
    pack = (root / "template" / "chat" / "AGENTS.md").read_text()
    mirror = (root / "opencode.json").read_text()

    for name, text in (("AGENTS.md", pack), ("opencode.json", mirror)):
        assert NEEDS_MORE_THAN_SQL_MARKER in text, name
        # The two halves the check depends on. Teaching the token without the order teaches a model
        # to claim before it has measured, and every one of those claims is dropped.
        assert "on a line of its own" in text, name
        assert "tried a statement" in text, name


# ---- the grant (Project owner, 2026-09-18: accept AND decline are per calculation) --------------

def _grant_for(orch, store, tid, prompt="correlate signups with seats"):
    """Mint one through the real producer rather than reaching into the dict.

    A grant handed to the test by hand would prove the arming path and nothing about whether a card
    ever carries one — and the card is the only thing that may create a grant.
    """
    orch._statements_tried[tid] = 1
    events = list(orch._chat_other_lane_offer(store, tid, prompt, claimed=True) or [])
    return events[0]["grant"]


def test_the_grant_is_single_use_and_cannot_be_invented(tmp_path: Path):
    """The grant suppresses bounded arming, so it is not a request-body boolean like the four flags
    beside it. Those gate a CARD and a forged one skips a question; a forged one here would hand a
    browser the shell lane. Minted by the server when the card is drawn, spent once, then gone."""
    orch, _ = _orch(tmp_path, [Turn(text="Counted 41,234 accounts.")])
    tid = orch.create_thread()["id"]
    store = ThreadStore(orch._chat_project().record.path)

    grant = _grant_for(orch, store, tid)
    assert grant, "the card carries the grant it offers"

    assert orch._spend_other_lane_grant(tid, grant) is True
    # Spent on presentation, so the second attempt is an ordinary bounded turn rather than a second
    # unbounded one. This is the line between a per-calculation grant and a standing one.
    assert orch._spend_other_lane_grant(tid, grant) is False
    assert orch._spend_other_lane_grant(tid, "lane_grant_made_up") is False
    assert orch._spend_other_lane_grant(tid, "") is False
    # And a grant minted for one conversation is not spendable in another.
    other = orch.create_thread()["id"]
    assert orch._spend_other_lane_grant(other, _grant_for(orch, store, tid)) is False


def test_a_replay_under_a_grant_is_not_armed_read_only(tmp_path: Path):
    """THE PLANT FOR THE GRANT ITSELF. Everything else about this door is bookkeeping if the accept
    does not actually reach the lane with Python — that is the calculation the person clicked for."""
    from .fake_opencode import Turn as FakeTurn
    from .test_chat_turn import IntentGateway, ObservedControlOpenCode

    turns = [FakeTurn(text="Counted 41,234 accounts.")]
    orch, oc = _orch(
        tmp_path, turns,
        gateway=IntentGateway({"label": "data_answer", "confidence": 0.91}),
        client=lambda ws: ObservedControlOpenCode(ws, list(turns)),
    )
    project = orch.project(start_preview=False)
    oc.control = project.control
    tid = orch.create_thread()["id"]
    store = ThreadStore(orch._chat_project().record.path)
    grant = _grant_for(orch, store, tid)

    list(orch.chat_stream(tid, "correlate signups with seats", other_lane_grant=grant))
    assert oc.snapshots
    assert not oc.snapshots[0].read_only_turn, (
        "the accepted calculation runs on the lane that has Python")

    # THE SAME TURN WITHOUT A LIVE GRANT IS BOUNDED. The grant above is spent by now, so this is
    # both the spent case and the forged case: neither buys the shell lane.
    oc.snapshots.clear()
    list(orch.chat_stream(tid, "correlate signups with seats", other_lane_grant=grant))
    assert oc.snapshots and oc.snapshots[0].read_only_turn, (
        "a spent grant is an ordinary bounded turn")


def test_accepting_one_calculation_leaves_the_investigation_answer_alone(tmp_path: Path):
    """THE #389 QUESTION, AND THE REASON THE GRANT IS NOT `decide_thread_investigation`.

    The decline was always per-calculation. Reusing the Thread-wide grant for the accept would have
    made the two halves disagree, and `decide_thread_investigation` overwrites a `declined` row with
    `{"state": "open"}` — so accepting would have re-opened a Thread #389 says can only be entered
    through the investigation card. Nothing in this door reads or writes that record, in either
    direction, and this test is what holds that.
    """
    orch, _ = _orch(tmp_path, [Turn(text="Counted 41,234 accounts.")])
    tid = orch.create_thread()["id"]
    store = ThreadStore(orch._chat_project().record.path)

    orch.decide_thread_investigation(tid, "decline")
    before = store.read_investigation(tid)
    assert before.get("state") == "declined"

    # The card is still offered in a Thread that declined an investigation — "no" to looking across
    # sources is not "no" to working out this number — and accepting it changes nothing on the
    # record.
    grant = _grant_for(orch, store, tid)
    assert grant, "a declined Thread is still offered the door"
    list(orch.chat_stream(tid, "correlate signups with seats", other_lane_grant=grant))

    assert store.read_investigation(tid) == before, (
        "accepting one calculation neither opens nor re-opens an investigation")


def test_a_replay_under_a_grant_does_not_write_the_question_twice(tmp_path: Path):
    """The grant joins `already_asked` and the three gate flags in deciding `asking`.

    The turn that drew this card wrote the question into the Thread before it answered it, so a
    replay that wrote it again would print the person's sentence twice under one card — the defect
    every flag beside it exists to avoid, arriving through a fifth door.
    """
    orch, _ = _orch(tmp_path, [Turn(text="Counted 41,234 accounts.")])
    tid = orch.create_thread()["id"]
    store = ThreadStore(orch._chat_project().record.path)
    grant = _grant_for(orch, store, tid)

    list(orch.chat_stream(tid, "correlate signups with seats", other_lane_grant=grant))
    asked = [e for e in orch.thread_history(tid)
             if e.get("type") == "user" and e.get("text") == "correlate signups with seats"]
    assert asked == [], "the replay adds no second copy of a question already on the Thread"
