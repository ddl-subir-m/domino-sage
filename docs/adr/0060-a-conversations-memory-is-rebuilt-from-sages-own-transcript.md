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

## Amendment, 2026-09-18: the seed is a second function, and a clear is a session drop

Two things were wrong in the sections above. The decision is unchanged; the implementation note
under *"The summary"* and an unstated hazard are corrected here.

**`recall.seed` is not widened. `recall.reseed` is built beside it.** The note above said the guard
*"needs a second key"*. It does not, because **`seed`'s guard has never executed in production**.
The caller appends the turn's own `user` row (`service.py:10966`) before it reads the transcript
back (`:11419`), so `seed`'s backward scan meets a `user` row first and returns `""` on every turn
there has ever been. Widening it would have hung a live decision on a branch nothing runs, and
would have made that branch fire for the first time — seeding a summary-scoped clear, a live change
to the recall ladder (ADR-0022) that this ADR did not decide and has no business making as a side
effect. That change is filed separately (#432). `reseed` answers its own question — *this session
was minted a moment ago and holds nothing* — and shares only `chat_summary`.

`_ensure_thread_session` (`service.py:9229`) therefore returns `(sid, minted)` rather than `sid`,
which is the plumbing this ADR called *"the work"*. That part was right.

**A clear is carried out by dropping the session, so the rebuild path fires on the very next turn
by construction.** `clear_recall` calls `store.clear_session_id(thread_id)` (`service.py:9657`) and
then appends the `CLEARED` row. The next turn finds no session, mints one, and is `minted=True` —
so an unguarded `reseed` would answer *"forget this"* by handing the model a summary of exactly
what it was asked to forget. The seam this ADR was written to repair and the mechanism a clear
already used are the same mechanism, and nothing above noticed.

The guard: `reseed` truncates at the newest `CLEARED` row **of either scope**, and carries only
what was said after it. `reseed` also drops the turn's own trailing `user` row, without which the
model is handed the question twice: once as the thing already said, once as the thing being asked.

**Corrected on the same day this amendment was written.** It first said a `SUMMARY`-scoped clear was
carried whole, on the reasoning that the rung promises a short summary survives. That shipped, and
review found it wrong twice over. `clear_recall` calls `clear_session_id` **outside** its
`if scope == EMPTY:` block, so a clear of either scope drops the session and lands here — meaning
reading the scope answered *"start over, keep the gist"* by shovelling the **whole** pre-clear
transcript into the fresh session, and handed the softer rung a fresh chance to re-poison a
Conversation the person cleared in order to escape a refusal. Keeping that promise is `seed`'s job,
`seed` has never done it (#432), and switching it on here would have been ADR-0022's decision made
silently inside this one — the exact thing the paragraph above claims to avoid.

**What this amendment does not settle.** ADR-0022's `EMPTY` rung still prints its divider on a turn
where the carried text is now empty, and #432 records that a summary-scoped clear promises a
summary and sends none. Both are the recall ladder's, not this decision's.

## Second amendment, 2026-09-18: the trigger is a committed session id, not a restart

The reporter has since said they were **in the same conversation and the workspace had never
restarted**. That refutes the mechanism this ADR leads with. The section above already refused to
claim it — *"It does not establish that a Domino workspace restart wipes the store"* — so the
caveat was right and the narrative around it was not.

**The general mechanism is that the session id is committed to git.** `_PROJECT_IGNORE`
(`manager.py:257`) ignores `.sage/scratch/`, `.sage/chat-work/`, `.sage/threads/*/.*.tmp` and the
upload ledger's staging twin. It does **not** ignore `.sage/threads/<id>/session.json`, which
`ThreadStore.write_session_id` (`threads.py:252`) writes. `git check-ignore` on that path exits 1.

So a Thread's OpenCode session id is **durable, versioned, cloned state that names a
container-local object**. It rides a clone into a container that has never heard of it, and
`client.messages(sid)` 404s on the **first turn there** — measured directly against opencode-ai
1.18.4 on 2026-09-10: an unknown session id returns 404 on `GET /session/{id}`,
`GET .../message` and `POST .../message`.

A restart is therefore one way in, and the narrow one. The wide one needs **no restart at all**: a
new workspace on an existing Project is amnesiac on its first turn, by construction, while the
transcript clones back in full beside it. That is why `/api/threads/<id>/history` returned nine
events and `_warn_if_history_lossy` stayed quiet — **both of the reporter's ruled-out checks read
the half that survives.**

This makes the defect **more** severe than stated above, not less. *"Every OpenCode restart is
total, silent conversation amnesia"* should read: **every container that did not personally mint
the session id inherits a dead one, and a committed id guarantees it will find one to inherit.**

**A third path was found after this was written, and it needs no clone either.** The catch at
`service.py:9258` is a bare `except httpx.HTTPStatusError`, and `client.messages` raises that
for every 4xx **and every 5xx**. A transient 503 or 429 from OpenCode is therefore read as
*"this session does not exist"*, and the recovery overwrites `session.json` — so a live
container can detach a Conversation from a session it minted itself, permanently. That is #434,
and it is the only mechanism found so far that explains memory lost mid-life in a workspace that
never restarted and never cloned.

**The decision does not change, and neither does the fix.** `_ensure_thread_session` returns
`minted=True` on both paths — the 404 and the directory mismatch — so `reseed` fires for this case
as designed. What changes is the population: this is the ordinary path, not the rare one.

**A question this raises and does not settle:** whether `session.json` should be in
`_PROJECT_IGNORE` at all. A container-local handle kept in version control is the thing that
carries the corpse from one container to the next; ignoring it would make a clone mint cleanly
rather than probe a dead id, and `minted` would still be true, so the seed would still run. It is
not free — `clear_session_id`'s contract and the *"there WAS one and OpenCode does not have it"*
log line both read that file — and it is filed as #433 rather than decided here.
