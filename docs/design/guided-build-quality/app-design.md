# App structure and runtime behavior

Read this file for delivery slices A, B, and D. All paths below are repository-relative unless
described as generated-app paths. New helper names are proposed interfaces.

## A. A screen boundary from the first build

### Target structure

For a new FastAPI app:

```text
static/app.js                       Mount, ConfigProvider, ErrorBoundary, screen selection
static/components/MainScreen.js     Initial screen; replace its placeholder during the build
static/components/<Name>.js         Extra screens or reused components, when needed
static/app.css                      Existing tokens and component-prefixed styles
static/index.html                   Script loading in dependency order
app.py                              App-specific routes, when needed
.sage/queries.json                  Named queries for bound Data Sources
```

`MainScreen.js` registers `window.app.MainScreen`. Initialize `window.app` without replacing an
existing namespace. `app.js` mounts the screen inside the existing theme and error wrappers.
Keep shared view state at the nearest common owner. One screen can own its own filters. A shell
owns only state that must cross screens. One screen does not require a router or screen switcher.

Keep code in plain scripts for FastAPI: no imports, exports, JSX, package installation, or bundler.
Load Sage/vendor helpers first, leaf components next, screens next, and `app.js` last. Preserve
relative URLs and the existing runtime-error reporter. Use `sage.url` for app requests.

For React, use `src/App.tsx` as the shell and `src/screens/MainScreen.tsx` for the initial screen.
Use normal TypeScript exports/imports and the existing wrappers. Shared components remain under
`src/components/`. Do not copy the `window.app` convention into React.

### Required changes

1. Change both starters and their instructions together. The starting code must demonstrate the rule.
2. Replace the instruction to split only when the entry file grows. State who owns shell, screen,
   data access, calculations, and shared state. Keep small functions in the screen when that is clear.
3. Update the implementation rule that currently insists on edits to the entry file. A valid
   follow-up may change only a screen, query, or supporting app file. Preserve the existing
   no-progress check on actual app changes and both `NOTHING_TO_BUILD` and `ALREADY_DONE`
   exceptions. Keep the evidence gate for `ALREADY_DONE`; never require a token edit to a screen
   just to satisfy the new layout rule.
4. Update placeholder detection. `SAGE001` in `feedback/runner.py` reads only `static/app.js`
   today, so moving `sage-placeholder` into `MainScreen.js` without extending it lets an untouched
   starter pass. Write that failing test first. Detect the seeded placeholder in loaded app-owned
   screen code as well as the legacy entry. Exclude unused examples, vendor code, and Sage helpers.
   Do the same for the React stack's placeholder check, wherever it lives.
5. Preserve `SAGE002`. Missing script tags must fail code checks. `SAGE002` proves a tag exists,
   not its order: with both tags present and the screen loaded AFTER `app.js`, it passes and the
   page breaks. Add that reversed-order case as a fixture and make it fail a code check with
   actionable feedback (a static check on `index.html` order is enough; a browser is not needed).
   #691 already filters false `no-undef` errors for globals declared by other app scripts.
   Preserve both per-file and final checks. Its name scan covers app scripts on disk, so it is
   not evidence that a script is loaded. Keep real undefined-name and hook-order errors visible.
6. Ensure plan step file lists include any shell/index edits needed to wire a new screen. An edit
   allowlist must permit the smallest complete change.
7. Keep branded names and instruction-profile markers intact. Verify the final provider request,
   not only the template on disk, contains the intended rule once.

### Architecture rules to add

- Reuse the app's existing interface before adding another data-loading path.
- Keep one implementation of a business calculation used by several views.
- Keep one applied selection for related views. A chart click changes that selection.
- Put computation over the full warehouse population in SQL. Pure transformations of a complete
  local result may remain in a named JavaScript/TypeScript function.
- Validate inputs at the boundary that consumes them. Show loading, successful-empty, and error
  states separately.
- Extract a shared component when it is reused or makes a screen easier to change. File length
  alone is not a reason to add layers.

Place one working example of a screen plus query/control usage in each stack's existing example
convention. It must use synthetic fixture data or a declared fixture query. Do not ship a fake
query name as a working production connection. Test the example. Avoid an architecture essay in
every prompt; the core rules and a conditional pointer to the example are sufficient.

### Acceptance

- A seeded app still fails the unfinished-placeholder check until its screen is implemented.
- Implementing only the screen can produce a valid completed app.
- A two-screen app loads both screens and keeps its theme/error handling.
- A follow-up to one screen can leave the shell and other screen byte-identical.
- A missing script registration produces actionable feedback; a correctly ordered app renders.
- A screen script loaded after `app.js` fails a code check; the untouched seeded starter fails `SAGE001`.
- A legacy app with its UI in the entry file continues to work without automatic extraction.
- A repeat of a completed screen change can retain the checked `already done` outcome without
  edits. An unsupported claim still reaches the existing recovery path.

## B. Queries, request timing, and correctness

### Reuse the query hook

Use `sage.useQuery` in FastAPI and `useQuery` in React. Both already exist. Extend them only where
a behavior test proves a gap. Keep query-name and canonical-parameter cache keys, explicit refresh,
and the current response shape. Do not add a competing request/cache framework.

The hook must reject stale completions by request identity, in addition to aborting old fetches.
Each effect instance has an active flag or generation. Cleanup invalidates it before aborting.
Only a current request can update the visible state or overwrite the cache for that request key.
Test A -> B -> A changes: an older A response must not replace a newer A result. An abort is not a
visible query error. Browser cancellation is not proof that the warehouse cancelled its work.

### Remote text search

Proposed shared helper: `useDebouncedValue(value, delayMs = 300)`, exported with the existing query
helpers for each stack. It returns the settled value and clears its timer on change and unmount.
Use fake time to test it. The initial value is available immediately, including a URL-restored value.

Keep draft text separate from the applied query value:

```text
typing -> immediate input update -> 300 ms pause -> applied value -> query
```

For a costly or multi-field query, use an explicit Apply/Search action instead. Selects and toggles
apply immediately by default. Do not delay all controls. An entirely local filter over complete
data does not need a remote-search delay. Treat IME composition as unfinished input; apply after
composition ends. Pressing Enter applies the current text immediately when Search is offered.

### Query result shape

| Result | Contract |
|---|---|
| Detail table | Explicit columns, filters as parameters, stable ordering with a tie-breaker, bounded page. |
| Chart/KPI | Aggregate over all matching rows in the store; return the summary. |
| Filter choices | Query the relevant distinct values; do not derive a complete choice list from a truncated detail page. |
| Local file | Filter in the browser only when the loaded data is complete. For large files, use an app route to compute the requested result. |

Use a default detail page of 50 rows, with a validated maximum of 200 for the first implementation.
Use the Data Source's SQL dialect. Bind values through the existing named-query parameter system;
do not interpolate search text, sort identifiers, or raw URL values into SQL. Use fixed allowlisted
sort variants. For a normal detail table, bounded offset pagination is the initial example. A
large/changing dataset that needs stable traversal requires a cursor based on the ordered keys.

Every declared query parameter is required. "All" is the `__all__` sentinel tested in SQL, as
`bound_schema.py` already teaches (`WHERE (:region = '__all__' OR region = :region)`). Do not add
a separate unfiltered query as an alternative, and do not omit a declared parameter. Reset page position when filters
change. A limit bounds the returned rows; it does not prove low scan cost.

Show full-population totals only when the query computed them. Otherwise label the page count as
rows shown. Preserve `truncated` and `dataUsed`. Do not use the existing global row cap as pagination.
Update the canonical query guidance in `backend/sage/resources/bound_schema.py` rather than placing
different SQL rules in each template.

### Acceptance

- Rapid typing causes one settled remote search; Enter/Apply behaves as specified.
- The newest selection wins when responses arrive out of order, including A -> B -> A.
- Refresh bypasses a cached result and keeps the existing refreshing behavior.
- A failed request displays an error; it never becomes an empty result or a zero KPI.
- A fixture larger than the response cap produces the correct filtered aggregate.
- Two detail pages use stable ordering and do not repeat rows in a fixed dataset.
- Filter changes reset pagination. Invalid parameter and sort values cannot widen access.

## D. Shareable view state

Use this for dashboards or list screens that benefit from reopening a selection. Do not add it to
every form. The plan should state that the view can be shared when that behavior is proposed.

### Proposed helper

Add `static/sage/viewState.js` for FastAPI and `src/appViewState.ts` for React. Expose
`useViewState(schema) -> [state, patch]`. The FastAPI global is `sage.useViewState`.

The shell calls the hook once and passes state/actions to screens. A schema is defined outside
render. It declares each field's type, default, bounds/enum, and `shareable` status. Support string,
integer, boolean, and enum values initially. Dates use a validated string parser. Do not add a
general expression language.

`patch(values, {history: "replace" | "push"})` validates the patch, updates React state, and
serializes shareable fields. Replace is the default for filter changes; use push for deliberate
screen navigation or an explicit Apply action where Back should undo the selection. Use only the
fragment; preserve the current pathname, query, app prefix, and `history.state`.

```text
#v=1&screen=revenue&month=2026-03&region=EMEA
```

Use `URLSearchParams` encoding and a stable key order. Omit default values. On load, parse known
keys, validate them, and fill defaults. Unknown keys are ignored. Repeated keys are invalid for
that field and use its default. Invalid values use the field default; never pass them to a query.
Listen for `hashchange` and `popstate`, and update state only when the parsed values differ.
History API writes do not emit `hashchange`, so `patch` must update state itself.

Reserve the `#/sage/` namespace. The shipped keys helper already opens `#/sage/keys`. A view-state
hook must leave that hash untouched, retain the last applied view in memory, and let the keys
helper handle it. Initial load on a reserved route uses default view state without rewriting the
URL. An explicit later screen/filter action may leave the reserved route. Add a regression test
that the key-settings deep link still opens. A legacy app using another hash grammar needs an
explicit, tested migration when it opts in; do not silently replace its existing screen routes.

Only explicitly shareable applied state enters the URL. Free-text search is not shareable by
default. Keep row values, credentials, and unfinished form contents out of the hash. Preserve
per-screen selections in the single state owner when switching screens.

This is the generated app's URL. Workbench's `#/build/...` route is a different owner. The preview
uses the child page's location; never read or change `window.top.location`. Persistence across a
Workbench iframe recreation is outside v1. Verify refresh/sharing on the published app URL. A
shared link restores selection, not an old data snapshot or another viewer's permissions.

### Distribution

Register the helper as Sage-owned in the stack metadata and instructions. New apps load/import
it from the starter. Existing apps opt in when the requested feature needs it; materialize the
helper before the builder adds a reference. Do not assume the existing refresh routine installs
new files: it preserves existing apps and has tests against adding absent helpers.

Reuse `_prepare_turn_app` and `WorkspaceManager.for_app` from #690. They refresh the app pinned
to the turn after `_pin_turn_app`, under the turn lock, even when another app is selected.
Do not add a second refresh path or change the selected app to install a helper. If a preview
config changes, use the existing restart path for that app's view only.

Build and test the installer BEFORE the hash grammar. The first two tests are: an existing app's
refresh leaves an absent helper absent, and a Build turn for app A while app B is selected
installs only into A. The URL behavior is only worth testing once those pass.

Implement a narrow, idempotent helper installer for this opt-in if needed. Verify the known owned
path, write the shipped helper, and include it in the existing snapshot/publish workflow. Never
overwrite user screen files to install a helper. Keep legacy helper naming rules intact.

### Acceptance

- A copied published URL and a reload restore the applied selection.
- Back/Forward and direct hash edits update controls and results without loops.
- Filter changes preserve the selected screen; switching screens preserves their selections.
- Invalid, duplicate, or unknown URL values cannot produce an invalid query.
- Draft/private search text stays out of the URL.
- A prefixed published URL remains valid. Preview hash changes do not change Workbench routing.
- The existing `#/sage/keys` deep link still opens its dialog.
- With app B selected, a Build turn for app A refreshes/installs only A's required helper and
  leaves B's files, selection, and preview intact. An absent helper stays absent until opt-in.

## Migration rules shared by A, B, and D

New starters receive the full structure. Existing user-written entry/screen files are not reseeded.
Refreshing an existing Sage-owned query helper is permitted through the current ownership path.
Adding a previously absent helper requires the explicit install path above.

For old instructions, use a versioned, bounded supplement in the existing instruction-profile
assembly. It supersedes only the old screen-layout/query guidance. Keep the app's custom rules,
branding, and user skill replacements. Do not append repeated supplements each turn. Test the
actual provider payload on both stacks. Start with new apps for structure; migrate an old screen
only as part of a requested change, and preserve unrelated code.

When editing prompts or helpers, update their canonical generator and the checked-in output using
the repository's existing generation path. Locate that path before editing mirrored prompts in
`opencode.json`; do not maintain a second hand-written copy.
