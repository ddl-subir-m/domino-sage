"""A gateway guardrail block, said plainly instead of dumped.

A guardrail refusal is not a fault and it is not Sage's: the gateway was asked to police what
passes through it, and it did. But it arrives buried — the gateway answers 400, the shim wraps
that in a 502 whose message quotes the body, OpenCode quotes the shim — and the nest reached the
Thread whole. Live, a Thread asked what was in an attached JSON file, the file held phone numbers,
and the person read three levels of escaped JSON to learn that a policy about phone numbers had
stopped a turn in which they had typed none. The file held no phone numbers either: three sales
forecasts ran over a million, and a pattern that accepts a decimal point as its right boundary
matched their seven-digit integer parts.

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


def test_it_names_the_file_the_turn_read():
    """The half the person can act on. The values are theirs and the file is theirs, but nothing on
    screen said WHICH file — which is the whole reason this took an afternoon to find."""
    said = _chat_error_text(LIVE, [{"name": "Narnia_LensLogic_20240801_224434.json"}])
    assert "Narnia_LensLogic_20240801_224434.json" in said
    assert "files it opened" in said


def test_it_still_reads_as_a_sentence_with_no_attachment_to_name():
    said = _chat_error_text(LIVE)
    assert "Take the matching values out, or ask your Domino administrator" in said
    assert "This turn read" not in said


def test_the_way_out_is_not_named_here(caplog):
    """A first refusal may be a blip, and the exit costs the model everything it has been told. So
    the offer is `recall.offer`'s to make on the second identical refusal (ADR-0022), not a
    sentence shown to everyone whose turn failed once."""
    said = _chat_error_text(LIVE, [{"name": "f.json"}])
    assert "Recall" not in said
    assert "new Conversation" not in said


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
