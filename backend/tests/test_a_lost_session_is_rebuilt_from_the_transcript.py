"""A Conversation whose OpenCode session is gone is rebuilt from Sage's own transcript (ADR-0060).

The two halves of a Conversation have different lifetimes. `history.jsonl` is on the Project volume
and survives; the OpenCode session that actually holds the model's memory is on the container
overlay and does not. So a restart leaves the person reading nine events while the model starts at
nothing, and #427 is the turn that answered from the nothing — "there's no Mixpanel question in our
conversation yet" — with the question sitting eight events above it.

`reseed` is what a freshly minted session is told. It is a separate function from `seed` on purpose,
and `test_the_rebuild_answers_the_history_shape_the_caller_actually_passes` below is why.
"""
from __future__ import annotations

from sage.orchestrator import recall
from sage.shim.chat_paths import text_key


def _user(text: str) -> dict:
    return {"type": "user", "text": text}


def _said(text: str) -> dict:
    return {"type": "agent", "kind": "text", "text": text}


def _cleared(scope: str) -> dict:
    return {"type": recall.CLEARED, "scope": scope}


def test_a_thread_on_its_first_turn_has_nothing_to_rebuild():
    # Every first turn mints a session. Nothing was lost, so nothing is carried and — because the
    # caller gates the notice on this same string — the person is told nothing either.
    assert recall.reseed([]) == ""
    assert recall.reseed([_user("hi")]) == ""


def test_a_rebuilt_session_carries_what_was_said_before_it():
    history = [_user("how many mixpanel events in the last 30 days?"),
               _said("There were 41,002 events from 3,118 distinct users."),
               _user("now try again")]

    carried = recall.reseed(history)

    assert "how many mixpanel events in the last 30 days?" in carried
    assert "41,002 events" in carried


def test_the_question_being_asked_is_not_summarised_back_as_something_already_said():
    # The caller appends this turn's `user` row (service.py) BEFORE it reads the transcript back,
    # and the same text is appended to the prompt as the actual question. Carrying it here too would
    # hand the model its own question twice — once as history, once as the ask — which reads as a
    # question already answered.
    history = [_user("first question"), _said("first answer"), _user("the question being asked")]

    carried = recall.reseed(history)

    assert "first question" in carried
    assert "the question being asked" not in carried


def test_a_complete_clear_survives_the_rebuild_that_follows_it():
    # The trap this function exists to avoid. A complete clear is carried out by DROPPING the
    # session, so the very next turn mints one and arrives here. Summarising the whole transcript
    # would hand straight back what the person just asked Sage to forget, and ADR-0022's `EMPTY`
    # rung would become a no-op that still prints its divider.
    history = [_user("forget this entirely"),
               _said("Something said before the clear."),
               _cleared(recall.EMPTY),
               _user("what can you do?")]

    assert recall.reseed(history) == ""


def test_a_complete_clear_still_carries_what_was_said_after_it():
    history = [_user("forget this entirely"),
               _said("Something said before the clear."),
               _cleared(recall.EMPTY),
               _user("what is in the orders table?"),
               _said("Twelve columns, 4M rows."),
               _user("chart it")]

    carried = recall.reseed(history)

    assert "forget this entirely" not in carried
    assert "Something said before the clear." not in carried
    assert "what is in the orders table?" in carried
    assert "Twelve columns, 4M rows." in carried


def test_a_summary_scoped_clear_is_not_undone_either():
    # The scope is not read, and this is the test that says why. `clear_recall` drops the session
    # for BOTH scopes, so a summary-scoped clear reaches the rebuild path exactly as a complete one
    # does. Carrying it whole "because that rung promised a summary" answers "start over, keep the
    # gist" by returning the entire pre-clear transcript — and does it under a notice telling the
    # person the model LOST a conversation they chose to clear.
    #
    # Keeping that promise belongs to `seed`, which has never kept it (#432). It is not this
    # function's to keep by accident.
    history = [_user("what is in the forecast file"),
               _said("Three predictions over a million."),
               _cleared(recall.SUMMARY),
               _user("and now?")]

    assert recall.reseed(history) == ""


def test_what_was_said_after_a_summary_clear_is_still_carried():
    # The clear is a floor, not a stop: rows below it were never asked to be forgotten, and losing
    # them to a restart is the thing this function exists for.
    history = [_user("forget the forecast talk"),
               _cleared(recall.SUMMARY),
               _user("what is in the orders table?"),
               _said("Twelve columns, 4M rows."),
               _user("chart it")]

    carried = recall.reseed(history)

    assert "forget the forecast talk" not in carried
    assert "what is in the orders table?" in carried
    assert "Twelve columns, 4M rows." in carried


def test_a_withheld_message_is_not_replayed_by_a_rebuild():
    # `chat_summary` applies the withhold restrictions, and a rebuild must not become the route by
    # which content the person stopped sending comes back.
    poison = "my account is 4871715921430428"
    history = [_user(poison),
               {"type": recall.WITHHELD, "keys": [text_key({"content": poison})],
                "labels": ["the message you sent"]},
               _user("carry on")]

    carried = recall.reseed(history)

    assert "4871715921430428" not in carried
    assert "a message in this conversation is not being sent" in carried


def test_the_rebuild_answers_the_history_shape_the_caller_actually_passes():
    """Why `reseed` is not a second key on `seed`.

    `seed` returns "" at the first `user` row scanning back. The caller appends this turn's `user`
    row before reading the transcript, so that row is always last and `seed` has therefore always
    returned "" on an ordinary turn — its tests pass only because every one of them calls it
    directly with rows that stop at the `CLEARED` event.

    Filed separately rather than fixed here (#432): making `seed` fire would start seeding the
    summary-scoped clear for the first time, which is a live change to the recall ladder that
    ADR-0060 did not decide. This test pins the shape so that fix cannot land unnoticed — if `seed`
    starts answering this history, the first assertion fails and someone reads this docstring.

    Two histories, because the two functions answer two different questions and an earlier version
    of this test ran them together and read the result as one. `seed`'s trigger is a clear;
    `reseed`'s is a lost session, and it declines a clear on purpose.
    """
    # `seed`'s own trigger, on the shape its caller actually passes. It cannot see past the
    # trailing `user` row to the CLEARED event, and never has.
    after_a_clear = [_user("what is in the mixpanel table?"),
                     _said("It holds events."),
                     _cleared(recall.SUMMARY),
                     _user("now try again")]
    assert recall.seed(after_a_clear) == ""

    # `reseed`'s trigger: nothing was cleared, the session was lost. The same caller shape.
    after_a_restart = [_user("what is in the mixpanel table?"),
                       _said("It holds events."),
                       _user("now try again")]
    assert recall.seed(after_a_restart) == ""
    assert "what is in the mixpanel table?" in recall.reseed(after_a_restart)
