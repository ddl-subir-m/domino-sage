# Build sequence: Domino AI app builder

Prompt sequence for taking the builder project (unified Domino-hosted coding agent + live
preview + LLM switching + cost dashboard + asset explorer) from spec to shipped v1, using
the gstack skill suite.

Source doc: https://docs.google.com/document/d/1zDC3U_uTdrF-oBBUBGoN6lCSEzJEweEJmtAKS9cw5j4/edit

## Phase 1 — Spec it

### `/spec`
```
Spec out the Domino AI app builder described in this doc: [paste doc link/content].
Core scope: a Domino-hosted (not Electron, not Workspaces) unified builder container with
a coding agent, live app preview, and an IDE-mode escape hatch. Needs: (1) LLM switching —
modal-on-build, auto plan/implement mode, and sensitivity-triggered switch to a sovereign
model, (2) a per-project cost dashboard (can be stubbed), (3) a permission-scoped Domino
asset/resource explorer where attaching a dataset signals build intent.
Resolve these open questions as part of the spec instead of deferring them:
- Pick one coding harness for v1 (Claude Code, OpenCode, or Pi) and justify it — Claude Code
  is heavy for small OS models.
- Decide exact v1 scope for auto mode (what's real vs. stubbed for the demo).
- Assume sensitivity tags pre-exist on datasets; no tagging intelligence needed.
- Out of scope for v1: non-app workflows (e.g. Excel analysis), harness auto-adaptation
  per model, user-configurable harness.
Output a phased spec (demo-able v1 vs. later).
```

### `/domain-modeling`
```
Pin down the ubiquitous language for the builder project before we design: what exactly is
a "sovereign model" vs. "vendor model", a "coding harness", the "asset explorer" vs. MCP
tools used by the coding agent itself, "attach = intent", and the three LLM-switching modes
(modal-on-build, auto/plan-implement, sensitivity-triggered). Record these as an ADR so the
plan-review and design phases use consistent terms.
```

### `/prototype` (optional — only if the auto-mode state model or asset-explorer permission logic feels risky)
```
Throwaway-prototype the auto-mode plan→implement pipeline: forced expensive-model plan phase,
context reset, then cheaper-model implement phase, with the plan output declaring which model
each phase uses. I want to sanity-check the state transitions before building it for real.
```

## Phase 2 — Review the plan

### `/autoplan` (fastest — runs all four lenses with auto-decisions)
```
Run autoplan on the builder spec. Flag anywhere the CEO, eng, design, or DX lens disagrees
with the v1 scope decisions (harness pick, auto-mode scope, sensitivity-switch assumption)
so I can rule on those explicitly rather than auto-deciding them.
```

Or run interactively instead, same spec as input each time:
`/plan-ceo-review` → `/plan-eng-review` → `/plan-design-review` → `/plan-devex-review`

## Phase 3 — Design

### `/diagram`
```
Diagram the builder container architecture: coding agent, live preview pane, IDE-mode toggle,
LLM router (sovereign vs. vendor), cost dashboard, and asset explorer, showing how the
asset explorer's "attach" action feeds intent to the coding agent, and how a sensitivity tag
on an attached dataset triggers the LLM switch.
```

### `/codebase-design`
```
Help me design the module boundaries for the builder: the LLM router (mode selection +
sensitivity-triggered switch), the coding-harness adapter, and the asset-explorer/permission
layer. I want deep modules with narrow interfaces — flag if the harness adapter is leaking
harness-specific details into the router.
```

### `/design-consultation` (only if the builder UI needs its own visual identity beyond existing Domino patterns)
```
Propose a design system for the builder's UI: the model-selection modal, the cost dashboard,
and the asset explorer panel, consistent with Domino's design system but suited to a
Replit-like coding/preview experience.
```

## Phase 4 — Build

### `/tdd` (run once per vertical slice)
```
Build the LLM router test-first: given a project with no sensitivity tags, modal-on-build
mode should honor the user's explicit model choice; given a project where an attached asset
carries a sensitivity tag, the router should force the sovereign model and surface that
switch in the UI, even if the user picked a vendor model at build time.
```
Repeat for: coding-harness integration, asset explorer + permission scoping, cost dashboard
stub, IDE-mode toggle.

## Phase 5 — Verify

### `/qa`
```
QA the builder end-to-end: create a project, attach a sensitivity-tagged asset, confirm the
LLM switch fires and is visible in the UI, run the auto-mode plan→implement flow, check the
cost dashboard reflects the two phases' models, and flip into IDE mode and back.
```

### `/design-review`
Visual QA pass on the same flows.

### `/code-review` or `/simplify`
Correctness + cleanup pass on the diff before shipping.

### `/verify` (plain skill, not gstack)
Drive the actually-running app end-to-end rather than relying on tests alone — this is a
UI-heavy feature.

## Phase 6 — Ship

### `/ship`
```
Ship the v1 builder demo slice: merge base, run tests, review diff, bump version, update
changelog, PR.
```

### `/land-and-deploy`
Once ready to deploy as the actual Domino-hosted app.

---

**Note:** several open questions in the source doc are marked "Subir to decide" (model-
selection UI exposure, harness configurability, LLM Gateway integration for guardrail
alarms). The `/spec` prompt above bakes in defaults (pick one harness, no user pref, gateway
integration deferred) — confirm those are the right calls before running it, since every
later phase builds on whatever `/spec` locks in.
