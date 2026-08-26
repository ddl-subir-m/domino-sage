---
status: accepted
---

# Workbench Chat: the prototype is the shell, artifacts are files

Sage grows a Chat mode beside the existing builder. Decisions 2 and 3 below still hold. Decision 1
(scratch-in-the-App, Untitled as a chip on this container) is **superseded by
[ADR-0004](0004-workbench-is-the-door.md)**. The mock that set the UX is
`etanlightstone/sage_explorations`. Manage and Code are owned by a parallel branch.

## Decision 1 — superseded by [ADR-0004](0004-workbench-is-the-door.md)

The Workbench App is the door into this viewer's Sage Builder in a `sage-*` Project. Default
replaces Untitled. New conversation is still a Thread, not a Domino project — that part of the
old decision survives in ADR-0004. Scratch `/tmp`, disabled Publish on the App, and New project
as a no-op do not.

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
