---
status: accepted
---

# The gate that widens the question is asked before the one that narrows it

Two gates fire in `_chat_stream`, in this order:

| line | gate | asks |
|---|---|---|
| `10839` | `_chat_table_offer` | **which one table?** single select |
| `10946` | `_chat_investigation_offer` | shall I look **across several**? |

The first returns and ends the turn, so the second is only ever reached on the replay after a
table has been chosen. A prompt the system is about to describe as spanning several sources is
first put to the person as *"Pick the Table to use"*. Both gates read the same sentence and hold
opposite premises about it.

The ordering is not the mistake, and #392 was right to defend it. The comment at `:10570` gives
the constraint:

> After the classifier, unlike the two above it, because "would this turn be bounded" is a fact
> about `intent.label` and there is no cheaper way to know it.

The investigation gate needs the classifier. The table gate does not. Reordering unconditionally
makes every table card wait on a model call, on every turn, for a population that is almost
entirely not investigative.

## What was measured

**The pick binds nothing on the investigative lane.** Live on cloud-dogfood, twice — 2026-09-17
(`84fea32`) and 2026-09-18 (`6c933ab`). The ranker found `MIXPANEL__EVENT`, `MIXPANEL__PROFILE`,
`GONG__TRACKERS`, `GONG__CALL_TRANSCRIPTS`, `SFDC__ACCOUNT`, `SFDC__OPPORTUNITY` — it identified a
three-system question correctly — and then asked the person to choose one of them. The pick was
`MIXPANEL__EVENT`. The replayed turn ran 14 and 41 `bash` calls respectively and returned a table
titled *"DMM Customer Activity — Fused Signals (Mixpanel + Salesforce + Gong)"*. It queried all
three sources anyway.

**The shell is why, and the shell is granted after the card is drawn.** Measured in one session on
`6c933ab`:

    confidence 0.60 -> intent.valid False -> not read-only -> 11 tools, bash included
    confidence 0.90 -> intent.valid True  -> read-only     ->  7 tools, no bash

`chat_intent` produces that number at `:10921`, eighty lines and a `return` after the table card
has already ended the turn. So the card is put to the person at a moment when nothing in the
system yet knows whether their answer can be honoured, and on the investigative lane the answer is
no.

**The pick is not a fence, and it is not even a table.** `confirm_thread_table_candidate`
(`service.py:18544`) writes `{database, schema, table}` onto the Thread's context row and reads
that table's `columns` into the row, which is *"what the turn prompt renders from"*. At read time
(`liveread/run.py:215`):

    known = turn.scope_for.get((name, table)) or turn.scope_for.get((name, "")) or ("", "")

The table name always comes from the model's own arguments. What the recorded row supplies is the
**database and schema** those bare names resolve in — *"the recorded position is a default, not a
fence."* So the card buys two things: a position, and a set of column names in the prompt. It does
not choose which table gets read. *"Pick the Table to use"* over-claims a fence it does not hold,
and names the wrong noun while doing it.

Chat is not the odd one out here. `scope_for` is filled from the Conversation's chip at
`service.py:9986` and from the Built App's Binding at `:10016`, and **both** contribute
`(database, schema)` alone. What bounds what a turn may reach is the Binding — the grant on the Data
Source — never the table recorded on it. CONTEXT.md's **Table** term was sharpened alongside this
decision to say so.

**The two gates do not share conditions**, which is why reading one gate's trigger at the other is
not the same as reordering them:

    TABLE       T1 the card is not already answered
                T2 the prompt NAMES an unscoped Data Source (@mention or prose, table_search.py:116)
                T3 the source walks a whole database
                T4 the walk found at least one candidate

    INVESTIGATION  I1 investigation state is not open|declined
                   I2 intent.usable_label AND label in {data_answer, data_artifact}
                   I3 a bound data_source|datasource|table context item
                   I4 _looks_investigative(prompt)  (service.py:2330, pure, no model call)

T2 implies I3, so the exposure is I1 and I2 — and both are live. On 2026-09-17 the investigative
prompt classified at **0.60** against `MIN_CONFIDENCE = 0.65`, so I2 failed and the investigation
card could not be drawn at all (#401). And once a Thread has declined, I1 fails permanently,
because the card is the only door in (#389).

Amended while building #401. I2 now reads `intent.usable_label`, which asks for the label without
asking how sure the classifier was, so the 0.60 prompt above passes it and the card IS drawn. The
gate order below is unchanged and so is `MIN_CONFIDENCE`; what changed is that the exposure this
paragraph measured is now I1 alone.

Suppressing the table card on `_looks_investigative` alone therefore drops it for turns that never
reach an investigation card. Two questions become none, on the very prompt that opened the ticket.

## The decision

**The broad question is asked first, and the narrow one only if the broad one is refused.**

    today     narrow -> broad     "pick one table"  then  "shall I look across several?"
    funnel    broad  -> narrow    "shall I look across several?"  then, if no,  "pick a table"

Both premises now agree, because the second only exists once the person has refused the first.

**The investigation card does not have to carry the table question's job, because a decline
replays.** `answerInvestigationAndAsk` (`store.js:6737`) posts the prompt again with
`investigationAnswered: true` and does **not** set `skipTableGate`. The replayed turn re-enters
`_chat_stream` from the top, the table gate's four conditions are all still true, and the table
card draws then. That is left exactly as it is; it needs no code. A turn that skips the table gate
and is then declined is not stranded — it comes back to the gate it skipped, at the moment the
question has become coherent.

That is also the moment the pick is most useful. A declined turn is bounded, and on the bounded
lane `live_read_table` is the only tool that reaches a warehouse (#408). The pick is what hands
that turn the chosen table's column names in its prompt, and a `database.schema` for bare names to
resolve in — without which `DWH.MARTS.T` becomes `..T` and the store refuses a statement nobody can
read.

**The classifier is forced lazily, behind the free predicate.** `_looks_investigative` is a pure
function of the prompt and costs nothing. So:

    _looks_investigative AND I1 AND I3 AND not an explicit build request?
        no  -> table gate exactly as today. No model call. Nothing changes.
        yes -> force the classifier NOW, once per turn, memoised
               I2 holds?  yes -> draw the investigation card, return
                          no  -> table gate exactly as today

The `:10570` constraint is a rule about cost, not about all turns, and it keeps its meaning for the
population it was written about: a non-investigative prompt still reaches its table card with no
model call in front of it. An investigative prompt pays a call the turn was going to make eighty
lines later anyway — and across the two-leg sequence it is not a new call at all, it is the same
call moved from leg 2 to leg 1.

I1 and I3 are free and are checked first, so an already-declined Thread never pays for the call.

**The early offer is guarded on the explicit-build regex**, checked as a pure predicate and never
by calling `_explicit_handoff`, which writes (`mark_handoff_suggested`, `append_history`) and would
mint two suggestions if called twice. Forcing the classifier at the table gate puts the
investigation card above `_explicit_handoff`, and `_FUSES_SOURCES` matches *"cross-references"*, so
*"build me a dashboard that cross-references Gong and Salesforce"* is both shapes at once. Today it
goes to Build. Unguarded, it would first be offered a grant that only applies to Chat turns in this
conversation: accepted, the replay leaves for Build and the grant is spent on nothing; declined, it
shuts #389's one-way door over a sentence that never wanted a Chat investigation.

The offer also moves above the Dataset gate, and that is allowed to stand. A Dataset question is
still a Chat question, the funnel applies to it unchanged, and a decline replays down into the
Dataset gate the same way. The ordering assertion at
`test_a_bound_dataset_with_no_files_asks_which_ones.py:900` is re-read rather than assumed.

**Both cards change what they say.** The funnel does not retire the table card — it still stands on
a non-investigative prompt, on a low-confidence investigative one, and on every prompt in a Thread
that has already declined. On all three it currently claims a fence it does not hold.

The investigation card's closing sentence becomes false under the funnel and is replaced:

    was   Otherwise {assistantName} answers from what is already in this conversation.
    now   Otherwise {assistantName} answers this one question, and will ask where to start reading.

The table card says what the pick is actually for:

    was   Pick the {scope} to use. {assistantName} will then answer your question.
    now   Pick a {scope} to start from. {assistantName} reads its columns and can still
          reach others in {name}.

*"will then answer your question"* is dropped rather than reworded. It is a promise #407 and #408
currently break, and the card has no business making it.

**The offer stays gated on I2.** The alternative — offering on prose and a bound source alone, which
would make this fix work today rather than when #401 lands — was considered and rejected. It buys a
working fix by widening the population offered a **permanent** decision, earlier in the
conversation, which is the exact scenario #389 names as its worst: *"they ask their first data
question, get a card they did not expect, and press Just answer this."* A classifier saying this is
a data question is a second opinion the words alone are not, and that is worth waiting for.

## What this does not do

**It does not fix the reported case. It is inert until #401.** Traced through the funnel, the
2026-09-17 prompt classifies at 0.60, I2 fails, and the table card draws exactly as it does today —
plus a classifier call on a leg that currently makes none (measured: 12.4s, `0 model calls`). Until
the threshold question is settled, this decision makes its own opening example marginally slower and
no better. That is the cost of not spending #389's door, and it is recorded here rather than
discovered later. **#392 should not be closed as fixing the reported prompt; it lands the ordering,
and #401 arms it.**

If #401 stalls, there is a cheaper second opinion available that is not the classifier and not the
prompt's wording: the ranker at T4 already found `MIXPANEL__`, `GONG__` and `SFDC__` — the
catalogue's own evidence that the question spans several systems, computed for free, and not the
model deciding what it may reach. It is an unmeasured heuristic and is deliberately not built here.

**It does not make the bounded lane answer.** The turn measured at `decision="timeout"` after 260.0s
died on an undeclared `external_directory` permission that OpenCode resolves to `ask` with nobody
headless to answer it (#407), on a lane whose seven tools cannot express a `COUNT(DISTINCT)` (#408).
Neither defect knows this gate exists. They are downstream of every option considered here,
including doing nothing, so they could not select between them. This decision settles which question
the person is asked. #407 and #408 settle whether the answer runs.

**It does not give the table card a way to be refused.** The card has table buttons and "Show all N"
and nothing else (`message-blocks.js:1599-1663`): it can be answered but not refused, and a person
who wants neither must abandon it and rephrase without naming the store. The funnel narrows that gap
on the turns where the investigation card fires and does not widen it anywhere. The gap is
pre-existing, it has a clean statement of its own, and it is #414 rather than a third button in a
decision already settling a gate order, a lazily-forced classifier and the copy on two cards.

**It does not reopen #389.** A decline stays permanent and stays the only door. Everything above is
designed around that, which is why I2 survives and why the build-request guard exists.

**It does not touch `MIN_CONFIDENCE`.** Raising it flips investigation turns out of the shell lane
and into the read-only one, which is the lane #407 and #408 describe. That is #401's decision to
make with that consequence in front of it, not a side effect of this one.
