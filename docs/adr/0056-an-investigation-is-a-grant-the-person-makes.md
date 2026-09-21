---
status: accepted
extends: ADR-0055 (a findings file is committed, and invisible only to the person — the same
         file, no longer the thing that decides what a turn may reach)
supersedes: the continuation rule as ADR-0055 records it ("once a Thread has a `findings.md`,
         every later turn in it keeps `bash`")
---

# An investigation is a grant the person makes, not a file the model wrote

#364 bounds a Chat turn to the tools its intent needs. A question about data arms
`arm_read_only("question")`, `READ_ONLY_DENIED = WRITE_TOOLS | SHELL_TOOLS`
(`backend/sage/router/phase_classifier.py:48`) takes the shell with it, and `live_read_table`
accepts no SQL — so a bounded turn has no route to a warehouse at all, because the route is Python
and Python is bash.

An investigation (#378) is a question that needs several turns of exactly that. It has to be
exempt, and this record is about **who may grant the exemption**.

## The answer: the person, by clicking, and never anything else

```
offer   ->  a turn that would be bounded yields a card and ENDS
accept  ->  write_investigation(thread_id, {"state": "open", ...}), then replay the question
gate    ->  investigating = the flag is "open"
close   ->  write_investigation(thread_id, {"state": "closed", ...}); findings.md is untouched
```

The record is `context.json` on the Thread — the same row that holds the chips and the table a
person confirmed, written through `ThreadStore.write_investigation`
(`backend/sage/workspace/threads.py`). The model cannot reach it. It has no tool that writes there,
and the door that does (`POST /api/threads/{id}/investigation`) is reachable only from the
Workbench.

## Why it is not the file

#381 gated the exemption on `findings.md` existing, and ADR-0055 wrote that rule down along with
its safety argument: *"only a turn that was already unbounded can create the file that widens the
turns after it"*.

**The argument was true and it made the gate useless.** Measured on `54ce27fa`: every question
#378's own end-to-end check is built from classifies `data_answer` — the classifier's definition is
"answer from bound data without writing an artifact" (`chat_intent.py:32`) — and `data_answer` is
one of the two bounded lanes. `data_artifact`, the other, leaves read plus `artifact_write`, and
`write_chat_artifact` refuses any path outside `examples/<threadId>/` and any extension but `.png`
or `.table.json`. The third route in is the classifier's own fallback through
`_plain_chat_answer_only`. So no first turn of any investigation could write the file, the file was
never written, and the exemption never fired on any Thread.

A condition only an already-exempt turn can create is not a gate. It reads exactly like one.

**A prose trigger was built and rejected, and rebuilding it is the mistake this record exists to
stop.** Commit `55f5a432` made `investigating` read *"the file exists OR the prompt asks to
investigate"*, with a narrow explicit-verb regex. It was dropped, for four reasons that are still
reasons:

1. It infers a **durable capability grant from wording**, which is the judgement #364 removed on
   purpose.
2. It falsified ADR-0055's safety paragraph and left it standing.
3. The grant was one turn plus whatever that turn wrote — and the pinned prompt tells the
   newly-unbounded turn to keep its measurements in `findings.md`, so the exempt turn was
   instructed to create the permanent grant. Nothing on screen said so and nothing took it back.
4. Negation is unreachable by regex. *"don't investigate this, just give me the number"* fired.

The same words are read here, by `_looks_investigative` (`service.py`) — and they decide **whether
a card is drawn**, nothing else. That is the whole difference. Wording chooses what to ASK; the
person chooses what to GRANT. Negation is therefore not solved and must not be attempted: a false
positive is a card with a `Just answer this` button on it, which is the point.

## The trigger reads the question's shape and never the evidence's quality

Three limbs, all of them about the question: it **names the act** ("investigate", "dig into", "get
to the bottom of"); it **fuses sources** ("across Gong and Salesforce", "based on Mixpanel, Gong
and Salesforce"); or it **doubts a column** — asks whether something is ACTUALLY true rather than
what a flag claims ("which customers actively use it", "do they really log in").

**Signal strength and data quality are the wrong axis, and that is a decision rather than an
omission.** The obvious alternative is already a live defect one file over:
`template/skills/investigate-weak-signals/SKILL.md` opens *"answer a question no single table
answers, where every signal is weak"*, and the second clause is a precondition a model can evaluate
and find false. Ask "which customers actually use Model Monitor?", let it glance at the catalogue,
let it find a clean-looking `DMM_ENABLED` column — the signals do not look weak, so the skill is
skipped, in exactly the case its own section on *existence is not usability* exists to catch. Only
one of that skill's six sections is about fusion; the other five apply to any multi-turn
cross-source question whether or not a single signal turns out to be weak.

Here it would be worse. At offer time this turn has measured **nothing**, so how weak the evidence
is is not a fact the gate has — and a gate that guessed it would withhold the offer from the
question that needed it most. Weak signals are a condition under which fusion pays off. They are
not what makes a question an investigation.

## What opening grants, and for how long

Every turn in that Thread keeps its shell and its Python while the flag stands — both arming sites
in `_chat_stream` fall together, because either one alone closes the route: `data_answer` takes the
shell, `data_artifact` takes the shell and leaves a writer that cannot reach `findings.md`.

It is scoped to **one Thread** and to **no time at all**. It ends when the person closes it, when a
complete Recall clear takes it, or when the Conversation is deleted. It does not expire, and that
is a decision rather than an omission: an expiry would take the capability back in the middle of the
work it was granted for, and the person would learn about it from a turn that suddenly answered in
prose.

## What it is visible as

Two things, because they answer different questions:

- **The bar above the composer** (`modes/chat.js`) says an investigation is open, right now, and
  carries `Close investigation`. It is drawn off the Thread's record, so it is true on a reload, in
  a second tab, and a week later.
- **A row in the transcript** (`investigation-state`) says when it was opened and when it was
  closed. A bar cannot answer that, and a transcript read later is where the question gets asked.

This is the review's sharpest objection to the rejected design answered directly: *nothing told the
user their Thread was now unbounded, and nothing took it back.*

## Closing is not deleting

Closing clears the flag and **leaves `findings.md` exactly where it is**. Until this ticket the two
were one act, because the state WAS the file: the only way to stop a Thread being unbounded was to
remove the measurements. They are different things — the grant is a capability, the findings are
the work — and nobody asked to lose the work.

The one door that takes both is a **complete** Recall clear (`recall.EMPTY`), which is the person
saying *start over*. ADR-0055 already unlinks the file there; `clear_recall` now closes the
investigation beside it, because a conversation that started over holding an open grant is one
whose turns keep a shell for a reason nothing on the record explains any more. A summary-scoped
clear takes neither, for the reason ADR-0055 gives: it trims talk, and neither a measurement log
nor a deliberate grant is talk.

## The offer, and what it costs

An offer fires when the classifier put the turn on a data-shaped label (`intent.usable_label` and
the label is `data_answer`, `data_artifact` or `build_app`) **or did not answer at all** (`timeout`,
`error`, `invalid-json`), a Data Source or table chip is on the Thread, the sentence looks
investigative, and no decision has been recorded. Amended by #488 — the first condition used to
read *"would be bounded"*, `data_answer` or `data_artifact` only; see the residual list below for
what that cost and why it changed. It yields its card and
`{"type": "done", "ok": False, "decision": "investigation offer"}`, both appended to history so the
card survives a reload, and **the turn does not run** — the shape `_chat_table_candidates_events`
already uses for the table card.

Amended while building #401. That first condition read `intent.valid`, which also asks whether the
classifier cleared `MIN_CONFIDENCE`, and the reported question scored 0.60 — so the person was never
asked about the turn that most wanted an investigation. `usable_label` asks only whether the
classifier returned a label it meant. The threshold did not move and the arming sites below still
read `valid`: putting a card in front of someone is a different bet from arming a read-only lane off
a guess, and one field was answering both.

**Both buttons replay the question.** The card ended a turn that would otherwise have answered, so
an answer that only recorded a decision would charge the person a round trip for a card they did
not ask for. `Investigate` runs it unbounded; `Just answer this` runs exactly the turn that would
have run anyway. The replay carries `investigationAnswered`, which suppresses
the echo and skips the gate. The record alone would already decline to offer — both answers leave a
state the gate falls through on — so the skip is belt to that braces: it covers the window where the
record is not what the replay reads, a write that did not land or a second tab whose click landed
first.

**A decline is remembered and a close is not.** Declining retires the card for that Thread, the way
the recall offers retire: an offer that came back would be the same question asked until it got the
answer it wanted. Closing returns the Thread to where it started, so a later investigative question
may offer again — which is the only way to open a second one.

**That makes a decline a one-way door, and the card is the only way in.** There is no menu item, no
composer control and no command that opens an investigation; `decide_thread_investigation` has two
callers, the card's buttons and the bar's Close, and the bar draws only while one is open. So
somebody who presses `Just answer this` on their first data question cannot investigate anything
later in that conversation — question twenty gets no offer, and nothing on screen says why. The only
way back is a complete Recall clear, which drops the session too, or a new conversation. The
justification above is about *the same question*; this applies it to *every future question in the
Thread*, and that gap is the most likely thing here to want a follow-up. It is recorded rather than
closed because the ticket settles the permanence and the alternative — a second control that opens
one without a card — is a surface this record is not the place to design.

## Consequences

- **`findings.md` is a record and never a permission.** ADR-0055's "Deleting it" section and its
  "continuation rule" section describe the model this replaces; both now read the flag. Everything
  else ADR-0055 says about the file still stands unchanged — it is committed, it is pushed, it is
  invisible to the person, its aggregates-only rule is a prompt rule, and its ceiling is a refusal.
- **The scoped artifact writer is closed on an investigating turn**, because `investigating` sets
  `artifact_token` to `None`. An artifact still lands: the turn has a shell, writes straight into
  `examples/<threadId>/`, and `new_artifact_paths` collects it at turn end. Both halves are pinned
  by `test_an_investigating_turn_that_asks_for_a_table_still_lands_one`. Not repaired by minting the
  token anyway — that token IS the bounded lane, and handing it over would take away the shell the
  investigation runs on.
- **A false positive is a card, and a card is not free.** The trigger does not read negation, so a
  question that says *"don't investigate, just give me the number"* draws an offer. The card ENDS
  the turn, so the cost is one click and one round trip before the question runs — and the decline
  is remembered. Priced rather than dismissed: the opposite failure, a trigger narrow enough never
  to misfire, is the one that produced a gate nothing could open.
- **Two labels are offered nothing, and that is the narrowing's price.** The offer requires
  `intent.usable_label` and a data-shaped label, which buys most of a prose trigger's false
  positives for free — a classifier saying this is a data question is a second opinion the words
  alone are not. But `plain_answer` arms `arm_read_only("question")` too, and when the classifier
  answers `other_chat`, `answer_only` falls back to `_plain_chat_answer_only`. Named rather than
  closed: widening the gate to those two would put the offer in front of every prose question,
  which is the judgement the label check exists to avoid making.

  **Amended 2026-09-21 (#488): two cases this paragraph used to name are now admitted, and the
  reasons are the measured ones.** *`build_app`* — `chat_intent` makes every "report" a
  `build_app`, *"even if they involve data"*, and the sentence that opened #488 was a report that
  fused three warehouse systems: this ADR's own example shape, with a chip on the Thread, refused
  on the label alone. A chip, fusion prose and a data-shaped label are three signals, not prose
  alone; the funnel's explicit-build guard (ADR-0059) is what keeps a Build request out, and it
  still does. *A classifier that did not answer* (`timeout`, `error`, `invalid-json`) — the reason
  for refusing it was "arming a lane off a guess"; but those fallbacks run the turn on the
  eleven-tool shell lane with NO grant, no findings file and no card, which is the capability
  without the frame. Measured: a 5 s classify timeout put the #488 question on that lane for 107 s
  until the repeat brake ended it. A card is the cheaper bet. `low-confidence` and
  `no-bound-context` mean the classifier WORKED and declined, and are not this case.
- **Answering one card can now get you another.** The replay carries `investigationAnswered` and
  nothing else, so a question that first drew a table card, then got its table, can meet this card
  next: two clicks and two round trips for one question. Chat's other two gates are state-backed —
  the table is on the row, the chip is on the Thread — so they do not come back, with one exception
  that predates this: `_dataset_dismissed` is an in-process set, so a Sage Builder restart between
  the dataset dismissal and this click re-draws the dataset card. Chat has no `answered` bag to
  forward (that is Build's), and inventing one here would be a third record of which gates a turn
  has passed.
- **A Thread bound only to a Dataset or a file is never offered one.** The gate asks for a Data
  Source or table chip, because an investigation is a warehouse act. A Dataset question that needs
  several turns has no way in through this door.
- **`context.json` is read-modify-write with no lock, and this key joins that.** `add_context`,
  `remove_context` and `confirm_thread_table_candidate` each read the row and write it whole, so two
  doors pressed in the same instant can lose one of each other's edits — a grant, or a table scope.
  Older than this key and not narrowed by it; recorded because the grant is now one of the things
  that can go missing.
- **The offer costs a classifier call's worth of latency and nothing else**, because it sits after
  a call the turn was already paying for. It sits after the table and Dataset gates for the same
  reason those sit where they do: a question about which table is answered before a question about
  how hard to look.
- **Nothing expires and nothing audits.** There is no list of open investigations across Threads,
  and no record anywhere of how many turns one spent. A Project with forty conversations has forty
  places to look. Accepted for now: the grant is per-Thread and visible in the Thread that holds
  it, which is where the person who made it is.
- **A turn streaming while the flag changes reads the value it started with.** The door takes no
  turn lock, for the reason `clear_recall` does not (ADR-0055): the offer card is clickable while a
  later turn runs, and the bar is clickable always. So closing an investigation mid-turn lets that
  turn finish with the shell it was armed with. One turn, bounded by `_CHAT_TURN_MAX_S`, and the
  next one is bounded — reported here rather than closed, because giving this door the lock changes
  what it may answer and is a decision this record does not make.
