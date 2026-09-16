---
name: investigate-weak-signals
description: Investigate a question that spans several sources and that no single table answers. Covers finding the tables without pulling the column catalogue, measuring whether a column is usable before trusting it, staging work across turns in a findings file, when to ask the person instead, and fusing weak signals into a score and a confidence.
---

# Investigate weak signals

Use this when the question spans several sources, none of them agrees with the others, and no
single table holds the answer — "which of our customers actually use the monitoring feature",
"which accounts are at risk", "where did this cohort come from". A coverage report is not the
answer. A score with a stated rationale is.

It does not apply to a question one query answers. Run the query.

---

## 1. The one-script rule is per question, not per investigation

The always-on prompt tells you to do the whole job in one script, because looking in one step and
computing in the next costs a whole round trip. That rule is about **one question**. It is not
about a whole investigation.

An investigation is a sequence of questions, and each answer decides the next one. You cannot write
the join before you know which column carries the key, and you cannot know that without measuring
it. So:

- Inside one question — discover, measure, compute, print: **one script**.
- Across questions — one script each, in order, each one reading the last one's numbers.

You are not breaking the rule by taking four scripts to answer a question that needed four. You
break it by splitting a single lookup-and-compute across two round trips.

## 2. Discovery is two stages: names, then columns

**Never pull the whole column catalogue.** Measured on one warehouse: every column in the database
is 17,049 rows, roughly 237,000 tokens (ADR-0038). It cannot enter a prompt and it cannot enter
your context either.

**Stage one — names only.** `INFORMATION_SCHEMA.TABLES` gives you table names and `ROW_COUNT` in
one cheap query. `ROW_COUNT` is free and it tells you which tables must be date-filtered before you
touch them — a 131-million-row event table is not one you `COUNT(*)` casually.

```sql
SELECT TABLE_SCHEMA, TABLE_NAME, ROW_COUNT
FROM   <db>.INFORMATION_SCHEMA.TABLES
WHERE  TABLE_SCHEMA = '<schema>' AND TABLE_NAME ILIKE '%<term>%'
ORDER  BY ROW_COUNT DESC
```

**Stage two — columns for the shortlist only.** Once you have picked five or six tables, ask
`INFORMATION_SCHEMA.COLUMNS` for those tables by name. Not the schema. Not the database.

Write the table-level facts to findings as you go, so the next turn does not re-derive them.

## 3. Existence is not usability

A column being in the catalogue says nothing about whether you can use it. **Between finding a
column and using it, measure it.** This step is not optional and it is not slow — one query covers
several columns.

For each candidate column:

- `COUNT(*)` — the denominator. Everything else is read against this.
- `COUNT(col)` — how many rows actually carry a value.
- `COUNT(DISTINCT col)` — whether it identifies anything, or is one value wearing a column's name.
- `MIN(col)`, `MAX(col)` for dates — whether the data is live or stopped two years ago.

```sql
SELECT COUNT(*)                AS rows,
       COUNT(SFDC_CONTACT_ID)  AS filled,
       COUNT(DISTINCT SFDC_CONTACT_ID) AS distinct_ids
FROM   DWH.MARTS.MIXPANEL__PROFILE
```

**Anything under about half populated is a CEILING, not a fact.** Write it to findings with the
word CEILING in it, because it caps every downstream answer that joins through it. A join key
populated 18.7% of the time means no rollup across that join can be more than 18.7% complete,
however clean the SQL is. The confidence number in §6 reads these lines.

A ceiling is not a reason to stop. It is a reason to say so in the answer.

## 4. When to ask the person

Three buckets. Put every uncertainty in one of them before you act on it.

| Kind | Example | What you do |
|---|---|---|
| Answerable by query | which tables exist, which columns, fill rates, cardinality, date ranges | **Query it. Never ask.** |
| Answerable by convention | `MARTS.X` vs `STAGING.STG_X`; which of two date columns is the event time | **Decide, state the assumption in findings, move on** |
| Not in the data | what counts as "active"? is this account us? which of these two products do you mean? | **Ask, and end the turn** |

Asking for something the warehouse can tell you is the most expensive mistake here: it spends the
person's turn on work you could have done in four seconds.

**You ask in prose, at the end of a turn.** You cannot render a picker or a candidate card — those
are the application's own code paths, fired by the table gate, not something you can trigger. So
state what you measured, state the choice, ask the one question, and stop. Put the question and its answer in
findings when it comes back, because the next turn will not remember the conversation.

## 5. The findings file

Long investigations keep measurements in the findings file the always-on prompt names — under a
prompt that carries one, `.sage/threads/<threadId>/findings.md`. The prompt is what permits that
path, not this skill. **If your prompt does not name a findings file, do not write under `.sage/`**:
keep the grammar below in the scratch file it does allow, and say in your answer that the notes
will not survive the turn.

**Measurements, never conclusions.** "The join is broken" is not an entry. "`SFDC_CONTACT_ID`
populated 16,756/89,399 (18.7%)" is. The test is whether the line is still safe to trust when it
is read a week later: a number with its denominator, its time and the statement that produced it
can be re-checked; a verdict cannot.

**Aggregates and column facts only** — counts, rates, ranges, distinct-counts, column names. Never
a value copied out of a row: no identifiers, no names, no exemplars. Where the prompt names that
file it is committed with the Project, so a row copied into it outlives the conversation.

```markdown
# Findings — <the question, one line>
<!-- Working notes. Aggregates and column facts only. Never rows. -->

## Question
<the person's question, in their words>

## Schema facts
<!-- slow-moving: what exists, and how full it is -->
- `DWH.MARTS.MIXPANEL__PROFILE` — 23 cols, 89,399 rows. 2026-09-16T11:02Z.
  `SELECT COUNT(*) FROM DWH.MARTS.MIXPANEL__PROFILE`
- `DWH.MARTS.MIXPANEL__PROFILE.SFDC_CONTACT_ID` — populated 16,756/89,399 (18.7%).
  2026-09-16T11:04Z. `SELECT COUNT(SFDC_CONTACT_ID), COUNT(*) FROM ...`
  CEILING: caps every account rollup that joins through this column.

## Evidence
<!-- fast-moving: the numbers you will quote. One line = one measurement. -->
- 2026-09-16T11:12Z — `Monitoring tab visited`, 90d: 291 events, 89 distinct users.
  `SELECT COUNT(*), COUNT(DISTINCT DISTINCT_ID) FROM ... WHERE ...`
  Separation: weak — a tab visit includes curiosity.

## Asked and answered
<!-- only what the warehouse could not answer -->
- 2026-09-16T11:20Z — asked whether "active" means visited / configured / data flowing.
  Answered: configured. Not recorded in any table.

## Open questions
- Two accounts marked `MODEL_MONITORING='Yes'` have zero events in 180d. Unexplained.
```

Every **Evidence** entry carries all five of: the UTC timestamp; the statement that produced it;
the number **and its denominator**; the fully-qualified object; and one clause on how much the
signal separates. The last field is what makes honest fusion possible instead of invented.

Read the file before you plan a turn. Append as you measure — not at the end, because the turn may
not reach the end.

## 6. Fusion: two numbers, not one

A single ranked number cannot carry this answer. Report two.

| | Means | Driven by |
|---|---|---|
| **Score** | how much positive evidence exists | the signals that fired |
| **Confidence** | how much of the evidence surface was observable *for that subject* | coverage, read from the Schema facts |

**Evidence only accumulates upward. Absence never subtracts.**

An account with 140 contacts, zero profiles in the product-analytics source, and a CRM field
marked `Yes` scores near zero on a naive sum, because almost nothing fired. That ranking is wrong.
The correct output is **"cannot assess — unobservable"**, which is a different answer and a more
useful one: it says go and look, rather than saying no.

**Negative evidence is admissible only where the subject is observable.** `MODEL_MONITORING = No`
counts against an account only if that account appears in the analytics source at all. For an
invisible deployment, "No" is an unfilled field, not a denial.

**Every rationale names each signal's coverage and its denominator.** A score resting on a
7.5%-coverage signal says so in its own sentence. Write the rationale so that the reader can see
which of the two numbers is doing the work:

> Acme — score 3 of 5, confidence LOW. Fired: 12 monitoring-tab events in 90d (coverage: 27.8% of
> profiles tie to an account, 24,832/89,399); CRM `MODEL_MONITORING = Yes` (coverage: 24.3% of live
> customers have the field filled, 17/70). Did not fire: no configuration event — but the
> configuration signal itself fired for only 4 users across the whole warehouse, so its absence
> here is weak evidence.

---

## Worked example

*One investigation's numbers. They are here to show the shape, not to be reused — re-measure
before trusting any of them.*

**Question:** which of our customers actively use the monitoring feature? Sources: a CRM, a
product-analytics warehouse, a call recorder. None agrees with the others.

1. **Names.** `INFORMATION_SCHEMA.TABLES` on the marts schema: 238 tables, 278M rows. The event
   table is 131M rows across 106 columns — always date-filtered from here on. Shortlist of six.
2. **Columns for the shortlist**, then measure them. The profile-to-CRM join key is populated
   16,756/89,399 — **18.7%**. Written to findings as a CEILING.
3. **Coverage of each source, per subject.** Profiles tied to an account: 27.8%. Calls tied to an
   account: 26.8%. Live customers with the CRM field filled: 24.3%. Three sources, each covering
   about a quarter — which is the whole reason no single one of them can answer.
4. **Not in the data.** "Active" is not a column. Asked in prose at the end of the turn: visited,
   configured, or data flowing? Answered: configured. Recorded under **Asked and answered**.
5. **The funnel, 90 days.** Tab visited: 291 events / 89 users. Configured: 14 / 4 users. Data
   logged: 19 / 2 users. A 4.5% look-to-configure rate — so "visited" and "configured" are
   different questions with two orders of magnitude between them.
6. **Fusion.** Score from what fired. Confidence from coverage. Accounts with no analytics presence
   come back "cannot assess — unobservable" rather than ranked last. Every line of the rationale
   names its denominator.

Two traps this example hit, and every warehouse has its own:

- **The most active "customer" was the vendor itself.** Exclude your own organisation, and exclude
  free-mail and test domains, before any domain-to-account mapping.
- **A model id baked into an event name** made the event-name space unbounded. Group by a prefix,
  or you will count one event a thousand times under a thousand names.
