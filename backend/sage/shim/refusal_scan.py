r"""Where an outgoing request matches what a PII guardrail refuses — with the values masked.

The gateway says `Blocked by guardrail: Block PII` and nothing else. It does not say which field,
which message, or which characters, and the request is not recorded anywhere, so the only way to
find the carrier has been to guess at it and re-send. Diagnosing one live refusal on 2026-09-11 cost
an afternoon and ended without an answer: every surface Sage exposes was clean, because the search
was run against the wrong rule.

The rule is broader than "personal data" suggests, which is why guessing fails. Measured against the
live gateway that day:

    555-123-4567       refused       and the separated form, which no digit run matches
    555.123.4567       refused       dot and space separate too
    555/123/4567       allowed       but a slash does not
    1234567890         refused       a bare ten-digit run is a phone number
    1757592000         refused       the same, and that is a unix timestamp in SECONDS
    1757592000000      allowed       the same instant in milliseconds is thirteen digits
    2147483647         refused       INT32_MAX
    123456789          allowed       nine digits is not enough
    7777777777777777   refused       sixteen is a card number, Luhn or not
    777777777777777    allowed       fifteen is not, and neither is seventeen

The digit windows are exactly 10-11 and exactly 16 — 12 through 15 and 17 through 19 all pass, and
so the obvious `\d{13,19}` for a card number is wrong in the direction that matters: it matches a
millisecond timestamp, which is the single most common long number in a payload, and would have sent
the next reader to a field the gateway never objected to.

The boundary is a WORD boundary and not merely a digit one, which is the difference between a useful
scan and a misleading one. Measured:

    1234567890         refused       bare
    x 1234567890 y     refused       space either side
    (1234567890)       refused       punctuation either side
    a1234567890        allowed       a letter touching it
    1234567890a        allowed       the same on the right
    _1234567890        allowed       underscore is a word character too

That last group is the load-bearing one. Sage's own ids are hex — `app_1a0908182187e6abddb1f` holds
the ten-digit run `0908182187` — and a digit-boundary rule flags every one of them while the gateway
refuses none. Verified against the live gateway: that app id, that path, and that id inside a
sentence all pass. An id is NOT a carrier, and a scan that says it is sends the next reader to a
field the gateway never objected to, which is this module's one job not to do.

So an ordinary row id, an account number or a seconds-precision timestamp refuses a turn, and
nothing about the payload looks like PII to a person reading it. Naming the offset is the whole
value here: it turns "something in 170KB" into one field.

Nothing returned by this module carries a matched value. A guardrail refusal is the one moment we
are certain the payload holds something a policy objects to, and writing it to a log to prove the
policy was right would be the same mistake the policy exists to prevent.
"""

from __future__ import annotations

import re
from typing import Any

# Named for what they cost, not for what they are: `digits_10_11` is the phone rule, and it is first
# because it is the one that fires on data nobody would think to look at.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("10-11 digit run (phone)", re.compile(r"\b\d{10,11}\b")),
    # The separated form is a SECOND rule, not a variant of the one above: `555-123-4567` carries a
    # run of three, a run of three and a run of four, so no digit-run pattern reaches it at any
    # length. Missing it was worse than a plain miss — the caller's else-branch tells the next reader
    # that the gateway's rules are not ours and to go and re-probe, which for a plain phone number
    # sends them to re-derive a row `scripts/guardrail-probe.py` has had in its table all along.
    # Separators are space, dot and hyphen, each position independent (`555-123.4567` is refused
    # live, so this is not a backreference) and `/` is NOT one — `555/123/4567` passes.
    ("phone (separated)", re.compile(r"\b\d{3}[ .-]\d{3}[ .-]\d{4}\b")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("16 digit run (card)", re.compile(r"\b\d{16}\b")),
]

_KEEP = 2          # leading characters left readable, enough to recognise a timestamp or a domain
_MAX_HITS = 12     # a refused payload can match in hundreds of places; the first few locate it


def _mask(value: str) -> str:
    """Enough of the shape to recognise it, never enough to read it."""
    head = value[:_KEEP]
    return head + "#" * (len(value) - len(head))


def _walk(node: Any, path: str, out: list[str]) -> None:
    if len(out) >= _MAX_HITS:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            _walk(v, f"{path}.{k}" if path else str(k), out)
        return
    if isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, f"{path}[{i}]", out)
        return
    if not isinstance(node, str):
        return
    for label, pattern in _PATTERNS:
        m = pattern.search(node)
        if m is None:
            continue
        # The offset, because the path alone is not enough: one `messages[n].content` is most of the
        # payload, and "somewhere in 60KB" is the answer we already had.
        out.append(f"{path or '<root>'} +{m.start()} {label} ({_mask(m.group(0))})")
        if len(out) >= _MAX_HITS:
            return


def candidates(request: dict) -> list[str]:
    """Every place this request matches a refusal rule, masked, most-specific rule first.

    Best-effort and never raised through: this runs on a path that is already failing, and a
    diagnostic that turns a refusal into a traceback has made the turn worse rather than better.
    """
    out: list[str] = []
    try:
        _walk(request, "", out)
    except Exception:  # pragma: no cover - a scan must never replace the error it is explaining
        return []
    return out
