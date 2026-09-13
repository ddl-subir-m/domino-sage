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
to anything. `MENTION_MARK` is shared with the prompt that writes it rather than copied here, so a
reword cannot leave this file reading for words nothing produces — and it is read from `chat_paths`
rather than from the driver that writes it, because importing a string out of the driver costs this
module httpx and 148 more modules (MEASURED). That comment lives with the constant.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..shim import refusal_scan
from ..shim.chat_paths import (
    MENTION_MARK,
    MENTION_PATH_LINE,
    apply_withheld,
    content_text,
    file_key,
    read_path_from_tool_call,
    text_key,
)

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
    # Whether this carrier is data the turn fetched, rather than prose somebody wrote. NOT the
    # same question as `is_file`, which is narrower on purpose: `is_file` needs a path to put on
    # the card and only `read`-shaped calls carry one, so a `bash cat`, a `grep` or a live-read's
    # rows are data with no filename. `surviving` counts these; the card names the ones with a
    # name. Defaulted so the older three-argument construction still reads.
    #
    # Usually a tool result, but not always: an @mention inlines its file's descriptor into the
    # PROMPT, so those rows arrive in a *user* message that also holds the person's typed words
    # (#290, `_carries_mention`). Data is about where the material came from, not which role
    # carried it.
    is_data: bool = False


@dataclass
class Found:
    carriers: list[Carrier] = field(default_factory=list)
    calls: int = 0
    # Everything that COULD have been withheld, and how much of that was data. Both are counted
    # because `surviving` below is a question about DATA, and a payload is mostly prose.
    total: int = 0
    total_data: int = 0
    # A verify probe came back CLEAN with these withheld. False means the refusal survives and the
    # caller must NOT claim it has fixed anything.
    complete: bool = False
    # "" when the search ran to an answer; otherwise why it stopped, for the log and the card.
    stopped: str = ""

    @property
    def surviving(self) -> int:
        """How much of this turn is left to answer FROM once the carriers stop being sent.

        Counted over DATA. A carrier is any withholdable message, and most of a payload is prose —
        the question, Sage's earlier answers. Prose is not material a question is answered from, so
        counting it told a one-file conversation that two things survived the loss of its only
        file, and the card offered to carry on over nothing (#288).

        Data is `is_data`, not `is_file`: a turn can fetch rows through `bash cat`, `grep` or a
        live read, and `is_file` is set only where `read_path_from_tool_call` finds a path to put
        on the card, so none of those are files. Counting files would get the mirror case WRONG —
        one `read` file refused while a `cat` of a clean one survives reads as zero, and suppresses
        a re-run that would have worked. That is a new wrong answer in the opposite direction from
        the bug, which is worse than the bug.

        A conversation that fetched no data at all falls back to counting carriers, because there
        the prose IS the material: an earlier answer refused leaves the question standing, and
        re-running it is worth the call.

        Read that fallback as the population it is for — a turn that fetched NOTHING — and not as
        a general rule. A turn whose only data is an @mention used to land in it and get the
        pre-#288 answer, because an @mention's descriptor rides in a *user* message and a user
        message was prose by definition. It no longer does: `_carries_mention` marks that message
        `is_data`, so one @mention and nothing else counts as one piece of data, and losing it
        leaves zero (#290). The fallback now covers only what its name says.

        Says nothing about whether the QUESTION is one of the things going — that is
        `prompt_withheld`, the other half of "is re-running this worth a call", and the two come
        apart in the case a person meets most.
        """
        if self.total_data:
            return max(self.total_data - sum(1 for c in self.carriers if c.is_data), 0)
        return max(self.total - len(self.carriers), 0)


def carriers(messages: list[dict]) -> list[Carrier]:
    """Every part of this payload that could be withheld, newest last.

    Two kinds, because a guardrail does not care which one carried the value. A tool result is named
    by the file its `tool_call` opened, which is the name a person recognises. Everything else is
    named by content fingerprint — that is what covers pasted text, an @mention's inlined descriptor
    (which rides in a *user* message, not a tool result, and is counted as data all the same: see
    `_carries_mention`) and a compaction summary that copied a value out of a file before any of
    this ran.

    Deduped by key on purpose: the same file read in three turns is ONE carrier, and withholding it
    reaches all three messages. That is also what makes the bisect converge when a paged read put
    the same file in four messages.
    """
    out: list[Carrier] = []
    seen: set[str] = set()
    for carrier, _message in _walk(messages):
        if carrier.key not in seen:
            seen.add(carrier.key)
            out.append(carrier)
    return out


def prompt_withheld(messages: list[dict], withheld: list[Carrier]) -> bool:
    """Is the turn's own question one of the things being taken away?

    A fact about the payload, not about what the gateway refused, which is why it sits beside
    `search` rather than inside it: `Found` says what a probe proved, and this says whether re-asking
    is worth a call. `surviving` cannot answer it — that counts what is left to answer FROM, and a
    question can be withheld while every file it read survives. That pair is the common case, not a
    corner: a guardrail matches typed words far more often than an attachment.

    Only the LAST user message counts. An earlier question, or an answer above, can be withheld with
    this turn's question still standing, and re-running then is worth the call.
    """
    keys = {c.key for c in withheld}
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") in ("user", "human") and _has_text(m):
            return text_key(m) in keys
    return False


def _walk(messages: list[dict]):
    """Every (carrier, message) pair, undeduped and in payload order.

    Split out because `suspects` needs the message a carrier came from and `carriers` deliberately
    throws it away — one file read three times is ONE carrier, and which of the three messages it
    was is exactly what the dedupe exists to forget.
    """
    paths: dict[str, str] = {}
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        for call in m.get("tool_calls") or []:
            if (isinstance(call, dict) and (path := read_path_from_tool_call(call))
                    and (cid := str(call.get("id") or ""))):
                paths[cid] = path
    for m in messages:
        if not isinstance(m, dict) or m.get("role") in _NEVER:
            continue
        cid = str(m.get("tool_call_id") or "")
        # Two conditions that used to be one. Naming a carrier by its file is about being a TOOL
        # RESULT, while counting it as data is about where the material came from — and `is_data`
        # is now the wider of the two (#290). A user message has no `tool_call_id` so the file
        # branch could not fire on one anyway; it says `is_tool` because that is what it means.
        is_tool = m.get("role") == "tool"
        is_data = is_tool or _carries_mention(m)
        if is_tool and cid in paths:
            path = paths[cid]
            yield Carrier(file_key(path), os.path.basename(path) or path, True, True), m
        elif _has_text(m):
            yield Carrier(text_key(m), _text_label(m), False, is_data), m


def suspects(messages: list[dict]) -> set[str]:
    """Carrier keys a LOCAL scan flags, as a hint for `search`'s fast path — never as an answer.

    `refusal_scan` is asked one message at a time, through its own public `candidates`, so the rules
    stay in the module that owns them: nothing here parses its log line, and nothing here holds a
    second copy of a regex that has already needed correcting twice from live measurement.

    Local and free — regex over a payload already in memory, no gateway, no I/O. A miss costs the
    search nothing and a false hit costs it one call, which is the whole reason this is allowed to
    be a guess at all.
    """
    hit: set[str] = set()
    for carrier, message in _walk(messages):
        if carrier.key in hit:
            continue
        try:
            if refusal_scan.candidates({"messages": [message]}):
                hit.add(carrier.key)
        except Exception:  # pragma: no cover - a hint must never replace the failure it explains
            return hit
    return hit


def _carries_mention(message: dict) -> bool:
    """Does this user message carry an @mention's inlined descriptor, and not only prose?

    A file a person @mentions is not read by a tool. `describe` inlines its shape — column names,
    inferred types, and the vocabulary of any column that has a small one — into the PROMPT, so the
    material arrives as text in a user message and `surviving` counted it as prose (#290).

    Scoped to the user roles on purpose, which is the same population `prompt_withheld` reads. Those
    two ask DIFFERENT questions of one message — this one asks what the turn has to answer FROM, that
    one asks whether the turn's own question is going away — and a mention-bearing message is
    routinely both at once, because the descriptor is appended to the words the person typed. They
    agree by construction rather than by accident: withholding that message takes away the rows AND
    the question, so both say so, and the card neither re-runs nor claims a survivor.

    The person's prose stays prose, and it takes BOTH constants to say so. `MENTION_MARK` alone is
    not rare: `resources/bindings.py`'s `mention_note` opens with the same phrase and rides the
    same prompt text, so the unanchored form marked every Build turn that @mentioned a RESOURCE as
    data — a live case, not a hypothetical one, and the reason the pair exists. That note is
    correctly prose: it carries identities, and the rows a Resource yields arrive later as a live
    read's tool result, counted there. A turn that only typed is still counted the way #288 left it.

    What the pair rules out is a phrase produced INCIDENTALLY. It does not rule out a whole
    rendered prompt pasted back — "why did it say this?" carries the preamble and a `path:` entry
    together — and that message is marked data. Irreducible here: any marker a producer can write,
    a person can paste, and this layer sees only text. The harm is bounded and in the known
    direction (one card declines a re-run), so it is recorded rather than guessed at. Fixing it
    needs a signal that is not in the payload — the turn knowing what IT appended.
    """
    if message.get("role") not in ("user", "human"):
        return False
    text = content_text(message.get("content"))
    return MENTION_MARK in text and MENTION_PATH_LINE in text


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


def search(messages: list[dict], ask, *, cap: int = MAX_CALLS, hint=()) -> Found:
    """Which carriers the gateway is refusing, or as much of that as `cap` calls can prove.

    `ask(messages) -> BLOCKED | CLEAN | UNKNOWN` is the only thing here that talks to a gateway.
    `hint` is carrier keys somebody suspects, and it is spent on a FAST PATH rather than an
    ordering — see below for why ordering would buy nothing.

    Probes are ordered newest-first at every split, which is tidy and buys nothing. MEASURED: the
    cost is identical wherever the carrier sits — 4 files/7 calls, 8/9, 16/11, the same for every
    position. `_find_all` has to probe BOTH halves at every level because it finds all carriers
    rather than the first, so one half recursing and the other stopping clean is two probes per
    level whatever order they are in. Ordering only pays in a search that can stop early.

    Which is what `hint` buys instead: withhold the hinted carriers and ask once. A CLEAN answer
    ends the search at two calls, because a payload that comes back clean cannot still hold a
    carrier. Anything else falls through to the full search below, one call poorer and none the
    wiser — so a hint is free to be wrong, and cannot be right in a way that takes a file away
    without the gateway saying so.

    It can also be right and INCOMPLETE, which is the same fall-through: a SECOND carrier the scan
    did not flag leaves the confirming probe still BLOCKED, and the full search runs behind it. The
    only thing that causes that is a rule the scan gets wrong or does not have — `suspects` reads
    whether `candidates` found anything at all and discards the list, so neither its `_MAX_HITS` cap
    nor its one-match-per-pattern shortening can reach this: both shorten a non-empty answer and
    neither can make a match report as none.

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
    the right tool for that. Its rules now fence on a word boundary and carry the separated phone
    form, both corrected from live measurement after this paragraph first claimed otherwise — which
    is the point, not a footnote. They are a reading of somebody else's policy, they have been
    wrong twice, and the administrator who owns that policy can change it again without telling
    Sage. So the scanner may say who is asked FIRST; it must never decide the answer.

    So: hint from a scanner if you like, verdict from the gateway always. A recovery that takes a
    person's file away has to be right rather than probable, and the rule it would otherwise depend
    on belongs to an administrator who can change it without telling Sage.
    """
    found = Found()
    all_carriers = carriers(messages)
    found.total = len(all_carriers)
    found.total_data = sum(1 for c in all_carriers if c.is_data)
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

    # The fast path. Only worth a call when the hint names something this payload actually holds,
    # and never when it names everything — "withhold all of it and the refusal goes" is the floor
    # probe below, which proves the cause is reachable and nothing about which carrier it is.
    picked = [c for c in all_carriers if c.key in set(hint or ())]
    if picked and len(picked) < len(all_carriers):
        rest = [c for c in all_carriers if c not in picked]
        if probe(rest) == CLEAN:
            found.carriers = picked
            found.complete = True
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
