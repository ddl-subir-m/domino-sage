"""Clearing Recall on a Conversation the gateway keeps refusing (ADR-0022).

The refused value arrives as a tool result, so it lands in Recall, and Recall is sent again on
every turn. Live: a JSON file of sales forecasts tripped a "Block phone numbers" guardrail on three
predictions over a million; the next question attached a CSV holding nothing that could match and
was refused too; the same CSV in a new Conversation was answered.

Every rung here is derived from the transcript. Nothing counts, nothing is stored.
"""
from __future__ import annotations

from sage.orchestrator import recall

RAW = ('Provider request failed with HTTP 502: {"error":{"message":"gateway returned 400 for '
       'https://host/v1/chat/completions: {\\"detail\\":{\\"error\\":{\\"message\\":\\"Blocked by '
       'guardrail: Block phone numbers\\",\\"type\\":\\"guardrail_blocked\\"}}}"}}')
KEY = "guardrail:Block phone numbers"


def _err(key: str = KEY, message: str = "refused") -> dict:
    return {"type": "error", "reason": key, "message": message}


def _cleared(scope: str) -> dict:
    return {"type": recall.CLEARED, "scope": scope}


def test_the_refusals_identity_is_the_gateways_words_not_the_sentence_shown():
    """The shown sentence names the turn's Attachment, and the ladder exists precisely because the
    NEXT turn fails on a different one. Keyed on the prose, the live pair never connects."""
    assert recall.reason_key(RAW) == KEY
    assert recall.reason_key(RAW.replace("Block phone numbers", "Block emails")) != KEY
    # Two turns, two different files, one refusal happening twice.
    narnia = [_err(recall.reason_key(RAW), "…read Narnia_LensLogic.json…")]
    both = narnia + [_err(recall.reason_key(RAW), "…read synthetic_adverse_events.csv…")]
    assert recall.offer(narnia) is None
    assert recall.offer(both) == recall.SUMMARY


def test_a_single_refusal_is_noise():
    """One refusal must not spend the Conversation's context. It may be a blip; two identical ones
    mean it is in Recall."""
    assert recall.offer([{"type": "user", "text": "hi"}, _err()]) is None


def test_the_second_identical_refusal_offers_a_seeded_clear():
    assert recall.offer([_err(), _err()]) == recall.SUMMARY


def test_a_different_refusal_does_not_advance_the_ladder():
    assert recall.offer([_err(), _err("http:500")]) is None


def test_refused_again_after_a_seeded_clear_offers_a_complete_one():
    """Sage's own answer prose is in the transcript, so `chat_summary` can carry the value straight
    back into the fresh session. That is the likely case, not the exotic one."""
    assert recall.offer([_err(), _err(), _cleared(recall.SUMMARY), _err()]) == recall.EMPTY


def test_refused_again_after_a_complete_clear_offers_nothing():
    """Recall is empty and it is still refused, so the value is in the message just typed or the
    file it names. There is nothing left for clearing to reach."""
    history = [_err(), _err(), _cleared(recall.SUMMARY), _err(), _cleared(recall.EMPTY), _err()]
    assert recall.offer(history) is None
    assert recall.terminal(history) is True


def test_terminal_is_not_declared_before_the_ladder_is_spent():
    assert recall.terminal([_err(), _err()]) is False
    assert recall.terminal([_err(), _err(), _cleared(recall.SUMMARY), _err()]) is False


def test_declining_re_offers_rather_than_hiding_the_only_exit():
    """Unlike a Build offer, `Not now` here is not a preference about a want — it is a judgment made
    before trying anything else, and the refusal is unrecoverable by any other means."""
    assert recall.offer([_err(), _err()]) == recall.SUMMARY
    assert recall.offer([_err(), _err(), _err()]) == recall.SUMMARY


def test_a_conversation_that_recovers_starts_the_ladder_over():
    """A clear that worked, and a new refusal much later, must not open on the last rung: the
    window is what happened between the two identical refusals, not the whole transcript."""
    history = [_err(), _err(), _cleared(recall.SUMMARY),
               {"type": "user", "text": "ok"}, {"type": "agent", "kind": "text", "text": "done"},
               _err(), _err()]
    assert recall.offer(history) == recall.SUMMARY


def test_the_offer_only_follows_a_refusal():
    assert recall.offer([_err(), _err(), {"type": "done", "ok": True}]) is None
    assert recall.offer([]) is None


def test_a_seeded_clear_carries_what_was_said_and_nothing_after_it():
    said = [{"type": "user", "text": "what is in the forecast file"},
            {"type": "agent", "kind": "text", "text": "Three predictions over a million."}]
    seeded = recall.seed(said + [_err(), _err(), _cleared(recall.SUMMARY)])
    assert "what is in the forecast file" in seeded
    assert "Three predictions over a million." in seeded


def test_a_complete_clear_carries_nothing():
    said = [{"type": "user", "text": "what is in the forecast file"}]
    assert recall.seed(said + [_cleared(recall.EMPTY)]) == ""


def test_the_seed_is_carried_once_not_on_every_later_turn():
    """It seeds the FIRST turn after the clear. After that the fresh session holds the conversation
    itself, and re-sending the summary would say everything twice."""
    said = [{"type": "user", "text": "first"}, _cleared(recall.SUMMARY)]
    assert recall.seed(said) != ""
    assert recall.seed(said + [{"type": "user", "text": "second"}]) == ""


def test_a_conversation_that_never_cleared_seeds_nothing():
    assert recall.seed([{"type": "user", "text": "hi"}]) == ""
    assert recall.seed([]) == ""
