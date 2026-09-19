"""Recovering a Conversation whose Recall the gateway keeps refusing (ADR-0022).

Recall is what the model has been told about this Conversation so far (CONTEXT.md). It lives in the
OpenCode session, not in `history.jsonl`, so it holds what the person never sees: the contents of
files a turn read. When the gateway refuses one of those contents, it refuses every later turn too,
because every later turn sends them again. Nothing the person can reach is wrong, and nothing they
can do makes it right.

Everything here is DERIVED from the transcript, the way `service._turn_revert` is derived rather
than stored. The transcript already records refusals, and clearing Recall writes an event of its
own, so the rung of the ladder is a question the transcript can answer. A stored counter would be
one more thing to fall out of step with what actually happened, and it would not survive a Sage
Builder restart — while the poison would, since `_recover_session` reads the session id back off
disk.

The ladder has three rungs and then stops:
  1. Two identical refusals   -> offer to clear Recall, seeded with what was said.
  2. Refused again after that -> the seed carries it too; offer to clear Recall completely.
  3. Refused again after that -> it is in the message just typed, or the file it names. Say so and
     stop offering, because there is nothing left that clearing can reach.
"""
from __future__ import annotations

import re

from .chat_compact import chat_summary

CLEARED = "recall-cleared"
SUGGEST = "recall-suggest"

SUMMARY = "summary"   # cleared, but told what was said
EMPTY = "empty"       # cleared, told nothing

# The rungs BELOW clearing, added once the gateway could be asked what it matched rather than
# guessed at (see `withhold.py`). Clearing Recall empties it; these take away one named thing and
# leave the Conversation standing, so they are tried first and `offer` catches what they cannot
# reach. All three are written on Chat and Build alike, into whichever transcript owns the turn.
SEARCH = "withhold-search"      # a search is running; the card shows a spinner
FOUND = "withhold-found"        # the search finished, with or without an answer
WITHHELD = "recall-withheld"    # the person said yes; this content is no longer sent
REBUILT = "recall-rebuilt"      # the session was gone; what it held was rebuilt from the transcript


def withheld(history: list[dict]) -> frozenset[str]:
    """Everything this Conversation has stopped sending, derived from the transcript.

    Derived rather than stored, for the reason the whole module is: the transcript survives a Sage
    Builder restart, and so does the poison — `_recover_session` reads the session id back off disk,
    so a withhold held only in memory would let a restarted Conversation refuse all over again.

    The rows carry fingerprints, never the refused text (`chat_paths.text_key`). That matters more
    here than anywhere else in Sage: what is being written down is the thing a policy just refused
    to move.
    """
    keys: set[str] = set()
    for row in history or []:
        if isinstance(row, dict) and row.get("type") == WITHHELD:
            keys.update(str(k) for k in (row.get("keys") or []) if k)
    return frozenset(keys)

# The gateway's own words for a guardrail refusal, which `service._guardrail_sentence` also reads.
_GUARDRAIL = re.compile(r"Blocked by guardrail:\s*([^\"'}\\]+)")

_KEY_CHARS = 200


def reason_key(raw: str) -> str:
    """A refusal's identity, stable across turns — NOT the sentence shown on screen.

    The shown sentence names the Attachment of the turn that failed, and the whole point of the
    ladder is that the NEXT turn fails too, on a different Attachment. Live, a JSON file was refused
    and then a CSV holding nothing that could match was refused after it; keyed on the rendered
    prose those two are different refusals and the offer never appears. Keyed on what the gateway
    said, they are one refusal happening twice, which is what they are.
    """
    text = " ".join((raw or "").split())
    match = _GUARDRAIL.search(text)
    if match:
        return f"guardrail:{' '.join(match.group(1).split()).strip(' .;:')}"
    return text[:_KEY_CHARS]


def _errors(history: list[dict], key: str) -> list[int]:
    return [i for i, e in enumerate(history or [])
            if isinstance(e, dict) and e.get("type") == "error" and e.get("reason") == key]


def _last_refusal(rows: list[dict]) -> tuple[int, str] | None:
    last = len(rows) - 1
    while last >= 0 and rows[last].get("type") in {SEARCH, FOUND}:
        last -= 1
    if last < 0 or rows[last].get("type") != "error":
        return None
    key = str(rows[last].get("reason") or "")
    # A repeated transport failure says nothing about the content in Recall. Older histories
    # also carry these keys, so filter when reading rather than only when recording new errors.
    return (last, key) if key.startswith("guardrail:") else None


def offer(history: list[dict]) -> str | None:
    """Which clear to offer for the refusal the transcript ends on, or None to offer nothing.

    None covers three different situations on purpose: the turn did not fail, it failed once (one
    refusal is noise — a blip must not be answered by throwing context away), or both rungs have
    already been used and the value is somewhere clearing cannot reach.
    """
    rows = [e for e in (history or []) if isinstance(e, dict)]
    refused = _last_refusal(rows)
    if refused is None:
        return None
    last, key = refused
    seen = _errors(rows, key)
    if len(seen) < 2:
        return None
    # Only what happened BETWEEN this refusal and the last identical one. A clear that came before
    # the previous refusal was already answered by it, and a Conversation that recovers and is
    # refused again later starts the ladder over rather than opening on its last rung.
    window = [e for e in rows[seen[-2] + 1:last] if e.get("type") == CLEARED]
    if not window:
        return SUMMARY
    return None if window[-1].get("scope") == EMPTY else EMPTY


def offer_now(history: list[dict]) -> str | None:
    """Which clear to offer on a SINGLE refusal, for a caller whose one refusal is evidence enough.

    `offer` waits for two because one may be a blip, and clearing costs the model everything it has
    been told. The Chat → Build handoff is the exception, and it is the reason this exists: it is
    not an ordinary turn. It is a click made deliberately, on a card already on screen, and it
    plans in the Thread's OWN session — so a refusal there is a fact about the Conversation rather
    than about one request. Making that click a throwaway, purely to reach a rung the next click
    would reach anyway, is a wasted failure in front of somebody who has already had one.

    Every other rule is `offer`'s, unchanged, which is the point of sharing the window: which rung
    comes next, and when to stop offering, are decided the same way on both paths. The only
    difference is the count required to open the ladder at all.

    NOT a general loosening. `offer` stays as it is for Chat turns and Build turns, where a single
    failure really can be a blip and the person has lost nothing by trying again.
    """
    rows = [e for e in (history or []) if isinstance(e, dict)]
    refused = _last_refusal(rows)
    if refused is None:
        return None
    last, key = refused
    seen = _errors(rows, key)
    # Since the previous identical refusal, exactly as `offer` measures it — or since the start of
    # the Conversation, when this refusal is the first of its kind and there is no previous one to
    # measure from.
    start = seen[-2] + 1 if len(seen) >= 2 else 0
    window = [e for e in rows[start:last] if e.get("type") == CLEARED]
    if not window:
        return SUMMARY
    return None if window[-1].get("scope") == EMPTY else EMPTY


def terminal(history: list[dict]) -> bool:
    """True when the transcript ends on a refusal that survived a complete clear.

    The message owes the person a different sentence here: not "clear Recall", which they have
    already done to no effect, but where the value must therefore be.
    """
    rows = [e for e in (history or []) if isinstance(e, dict)]
    refused = _last_refusal(rows)
    if refused is None:
        return False
    last, key = refused
    seen = _errors(rows, key)
    if len(seen) < 2:
        return False
    window = [e for e in rows[seen[-2] + 1:last] if e.get("type") == CLEARED]
    return bool(window) and window[-1].get("scope") == EMPTY


def seed(history: list[dict]) -> str:
    """What the first turn after a clear carries, or "" when this is not that turn.

    A summary-scoped clear promised the model would keep "a short summary of what was said", and
    this is where that promise is kept. It reads the transcript BEFORE the clear, which is the only
    copy that still exists — the session it was said in is gone by now.

    `chat_summary` is what renders it, unchanged from the job it already does carrying Chat into
    Build (#53): it needs no model call, which matters because the gateway is refusing this
    Conversation, and it drops tool calls as "transcript furniture", which is where the refused
    value came from in the first place.
    """
    rows = [e for e in (history or []) if isinstance(e, dict)]
    for i in range(len(rows) - 1, -1, -1):
        kind = rows[i].get("type")
        if kind == "user":
            return ""            # a turn already ran since the clear; the session is no longer new
        if kind == CLEARED:
            if rows[i].get("scope") != SUMMARY:
                return ""
            return chat_summary(rows[:i])
    return ""


def reseed(history: list[dict]) -> str:
    """What a freshly minted session is told this Conversation already holds (ADR-0060).

    `seed` above answers "did the person just clear Recall". This answers a different question about
    a different fact: the session this Thread was talking to is GONE. OpenCode's store sits on the
    container overlay and dies with the workspace, while `history.jsonl` sits on the Project volume
    and does not — so `_ensure_thread_session` mints a new session, the model starts at nothing, and
    the person goes on reading a full transcript (#427). Only that caller knows a session was just
    minted, which is why nothing here tries to infer it from the rows.

    Deliberately NOT built on `seed`'s guard, and this is the whole reason it is a separate
    function. That guard returns "" at the first `user` row, and the caller appends the current
    turn's `user` row (`service.py:10955`) before reading the transcript back (`:11408`) — so on any
    ordinary turn it already returns "" and always has. Widening it would have hung this decision on
    a branch that has never run in production. Filed separately rather than fixed here, because
    making it fire would start seeding the summary-scoped clear for the first time, which is a live
    change to the recall ladder (ADR-0022) that ADR-0060 did not decide.
    """
    rows = [e for e in (history or []) if isinstance(e, dict)]
    # The turn being assembled has already written its own `user` row, and this prompt ends with
    # that same text. Summarising it as well would hand the model its question twice — once as
    # something already said, once as the thing being asked.
    if rows and rows[-1].get("type") == "user":
        rows = rows[:-1]
    # EITHER scope, and the scope is deliberately not read. `clear_recall` calls
    # `ThreadStore.clear_session_id` OUTSIDE its `if scope == recall.EMPTY:` block, so a clear of
    # any kind is carried out by dropping the session — which means the next turn mints one and
    # lands here BY CONSTRUCTION, for a loss that was requested rather than suffered.
    #
    # Truncating at the newest clear of either scope is what keeps this decision to its subject.
    # Reading the scope and carrying a SUMMARY clear whole looks right — that rung did promise a
    # summary survives — and is wrong twice over: it answers "start over, keep the gist" by shovelling
    # the WHOLE pre-clear transcript into the fresh session, and it hands the ladder's softer rung
    # a fresh chance to re-poison a Conversation the person cleared to escape a refusal. Keeping
    # that promise is `seed`'s job, it has never actually done it (#432), and turning it on here
    # would be ADR-0022's decision made silently inside ADR-0060's.
    #
    # Rows said AFTER the newest clear are carried: nobody asked to forget those, and losing them
    # to a restart is the thing this function exists for.
    for i in range(len(rows) - 1, -1, -1):
        if rows[i].get("type") == CLEARED:
            rows = rows[i + 1:]
            break
    return chat_summary(rows)
