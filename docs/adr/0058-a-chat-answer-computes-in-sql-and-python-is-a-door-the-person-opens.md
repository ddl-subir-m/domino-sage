---
status: accepted
---

# A Chat answer computes in SQL, and Python is a door the person opens

A Chat data turn is armed read-only when `chat_intent` is confident, and `READ_ONLY_DENIED =
WRITE_TOOLS | SHELL_TOOLS` (`backend/sage/router/phase_classifier.py:48`, applied at
`backend/sage/shim/enforcement.py:283`) then takes away both the shell and the ability to write a
file. That lane exists for a reason the enforcement comment states plainly:

> A Chat turn explicitly armed read-only is not an Artifact turn; it's a plain answer. It keeps
> Chat's Thread scoping but inherits the same no-shell/no-write guarantee as Ask, or one simple
> question can become a multi-step agent loop.

The guarantee is sound and #400 is its evidence: the same warehouse, the same model, on the lane
that keeps `bash`, answered a three-source investigation in **400.2 seconds across 14 shell calls**.
A lane that answers "how many events last month" must not be able to become that.

What the lane was never given is a way to compute. Measured live on `6c933ab`, the read-only set is

    delegated_model_call, glob, grep, live_read_files, live_read_table, read, skill

and the only one of those that reaches a warehouse is `live_read_table`, which by its own
declaration returns *"a few real rows"* (`limit`: *"Default 5, capped"*), hands back *"the columns,
a row count and a path — not the rows themselves"*, and offers exactly one aggregate: `sum` with a
`group_by`. There is no `COUNT`, no `DISTINCT`, no `WHERE`.

So the lane cannot answer *"How many Mixpanel events in the last 30 days, and how many distinct
users?"* — not badly, but at all. No combination of arguments returns either number.

`template/chat/AGENTS.md` does not know this. It tells the turn to *"query the data with Python
instead"*, asserts that *"everything they do, the Python below also does"*, and then forbids the
only true sentence available: *"Never tell the person you cannot see their data because a tool is
missing."* The agent is told the answer needs Python, given no Python, and instructed not to say so.

It behaves exactly as that combination predicts. From the Thread, verbatim:

> I need to run this as Python. Let me write the script to `/tmp` and execute it via the file
> system — but I can only read files, not execute them here in the chat agent. … Let me try a
> direct SQL query through the Data Source tool using `live_read_table` with the aggregation
> embedded in a subquery-style table name: … Actually, looking at this carefully — the
> `live_read_table` tool reads from a named table. For a custom aggregation query, I need Python
> execution. Let me write the script to `/tmp` and use a shell-based approach:

Every step of that reasoning is correct. It then attempted the `/tmp` write-and-run it had just
said it could not do, hit an OpenCode permission that resolves to `ask` with nobody headless to
answer it (#407), and the turn died after 240 seconds of silence having produced no answer.

## The decision

**A Chat turn on the read-only lane computes in SQL.** It composes a statement; Sage executes it
server-side against the bound Data Source under the existing sensitivity gate, with one statement,
read-only (no DDL, no DML), a row cap and a statement timeout set well below
`_CHAT_TOOL_QUIET_TIMEOUT_S`. The agent never receives a shell, a token or a gateway URL.

SQL rather than a structured aggregate API with `count`/`distinct`/`where`. The transcript above is
the argument: handed an API that could not express its question, the agent tried to smuggle the
aggregation through the `table` argument. Any structured surface is under-expressive at the next
unanticipated question, and the next one is a join — which is what a cross-source investigation
needs and what no aggregate API provides. A bounded SQL tool also grants no new access: the other
lane already runs arbitrary SQL against the same Data Source through `domino_data`, so this is
strictly narrower than what Sage permits today.

**Aggregate results reach the model. Rows do not.** ADR-0041 keeps read rows out of the transcript
and out of Recall, and a `COUNT(*)` must not be collateral damage of that rule — the model cannot
state a number it was never given.

The rule is keyed on the *kind of value*, not on the word "aggregate", because those are not the
same thing:

    SELECT MAX(EMAIL)       FROM CUSTOMERS   -- one row, one cell, a real address
    SELECT LISTAGG(NAME)    FROM ACCOUNTS    -- one row, one cell, the whole column
    SELECT ARRAY_AGG(SSN)   FROM PEOPLE
    SELECT ANY_VALUE(PHONE) FROM CONTACTS

Every one of those is an aggregate and every one returns a value that was sitting in a row. A rule
that asked "is this an aggregate?" would hand all of it to the model and from there into Recall,
which is the exact leak ADR-0041 exists to prevent.

So: **a result reaches the model when every output column is a numerically derived aggregate** —
`COUNT`, `COUNT DISTINCT`, `SUM`, `AVG`, `STDDEV`, `VARIANCE`, `MEDIAN`, `PERCENTILE_CONT`, `CORR`,
the `REGR_*` family, and `MIN`/`MAX` over a numeric column. Value-selecting aggregates —
`LISTAGG`, `ARRAY_AGG`, `ANY_VALUE`, `MODE`, and `MIN`/`MAX` over text — go to the card, like rows.

**Group-by labels are allowed to the model, deliberately and not by accident.** `SELECT
ACCOUNT_NAME, COUNT(*) … GROUP BY 1` returns stored values in its first column and is the shape
every real analysis takes. It is permitted because the alternative is a rule that forbids the most
ordinary useful query, and because the shell lane already returns exactly this today. It is written
down here rather than left to fall out of the rule, so that a later reader can see it was a choice.

**When SQL cannot express the question, the turn offers the other lane rather than failing.** SQL
reaches further than it first appears — correlation, percentiles, ranking, cohorts, funnels and
cross-source joins are all one statement. What it does not reach is a CSV or Dataset file, model
fitting, and anything wanting a library. For those the turn says so and offers:

> That needs a calculation I can't run here. Want me to work it out? It'll take a few minutes.

On yes, the work runs on the lane that already has Python. This is a door onto an existing
capability, not a new one. Without it the first question outside SQL fails the way today's does,
and the reason for the read-only lane is defeated the moment anyone "fixes" it by adding a shell.

`AGENTS.md` changes to match: the claim that Python does everything the tools do is removed, and
the sentence forbidding the agent from naming a missing capability is removed. A prompt that bans
the true answer guarantees a false one.

## What this does not do

It does not make the shell lane faster. A question that needs Python still costs what #400
measured, and the round-trip count on that lane is untouched by this decision. What changes is that
far fewer questions go there, and that going there is something the person agreed to.

It does not make the feature set uniform across Data Sources. The reach described above is
Snowflake's. A different warehouse will refuse `CORR` and may refuse more, and the tool surfaces
the store's own error rather than reporting the analysis as impossible — the distinction #399 had
to be reopened to make once already.

It does not remove `live_read_table`. Showing a few real rows on a card is a different job from
computing, it keeps its card-only guarantee, and nothing here changes it.

## Amendment: the artifact lane is in scope (2026-09-18)

This ADR is written throughout for "a Chat turn on the read-only lane". The `data_artifact` lane
has the same hole and is covered by the same decision. Measured on `main@86aae12a`, the two tool
sets differ by one entry:

    read-only:  delegated_model_call, glob, grep, live_read_files, live_read_table, read, skill
    artifact:   delegated_model_call, glob, grep, live_read_files, live_read_table, read, artifact_write

Neither can filter, count or join. So the sentence above — *"the lane cannot answer 'How many
Mixpanel events in the last 30 days, and how many distinct users?' — not badly, but at all"* — is
true of both, and was measured on the artifact lane a second time before this amendment was
written: the turn composed the correct statement, reported *"This turn doesn't have the tool to
execute it and get back numbers"*, and ended `decision="table generation failed"`.

The artifact lane is the worse half, because of what it has already promised. A read-only turn that
cannot compute answers in prose and disappoints. An artifact turn has been labelled `data_artifact`,
has armed `artifact_write`, and has been told by its own turn prompt to write the table with it. It
then cannot produce a number to put in one.

**The decision therefore reads `a Chat data turn` wherever it read `a Chat turn on the read-only
lane`.** The SQL tool joins the allowlist at `enforcement.py:321` in the same change that gives it
to the read-only path, and the door below is reachable from both.

Three things do NOT change, and are restated because each looks like it might:

- **The value-return rule is unchanged.** Numerically derived aggregates reach the model;
  value-selecting ones go to the card. This already covers the artifact case: a chart needs a label
  and a number per bar, which is exactly the group-by shape the rule admits on purpose.
- **`artifact_write` keeps its bounds.** `examples/<threadId>/`, `.png` or `.table.json` only. A
  turn that can now compute is not a turn that can write anywhere.
- **The read-only lane's guarantee is untouched.** Arming still removes the shell on both lanes,
  and #400's 400.2 seconds remains the reason.

Recorded as an amendment rather than a new ADR because it changes this decision's scope and nothing
else; a reader who finds only the body above would build the SQL path into one lane and leave the
other exactly as this ADR describes it.

## Amendment: where the door is, and why it is not a gate (2026-09-18)

The body above decided that a turn which cannot express its question in SQL offers the other lane.
This records where that offer fires, because the obvious place is wrong and the reason is not
obvious.

**The offer is drawn at the turn's conclusion, not in the gate block.** Two gates already fire in
`_chat_stream` before the turn runs, and ADR-0059 fixed their order — the broad question before the
narrow one. This is not a third one, and that ordering is untouched. The condition here cannot exist
before the turn runs: it needs the model to have composed a statement AND to have said that one
SELECT does not reach the question, and neither fact exists while the gates are firing. A card put
up there would be asking the person to predict what the model is about to find out.

**The model proposes with a marker and a cheap check verifies it.** The model is the only thing in
the loop that reads the question and knows the tool's reach, so it makes the call. It claims by
emitting `NEEDS_MORE_THAN_SQL` on a line of its own, which is the `NOTHING_TO_BUILD` mechanism and
deliberately the same one: a token no model emits by accident, tolerated with the wrappers a model
reaches for unprompted, NOT tolerated mid-sentence — that is where a model quotes the marker while
explaining itself — and stripped before the prose is shown, because a bare token under a friendly
answer reads as a leaked error code.

The check asks one thing: did this turn actually send a statement. A turn that composed nothing has
not established that SQL could not express the question, and an unchecked claim turns this door into
the default, which is what #400 measured at 400.2 seconds. Nothing re-reads the prompt to second-
guess the model about SQL; that would be Sage deciding what the model may reach, which is the shape
#381's review rejected. The check catches habit, not error. It counts ATTEMPTS rather than
successes, because a statement that timed out was still composed and sent — `StatementTimeout` is
its own type precisely so a turn can tell "the store said no" from "I have no way to ask".

**"No" is cheap here, and that asymmetry with the investigation card is deliberate.** The prose this
card sits under is the answer — what SQL could reach — so declining records the answer and replays
nothing, and writes no row: a line saying a capability was NOT granted is a receipt for nothing
happening, which is the rule ADR-0056 already states for its own decline. The investigation card
must replay on BOTH buttons because it ends the turn before the model runs and therefore owes an
answer either way. Do not make the two symmetrical; the difference is the position, not an
oversight.

**Accepting grants ONE calculation, not the Thread.** The accept mints a grant server-side when
the card is drawn, ties it to that card, spends it once on the replay, and then it is gone. It does
not open an investigation and it neither reads nor writes investigation state.

This is the decision, and the first draft got it wrong in a way worth recording. It reused
`decide_thread_investigation("open")` — ADR-0056's standing grant — on the argument that a grant the
person can see and take back beats one that expires silently. But the decline here was always
per-calculation, because "no" to working out one number is not "no" to the conversation. That left
the accept Thread-wide and the decline per-question, and **that mismatch, not the offer, is what
reached into #389**: `decide_thread_investigation` early-returns only when the state is already
`open`, so a `declined` row is overwritten with `{"state": "open"}` and accepting would re-open a
Thread that #389 says can only be entered through the investigation card. Making both halves
per-calculation removes the second door rather than trading it for a different cost — and it also
removes the alternative that was weighed against it, because this card never reads investigation
state at all, so one early "Just answer this" cannot silently kill the feature either.

**The grant is server-minted and single-use, and deliberately not a request-body boolean.**
`skipTableGate`, `investigationAnswered` and `datasetDismissed` are all client-supplied, and every
one of them gates a CARD: the worst a forged one does is skip a question. This flag suppresses
bounded arming, so a forged one would hand a browser the shell lane. It is the shape
`arm_chat_artifact()` and `arm_read_only()` already use in the same function. Spent on
presentation rather than on success: a replay that then fails has still used its click, and leaving
the grant live would let one card be redeemed repeatedly, which is the standing grant again by
another route.

The grant meets the standing one at exactly two places — the two arming sites — and nowhere else.
The table gate keeps reading `investigating` alone, because a grant for one calculation says nothing
about which table the next question should start from.

**The fire rate is the open risk, and it is not a test question.** The trigger is a token taught in
a prompt, and no suite measures whether a prompt is obeyed: every test here supplies the marker, so
all of them together say nothing about how often a model emits one unprompted.

The safe degradation above makes that worse rather than better, and it is worth being explicit about
the trade. A false marker is stripped by the statement check and a missed one leaves the turn ending
exactly as it ends today, so neither failure shows the person a wrong card — which is the right
property to build for, and also the property that makes both failures invisible from here. A door
that never opens looks exactly like a door nobody needed. That is how #428 shipped and how #408
shipped unreachable.

So the rate belongs in a live measurement, and until one exists this feature is UNVERIFIED in
production however green the suite is. A `log.info` fires on both arms of the check — offered, and
claimed-without-a-statement — so a silent zero is visible rather than assumed.

## Catalog discovery — #557, 2026-09-25

A live model replay found a conflict in the investigation path. The skill told the model to read
table names and column facts. The query ran, but the number-only disclosure rule hid those facts.
Haiku then guessed table names and could not answer; Xiaomi encountered the same refusal. Adding
a grouped count let the control model see the same names. Models must not need that workaround.

Direct catalog fields now have a narrow exception. One plain SELECT may read allowlisted fields
from `INFORMATION_SCHEMA.TABLES`, `COLUMNS`, or `SCHEMATA`. On a verified Snowflake connector it
may also read `SNOWFLAKE.ACCOUNT_USAGE.TABLES` or `COLUMNS`. The fields are catalog/schema/table/
column names, data type, ordinal position, nullability, and table row count. The source identity is
parsed from SQL; an ordinary table with a metadata-like name or alias does not qualify. Wildcards,
comments, defaults, functions, joins, CTEs and nested queries do not get this exception. Existing
derived-number rules still apply independently.

Quoted identifiers keep their case. Only the known uppercase Snowflake catalog spellings qualify
when quoted; ambiguous quoted spellings on other connectors are refused. A SQL Server database
named `SNOWFLAKE` does not get Snowflake's system-catalog exception.

The source grant, 500-row store request and model value-size limit stay in force. The operation
receipt names the metadata fields sent and classifies the read as working material. It records no
SQL text or metadata values in saved history. Ordinary stored row values remain under the existing
disclosure rule. The replay used real models with synthetic local tables, so it does not establish
production warehouse access.
