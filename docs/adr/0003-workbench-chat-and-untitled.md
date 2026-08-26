---
status: accepted
---

# Workbench Chat: Untitled is a chip on this workspace, the prototype is the shell, artifacts are files

Sage grows a Chat mode beside the existing builder. Three product decisions are locked together
because each one is what makes the other two cheap to ship. The mock that set the UX is
`etanlightstone/sage_explorations` (live at the Sage Workspace prototype). Manage and Code are
owned by a parallel branch; this slice ships the chrome and makes Chat and Build real.

## Decision 1 — Untitled is a chip on this container's workspace; Threads live inside it

The UI says **Untitled**. That is a Sage overlay on `.sage/settings.json` (`"untitled": true`), not a
Domino project rename. There is no Control Plane rename API.

The Workbench is one orchestrator process with two launch paths, and no Hub:

- **Published App** (this repo's `app.sh`, `SAGE_PROXY_MODE=app`): Chat and Build run in a scratch
  workspace. The chip can say Untitled. Files do not survive an App rebuild. **Publish is disabled**
  — `DOMINO_PROJECT_ID` is Sage itself.
- **Sage Builder workspace** (`sageBuilder` in a git-based app project, `/mnt/code`): Chat and Build
  run on that project's repo. First boot hydrates Untitled from the Domino slug when it matches
  `sage-<user>-<id>` (or a legacy project named Untitled). **Publish** ships that project as a Built
  App.

**New conversation** creates a Thread inside the current workspace (a new OpenCode session +
`.sage/threads/<id>/`), not a new Domino project. Hub-style provisioning of `sage-<user>-<id>`
git projects is gone; restoring it is a later slice.

### Considered options

**New Domino project per Thread** — closest to the old hub (one named app per create). Rejected:
provisioning GitHub + project + workspace on every "New conversation" makes ChatGPT-like Chat
unusable, and scratch projects would multiply as colliding `Untitled` / `sage-untitled-2`, `-3`, …

**Ephemeral Personal sandbox, graduate later** — the mock's Decision 2. Rejected for Sage Builder:
Untitled work in `/mnt/code` survives refresh because it is the project. In App mode the scratch dir
*is* ephemeral across rebuilds until provision returns.

**Optimistic local workspace, provision in the background** — correct isolation, much more work.
Deferred.

### Consequences

- Scope chip is this container's one project (Untitled overlay or the Domino name). Switching
  Domino project means launching Sage Builder in a different project, not a hub gallery.
- One container still hosts one Built App (the 1:1:1 topology is unchanged). Many Threads, one app.
  The mock's "eight apps in Market Risk Analytics" picker is not real in this slice.
- Chat turns must not fire the first-build plan gate. Untitled is a place to think; `has_built`
  stays false until a handoff actually builds.

## Decision 2 — Lift the prototype React shell; Sage stays the backend

The Workbench UI lives at `backend/sage/workbench/` and is the prototype's `sage_workspace_prototype/static`
brought into this repo. Chat and Build become two modes over the orchestrator we already have.
Code and Manage routes render placeholders until the parallel branch merges.

Rebuild the chrome in `backend/sage/ui/index.html` was rejected: matching the mock by restyling
4.5k lines of vanilla JS is slower than lifting a shell that already has the composer, chips,
resource panel, and conversation rail, and that shell is the merge surface the other branch needs.

### Consequences

- Orchestrator serves the Workbench as the default UI. The current builder HTML remains until
  Build mode is wired through the new shell, then it is deleted rather than kept as a second app.
- Prototype `/api/*` fixtures are replaced by orchestrator routes. Do not keep a mock FastAPI
  beside the real one.
- Hash routing (`#/chat`, `#/build/<threadId>`) stays, because Domino's nginx prefix rewriting
  is the environment this UI will actually run in.

## Decision 3 — Chat artifacts are workspace files, not a chart DSL

The chat agent writes **files** the UI already knows how to show: PNG for a chart, JSON for a
table, markdown for a note. The handoff copies those files. There is no Highcharts/Plotly schema
the agent must emit, and no in-memory chart object that Chat and Build share.

This is the mock's hard constraint (Decision 4 there): Chat and Build may even be separate apps
later; the only shared context is the project filesystem.

### Considered options

**Prototype Highcharts card schema** — the mock's `chartId` pointing at fixture JSON. Rejected:
it is a second language the agent must learn, and it is not a file the builder can pick up.

**Plotly/Vega spec as the interchange** — richer, more work, not needed to prove Chat. A later
slice can add a spec file beside the PNG if interactivity earns it.

### Consequences

- Artifact directory is `examples/<threadId>/` (visible, carried into the Built App) plus a
  manifest at `.sage/threads/<id>/artifacts.json` (the UI index).
- `sage-chat` may write under `examples/` only. The shim enforces the allowlist, because the
  workspace root still has the React `AGENTS.md` that tells implement to edit `src/`.
- See [docs/workbench/chat.md](../workbench/chat.md) for the file contract and
  [docs/workbench/handoff.md](../workbench/handoff.md) for what crosses into Build.
