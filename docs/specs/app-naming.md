# Spec: a Built App's name is written, never scraped

Status: ready to split into tickets
Grilled: September 8, 2026
Supersedes the naming half of #211

## The problem

`+ New app` makes a Built App called `Built App 2`. That reads as a product name, but it
is a placeholder, and it sits in the app switcher beside apps somebody actually named.

The deeper fault is one rung below it. `_app_display_name`
(`backend/sage/orchestrator/service.py:2763`) names an unnamed app from the first thing
typed into it (`title_from_prompt`, #211), and `plan_title`
(`backend/sage/orchestrator/handoff.py:376`) falls back to a plan's cleaned first line.
Both turn a sentence into a name. Both reach Domino: publish passes
`_app_display_name(...)` as the deployed App's name
(`backend/sage/orchestrator/service.py:11198`), so a prompt can become the public name of
a deployed App.

## The rule

**A name is written, never derived from a sentence.**

Three writers can name a Built App: the person, the planner (through a `# ` heading the
plan shape already asks for), and the person again at publish. Nothing else.

**A placeholder sits outside the term vocabulary.** `Draft app 2` is deliberately not
`Built App`, not `App`, and not `Untitled`. It is not a name, so it does not use the
words reserved for names.

Both sentences belong in `CONTEXT.md`, beside the existing rules for `App` and
`Untitled`.

## The ladder

Before:

| # | Name | Source |
|---|---|---|
| 1 | rename | written |
| 2 | plan title | heading, else scraped first line |
| 3 | first prompt | scraped |
| 4 | caller fallback | Domino project name, publish only |
| 5 | `Built App <n>` / `Unnamed Built App` | placeholder |

After:

| # | Name | Source | When |
|---|---|---|---|
| 1 | `displayName` | written by the person, at rename or at publish | always wins |
| 2 | the plan's `# ` heading | written by the planner | a plan with a heading exists |
| 3 | `Draft app <n>` | placeholder | before the first build |
| 4 | `Unnamed app <n>` | placeholder | after the first build |

Rungs 3 and 4 are always numbered. `<n>` is the app's position in `sibling_app_ids`, as
today: it shifts when an older app is deleted, and that is accepted, because the number
is no longer the only thing telling two rows apart (see "The row").

Removed: the prompt rung, the first-line scrape *for the app*, the `App` return value
reaching a name, and the caller-fallback rung.

## The row

Every app switcher row gets a one-line subtitle carrying the most recent true stamp:

- `Started <date>` — created, never built
- `Built <date>` — built, never published
- `Published <date>` — published

Relative inside 7 days (`2 days ago`), `Month Day, Year` beyond it, per the Domino date
rule.

Stamps only. **No turn count**: `_app_row` runs per app per render, and counting turns
means walking an append-only log that reaches megabytes
(`backend/sage/workspace/manager.py:1088`).

This is what answers "which of these two drafts is which" now that the prompt no longer
does.

## Publish

The publish flow shows a name field, **always**, pre-filled:

- with the app's current name when it has one (rung 1 or 2),
- with the Domino project name when the name is a placeholder.

Enter accepts. Accepting **writes `displayName` back to the app**, so the switcher stops
saying `Draft app 2` the moment the app is published.

Not blocking: a pre-filled field with a sensible default asks the question without
standing in the way.

## Changes, by site

### Server

1. `backend/sage/orchestrator/service.py:2763` `_app_display_name` — drop the
   `first_prompt` rung and the `fallback` parameter. Read the plan's heading only, not
   `plan_title`. Return `Draft app <n>` / `Unnamed app <n>`, switching on
   `workspace.has_built()`. Rewrite the docstring: the "two halves split by `fallback`"
   description stops being true.
2. New heading-only reader for rung 2. `plan_title` is **not** changed (see below).
3. `backend/sage/workspace/manager.py` — stamp `createdAt` into the app's
   `settings.json` at `create_app`, beside `builtAt` and `publishedAt`. Add a reader.
4. `backend/sage/orchestrator/service.py:4257` `_app_row` — add `createdAt` to the
   payload. `builtAt` and `publishedAt` are already there.
5. `backend/sage/orchestrator/service.py:11198` — publish takes the name from the
   request, not from `_app_display_name(..., project_name)`. Write it through
   `set_display_name` before publishing.
6. `backend/sage/workspace/manager.py:1081` `first_prompt` — now unused by naming. Check
   for other callers before removing; if none, remove it and its docstring.

### Workbench

7. `backend/sage/workbench/js/modes/builder.js` — the app switcher row renders the
   subtitle from `createdAt` / `builtAt` / `publishedAt`.
8. Publish dialog gains the pre-filled name field.

### Docs

9. `docs/adr/0042-*.md` — the decision, its cost, and why #211's answer is being
   reversed.
10. `CONTEXT.md` — the two rule sentences, beside the `App` and `Untitled` rules.

## What does not change

- **`plan_title` (`backend/sage/orchestrator/handoff.py:376`).** It has six callers and
  five of them are plan cards, plan documents and handoff rows, not apps. A card's first
  line is a fine caption. Only the app ladder refuses a non-heading title.
- **Conversation titles.** A Conversation still takes the prompt's text through
  `title_from_prompt` (`backend/sage/orchestrator/service.py:4704`). The app and the
  Conversation deliberately diverge: a thread is a thread, an app is a thing.
- **The brand pack.** The placeholders are plain literals. `{builtApp}` renders
  `Built App`, which would give `Draft Built App 2`.
- **Numbering.** Position in `sibling_app_ids`, as today. No stored counter.

## Migration

Rung 3 was computed, never stored. On deploy, every unnamed app currently showing a
prompt-derived name becomes `Draft app <n>` or `Unnamed app <n>`.

**Accepted. No backfill.** Writing today's computed names into `displayName` would
enshrine scraped sentences as real names, permanently, past the rule this spec exists to
set.

Apps created before change 3 have no `createdAt`, so their subtitle starts at `Built` or
shows nothing until they build.

## Success criteria

1. A new app, nothing typed: switcher reads `Draft app <n>` with a `Started <date>`
   subtitle.
2. Type a prompt, no plan yet: the name **stays** `Draft app <n>`. The Conversation takes
   the prompt's text.
3. A plan with a `# ` heading is proposed: the app takes the heading.
4. A plan with no heading is proposed: the app **stays** a placeholder. It is never named
   from the plan's first line.
5. After the first successful build, an unnamed app reads `Unnamed app <n>` with a
   `Built <date>` subtitle.
6. Publish shows a name field pre-filled with the Domino project name for a placeholder,
   and with the app's own name otherwise. Accepting sets `displayName`, and the switcher
   updates.
7. `_app_display_name` never returns `App`, `Untitled`, or any string containing
   `Built App`.
8. `plan_title`'s own callers are unchanged: plan cards and plan documents still caption
   from a first line.

## Out of scope

**The rail-lag bug.** The server names a Conversation at the start of a turn
(`backend/sage/orchestrator/service.py:5234`), but the Workbench only re-reads the list
in the `finally` after the turn ends (`backend/sage/workbench/js/store.js:5081`). So the
rail shows `New conversation` for the whole build, and the app switcher shows a stale
name with it.

That is a separate defect and it ships **first** — none of this spec is observable until
the screen refreshes when the name changes.

## Decision trace

Grilled September 8, 2026. Settled: placeholder is a state and not a name; the ladder
stops scraping at every rung; the number stays positional; publish asks once and writes
back; the row subtitle carries what the prompt used to; `plan_title` is left alone;
existing apps rename silently; the rule goes in `CONTEXT.md` and an ADR.
