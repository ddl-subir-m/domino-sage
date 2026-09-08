---
status: accepted
revises: ADR-0040 (its last paragraph credits #211 with making one-app-at-a-time liveable; the row
         subtitle takes that job over), ADR-0008 (the plan still names the app, but only through a
         heading somebody wrote)
---

# A name is written, never derived from a sentence

`+ New app` gives a Built App a name it never asked for. Press it twice and the switcher holds
`Built App 2` beside apps somebody actually named, so a placeholder reads as a product name. One
rung below that, `_app_display_name` names an unnamed app from the first thing typed into it
(#211), and the same function's answer is what Publish sends to Domino — so a request typed in a
hurry can become the public name of a deployed App.

Both faults are the same fault: a sentence was allowed to become a name.

## The rule

**A name is written, never derived from a sentence.** Three writers can name a Built App: the
person, the planner through a `# ` heading the plan shape already asks for, and the person again at
Publish. Nothing else.

**A placeholder sits outside the term vocabulary.** `Draft app 2` is deliberately not `Built App`,
not `App`, and not `Untitled`. It is not a name, so it does not spend the words reserved for names.

Both sentences are in `CONTEXT.md`'s preamble, which is the only part of that file that governs
the vocabulary as a whole. They do not become glossary entries: the entries define terms, and one of
these two says what is *not* a term. The rules they sit against are `_Avoid_` lines inside entries —
`App (unqualified)` on **Built App**, `Untitled` on **Default** — not a section. Full reasoning and
the change list: `docs/specs/app-naming.md`.

## The ladder

| # | Name | Source | When |
|---|---|---|---|
| 1 | `displayName` | written by the person, at rename or at Publish | always wins |
| 2 | the plan's `# ` heading | written by the planner | a plan with a heading exists |
| 3 | `Draft app <n>` | placeholder | before the first build |
| 4 | `Unnamed app <n>` | placeholder | after the first build |

Four rungs removed: the first prompt, the plan's cleaned first line, the `App` string `plan_title`
returns for a plan it cannot read a title out of, and the caller fallback Publish passed. A rung is
allowed here only if a person or the planner wrote what it returns.

**And one writer removed, which is not a rung.** A Chat→Build handoff writes a name straight into
`displayName` (`service.py:6438`), reading the plan document's stored title. That title was written
at `service.py:6218` as `plan_title(plan_md) or thread.get("title") or "App"` — a scraped first line,
else the Conversation's own prompt-derived title, else the literal `App`. That is rung 1 being
written by nobody, and it is the leak that survives every rung above, because a stored name beats the
whole ladder. Under this rule the handoff may store the plan's `# ` heading and nothing else: no
heading, no write, and the app stays a placeholder until somebody names it.

`<n>` stays a position in `sibling_app_ids`, as today. It shifts when an older app is deleted, and
that is accepted for the reason below. **Unlike today, it is always shown.** The current code numbers
only where `len(siblings) > 1`, so a Project with one app gets a bare `Unnamed Built App`; rungs 3
and 4 are numbered whether or not there is a sibling to be told apart from.

## Why #211's answer is reversed

#211 was not wrong about the problem. Four rows all reading `Unnamed Built App` is unusable, and
ADR-0040 records that as the thing which made one-app-at-a-time hurt. It was wrong about which
column answers it.

The prompt was carrying two jobs — *tell these two drafts apart* and *be this app's name* — and it
is only good at the first. A name is quoted back at the person in sentences that assume it means
something ("Use in ...", the Publish confirmation, the deployed App's title in Domino), and a
half-typed request does not survive being quoted. So the job moves to a column that is allowed to
be a sentence:

**Every switcher row gets a one-line subtitle carrying the most recent true stamp** — `Started
<date>` before the first build, `Built <date>` after it, `Published <date>` once published, per the
Domino date rule. That is what answers "which of these two drafts is which" now that the prompt does
not.

Stamps only, and no turn count: `_app_row` runs per app per render, and counting turns means
walking an append-only log that reaches megabytes.

One exception, and it cannot be fixed after the fact: `createdAt` starts being stamped when this
ships, so an app made before that has no `Started` date. Its row carries no subtitle at all until it
builds, rather than a date invented for it.

## What this costs

1. **A placeholder is less informative than a scraped prompt.** `Draft app 2` says less than
   `Sales dashboard by region` did. Accepted: a row that is honestly a placeholder beats a row that
   lies confidently.
2. **The subtitle only reaches the rail.** `_app_display_name` has seven production callers, and
   the subtitle is added to one of them, `_app_row`. At `service.py:5059` (the "Use in ..."
   receipt), `11313` (the missing-app Problem), `11617` and `11756` the name is quoted into a
   sentence with nothing beside it, so those sentences go from naming an app to naming a
   placeholder and offer no second column to recover from. Not closed here: the answer is for
   those sentences to stop leaning on a name that may not exist, and none of them is wrong enough
   yet to pay for that.
3. **Publish asks a question it used to answer silently.** The name field is always shown,
   pre-filled with the app's own name, or with the Domino project name when the name is a
   placeholder. Enter accepts. Accepting writes `displayName` back, so the switcher stops saying
   `Draft app 2` the moment the app is published — the ask buys the write.
4. **Existing apps rename silently on deploy, and some do not.** The prompt rung was computed and
   never stored, so every app named that way becomes `Draft app <n>` or `Unnamed app <n>` the moment
   this ships. **No backfill.** Writing today's computed names into `displayName` would enshrine
   scraped sentences as real names, permanently, past the rule this ADR exists to set.
5. **A derived name already stored survives, and that is the known leak.** Apps born from a Chat
   handoff carry the title above in `displayName` today. `set_display_name` records a bare string
   and no provenance, so nothing on disk tells a scraped sentence from a name somebody typed — a
   migration that cleared the derived ones would clear real names with them. So they stay, at rung 1,
   where rung 1 always wins. The rule holds from here forward and does not reach backwards. Publish's
   pre-filled field is where one of these gets corrected, by the person, once.
6. **The number still shifts when an older app is deleted.** Under #211 that was the only thing
   telling two rows apart, and this makes it one of two. That is why the shift is now affordable.
7. **An OEM pack loses two strings it renames today.** `service.py:2813-2814` runs both current
   placeholders through `brand.text`, and
   `backend/tests/test_the_service_speaks_the_packs_words.py:202` proves it by asserting
   `"Unnamed Creation"`. Plain literals take that back, and that test changes with them. Accepted:
   the alternative is a pack noun inside a string that exists to say the app has no name.

## What is deliberately left alone

- **`plan_title` itself.** The function does not change. Two of its six call sites do — the app
  ladder's, and `service.py:6218`, the one whose result becomes a `displayName`. The remaining four
  caption plan cards, plan documents and handoff rows, and a first line is a fine caption for one
  plan. It is the app name that refuses a title nobody wrote, not the function.
- **Conversation titles.** A Conversation still takes the prompt's text through
  `title_from_prompt`. The app and the Conversation diverge on purpose: a thread is a thread, an
  app is a thing.
- **The brand pack's reach over real terms.** The placeholders themselves become plain literals,
  because `{builtApp}` renders `Built App` and would give `Draft Built App 2`. A placeholder is not
  a term, so a pack has nothing to rename — but see cost 7: today it does rename them, and this
  takes that away.

## Consequences

- An unnamed app stays unnamed, visibly, until somebody writes a name. Typing a request into it
  does not name it.
- A plan with no `# ` heading leaves the app a placeholder. The heading is the planner's one chance.
- `_app_display_name` never *computes* `App`, `Untitled`, or any string containing `Built App`. It
  can still return one, from a `displayName` an old handoff stored — see cost 5.
- Nothing here is observable until the screen refreshes when a name changes; the rail-lag defect
  ships first.
- Reversing this means naming the writer. A rung that reads a sentence nobody wrote as a name is
  what this ADR refuses, whatever the sentence is.
