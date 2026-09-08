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
6. `backend/sage/workspace/manager.py:1081` `first_prompt` — no production caller left
   once change 1 lands. Three tests assert on it directly
   (`backend/tests/test_two_apps_nobody_named_do_not_read_alike.py:90, 99, 102`), so
   removing the method removes those assertions too. Decide that deliberately.
7. `backend/sage/orchestrator/service.py:6438` — the Chat→Build handoff writes the plan
   document's stored title into `displayName`. That title was written at
   `service.py:6218`, as `plan_title(plan_md) or thread.get("title") or "App"`. That is
   rung 1 written by nobody, and it survives every change above, because a stored name
   beats the whole ladder. **Two sites, and 6218 is the one that matters** — it holds the
   value 6438 reads back. Store the plan's `# ` heading only; with no heading, write
   nothing and leave the app a placeholder. The sheet payload's own `title`
   (`service.py:6152`) is a different expression that reads the same: it captions the
   handoff sheet in the browser, is never read back, and does not change.

### Workbench

8. `backend/sage/workbench/js/modes/builder.js` — the app switcher row renders the
   subtitle from `createdAt` / `builtAt` / `publishedAt`.
9. Publish dialog gains the pre-filled name field.

### Docs

10. `docs/adr/0042-*.md` — the decision, its cost, and why #211's answer is being
    reversed.
11. `CONTEXT.md` — the two rule sentences, in the preamble that governs the vocabulary as a
    whole.

## What does not change

- **`plan_title` (`backend/sage/orchestrator/handoff.py:376`).** The function is unchanged.
  It has six callers (`service.py:2789, 3623, 6152, 6218, 6321, 10205`); change 1 takes 2789
  and change 7 takes 6218, and the other four caption plan cards, plan documents and handoff
  rows, where a first line is a fine caption. Only the app ladder refuses a title nobody
  wrote.
- **Conversation titles.** A Conversation still takes the prompt's text through
  `title_from_prompt` (`backend/sage/orchestrator/service.py:4704`). The app and the
  Conversation deliberately diverge: a thread is a thread, an app is a thing.
- **The brand pack.** The placeholders become plain literals, because `{builtApp}` renders
  `Built App` and would give `Draft Built App 2`. This is a **loss, not a no-op**: today both
  placeholders go through `brand.text` (`service.py:2813-2814`) and a pack really does rename
  them. `backend/tests/test_the_service_speaks_the_packs_words.py:202` asserts
  `"Unnamed Creation"` and must change with it.
- **Numbering.** Position in `sibling_app_ids`, as today. No stored counter. Only the
  `len(siblings) > 1` half of the guard goes, so a solo app is numbered too. **The membership
  half stays.** `service.py:2812` reads `len(siblings) > 1 and workspace.app_id in siblings`
  and the next line calls `siblings.index(...)`; drop the whole condition and any workspace
  missing from its own sibling list raises `ValueError` — an app mid-delete, or the
  `_BlankWorkspace` stub in `backend/tests/test_the_service_speaks_the_packs_words.py:182`.
  `_app_row` runs this per app per render, so one orphan takes the rail down.

## Migration

The prompt rung was computed, never stored. On deploy, every unnamed app currently showing
a prompt-derived name becomes `Draft app <n>` or `Unnamed app <n>`.

**Accepted. No backfill.** Writing today's computed names into `displayName` would
enshrine scraped sentences as real names, permanently, past the rule this spec exists to
set.

**Apps born from a Chat handoff keep the derived name they already carry.** Change 7 stops
the write going forward and does not undo what is on disk. `set_display_name` stores a bare
string with no provenance, so nothing distinguishes a scraped title from a name somebody
typed, and a migration that cleared the first would clear the second with it. Those names
sit at rung 1 and win — the rule's one known leak backwards. Publish's pre-filled field is
where a person corrects one.

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
7. `_app_display_name` never *computes* `App`, `Untitled`, or any string containing
   `Built App`. A `displayName` stored before this change may still be one of those, per
   Migration.
8. `plan_title`'s own callers are unchanged: plan cards and plan documents still caption
   from a first line.
9. A Chat handoff whose plan has no `# ` heading leaves the new app a placeholder, and
   writes no `displayName`.

## Out of scope

**The rail bug — fixed, September 8, 2026.** Diagnosed and corrected before this spec's
tickets, because none of the naming work is observable until the screen moves when a name
changes.

It was not the lag it looked like. The server names a Conversation at the top of the turn,
and `sendBuildPrompt` **never re-read the Thread index at all** — it re-reads the preview
and the Bindings when a turn ends, and nothing else. `approveBuild` does have that read,
which is why the bug looked fixed from the plan card and broken from the composer. So the
rail kept the words `New conversation` for the whole build and for every build after it,
until something unrelated reloaded the list.

Fixed by telling rather than asking: the turn yields a `conversation_named` event carrying
the title it just wrote, and the rail applies it. No read, so the row is right within a
frame. Covered by
`backend/tests/test_the_rail_learns_the_name_while_the_turn_is_still_running.py`.

**Still open: the app switcher's own staleness.** The name of the selected app is refreshed
on the same schedule and has no equivalent event. `_app_change_event` already carries a
name and is the obvious carrier. Left out deliberately — ticket 216 changes what an app
name *is*, and wiring a stale name faster is worth less than wiring the right one.

## Decision trace

Grilled September 8, 2026. Settled: placeholder is a state and not a name; the ladder
stops scraping at every rung; the number stays positional; publish asks once and writes
back; the row subtitle carries what the prompt used to; `plan_title` is left alone;
existing apps rename silently; the rule goes in `CONTEXT.md` and an ADR.
