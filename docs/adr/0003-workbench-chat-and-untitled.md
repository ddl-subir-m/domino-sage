---
status: accepted
---

# Workbench Chat: Untitled is a hidden project, the prototype is the shell, artifacts are files

Sage grows a Chat mode beside the existing builder. Three product decisions are locked together
because each one is what makes the other two cheap to ship. The mock that set the UX is
`etanlightstone/sage_explorations` (live at the Sage Workspace prototype). Manage and Code are
owned by a parallel branch; this slice ships the chrome and makes Chat and Build real.

## Decision 1 — One Untitled Domino project per user, Threads inside it

The UI says **Untitled**. Behind it Sage immediately provisions a real git-based Domino project
through the existing hub pipeline (`provision/service.py`), with display name `Untitled` and
`.sage/settings.json` carrying `"untitled": true`. Persistence starts at message one. Rename
clears the flag and is the only time the display name changes.

A user has **at most one** Untitled project. Opening the Workbench reuses it. **New conversation**
creates a Thread inside that project (a new OpenCode session + `.sage/threads/<id>/`), not a new
Domino project.

### Considered options

**New Domino project per Thread** — closest to today's hub (one named app per create). Rejected:
provisioning GitHub + project + workspace on every "New conversation" makes ChatGPT-like Chat
unusable, and Untitled would multiply as `sage-untitled-2`, `-3`, …

**Ephemeral Personal sandbox, graduate later** — the mock's Decision 2. Rejected: the product
requirement is that Untitled work survives refresh, and we already have a persistence mechanism
(the Domino project). Faking ephemerality then copying into a project is two systems.

**Optimistic local workspace, provision in the background** — correct isolation, much more work.
Deferred until Untitled-reuse is proven too coarse.

### Consequences

- Scope chip lists the caller's Sage projects (Untitled + named). Switching scope is switching
  Domino project, which is today's hub list restyled.
- One project still hosts one Built App (the 1:1:1 topology is unchanged). Many Threads, one app.
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
