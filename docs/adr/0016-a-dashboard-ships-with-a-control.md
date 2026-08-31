---
status: accepted
extends: ADR-0002 (the server that runs named queries is what lets a Control filter at the store)
---

# A dashboard ships with a Control

The Builder produces dashboards nobody can turn: static charts, a static table, no way to ask a
second question. We decided that **a Built App showing a collection over a dimension ships with at
least one Control**, that the **plan names it before the build**, and that a **chart click writes a
Control** rather than filtering beside one.

A **Control** is an element that changes what the app shows without a rebuild — a select, a date
range, a search box, a toggle. It is fed either by a query parameter, which filters at the store, or
by state in the browser, which filters only what was already fetched. The two differ where a result
is truncated, and that difference is the subject of the last section.

_Avoid_: filter (that is one kind of Control), widget, interactivity, knob, facet.

## The bar

Three clauses, each checkable by looking at a built app:

1. **At least one Control.** A legend click is not one.
2. **At least two views respond to it.** A control that moves one chart is a chart option. A control
   that moves the chart *and* the table is a dashboard.
3. **The current selection is stated in words** — "March 2026 · EMEA · 412 rows".

The third clause is not polish. Without it a viewer cannot tell a filtered view from an empty one,
which is the distinction the unreachable-data rules in `backend/sage/resources/bound_schema.py`
already spend a paragraph defending. A blank chart under an unstated filter is the same lie as a
select holding only "All" beside an enabled button.

## The rule fires on data shape, not on the word "dashboard"

The rule applies when the app shows **a collection over two or more rows with at least one
low-cardinality column** — a category, a status, or a date. That column gets a Control.

It does not fire on the user asking for a "dashboard", and it does not fire on an agent's judgement
about whether one is wanted. Both readings put an LLM in charge of the precondition, and an LLM
asked for filters will find a dimension in a two-column table because it was asked to.

Three exemptions, named so the agent does not have to infer them:

- **Single-metric apps.** One number and a sparkline. A Control bar is larger than the content.
- **No dimension.** Data with no category, status, or date column has nothing to filter by.
- **Not a collection.** A form, a calculator, a chat UI. Nothing is being shown *over* anything.

The cost of a shape rule is accepted rather than hidden: a user who asks for "just the total" gets a
Control anyway, because the data had a region column. That is the trade for a precondition an agent
cannot argue itself out of, and the plan is where it gets argued instead — see below.

## The rule lives in both places, and they are not the same rule

**The plan prompt carries the intent.** `opencode.json` at the repo root is voiced into
`~/.config/opencode/opencode.json` at run time and ships with the Sage deploy at
`/opt/sage/opencode.json`. Every existing workspace gets a revised plan prompt on the next redeploy.
Controls therefore appear in `## What it does` and `## Screens`, in a plan the creator reads and can
edit before any code exists.

**AGENTS.md carries the mechanics** — use a query parameter, respect `truncated`, wire the enum,
pass the `AbortSignal`. `template/react-vite/AGENTS.md` is seeded into a workspace once and an
existing workspace never re-seeds, so what goes in it reaches new projects only.

This split is the reverse of the intuition that the template is the safe place and the prompt is the
risky one. The template is the half that cannot be taken back; the prompt is the half that can. So
the reversible half holds the judgement — *should this app have a Control, and over which column* —
and the permanent half holds only facts that do not change: parameter types, the row cap, the
truncation flag, the placeholder grammar.

Putting the judgement in the plan is what makes the shape rule above tolerable. A Control the data
shape demanded but the creator does not want is one line to delete from a plan, before a build
happens. A Control mandated only in AGENTS.md is one the creator can correct only after the fact,
by asking for its removal in a second turn.

## A chart click writes a Control

Cross-filtering is in scope, in exactly one form: **clicking a mark sets the Control for that
mark's dimension.** Clicking the EMEA bar sets `region = "EMEA"`; the select visibly moves to EMEA;
every view fed by that Control re-reads.

This is a click that *writes existing state*, not a second filtering mechanism:

- **No new state.** There is one selection, and it is the Control's.
- **No hidden selection.** Clause 3 of the bar is satisfied for free — the click is already
  displayed, because the select shows it.
- **No clear-selection affordance.** The way back is the select, which is on screen anyway.
- **No accessibility gap.** The keyboard path is the select. A chart click is never the only way to
  reach a filtered view.
- **It filters at the store.** A Control filters through a query parameter by default, so a chart
  click inherits server-side filtering and never touches a partial result.

A chart over a dimension that has no Control is not clickable. That is the rule, not an omission: a
click that cannot be shown in a Control is a selection the viewer cannot see or undo.

The alternative reading of cross-filtering — a click that filters the other views client-side,
beside the dropdown rather than through it — is refused. It doubles the state, hides the selection,
needs its own reset, has no keyboard equivalent, and raises a question with no good answer (does a
click replace the dropdown's value or intersect with it?). Every one of those costs disappears when
the click writes the Control.

## A Control filters at the store, and truncation is why

`runQuery` returns at most `_DEFAULT_MAX_ROWS` rows — **5000**, overridable by `SAGE_QUERY_MAX_ROWS`
(`template/react-vite/serve.py`) — and sets `truncated: true` when the store held more. Nothing in
`AGENTS.md` or `bound_schema.py` currently mentions that flag, so no agent has ever been told to
read it.

Filtering in the browser over a truncated result gives a **confidently wrong answer**. The app
fetches the first 5000 rows, the viewer picks EMEA, the chart shows 40 rows, the real answer is 900,
and nothing on screen looks wrong. Controls make this reachable in one click, so the flag stops
being a latent defect and becomes the ordinary case.

Three rules follow, and they belong in AGENTS.md with the Control mechanics:

- **Filter in SQL, through a declared parameter.** This is the default and it has no ceiling.
  `Param` types are `string`, `int`, `float`, `bool`, `date`, with an optional `enum` — which is a
  select, a date range, a search box and a toggle. The control vocabulary already exists.
- **Filter in the browser only when `truncated` is false.** Then the app holds the whole answer and
  a client-side filter is exact.
- **When `truncated` is true, say so on screen.** A stated row count that is capped reads
  differently from one that is complete, and clause 3 of the bar is where it goes.

One collision to write down, because it will otherwise be discovered per build: **every declared
parameter is required.** `Query.bind` refuses a missing parameter with a 400, so "All regions" has
no value to send. A Control with an all-values option needs either a sentinel the statement tests
for, or a second query without the predicate. The agent must be told which, or every Control it
builds will 400 on its default state.

## Considered options

**Put the whole rule in AGENTS.md only.** Rejected. It reaches new projects and never the existing
ones, and it puts the judgement — whether this app wants a Control — in the half that cannot be
revised. The creator's only correction is a second turn after the build.

**Put the whole rule in the plan prompt only.** Rejected. The mechanics are facts about `serve.py`,
not proposals: parameter types, the 5000-row cap, the required-parameter rule. A plan that argued
them would be reading code back to the creator, which the plan prompt forbids.

**Fire the rule on the user's request rather than the data shape.** Rejected. It asks a model to
decide whether a request is dashboard-shaped, which is exactly the judgement that produced static
dashboards in the first place. The shape rule is checkable; the intent rule is not.

**Defer cross-filtering.** Rejected. The reason to defer it was that it implied a second,
client-side filtering path that would collide with the row cap and need its own selection model. A
click that writes an existing Control has neither problem, and costs one `onClick` handler.

**Client-side filtering as the default, server-side as the exception.** Rejected. It is the more
obvious implementation and it is silently wrong above 5000 rows. Defaults should fail loudly.

## Consequences

- The plan prompt gains Controls in `## What it does` and `## Screens`. Plans get longer, including
  the plans of apps that then have their Control line deleted. That is the correction surface
  working, not noise.
- `template/react-vite/AGENTS.md` gains a Controls section next to `### Charts`, covering the
  parameter path, `truncated`, the required-parameter collision, and `AbortSignal` on a Control that
  re-queries. Existing workspaces do not get it.
- `truncated` reaches the screen for the first time. Apps built before this ADR filter over capped
  results without saying so; this does not retro-fix them.
- **Control** wants an entry in `CONTEXT.md`, since the plan prompt will show the word to creators
  and every user-facing word in this product has one. Not written here.
- Verification is a fixed prompt set scored against the three-clause bar, not a unit test. The set
  must include the three exemptions — a single-metric app, a form, and a dataset with no dimension —
  because making a Control appear is easy and keeping it away is the part that regresses. A presence
  test on the rule text in both files, in the shape of
  `backend/tests/test_sage_chat_prompt.py`, guards against silent deletion and proves nothing else.
