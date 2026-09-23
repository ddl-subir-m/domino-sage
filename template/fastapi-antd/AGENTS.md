# Building apps in this workspace

A **FastAPI + Ant Design** starter with **no build step**. The page is `static/index.html`; the
app is `static/app.js`; the server is `app.py`. React, Ant Design, Day.js and Highcharts are already
on the page as plain scripts, and the preview reloads on its own — nothing to install, compile or
bundle. **Build the user's app by editing `static/app.js` (and the files beside it), and `app.py`
when the app needs a route of its own.**

> **Every turn must end with edits to `static/` or `app.py`.** Do the minimal planning the task
> needs, then write code in the *same* turn — never stop at a plan, a todo list, or a question and
> wait for the user. A turn that produces no file edits has accomplished nothing.
>
> **One exception, and it is narrow.** If the request cannot be acted on at all — it asks about data,
> a file, or a table that is not in this project, and no edit to the app would be an answer — then
> say so plainly in a sentence or two and write nothing. End that reply with `NOTHING_TO_BUILD` on a
> line by itself. {assistantName} reads that line, ends the turn cleanly, and shows the user
> what you said.
>
> This is for having **nothing** to build. It is not a way to stop at a plan, to ask what to do next,
> to check a decision first, or to put off something awkward — all of those are the failed turn the
> rule above describes, and the marker does not make them succeed. If any part of the request can be
> built, build that part instead and say what you left out.

## Talking to the user
Everything you say back is shown directly to the person building the app, who may not be technical.
- Describe the app and what it does, not how the machinery works. Never mention your tools,
  permissions, modes, file access, or "the environment", and never invent tool names.
- Never say you're "blocked" or "unable". If a table or file the request needs isn't in the project,
  say which one is missing and what you'd build once it's there (see `NOTHING_TO_BUILD` above) —
  that's a fact about the project, not a capability you lack.
- **A bound {dataSource} or {dataset} is not missing.** `live_read_table` shows a few real rows out
  of a bound table and `live_read_files` says what a {dataset} holds (pass the read token from this
  turn's prompt as `token`) — use them whenever asked what the data looks like, don't send the user
  to the preview to go and look.
- Describe the app from the user's point of view, never the starter or "placeholder". Write plans
  as natural prose, not a list of look-alike sentences ("I will… I will…").

### The words for the things around this app
Say **{dataset}**, **{dataSource}**, **{modelApi}**, **{llmAlias}**, **{builtApp}** and **{gallery}**
when you name one of these to the user.
Recognise **Dataset**, **Data Source**, **Model API**, **LLM Alias**, **Built App** and **Gallery**
as the same six things when the user types one, and answer in the words above rather than repeating
theirs. Identifiers keep their own spelling regardless: `.sage/`, `static/sage/appQuery.js`,
`sage.runQuery`, `DatasetClient` and every other file, path, import and symbol are not words to
translate.

## Earlier turns
`.sage/history.md` records what happened before now — grep it before guessing whether something was
already asked for, built, or rejected. Past record, not current intent (that's `.sage/plan.md`);
regenerated each turn, so don't edit it.

## Rules that matter
- **Plan proportionally, then build in the same turn.** A simple app needs only a quick mental plan;
  a complex one deserves a short pass over components, state and the design-system states below.
  Never stop at a plan alone — the single exception is `NOTHING_TO_BUILD` (see the top of this file).
- **Do not touch** `app.sh`, `sage_serve.py`, `sage_queries.py`, `sage_domino.py`, `scripts/`,
  `static/index.html`, `static/theme.js`, or anything under `static/vendor/` or `static/sage/`
  (including `appLlm.js`/`appModelApi.js`/`appQuery.js` and their `.config.js` twins). They are
  {assistantName}'s: how the page is served, finds its own URL once published, reports a crash, and
  reaches its data and models — refreshed from the template, so an edit is lost and a broken one
  looks like a blank page. Call `sage.askModel`/`sage.runQuery`; never edit what implements them.
- **Never run `pip install`, `npm install`, or any other install.** The page loads its toolbox from
  `static/vendor/` and the server runs on an interpreter you cannot add to. Build with what's listed
  under "What exists" below; if a task truly needs a new package, say so instead of trying to install it.
- **Don't run a check or a server yourself.** {assistantName} compiles every `.py` file and
  syntax-checks every script the page loads the moment your turn ends, and feeds errors back to you.
  The preview reloads on its own. Starting `uvicorn` yourself binds a second port nothing looks at.
- **Send one edit at a time to a given file.** Parallel edits to the same file race each other and
  get rejected. Change a file, let it land, then make the next edit — different files at once is fine.
- **`.sage/` is metadata, not your spec.** The code in `static/` and `app.py` is the source of truth,
  with one exception: `.sage/queries.json`, which you write yourself when this app reads a
  {dataSource} (see "The server" below).
- **Never delete anything under `.sage/` or `public/data/`.** `public/data/` holds files the user
  attached in the builder; deleting one breaks that attachment. "Remove everything you built" means
  the app's own code, never these — rewrite `static/app.js` instead.
- **Read attached data at runtime; never paste rows into a source file.** Everything under
  `public/data/` is served at `sage.url("data/<slug>/<name>")` — `fetch` it and derive what you need
  in code, however few rows the request names. Pasting rows writes one huge file the model has to
  emit as an unbroken string, which is where builds break mid-call. Format numeric diagnostics with
  `round()` or fixed precision before printing — raw floats can print as a 10-11 digit string, which
  the gateway's PII rule reads as a phone number.
- **Carry Data used into the app UI.** When output comes from `sage.runQuery`, keep `result.dataUsed`
  with it and show the {dataSource} name, query, coverage and truncated state near the output.
  `dataUsed.modelView` says the query result was not itself sent to a model. If you separately pass
  values to `sage.askModel`, show that model's own outcome (via `onOutcome`) separately — a refused,
  interrupted or partial answer stays visibly incomplete, never presented as final.
- **Plain scripts, no modules.** No bundler, so no `import`, `export`, or JSX. Build elements with
  `React.createElement` (alias it to `h`), share code between files through `window.app`. As the app
  grows, split it into `static/components/*.js` and list each with a `<script>` tag in `index.html`
  **above** `static/app.js` — write the leaf component before the file that uses it.
- **Style with Ant Design and the tokens** in `static/app.css` `:root`. Reach for a component
  (`antd.Table`, `antd.Form`, `antd.Card`, `antd.Select`) before hand-rolling one. Never invent
  colors, fonts, shadows or radii outside the tokens.
- **Read git history without printing an email address.** Plain `git log` and `git show` print the
  commit header, and the author line carries one. `git blame` prints no header, but most of its
  forms carry the address on every line they emit — `-e`, `--show-email`, `--porcelain`,
  `--line-porcelain`, `--incremental` and `git annotate` all ask for it. What you are avoiding is
  the ADDRESS — not one command and not one flag — so work out what it will actually print rather
  than reaching for a form not named here. Do not work out which form is safe: run
  `git blame <file>` bare, or not at all. These print no commit header and no author line:
  `git log --oneline`, `git log --format="%h %s"`, `git show --stat --format=`, `git status
  --short`, `git diff`. Two routes put an address there, and neither is fixed after it printed —
  stop before it does.

## Design system checklist
Every app must look intentional, not "vibe-coded." `static/theme.js` already wires the
{platformName} theme into `antd.ConfigProvider` (keep that wrapper) and into Highcharts — don't
write competing CSS for what a token or component already covers.

- **Tokens** (`static/app.css` `:root`): `var(--accent)` for primary actions, `var(--text)` /
  `var(--text-muted)` for copy, `var(--border)`/`var(--bg)`/`var(--surface)` for structure,
  `var(--ok)`/`var(--warn)`/`var(--danger)` for status only — never for a data series. Never
  hardcode hex values. 8px spacing grid; keep the `@font-face` and its file alone.
- **One primary action per screen** (`antd.Button type="primary"`); buttons start with a verb.
  `antd.Form layout="vertical"`, validate on blur. Cap content width and center on large screens.
- **Charts:** Highcharts into a `<div>` from `React.useEffect`; destroy on cleanup; call
  `Highcharts.chart(el, opts)` then `requestAnimationFrame(() => chart.reflow())` — a chart built
  before its container settles renders with a stale size. Colors from `sage.accents[0..7]` or leave
  unset. Every series needs an explicit `name`. Axis titles with units; bar y-axes start at zero.
- **Controls** (a select/date-range/search that changes what's shown, over 2+ views of the same
  rows): hold selection in `React.useState`, derive views with `React.useMemo`, state the current
  selection in words near the charts. A chart click writes the Control rather than filtering beside
  it. If this app reads a store, the store's own filter (SQL, a declared parameter) replaces the
  `useMemo` instead.
- **States, scoped to what the request touches:** empty (`antd.Empty` + action, never a blank area),
  loading (`antd.Spin`/`antd.Skeleton`, wire the initial load in a mount effect so it can't get
  stuck spinning), error (`antd.Alert` + how to recover). An unreachable data source is not an empty
  list — every control goes inert at once, and needs its own message.
- **Accessibility:** AA contrast, never color alone for meaning, `aria-label` on icon-only buttons.

## The server, and the {platformName} platform API
`app.py` is FastAPI; `sage_serve.mount(app)` already serves the page and its data. Add a route only
for something the browser cannot do itself. Keep routes under `/api/`, return JSON, let errors print
to stdout (the App's log).

**Most apps never need to call the platform API directly.** Build from data already in this
project first. Only when the request names a snapshot, a version, an approval, a policy, a tag, or
which {datasetPlural} exist — load the `domino-platform-api` skill; it has the endpoints, the field
names, and the traps that produce silently-wrong results. The relay allow-list in `sage_domino.py`
is authoritative for what a page can call through `sage.url("api/domino/<path>")`: GET only, and
only the families listed there.

## What exists
- `static/app.js` — the app (currently a placeholder to replace). `static/app.css` — its styles.
- `app.py` — the server. Add routes under the `sage_serve.mount(app)` line.
- `static/components/` — put reusable components here, one script each, listed in `index.html`.
- `public/data/` — the files the user attached, served at `sage.url("data/...")`.

### On the page — this is the whole toolbox
| Global | Use it for |
|--------|-----------|
| `React`, `ReactDOM` | Everything. `React.createElement` builds elements; `React.useState` and friends are the hooks. |
| `antd` | Every component: `antd.Table`, `Form`, `Input`, `Select`, `DatePicker`, `Card`, `Tabs`, `Modal`, `Drawer`, `Tag`, `Alert`, `Empty`, `Spin`, `Statistic`, `Typography`, `Space`, `Flex`, `Layout`. |
| `icons` | Ant Design's icons, by name: `icons.SearchOutlined`, `icons.PlusOutlined`. |
| `dayjs` | Formatting, parsing and date ranges; what `antd.DatePicker` gives and takes. |
| `Highcharts` | Charts. Line, area, column, bar, pie, and the `more` and `funnel` modules are loaded. |
| `sage` | {assistantName}'s helpers: `sage.url`, `sage.runQuery`, `sage.askModel`, `sage.checkModel`, `sage.callModelApi`, `sage.theme`, `sage.accents`, `sage.ErrorBoundary`. |

### URLs: always relative, always through `sage.url`
A published app is served under a path its own code cannot know. `sage.url("...")` builds every URL
from it — attached data, the app's own routes, the platform relay. A leading-slash path
(`/api/...`, `/data/...`) works in the preview only to break once published. Keep a multi-view app on
one page, switching in state or with a `#hash` — never `history.pushState` paths.

There is no bundler, no TypeScript and no JSX here, and no second UI kit: everything is built from
Ant Design with `React.createElement`. If a request seems to need a package that isn't on the page,
build the nearest thing you can from what is here and tell the user what you left out.
