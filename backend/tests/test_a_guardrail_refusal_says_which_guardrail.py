"""A gateway guardrail block, said plainly instead of dumped.

A guardrail refusal is not a fault and it is not Sage's: the gateway was asked to police what
passes through it, and it did. But it arrives buried — the gateway answers 400, the shim wraps
that in a 502 whose message quotes the body, OpenCode quotes the shim — and the nest reached the
Thread whole. Live, a Thread asked what was in an attached JSON file, the file held phone numbers,
and the person read three levels of escaped JSON to learn that a policy about phone numbers had
stopped a turn in which they had typed none.

The gateway's own sentence is kept verbatim and quoted (ADR-0014). What is dropped is the
transport wrapper, which the gateway did not write.
"""
from __future__ import annotations

from sage.orchestrator.service import _CHAT_ERROR_MAX, _chat_error_text

LIVE = (
    'Provider request failed with HTTP 502: {"error":{"message":"gateway returned 400 for '
    'https://apps.cloud-dogfood.domino.tech/apps/llm_gateway/v1/chat/completions: '
    '{\\"detail\\":{\\"error\\":{\\"message\\":\\"Blocked by guardrail: Block phone numbers\\",'
    '\\"type\\":\\"guardrail_blocked\\"}}}","upstream_status":400}}'
)


def test_the_guardrails_own_sentence_survives():
    assert '"Blocked by guardrail: Block phone numbers"' in _chat_error_text(LIVE)


def test_the_transport_wrapper_does_not():
    said = _chat_error_text(LIVE)
    for noise in ("502", "400", "detail", "upstream_status", "chat/completions"):
        assert noise not in said


def test_it_says_where_the_guardrail_looked():
    """The half people get wrong: the turn carries the data, not only the sentence they typed."""
    said = _chat_error_text(LIVE)
    assert "not only what you typed" in said
    assert "administrator" in said


def test_a_longer_nest_still_names_its_guardrail():
    """Translating before the clip, not after. The live nest cleared 300 characters by 59, on the
    shorter of the two gateway URL forms — a longer host or guardrail name eats that margin."""
    long_url = LIVE.replace("apps/llm_gateway", "apps/" + "bda1c28f-b516-4df0-a00f-97176c9ff46c" * 3)
    assert len(long_url) > _CHAT_ERROR_MAX
    assert "Blocked by guardrail" not in long_url[:_CHAT_ERROR_MAX]
    assert '"Blocked by guardrail: Block phone numbers"' in _chat_error_text(long_url)


def test_every_other_failure_is_still_passed_through_untouched():
    assert _chat_error_text("Provider request failed with HTTP 500") == (
        "Provider request failed with HTTP 500")
    assert _chat_error_text({"data": {"message": "connection reset"}}) == "connection reset"
    assert _chat_error_text(None) == ""
