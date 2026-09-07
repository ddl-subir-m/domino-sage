---
status: accepted
extends: ADR-0010 (a Binding is declared, never inferred — this keeps that true while letting Sage
  do the finding), ADR-0020 (the working set is orientation, never context), ADR-0037 (what the
  record is called in prose)
---

# A found table is bound by the click, not by the agent

Observed live. A person added the `Snowflake-Data-Warehouse` Data Source, went no further, and asked
for a dashboard of daily Gong calls. Sage refused. They said "search for it, it will have Gong in
the table name". Sage refused again, and offered only the two database names it happened to hold.

The refusal was correct. `resources/bound_schema.py` writes this into the assistant's own prompt
when a Binding has no table: **"you cannot query until a Scope is set, and you cannot set one"**,
and hands it exactly one level of the cascade. The assistant obeyed a rule we wrote.

The rule exists for a good reason. [ADR-0010](0010-publish-reads-the-declaration-not-the-code.md)
says a Binding is always declared and never inferred from what the code turns out to touch, because
a published app refuses to read a Data Source it holds no Binding for, and an inferred one would let
an app work in the build session and break for every viewer.

## Sage finds; the person binds

The mistake was reading "never inferred" as "Sage must not look". Those are different acts.

Sage reads the Data Source's own catalog, ranks what it finds, and shows a few
[[Candidate]] tables. The person picks one. **That click is the declaration** — the same act the
panel's picker performs, reached from a different surface. Nothing is inferred, so ADR-0010 is
untouched, and the record written is the ordinary one, not a second parallel thing.

This is why the assistant gets **no new tool**. The search runs in Sage's own code, before the
assistant is asked to write anything, and the result arrives as a card the person answers. An
assistant holding a `find_and_bind_table` tool would be inferring a Binding on the first turn it
felt confident, which is precisely what ADR-0010 forbids. The assistant may say a table is missing.
It may not go and choose one.

Two consequences follow, and both are deliberate friction:

- With **no** Data Source bound, Sage lists the ones the person can reach and asks first. Two
  confirmations, even when they own exactly one Data Source. Using the only one silently is the
  same inference through a side door, and it would change behaviour under them the day a second
  one appears.
- The search always lands on **one** table. It never settles for a schema, because "somewhere in
  PUBLIC" does not answer "which table has the Gong data".

## What the catalog costs, measured

Measured against the live warehouse on 2026-09-07, because every option below turns on numbers we
did not have:

| Query | Result | Time |
|---|---|---|
| all 15 schemas | 15 rows | 0.58s |
| all table names in the database | 602 rows | 3.84s |
| all columns in the database | 17,049 rows | 4.25s |
| columns filtered to one shortlist | 398 rows | 4.63s |

Three things fell out, and each one settled a decision:

1. **One database-wide query, not one per schema.** `resources/provider.py` walks per schema and its
   own comment measures ~3s a level: 15 schemas is ~45s against 3.84s. So `SqlDialect` grows a
   database-wide statement beside `databases`, `schemas`, `tables` and `columns`. Where a connector
   has no such statement, ask the person for a schema rather than spend a minute in silence.
2. **Filtering buys tokens, never seconds.** The filtered query was *slower* than the unfiltered
   scan — it is the same scan. So the whole column catalog is pulled once and filtered locally, and
   the second question a person asks about that Data Source costs no query at all.
3. **The column catalog can never enter a prompt.** 17,049 rows is roughly 237,000 tokens; the
   gateway reports no context window for any alias, so this would be discovered as a refusal. The
   search is therefore two stages — names to a shortlist, then columns for the shortlist only, which
   for the Gong case is 398 rows and about 5,100 tokens.

## Why ranking needs a model, and which one

"Gong" matches **27** of the 602 tables, split across dbt layers: `MARTS.GONG__CALLS` is the modeled
table a daily summary wants, and `STAGING.STG_GONG__CALLS` is the raw one it does not. **They score
identically on any name matcher.** So a name ranker does not merely order badly here — it cannot see
the distinction that decides whether the dashboard is right.

The ranker runs on `catalog.ask`, making it the third classifier on that slot alongside the plan gate
and the handoff classifier, all three read-only and all three following the user's own model choice
(`orchestrator/scope.py`, `_model_for`). It copies that module's shape: `temperature: 0`, a hard
wall-clock timeout on a worker thread, and the breaker that stops calling after three unreadable
answers. It carries its own cost component, and **not** `phase="plan"` — deciding whether to plan is
planning overhead, while ranking tables is part of answering.

It fails **closed**, which inverts `scope.py`. A plan gate that cannot be reached must not block
builds, so that one fails open. A ranker that cannot be reached falls back to a layer heuristic
(prefer `MARTS`/`FACT`/`DIM`, demote `STG_`/`RAW_`/`TMP_`) over the same shortlist, and the card is
grouped by schema either way. It must never fall back to "no candidates" — a refusal is the failure
this whole decision exists to end.

## Staleness

The catalog is cached per Data Source for the session. A warehouse changes, so a cached list will
eventually name a table that has been dropped. The failure that hurts is not a stale list, though —
it is a stale *choice* becoming a Binding that points at nothing, which fails for the first viewer
of a published app. So the single chosen table is re-checked at the moment of confirmation, one
cheap query, before the record is written. Refreshing per session bounds the drift on top of that.

## In Chat, where there is no app

Chat mode has no Built App, so there is no Binding to carry a table. The confirmed table is written
to the Thread's own `context.json` and carried into `bind_data_source` by the existing
`_bind_from_handoff` path, which already records a scoped Binding in one call. The alternative —
refusing to confirm in Chat and telling the person to pick again in Build — makes them answer the
same question twice across a handoff, which is the friction this decision exists to remove.

## The Dataset half

The same act, with a different outcome: for a Dataset, confirming a candidate **attaches the files**
rather than writing anything on the Binding, because a Dataset Binding names the Dataset and stops
there (ADR-0037). It ships second. A wrong guess over files is recoverable — the assistant can read
them — while a wrong table is written into a record a published app depends on.
