---
status: accepted
---

# A read that finds the way is not a read that answers

An investigation spends most of its reads finding out where to read. The skill says so:
`template/skills/investigate-weak-signals/SKILL.md` prescribes stage one, names only
(`INFORMATION_SCHEMA.TABLES`); stage two, columns for a shortlist
(`INFORMATION_SCHEMA.COLUMNS`); then measurement — fill rates, cardinality, date ranges — before
a single column is trusted. That sequence is correct and it is why the lane works.

Every one of those reads publishes a `.table.json`, and every `.table.json` is drawn as a full
card (`store.js:1661`, `case 'table'` in `components/message-blocks.js`).

Measured on the dogfood workspace `sage-subir-mansukhani-66a821b1`, `sage_rev = eafcd76`, on
2026-09-19: one Gong investigation Thread held **60** table artifacts. Sorting them by title,
about 29 are catalogue listings — `all-dwh-schemas`, `gong-tables-grouped-by-schema`,
`sfdc-account-table-columns`. About 11 more are measurements the investigation took to decide
whether a column was usable — `gong-calls-row-count`, `dmm-event-totals`,
`gong-spotlight-field-coverage`, `model-monitoring-field-value-distribution`. The remainder are
findings. The person asked one question and got sixty cards, and the answer was among them.

[ADR-0062](0062-a-viewer-hides-what-sage-read-but-never-a-read-that-fell-short.md) governs
`data_used` and the investigation's own open/close line. Both are correct and neither is this
volume. `table` is a separate `block.type` and it stays drawn. This is the decision that was
missing.

## Why this is a decision and not a bug fix

Because a probe and an answer are not distinguishable after the fact, and the obvious places to
look for the difference do not hold it.

`record_artifact` (`workspace/threads.py:650`) writes `id`, `kind`, `name`, `title`, `path`,
`producedAt` and an optional `messageId`. Nothing says which tool made the row and nothing says
whether the person asked for it.

Worse, artifacts are not reported by the tool that wrote them. They are **discovered**.
`new_artifact_paths` (`service.py:11865`) diffs the workspace against the `tables.before`
baseline and returns whatever is new; `record_artifact` is then called with a path and nothing
else (`service.py:11868`). The comment at `service.py:11847` is explicit that this scan is the
only thing that turns such a file into a card. By the time a row is recorded, the statement that
produced it is gone.

So the classification has to happen where the statement is still in hand, and be carried
forward. The carrier already exists: the `DataUse` event holds `artifact` — the receipt path —
alongside `turn_id`, `columns` and `coverage` (`liveread/run.py:609`, and `DataUse.restore`
reads it back as `local_reference`). Joining the written paths to the turn's events by artifact
path needs no new index and no new file.

One thing must not travel. `liveread/run.py:608` stores `source_sha256` and says why:

> The HASH, never the statement. The event is persisted into the Thread's history, which is
> committed, and a statement carries literals.

A rule that needs the SQL in the browser, or in the Thread's history, would break that. So the
statement is read once, at the operation site, and only the verdict is persisted.

## The decision

An operation records a **`role`** — `'working'` or `'answer'` — on its `DataUse` event. At
publish, `record_artifact` is given the role of the operation whose `artifact` matches the
written path. The transcript folds the `working` rows of a turn behind one face and leaves the
`answer` rows drawn.

The role is decided in two parts, because one part can be mechanical and the other cannot.

**Catalogue reads are `working`, mechanically.** A read whose statement targets
`INFORMATION_SCHEMA`, `SHOW`, or an equivalent catalogue surface is finding the way by
definition. No judgement, no trust, testable from the statement alone. This is the ~29.

**The rest are declared.** The read tools take an optional flag the model sets when a read is a
step rather than the answer. This is the ~11, and there is no rule over SQL that could reach
them, because the same statement is either one depending on the question.
`SELECT COUNT(*) FROM GONG_CALLS` is working when the question is *which customers use model
monitoring* and is the answer when the question is *how many Gong calls are there*. The
statement is identical. Only the caller knows which it is, so only the caller can say.

## The fallback is `answer`, in both directions

An absent role draws. This is the same choice twice, for two different absences, and it is the
load-bearing part of this record.

**A row written before this exists** — every artifact in every current Thread — has no role, and
draws exactly as it does today. A silent default of `working` would fold the entire history of
every conversation on the day this ships.

**A model that does not set the flag** degrades to the catalogue rule alone. That still folds
about half the volume, and the half it folds is the half no judgement was needed for.

Both failures are the same shape: too much shown, never an answer hidden. That direction is not
negotiable here. A viewer who sees a probe they did not need has a cluttered transcript. A
viewer whose answer was folded away has been lied to about what Sage found, and has no way to
know it happened — the fold's face counts rows, it does not say what was in them.

This is the same asymmetry ADR-0062 §2 settles for a read that fell short, and it is settled the
same way: when the system is unsure, it shows.

## What was rejected

**Folding everything but the last table of a turn.** No new field, no model trust, smallest
diff. Rejected because it guesses. A turn whose answer is genuinely two tables loses one, and it
loses it silently — the fold's face would say "Sage read N tables" over a row that was the
point. A rule that can hide an answer is the one thing this record will not have.

**Deciding from the title or the path.** `staging-gong-table-names` does read like scaffolding.
It is also a bare substring test over an artifact path, which is open bug #450 — a random suffix
there already makes the disclosure record report data that was withheld. The same reasoning
would be wrong in the same way, and this time it would hide rows rather than misreport them.

**Never publishing probes at all.** The smallest transcript, because there is nothing to hide.
Rejected on two counts. It destroys the receipt — a person asking *why do you believe that* has
no way back to the read that decided it, which is the thing `data_used` exists to provide. And
it crosses into the lane #441 owns, where what a probe writes and commits is an open disclosure
question. A view decision should not settle a disclosure one.

**Extending ADR-0062's `dataAccessShown` to `table`.** One preference for all the machinery,
and the smallest new code. Rejected because it is a blunt instrument: the preference would hide
a `data_artifact` turn's requested table, which is the turn's whole output. ADR-0062's
preference governs *disclosure*, which is beside an answer. This governs *scaffolding*, which is
in place of one. Different questions.

## What this does not decide

**The other operation lanes.** `calculate` and `text_analysis` also record operations and can
write artifacts (`liveread/calculate.py:155`, `liveread/text_analysis.py:142`). They record
`answer` and always draw. The field exists on every operation so the event shape stays uniform,
but only the read lanes compute it. A calculation is something the turn was asked to do, and the
symptom this record answers gives no evidence against them. If evidence arrives, extending the
rule is a change to one site, not to this decision.

**How long the investigation takes.** Folding cards makes a transcript readable and makes
reopening it cheap — a folded row is never fetched, which is most of the cost measured in #451.
It does not make the investigation faster. #400 owns that, and its measurement stands: 49 model
calls, 487s of model time out of 598.6s, context growing 6KB → 290KB within the turn.

**What is committed.** This is a view over rows that are already written. `kept_rows`,
withholding and the disclosure verdict are untouched. #441 owns that lane.
