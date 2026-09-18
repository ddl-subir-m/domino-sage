---
status: accepted
---

# A Conversation's memory is rebuilt from Sage's own transcript, and the person is told

A person asked a question, was told a language model was missing, added one, and asked again. Sage
answered *"There's no Mixpanel question in our conversation yet — this is the first message you've
sent."* The Thread held nine events at that moment, `[0]`-`[8]`, beginning with their original
112-character question naming Mixpanel (#427).

Sage was not wrong. From the model's side the conversation **had** just started.

## The two copies, and why only one of them is real

| | where | lifetime |
|---|---|---|
| Sage's transcript | `.sage/threads/<id>/history.jsonl` — Project volume, committed | durable |
| the model's memory | `/home/ubuntu/.local/share/opencode/` — container overlay | **until the next restart** |

They are not two copies of one thing. They are one copy and one absence, because **the transcript is
never shown to the model.**

`_chat_prompt` (`service.py:10751`) takes `history=` and uses it in exactly one expression:

    carried = recall.seed(history or [])          # service.py:10803

`recall.seed` (`recall.py:174`) scans backwards and returns `""` at the first `user` row — *"a turn
already ran since the clear; the session is no longer new"* — and otherwise returns `""` unless the
newest non-`user` row is a `CLEARED` row scoped `SUMMARY`. On an ordinary turn it returns `""`.

So the nine events contributed **zero bytes** to that prompt, and contribute zero bytes to every
healthy prompt too. The ticket's fork was *"never passed"* versus *"passed and dropped"*. The answer
is neither: **never passed, by design, and never meant to be.**

The losing edge is `_ensure_thread_session` (`service.py:9229`). It tries the stored session id,
and on `httpx.HTTPStatusError` sets `sid = None`, falls through to `client.create_session(...)`, and
`store.write_session_id` overwrites the old id. No event, no warning, no log line. `XDG_DATA_HOME`
appears only in `backend/tests/` — four sites, all harnesses — so nothing redirects the store in
production.

**This is therefore not an incident. Every OpenCode restart is total, silent conversation amnesia.**
The reporter's *"re-pasting the question worked"* is not a quirk worth noting; it is the only
recovery that exists, and it is why this is under-reported rather than rare.

## The decision

**When Sage mints a new session for a Thread that already has history, it seeds that session with a
summary rendered from its own transcript, and says so in the conversation.**

Both halves, not either.

### The summary, because the machinery is already built and costs nothing

`chat_summary` already renders a transcript without a model call — chosen for exactly that reason
when it was built to carry Chat into Build (#53) — and it already drops tool calls as *"transcript
furniture"*. `recall.seed` already calls it, already reads the copy that outlived a session, and its
docstring already says why: *"it reads the transcript BEFORE the clear, which is the only copy that
still exists — the session it was said in is gone by now."*

That sentence was written about a clear. It is equally true of a restart. This decision widens the
same renderer to a second trigger rather than adding a mechanism.

**The implementation difficulty, stated so nobody mistakes this for a one-line change:** `seed`'s
guard keys on *turns since a clear* and bails at the first `user` row. The reset case keys on *the
session being new*, which is a different question about a different fact. The guard cannot be
loosened — it needs a second key, and `_ensure_thread_session` is the only place that knows a
session was freshly minted. That plumbing is the work.

### The notice, because a silent repair is the same defect one level quieter

A conversation that silently resumes from a summary is still a conversation whose state the person
cannot see. They will ask a follow-up that depends on a detail the summary dropped and get a second
confusing answer, with less to go on than before — the summary makes the seam harder to notice, not
easier.

So the turn says the memory was rebuilt from a summary. The person can then re-state what matters,
which is the thing they already do blindly.

## What this rejects, and why it is the first thing anyone will try

**Pointing `XDG_DATA_HOME` at the Project volume so OpenCode's sessions survive.** It looks free. It
is the reason this is an ADR.

OpenCode's DB holds every turn's **full tool results** — file bytes, command stdout, base64
attachments, whole prompts. Measured at 26.5 MB with a single prompt event of 4,068,816 bytes, with
no cap, no rotation and no Sage delete path. ADR-0036 (*"A deleted Conversation takes its transcript
with it"*) already cannot reach it.

So the ephemerality reported as the defect **is currently the only thing bounding that store's
lifetime.** Making the memory durable that way would move an uncapped store of real data rows onto
the Project volume and make ADR-0036's promise worse, in order to fix amnesia. The bug and a privacy
property nobody wrote down are the same mechanism, and this is the option that trades the second for
the first without noticing.

Two others were considered and rejected:

- **Replaying the full transcript into the new session.** Faithful, and it walks into ADR-0032:
  synthesised history carrying tool calls is the unsigned/mixed-model case that hard-400s a request.
  `chat_summary`'s dropping of tool calls is not a limitation here, it is the reason it is safe.
- **Carrying history in the prompt on every turn.** Makes Sage the source of truth and charges every
  turn for it, against live work on call count and turn latency (#400, #417).

## What this does not do

**It does not make the memory faithful.** A summary is lossy on purpose. The person is told, which is
the trade: continuity restored without making the sensitive store durable, and the summary is
rendered from Sage's transcript — already governed by ADR-0036 and ADR-0041 — rather than from
OpenCode's DB.

**It does not bound `opencode.db`, and once this lands nothing else does either.** The containment
described above is accidental: it exists because sessions die, not because anyone decided they
should. This decision stops relying on it without replacing it, so the store's lack of a cap, a
rotation and a delete path becomes load-bearing where it was previously masked. **That is a
consequence of this decision and not a part of it**, and it is filed separately rather than settled
here.

**It does not establish that a Domino workspace restart wipes the store.** The mechanism, the means
and an exact symptom match are all present; the receipt is not, and no longer exists. The codebase
documents and defends against precisely this condition in `_recover_session` (`service.py:6646`),
and #417 measures OpenCode boots as routine. That is strong and it is not a measurement.
