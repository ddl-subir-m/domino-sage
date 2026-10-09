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

## 1. The tools, and one step per question

- **`live_read_query`** — numbers and catalogue reads, one SELECT per call. What you compute and
  what you `GROUP BY` comes back to you; row text does not, it stays on the person's card. A
  result too large to return comes back as "first K of N rows".
- **`live_read_table` with `operation=analyze_text` and `sql`** — judging row text. The statement
  runs server-side and only its text column goes to the judging model; you get the judgments and
  their coverage, never the text. Pass `text_column`, `id_column`, `labels`, `purpose`, and
  `alias` when the person named a model. §4 is the method.
- **Python with `DataSourceClient`** — only when `live_read_query` is not in your tool list.

An investigation is a sequence of questions, and each answer decides the next: you cannot write the
join before you have measured which column carries the key. So one statement (or script) per
question, in order, each reading the last one's numbers. Four for a question that needed four is
right; splitting one lookup-and-compute across two round trips is the waste.

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

Snowflake opens each statement with no current database. Qualify the catalog as above. Do not
`USE DATABASE` or `USE SCHEMA`: each statement is its own session, so a USE does not apply to
the next one. If you do not know the database, `SHOW DATABASES` first. Schema filters are
uppercase — the person saying `dwh.marts` means database `DWH` and schema `MARTS`, not a table.
That error is not a dead connection, and it is not a reason to ask them for table names.

**Stage two — one columns read for the shortlist.** Once you have picked five or six tables, ask
`INFORMATION_SCHEMA.COLUMNS` once, for those tables by name, filtered to the columns you need:

```sql
SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
FROM   <db>.INFORMATION_SCHEMA.COLUMNS
WHERE  TABLE_SCHEMA = '<schema>' AND TABLE_NAME IN ('<T1>', '<T2>', '<T3>')
  AND  COLUMN_NAME ILIKE ANY ('%ID%', '%TEXT%', '%BODY%', '%DESCRIPTION%', '%TYPE%', '%STATUS%')
```

Not the schema, not the database, and not paged by `ORDINAL_POSITION`. If the reply says "first K
of N rows", narrow the `WHERE` — fewer tables, tighter patterns — rather than paging. Never
`GROUP BY` a catalogue read: each row is already one table or one column, so every count is 1.

**Budget: be querying real tables within about four statements.** Discovery past that is how a
turn ends with nothing measured.

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
however clean the SQL is. The confidence number in §7 reads these lines.

A ceiling is not a reason to stop. It is a reason to say so in the answer.

## 4. Text evidence: a match finds candidates, a model judges them

Support cases, call transcripts, notes. When the question is about what the text means — "asked
for", "complained about", "eliminate casual mentions" — a word match finds candidates and is never
the judgment.

**Do not count the candidates first.** The judging call's coverage IS the count, and a statement
that selects more than the record limit is refused before anything is judged — so a separate count
is a step that tells you nothing the judging call will not. Write the coverage to findings instead.

1. **Judge them in one `analyze_text` call per source**, with `sql` that does the whole selection:
   the match, the join to accounts, and any filter the question sets ("active customers", a date
   range). Always select a window around the match rather than the whole cell — it is never wrong
   for short text and it is what keeps long text and chunked transcripts inside a batch:

   ```sql
   SELECT c.CASE_ID, a.ACCOUNT_NAME,
          SUBSTR(c.BODY, GREATEST(REGEXP_INSTR(c.BODY, '\\bARM\\b', 1, 1, 0, 'i') - 600, 1), 1500) AS SNIPPET
   FROM   <db>.<schema>.<CASES> c
   JOIN   <db>.<schema>.<ACCOUNTS> a ON a.ACCOUNT_ID = c.ACCOUNT_ID
   WHERE  REGEXP_COUNT(c.BODY, '\\bARM\\b', 1, 'i') > 0
     AND  a.<ACTIVE_FILTER>
   ```

   `labels` names the decision, e.g. `["substantive_request", "casual_or_unrelated"]`; `purpose`
   states the rule in a sentence ("substantive asks for support on the ARM chip architecture; a
   passing mention or another sense of the word is casual"). Pass the account column as
   `group_by` and the reply counts labels per account — that IS the per-account answer, with no
   join-back step. Pass `id_column` too, so the saved table carries the record ids. Without
   `group_by`, a large judged set comes back as `counts` per label with no ids. Then do not invent
   ids: call again with `group_by`, or report the counts only.
2. **Only if the reply is refused as too many records**, narrow the `WHERE` (a tighter regex, a
   date range, one account band) and call again. Do not fall back to counting.
3. **If your prompt lists statements that judged text in this source earlier**, start from one:
   its tables, columns, joins and filters are already measured, so change only the match and the
   labels, and skip discovery for those tables.

A word search that returns 0 where a looser match found candidates is almost always a trap below.
Check it before concluding nothing matched. **Snowflake traps:**

- `'\b'` in a single-quoted literal is a backspace, not a word boundary. Write `'\\b'` or `$$\b$$`.
- `REGEXP_LIKE` and `RLIKE` anchor the whole value: `REGEXP_LIKE(t, '\\bARM\\b')` matches only a
  cell that is exactly "ARM". `.` does not cross newlines and matching is case-sensitive unless
  you pass parameters, so `REGEXP_LIKE(t, '.*\\barm\\b.*')` is FALSE for any multi-line text and
  misses "ARM". For a word in text, use `REGEXP_COUNT(t, '\\bword\\b', 1, 'i') > 0`, or
  `REGEXP_LIKE(t, '.*\\bword\\b.*', 'is')`.
- "Active customer" is a definition, not a column. Measure what the account table offers
  (`ACCOUNT_TYPE`, status, licence dates), then state the assumption in findings or ask (§5).

## 5. When to ask the person

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

## 6. The findings file

Long investigations keep measurements in the findings file the always-on prompt names — under a
prompt that carries one, `.sage/threads/<threadId>/findings.md`. The prompt is what permits that
path, not this skill. **If your prompt does not name a findings file, keep no notes**: nothing
on this turn can write them, so do not try, and do not mention notes, files or tools in your
answer. Answer from what you measured.

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
not reach the end. Append after every measurement that changes the plan: what a turn stopped at its
ceiling keeps is best-effort, and a line already in the file is the one sure to survive. Append a
measurement once. A line already in the file is not written again,
and a model-request receipt is not a measurement: requested model, response state, provider
receipt, cache, and fallback belong to the turn's record, not here.

## 7. Fusion: two numbers, not one

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
which of the two numbers is doing the work. That rationale, including an account's name, belongs
in the reply. The findings file stays aggregates and column facts: no account name, no identifier.

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

## Worked example: text evidence

*Shape only, no numbers.* **Question:** active customers who asked for ARM (the chip) support,
from support cases and call transcripts, with a named model eliminating casual mentions.

1. **Columns:** the prompt already lists the table names, so one filtered columns read for the
   case, transcript and account tables together — or none, when the prompt carries a statement
   that judged this source before.
2. **"Active":** the account-type column is in that read; decide and write it down, or ask.
3. **Judge:** one `analyze_text` per source, `sql` selecting id, account name and a snippet around
   `REGEXP_COUNT(<text>, '\\bARM\\b', 1, 'i') > 0`, joined to active accounts in the same
   statement; `group_by` the account, `id_column` set, `alias` as named. No count before it.
4. **Answer** from the per-account counts the two calls returned, with each call's coverage as the
   denominator. Three or four statements in all, two of them judging.
