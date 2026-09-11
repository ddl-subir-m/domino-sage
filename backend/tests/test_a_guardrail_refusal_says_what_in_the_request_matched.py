"""A guardrail names the policy; this names the field. Without leaking what is in it.

`Blocked by guardrail: Block PII` is the whole of what the gateway says. It does not name the
message, the field or the offset, and the request is recorded nowhere, so the carrier has to be
guessed at and the payload re-sent. One live refusal on 2026-09-11 survived a whole afternoon of
that: every surface Sage exposes read clean, because the search was run against "what looks like
personal data" and the rule is wider than that — a bare ten-digit run is a phone number, so a
seconds-precision timestamp refuses a turn while the same instant in milliseconds does not.
"""
from __future__ import annotations

from sage.shim import refusal_scan

# The shape the shim forwards, with one ordinary-looking carrier in it: `1757592000` is a unix
# timestamp in seconds, and it is ten digits.
REQUEST = {
    "model": "sonnet",
    "messages": [
        {"role": "user", "content": "here is the app source, nothing remarkable in it"},
        {"role": "user", "content": "the row was created at 1757592000 by the loader"},
    ],
}


def test_it_names_the_field_and_the_offset_not_just_the_turn():
    found = refusal_scan.candidates(REQUEST)

    assert len(found) == 1
    # The path AND the offset: one `messages[n].content` is most of a 170KB payload, and
    # "somewhere in there" is the answer we already had.
    at = REQUEST["messages"][1]["content"].index("1757592000")
    assert found[0].startswith(f"messages[1].content +{at} ")
    assert "10-11 digit run (phone)" in found[0]


def test_it_never_writes_down_the_value_it_found():
    """The one moment we are certain the payload holds something a policy objects to is the worst
    possible moment to copy it into a log to prove the policy was right."""
    found = refusal_scan.candidates(REQUEST)

    assert "1757592000" not in found[0], "the matched value was written out in full"
    assert "17########" in found[0], "not enough shape left to recognise what it was"


def test_a_clean_request_produces_nothing_to_chase():
    """So an empty result is evidence, not a silence: it says the gateway's rules are not the ones
    modelled here, which is the moment to re-probe rather than to keep reading the payload."""
    clean = {"model": "sonnet",
             "messages": [{"role": "user", "content": "build a chart of revenue by month"}]}

    assert refusal_scan.candidates(clean) == []


def test_nine_digits_are_not_a_phone_number_and_thirteen_are_not_either():
    """The boundary, measured live rather than assumed — it is what makes the rule surprising, and
    a scanner that gets it wrong sends the next person to the wrong field."""
    def one(value: str) -> list[str]:
        return refusal_scan.candidates({"messages": [{"content": f"id {value} here"}]})

    assert one("123456789") == []            # nine: allowed live
    assert one("1234567890") != []           # ten: refused live
    assert one("12345678901") != []          # eleven: refused live
    assert one("1757592000000") == []        # thirteen — the same instant in millis, allowed live
    assert one("7777777777777777") != []     # sixteen: refused live, Luhn or not
    assert one("777777777777777") == []      # fifteen: allowed live
    assert one("77777777777777777") == []    # seventeen: allowed live


def test_a_scan_never_replaces_the_error_it_was_explaining():
    """It runs on a path that is already failing. A diagnostic that raises has made the turn worse."""
    class Hostile:
        def __getitem__(self, k): raise RuntimeError("nope")
        def items(self): raise RuntimeError("nope")

    assert refusal_scan.candidates({"messages": Hostile()}) == []
