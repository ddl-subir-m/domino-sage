---
status: accepted
extends: ADR-0055 (the findings file — the same question, asked of a record the model writes),
         ADR-0060 (a Conversation's memory is rebuilt from Sage's own transcript — the same
         principle applied to what was said, where this one applies it to what was read)
---

# A turn is handed what earlier turns already read

A Chat turn is told which sources earlier turns in this Thread read, and where each result landed.
It is rendered from records Sage already keeps — the `data_used` events in the Thread's history and
the Artifacts under `examples/<threadId>/` — and **nothing new is written to disk for it.**

That is the whole of the decision. The rest of this record is the three alternatives that were
rejected, because each one is the thing a reader will propose again.

## What was measured

Workspace `sage-subir-mansukhani-66a821b1`, `sage_rev = d93485d`, Thread `thr_1a0b…a72a2`,
2026-09-19. An accepted investigation ran **10 turns, 53 live reads, 25 model calls** and never
advanced past turn 2. Turns 4-10 re-reported byte-identical numbers. **Every one closed
`{"ok":true,"decision":"answered"}`** — #442.

Three separate instructions told the model to keep its measurements in `findings.md`: the static
`sage-chat` agent prompt, the `investigate-weak-signals` skill, and `_findings_note` in the turn
prompt. The file was **404 after ten turns** — #443.

The answer to the person's question was on screen the whole time, in a card turn 2 published.

## Why Sage does not write the findings itself

The obvious fix — *Sage already holds the numbers, let it append them* — is **false**, and the code
says so in a comment written for a different reason.

`run.py:608` records `source_sha256`, the **hash** of the statement, never the statement: *"The
event is persisted into the Thread's history, which is committed, and a statement carries
literals."* And the measurement itself — `16,756/89,399 (18.7%)` — is a **value inside a result
cell**, which `verdict.discloses` decides whether the model may even see.

So Sage holds the **provenance of a read**, not the finding. A Sage-written findings file could only
contain what Sage can say truthfully, which is *this was asked, of that source, and the answer is in
that file*. **ADR-0055 therefore stands unreversed**, and ADR-0045 is untouched: no new class of
content reaches git because of this record.

## What is rendered, and why it takes two lists

Neither existing record covers every read:

| read | `data_used` event | result Artifact |
|---|---|---|
| `live_read_query` | yes (`run.py:600-624`) | yes |
| `live_read_files` (sum, analyze_text) | yes (`calculate.py:153`, `text_analysis.py:138`) | yes |
| `live_read_table` | **no** — `_table` (`run.py:292-319`) never calls `record_data_use` | yes |

So the turn prompt gets both, and they say different things. **"Already read in this Thread"** is
one line per source — the source, the most recent result Artifact, and how many turns touched it —
walked from history's `dataUsed` rows, capped at 20 lines, newest first. It reuses
`data_use_summaries`' walk (`handoff.py:394`) and **not** its formatter: `_data_use_line` builds up
to twelve column names into every line, which serves the Build handoff and not this.

**"Already written in this Thread"** keeps its list and gains one clause: reading an Artifact is
always free. The sentence previously read *"change one only if asked"*, and the measured failure is
a turn that was told a card existed and never opened it. The prohibition is unchanged; only the
permission that was missing is added.

## It is gated on data, never on a flag

`investigating` is a live local at `service.py:11363` and the `_chat_prompt` call is at `:11792`, so
the prompt could be told whether an investigation is open without a second read.

**It is not told, and must not be.** The block renders only when there are prior reads, which is
self-limiting: a Thread with none renders nothing, and a first turn pays nothing. Gating on
`investigating` instead would reproduce #443's own defect one level up — an instruction whose
audience is decided by a state, where the turns that need it most are the ones a mis-set flag
excludes. A condition that describes the data is checkable; a condition that describes the Thread's
mode is another thing to keep true.

## A turn says whether it advanced

`finish()` (`service.py:11243-11260`) is the Chat turn's terminal seam — it already stamps
`resolved_row()` and `dataUsed` onto the row on its way to history. It also stamps:

- **`reads`** — the set of result-Artifact paths this turn wrote.
- **`advanced`** — false when every path in `reads` was already written by an earlier turn in this
  Thread, and the turn wrote at least one.

The Artifact path is the unit because it is the only one with full coverage and it is stable under
repeat **by construction**: `result.record` names the file `f"{slug}.table.json"` (`result.py:39`)
with no uniquifier, so the same read run twice writes the same path. The source name is not usable
for this — `_source()` (`run.py:263`) returns `{}` when there is no Binding, so a read reached
through a Session chip alone records no source anywhere.

**The guard is load-bearing.** An empty set is a subset of everything, so without *"wrote at least
one"* every ordinary conversational turn would report `advanced: false`.

**`advanced` is stamped on every turn, never only when it fires.** A field present only on failure
cannot tell a turn that passed from a turn that ran before the field existed.

**No new `decision` value.** `answered` is left alone. #435 measured what a new `decision` costs:
`store.js` keys `NO_PLATFORM_FAULT` and `ASKED_FOR` on it, and a value missing from either list
regresses in silence. `advanced` is a field beside the decision, not a rival to it.

**The verdict is not fed back into the prompt.** It is computed at turn end and could only reach the
*next* turn, which is already being handed the read log — the part it can act on. Telling it that
the previous turn wasted itself is a judgement, and this block carries evidence with no verb, so
that a turn asked to re-check whether the warehouse moved is never argued out of doing it.

## A complete clear takes it

`recall.seed` returns `""` on the first turn after a complete clear, and `service.py:11116` says why:
*"being told nothing is the whole point."* ADR-0055 deletes `findings.md` on that same clear.

So the read log is bounded at the last `recall.CLEARED` row whose scope is `EMPTY`. A
summary-scoped clear leaves it whole, on ADR-0055's own reasoning: a measurement log is not talk,
and neither is a read.

## Consequences

- **The Artifact list is not bounded by a clear, and this record makes that reachable.**
  `_artifacts_present` has never respected `recall.CLEARED`, so cards from before a complete clear
  are named in the prompt today. That was inert while the sentence said *change one only if asked*;
  the clause added above invites the turn to open them. The gap is pre-existing and the fix is a
  behaviour change to a shipped surface — a turn that no longer knows a card exists may rewrite it —
  so it is **filed as its own issue by the change that lands this** — **#446** — not fixed here
  and not left unsaid.

- **`live_read_table` still emits no `data_used` event.** The Artifact list covers it, so nothing is
  invisible, but the richer line is unavailable for the most common read. Making `_table` record one
  would give a single record with full coverage at the cost of more rows in committed history —
  ADR-0045's question, and not asked here.

- **Nothing records whether a skill was loaded.** It cannot be established from Sage's own logs
  whether `investigate-weak-signals` reached the model on those ten turns. *"The method never
  arrived"* and *"the method arrived and was ignored"* want different fixes and stay
  indistinguishable. Out of scope, named so the next reader does not re-derive it.

## What this does not decide

Telling the **person** that a turn advanced nothing, and giving `decision` a value for it, are both
deferred on purpose. Neither can be priced until `advanced` has run on real Threads — and the rate
that matters is the one **after** this record ships, because a repeat then means Sage told the turn
what it had already read and it repeated anyway, which is a different and rarer event from the one
that was measured.

#378 — building the investigation feature — is untouched by this.

## A completed Chat turn must produce a result (#557, 2026-09-25)

An answer is nonempty final text or a validated, visible answer artifact. Tool activity and
artifacts marked `working` do not suffice. A terminal provider error takes precedence over
partial text. The existing session may make one recovery attempt, within the original deadline
and permissions. This allowance is shared with table repair, not added to it.

If recovery still leaves no result, Chat records `ok: false`, `decision: "empty answer"`, and
`advanced: false`, plus a clear error in the live stream and saved history. The Workbench treats
this as a failed answer, not a gateway fault. Successful conversational answers retain the
existing `advanced` rule. This does not detect repeated numerical findings or prove factual
correctness; those are separate from whether the turn produced a result.
