"""Finding what a guardrail matched, by asking the gateway instead of guessing.

A gateway guardrail reads everything a request carries, including the contents of files a tool
opened and text the person pasted. That lives in the Conversation's Recall and is sent again every
turn, so one refusal becomes every later turn refused (ADR-0022).

ADR-0022 could only offer to clear Recall, on the reasoning that there was "nothing to cut and no
knife". The first half of that has not changed: MEASURED 2026-09-11, the refusal body is 93 bytes,
byte-identical for an email, an SSN, a card number and for a payload whose offender was one message
in the middle. It names the guardrail and nothing else. The gateway is a **yes/no oracle**.

The second half has. Bisection is how you get more than one bit out of a yes/no oracle: send the
message list with different parts withheld, read GUARDRAIL or OK, and the answer falls out in
`2·log2(n)+1` calls — measured at 4 files/5 calls/5.5s, 16/9/10.2s, 64/13/26.5s. Payload size
barely matters (250x the bytes cost 70% more latency), so the budget here is CALLS, never bytes.

Two rules this module exists to keep:

1. **Withhold, never drop.** `apply_withheld` replaces content and keeps every message where it is.
   A `role:"tool"` message dropped while its `tool_call` stays behind is an HTTP 400 on gpt-5.4,
   sonnet, haiku and gemini alike. So every probe is well-formed by construction, which is what lets
   `CLEAN` mean "nothing in here is refused" rather than "malformed some other way".

2. **Prove the floor before blaming anything.** A probe with everything withholdable withheld is
   what separates "this message is the cause" from "the cause is somewhere this search cannot
   reach" — Sage's own system prompt, the request's shape, a tool name. Without it the recursion
   blames whichever innocent message it bottomed out on, which is a worse failure than saying
   nothing: the person takes away a file that was never the problem.

3. **Find every carrier, not the first.** The same value is routinely in more than one place — a
   file read in two turns, a file and Sage's own answer quoting it. With two carriers, every subset
   containing either one comes back blocked, so a find-FIRST bisect names one, withholds it, and the
   next turn is refused identically: Sage would have confidently named a file, taken it away, and
   changed nothing. `_find_all` isolates each carrier that is refused on its own, and `search` then
   spends one more call proving the result actually clears the refusal. When it does not, that is
   reported rather than papered over, and the caller falls back to ADR-0022's ladder.

Pure: no gateway, no I/O, no OpenCode. The caller passes `ask`, which is the only thing that talks
to anything.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..shim.chat_paths import apply_withheld, file_key, read_path_from_tool_call, text_key

BLOCKED = "blocked"   # the gateway refused this payload
CLEAN = "clean"       # the gateway accepted it
UNKNOWN = "unknown"   # anything else: a timeout, a transport fault, another 4xx

# Enough for 64 carriers (2*log2(64)+1 = 13) plus the verify. A search that needs more than this is
# not converging, and a person watching a failed turn should not wait on it.
MAX_CALLS = int(os.environ.get("SAGE_WITHHOLD_MAX_CALLS", "14"))

# Never withheld. Sage's own instructions are not a carrier anyone can act on, and an agent that
# loses them answers as a stranger — the failure ADR-0022 refused to ship.
_NEVER = frozenset({"system", "developer"})


@dataclass(frozen=True)
class Carrier:
    """One withholdable thing, and what to call it in front of a person."""
    key: str        # what `apply_withheld` matches on: "file:<path>" or "text:<hash>"
    label: str      # what the card says: a file name, or "the message you sent"
    is_file: bool


@dataclass
class Found:
    carriers: list[Carrier] = field(default_factory=list)
    calls: int = 0
    # Everything that COULD have been withheld. `total - len(carriers)` is what decides whether
    # re-running the turn is worth anything: withhold the only thing it read and there is nothing
    # left to answer from, so the card must offer to stop sending rather than to carry on.
    total: int = 0
    # A verify probe came back CLEAN with these withheld. False means the refusal survives and the
    # caller must NOT claim it has fixed anything.
    complete: bool = False
    # "" when the search ran to an answer; otherwise why it stopped, for the log and the card.
    stopped: str = ""


def carriers(messages: list[dict]) -> list[Carrier]:
    """Every part of this payload that could be withheld, newest last.

    Two kinds, because a guardrail does not care which one carried the value. A tool result is named
    by the file its `tool_call` opened, which is the name a person recognises. Everything else is
    named by content fingerprint — that is what covers pasted text, an @-mention's inlined sample
    rows (which ride in a *user* message, not a tool result) and a compaction summary that copied a
    value out of a file before any of this ran.

    Deduped by key on purpose: the same file read in three turns is ONE carrier, and withholding it
    reaches all three messages. That is also what makes the bisect converge when a paged read put
    the same file in four messages.
    """
    paths: dict[str, str] = {}
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        for call in m.get("tool_calls") or []:
            if isinstance(call, dict) and (path := read_path_from_tool_call(call)):
                if cid := str(call.get("id") or ""):
                    paths[cid] = path
    out: list[Carrier] = []
    seen: set[str] = set()
    for m in messages:
        if not isinstance(m, dict) or m.get("role") in _NEVER:
            continue
        cid = str(m.get("tool_call_id") or "")
        if m.get("role") == "tool" and cid in paths:
            path = paths[cid]
            carrier = Carrier(file_key(path), os.path.basename(path) or path, True)
        else:
            if not _has_text(m):
                continue
            carrier = Carrier(text_key(m), _text_label(m), False)
        if carrier.key not in seen:
            seen.add(carrier.key)
            out.append(carrier)
    return out


def _has_text(message: dict) -> bool:
    """A message worth probing. One with no text cannot be what a value-matching guardrail read."""
    content = message.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    return isinstance(content, list) and any(
        isinstance(p, dict) and str(p.get("text") or "").strip() for p in content)


def _text_label(message: dict) -> str:
    role = message.get("role")
    if role in ("user", "human"):
        return "the message you sent"
    if role == "tool":
        return "something a tool read"
    return "an earlier answer in this conversation"


def search(messages: list[dict], ask, *, cap: int = MAX_CALLS) -> Found:
    """Which carriers the gateway is refusing, or as much of that as `cap` calls can prove.

    `ask(messages) -> BLOCKED | CLEAN | UNKNOWN` is the only thing here that talks to a gateway.

    Probes are ordered newest-first at every split, which is tidy and buys nothing. MEASURED: the
    cost is identical wherever the carrier sits — 4 files/7 calls, 8/9, 16/11, the same for every
    position. `_find_all` has to probe BOTH halves at every level because it finds all carriers
    rather than the first, so one half recursing and the other stopping clean is two probes per
    level whatever order they are in. Ordering only pays in a search that can stop early, and this
    one deliberately cannot.

    So a hint about where to look — from `refusal_scan`, from recency, from anywhere — cannot be
    spent here as an ordering. It would have to buy a fast path: probe the single most likely
    candidate alone, and if the rest come back clean, stop. That is a different search with a
    different failure mode (it can miss a second carrier), and it is not what this does.

    Nothing here reads the payload to decide what a guardrail would object to, and the reason is not
    that such a rule is unknowable. It is that the rule is HARD, and being nearly right about it is
    worse than not guessing. Re-measured live 2026-09-11, `Block PII`'s numeric half fences on a
    WORD boundary, not a digit one:

        1234567890 / 0908182187        GUARDRAIL   bare 10-digit runs, leading zero or not
        a1234567890b / a0908182187b    OK          the same numbers fenced by letters
        app_1a0908182187e6abddb1f      OK          a real Sage id, which holds one of them
        555-123-4567                   GUARDRAIL   and a separator form exists as well
        777777777777777 / 16 / 17      OK / GUARDRAIL / OK   cards are exactly 16, word-fenced

    `shim/refusal_scan.py` locates matches for somebody DIAGNOSING a refusal, values masked, and is
    the right tool for that. Its regex is a digit-boundary one with no separator form, so it
    over-reports every run fenced by letters and misses `555-123-4567` entirely — which is why
    nothing here may read it as an all-clear. It could reasonably ORDER the candidates below, as a
    hint about where to look first; it must never decide the answer.

    So: hint from a scanner if you like, verdict from the gateway always. A recovery that takes a
    person's file away has to be right rather than probable, and the rule it would otherwise depend
    on belongs to an administrator who can change it without telling Sage.
    """
    found = Found()
    all_carriers = carriers(messages)
    found.total = len(all_carriers)
    if not all_carriers:
        found.stopped = "nothing to search"
        return found
    keys = {c.key for c in all_carriers}

    def probe(keep: list[Carrier]) -> str:
        """The verdict with only `keep` sent for real and every other carrier withheld."""
        found.calls += 1
        return ask(apply_withheld(messages, keys - {c.key for c in keep}))

    verdict = probe(all_carriers)
    if verdict != BLOCKED:
        # Not our failure to explain. A turn that fails for any other reason must fall through to
        # the caller's own error path rather than be reported as a guardrail block with no carrier.
        found.stopped = "not blocked" if verdict == CLEAN else "no verdict"
        found.complete = verdict == CLEAN
        return found

    # The floor: everything withholdable withheld. Without this the search cannot tell "this message
    # is the cause" from "the cause is somewhere I cannot reach" — Sage's own system prompt, the
    # request's shape, a tool name — and would blame whichever innocent message the recursion
    # bottomed out on. A blocked floor is the honest answer that there is nothing here to take away.
    if probe([]) == BLOCKED:
        found.stopped = "not in this conversation's content"
        return found

    found.carriers = _find_all(all_carriers, probe, found, cap)
    if not found.carriers:
        found.stopped = found.stopped or "nothing isolated"
        return found

    # The call that stops this being a guess. Everything found withheld, everything else real: if
    # the gateway still refuses, the carriers do not account for the refusal and saying they do
    # would be worse than saying nothing.
    if found.calls < cap:
        rest = [c for c in all_carriers if c not in found.carriers]
        found.complete = probe(rest) == CLEAN
    else:
        found.stopped = "out of calls"
    return found


def _find_all(candidates: list[Carrier], probe, found: Found, cap: int) -> list[Carrier]:
    """Every candidate the gateway refuses ON ITS OWN, by bisection over a known-blocked set.

    Split newest-last so the most recently added half is tested first. Recursion stops at one
    candidate, which is then proven rather than assumed — the probe above it said the half was
    blocked, and with one member that member is the reason.
    """
    if found.calls >= cap:
        found.stopped = "out of calls"
        return []
    if len(candidates) == 1:
        return list(candidates)
    mid = len(candidates) // 2
    older, newer = candidates[:mid], candidates[mid:]
    hits: list[Carrier] = []
    for half in (newer, older):
        if found.calls >= cap:
            found.stopped = "out of calls"
            break
        if probe(half) == BLOCKED:
            hits.extend(_find_all(half, probe, found, cap))
    return hits
