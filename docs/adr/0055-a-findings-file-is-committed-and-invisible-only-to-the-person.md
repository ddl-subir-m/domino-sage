---
status: accepted
extends: ADR-0045 (an Artifact commits the shape and the rows only by consent — the same question,
         asked of a file that is not an Artifact and spawns no card)
---

# A findings file is committed, and invisible only to the person

A Chat investigation that takes more than one turn keeps what it has measured in
`.sage/threads/<threadId>/findings.md`. The model writes it, the model reads it back, and nothing
on screen says it is there. It is committed with the rest of the Project and pushed to a remote
Sage has never read the URL of.

That is the whole of what this record is for. ADR-0045 is the rule that a file the model writes
does not reach git on the model's say-so, and this file does. Shipping that without writing it
down is the thing ADR-0045 exists to stop, so it is written down here, both halves, including the
half that does not reassure.

## What the file is

One Thread, one file, appended across turns. `template/chat/AGENTS.md:120` ("Keeping findings
across turns") is the only place the model is told about it, and #380 is what made it reachable:
only **this** Thread's record directory is linked into the turn's cwd, stale links are pruned on
the way in, and a symlink standing at the link site is cleared first. The turn cannot open a
sibling Thread's records through it.

## Why rows do not land in it

**The rule is in the pinned prompt, not in a filter.** `template/chat/AGENTS.md:126-131` says
aggregates and column facts only — counts, rates, ranges, distinct-counts, column names — and
bans any value copied out of a row: no identifiers, no names, no exemplars. Every entry carries a
UTC timestamp, the statement that produced it, and the number **with its denominator**. That form
ages visibly; a bare conclusion ages silently (§2.3 of #378, the same reasoning ADR-0041 applies
to Live read).

**Nothing enforces it after the fact, and the obvious candidate does not reach.**
`withhold_table_rows` (`backend/sage/workspace/threads.py:938`) is the pass that strips rows out
of what a turn committed. It roots at `root / "examples" / <threadId>` and `rglob`s
`*.table.json` under it. A file at `.sage/threads/<threadId>/findings.md` is outside that root and
carries the wrong suffix twice over, so that pass does not govern it and was never going to.

The rule is therefore a prompt rule held by a model, which is weaker than a filter and is being
recorded as such rather than described as "handled".

## It is committed, and pushed

`_PROJECT_IGNORE` (`backend/sage/workspace/manager.py:223`) keeps `.sage/scratch/`, the Chat
workdir, `.sage/threads/*/.*.tmp` and the upload ledger's half-written twin out of git. It does
not name `findings.md`, and nothing else does either. So the file is committed by the ordinary
`git add -A` every Chat save runs, and pushed with everything else — to a remote whose URL Sage
has never read.

This is not a new class of hole. `template/chat/AGENTS.md:106` already blesses `<slug>.sql`
saved next to a result, and `new_artifact_paths` commits any extension it finds under
`examples/<threadId>/`. What is new is the **purpose**: an incidental `.sql` is one turn's
by-product, while a file whose stated job is to accumulate data facts across turns industrialises
the same reach. That difference is the reason this record exists and the earlier ones did not need
one.

## "Invisible" means no artifact card. It does not mean unseen.

`new_artifact_paths` (`backend/sage/workspace/threads.py:1066`) collects only paths starting
`examples/<threadId>/`. `findings.md` never matches, so no card is drawn, the rail never lists it,
and the person is never shown that it exists.

**It is committed and pushed all the same.** Invisible to the person, visible to whoever holds the
remote. Those are two different properties and this record does not let them blur: the file is
hidden from exactly the party who could judge whether its contents should leave, and visible to
exactly the party Sage cannot see.

## Deleting it

**Inherited, for a deleted Conversation.** `purge` (`backend/sage/workspace/threads.py:460`)
removes every file in the Thread's record directory except the tombstone (ADR-0036). `findings.md`
is one of them and goes without being named — no exemption, no new code.

**Deliberate, for a complete Recall clear.** A clear appends `recall.CLEARED` and the model
restarts from `recall.seed` alone (`service.py:9234`, `:9261`). Dropping the OpenCode session used
to be the whole of what a turn is told; it is not any more, because the turn prompt points the
model at this file before it plans (#381). A clear that left it standing handed the
freshly-cleared model every measurement back on its next turn — a clear that did not clear,
against ADR-0022.

So `clear_recall` (`service.py:9240`) unlinks the file when — and only when — the scope is
`recall.EMPTY`:

| Scope | Session | `findings.md` |
|---|---|---|
| `recall.EMPTY` (complete) | dropped | **deleted** |
| `recall.SUMMARY` (seeded) | dropped | kept |

The asymmetry is the decision, not an oversight. A complete clear is the person saying *start
over* and it has to mean it. A summary-scoped clear trims talk and seeds the model with what was
said — and a measurement log is not talk, so taking it there would throw away work nobody asked to
lose on the softer of the two rungs.

## The cap is a refusal, not a trim

**Not yet in the tree.** #381 builds it; this record fixes its shape so the number is decided
once. It caps the file at **32 KB, enforced at turn end, with overflow refused rather than
trimmed**. Refusing is the point: a trim leaves a runaway silently half-recorded and still looking like a
complete log, while a refusal is visible and says which turn did it. A file that is quietly the
wrong size is worse than one that stopped.

## The continuation rule is a scope decision, not only a latency cost

**Also #381, also not yet in the tree** — `READ_ONLY_DENIED` is unconditional today, so an
investigation still loses its shell on the turn after it starts. The decision recorded here is
what #381 implements: once a Thread has a `findings.md`, every later turn in it keeps `bash`
(#381, Step 4). Without that, a `data_answer` turn is armed read-only and `READ_ONLY_DENIED = WRITE_TOOLS | SHELL_TOOLS`
(`backend/sage/router/phase_classifier.py:48`) takes the shell with it, so the investigation loses its only route to Snowflake mid-flight —
`live_read_table` accepts no SQL.

§2.6 of #378 prices this as latency. **It is also a scope decision, and that is the half a future
reader will come here for.** #364 bounds a Chat turn to the tools its intent needs. A Thread
holding this file is bounded no longer, for every turn it has left, and the model may open that
door **unprompted** — nothing asks the person before the first `findings.md` is written.

**It is not an escape hatch.** `write_chat_artifact` (`service.py:9831`) refuses any path outside
`examples/<threadId>/` and any extension but `.png` or `.table.json`, and a `data_answer` turn
holds no write tool at all. Only a turn that was already unbounded can create the file in the
first place, so the rule widens what an unbounded Thread keeps — it does not hand a bounded one a
way out.

Both halves stand. The second does not cancel the first: within a Thread, #364's bounding is
defeatable, by a decision the model makes and the person is not shown.

## Consequences

- The aggregates-only rule is a prompt rule. If it is broken, it is broken silently and the
  result is committed. A filter that could catch a row in prose does not exist and is not
  proposed here.
- **Neither delete reaches what is already committed.** Both doors unlink the working-tree copy.
  A `delete` commits and pushes that removal itself (`_flush_chat_save`); a clear does not — it
  rides the next Chat save, so a person who clears and never comes back leaves a HEAD that still
  carries the file. Either way every earlier commit carries the full log — `git show
  HEAD~1:.sage/threads/<threadId>/findings.md` returns it — and it is already on the remote, put
  there by the `chat (…)` commit that follows the turn that wrote it. This
  is [ADR-0046](0046-a-delete-reaches-the-rows-only-where-they-never-entered-git.md)'s rule
  arriving at a new file, and it is the reason this record does not say "the measurements are
  gone". What a complete clear guarantees is that the NEXT TURN is not handed them; what it does
  not touch is history. Nothing here untracks the path or rewrites it, and this record does not
  propose that.
- Neither door is labelled as being about this file, because the file is not shown. The
  `recall.CLEARED` row carries the scope and nothing about the findings, so the transcript cannot
  answer "where did the measurements go" either. Accepted for now on the grounds that the person
  was never told the file existed, and rejected as a reason to soften the clear.
- **A turn streaming in the same Thread can put the file straight back.** `clear_recall` takes no
  turn lock (its Build sibling does), and the offer card stays clickable while a later turn runs.
  The unlink lands, the running turn — which read the file at its start — appends it again, and
  the Conversation is cleared with its measurements restored. Reproduction: get refused twice, ask
  something else, then click *Clear Recall completely* while that turn streams. The session half
  does not have this exposure, because `session.json` is rewritten only when a session is CREATED
  (`service.py:8926`), not at turn end. Open, not handled: closing it means giving this door the
  turn lock, which changes what it may answer on both scopes and is a decision this record does
  not make.
- Anything that later exempts a path from `purge`'s "everything but the tombstone", or adds a
  `.sage/threads/` entry to `_PROJECT_IGNORE`, changes this record's answers. Both are one-line
  edits a long way from here.
