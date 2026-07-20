---
spec_title: Domino AI App Builder (codename: sage)
spec_status: v2 — reviewed via /autoplan (CEO + Design + Eng + DX)
spec_filed_at: 2026-07-20
spec_reviewed_at: 2026-07-20
spec_target: internal usable POC
source_doc: https://docs.google.com/document/d/1zDC3U_uTdrF-oBBUBGoN6lCSEzJEweEJmtAKS9cw5j4/edit
---

# Domino AI App Builder — Phased Spec (v2)

A Domino-hosted, Replit/Cursor-style builder: a per-user container running a coding agent,
a live app preview, and an IDE-mode escape hatch. It switches LLMs (pick-on-build, auto
plan/implement, manual toggle, sensitivity-triggered sovereign lock), routes every model
call through Domino's AI Gateway for cost capture and guardrail alarms, and exposes a
permission-scoped explorer of the user's Domino assets where attaching a dataset enriches
build context and can trigger the sovereign lock.

Target for v1 is an **internal usable POC**: robust enough to build several real apps.

> **v2 changes:** incorporates the /autoplan review (CEO, Design, Eng, DX lenses). The
> biggest structural corrections: the sovereign guarantee now lives in an egress-enforcing
> gateway proxy (not the router), topology is one container per user, and v1 adds a warm
> project template + an agent build/typecheck feedback loop. See "Review Summary" below.

## Context

Domino users who want an app today juggle Workspaces, Apps, and container/deploy details by
hand. There is no single place to describe an app in plain language, watch an agent build it,
see it run live, and drop into the code when the agent gets stuck. Meanwhile Domino's
AI-sovereignty position (run small open models on customer NVIDIA GPUs, keep sensitive data
off vendor APIs) has no product surface that makes the sovereign-vs-vendor tradeoff visible
and automatic. This builder is that surface.

## Locked Decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D1 | Coding harness | **OpenCode** | Open-source, model-agnostic, light enough for small OS/sovereign and vendor models. Claude Code too heavy for small OS models; Anthropic-centric. |
| D2 | Auto-mode scope | **Full pipeline + manual toggle** | Real plan→implement model switching with context reset; plus a Cursor-style user-facing plan/implement toggle. |
| D3 | LLM Gateway | **In v1** | Central routing + per-request cost/token capture + guardrail leak alarms. |
| D4 | Definition of done | **Internal usable POC** | Robust across multiple real projects. |
| D5 | App stack (agent output) | **React + Vite, single stack** | Users mainly build webapps; modern output; excellent HMR preview. **Held despite review pushback** — mitigated by warm template (C9) + agent feedback loop (C10) so small sovereign models mostly succeed. |
| D6 | Gateway integration | **Point at the existing OpenAI-compatible gateway; thin enforcement shim in front** | The Domino AI Gateway already fronts both vendor + sovereign models (OpenAI-compatible). We do not build a proxy from scratch — a thin shim consults `LLMRouter`, sets the `model` field, and is the egress choke. `GatewayClient` = an OpenAI client pointed at the gateway URL. Cost/guardrail exposure TBD (Risk 3, Step 2.3). |
| D7 | Starting point | **Greenfield from scratch** | Build fresh; do not fork or reference Felix's prototype. |
| D8 | Deploy scope | **Preview only in v1** | Build + live preview + IDE escape hatch + project download. Hosted Domino App publish is a later phase. |
| D9 | Container topology | **One container per user/project** | Domino-Workspace-style, single identity per container. Makes multi-user permission tests coherent, collapses permission-scoping to token pass-through, isolates workspace/preview/agent for free. (Added in v2.) |

## Review Summary (v2)

Full /autoplan run: CEO, Design, Eng, DX lenses (subagent voices; Codex voice degraded to
empty in this environment). Scores at review time: Design IA 2/10, state coverage 2/10,
model-switch transparency 3/10; DX 5/10 (TTHW ~5–15min → target <90s); Eng flagged 3
criticals. All findings below the "held" strategic decisions are folded into this v2.

**User decisions at the gate:** hold React+Vite (D5); keep current builder scope balance
(no moat rebalance); one container per user (D9); apply all engineering/design/DX fixes.

**Highest-confidence cross-lens themes, now addressed in v2:**
1. Enforcement seam mis-drawn → sovereign/zero-vendor guarantee moved to the gateway proxy
   + container egress allowlist (C4, build order step 1).
2. Gateway single point of failure on unpinned contract → failure states specified (States
   & Errors), AC6 rewritten, contract pinning is a hard prerequisite (Risk 3).
3. Sovereign leak windows → serialized turn queue, sticky session lock, resolve→switch→
   reset→inject ordering; gateway guardrails are preventive egress control (C3, C4).
4. Missing warm template + agent feedback loop → added as C9, C10.
5. "attach = build intent" overloaded → decoupled: NL description starts a build; attach is
   optional context enrichment + sensitivity trigger; injects a *reference*, not the data (C6).
6. No IA / onboarding / dead-ends → layout section, first-run states, persistent model
   badge, "detach to regain control" recourse (Information Architecture, States & Errors).

## Known Risks / Dependencies

1. **Small sovereign models are weaker at React/TS.** Mitigated (not eliminated) by the warm
   template (C9), the agent feedback loop (C10), a constrained component set + in-repo golden
   examples, a sovereign-tuned system prompt, and an iteration budget. A sovereign-path
   success bar is a v1 acceptance criterion (AC12).
2. **Domino App hosting for a React static bundle + backend is unconfirmed.** Blocks the
   deploy phase, not v1. Verify before P2.
3. **Domino AI Gateway — contract now largely known from the repo (see MODELS.md).** It's the
   existing OpenAI-compatible gateway (`https://<host>/apps/<id>/v1`), fronts both tiers by alias,
   auth is `Authorization: Bearer <dgw_ token>`, per-request tags are caller-settable via
   `X-LLM-Tag-*` headers (so per-phase cost IS achievable), cost/usage is exposed at
   `/api/usage/mine`, and **guardrails are preventive input/output egress control** (regex/LLM
   rules, block-before-provider), not just detection. **Remaining to get for the live spike:** the
   specific host + app id, a `dgw_` service token (or run as a Domino identity via the workspace
   sidecar), and the exact `/api/usage/mine` response shape. A conformance-tested fake satisfies
   integration tests until then.
4. **OpenCode drive + egress control.** The spike must prove OpenCode can be pointed at an
   OpenAI-compatible proxy for ALL model calls, that the proxy can override the model
   server-side, and that its event stream (messages, edits, tool/command runs) can drive the
   UI. This is the top build risk.

---

# v1 — Usable POC (in scope)

## Golden path

1. User opens their builder container from Domino (one container per user, running as their
   Domino identity).
2. **First-run state** orients them: what this is, an example prompt in the chat input, a
   pointer to the asset explorer. Fades for repeat users.
3. User **describes the app** in natural language. This alone starts a build — no dataset
   required.
4. Optionally **browses the asset explorer** (permission-scoped) and **attaches a dataset**
   to enrich context. Attach injects a *reference* (schema, column types, row count, small
   sample), not the raw data.
5. Agent (OpenCode) fills in a **warm React+Vite template** (deps pre-installed, Vite already
   serving a placeholder); user watches edits stream.
6. **Live preview** shows the running app immediately (placeholder first, then the real app),
   updating on change via proxied HMR.
7. After each agent edit, the **feedback loop** runs typecheck/build and captures console
   errors, feeding failures back to the agent to self-correct.
8. **Model control** works as one control with modes (Auto / Manual) plus a non-overridable
   sensitivity lock, all routed through the **AI Gateway**. A persistent badge shows the
   active model and why.
9. **Cost dashboard** shows real per-project token/cost from the gateway, broken down by
   model and plan-vs-implement phase.
10. If a **sensitivity-tagged** dataset is attached, the model is locked to the **sovereign**
    model; the lock and its reason are visible, with a "detach to choose another model"
    recourse. Gateway **guardrails preventively block/redact** sensitive egress; our UI surfaces those outcomes.
11. User can flip into **IDE mode** to edit files directly, and back; can **download the
    project**.

## Components

### 1. Builder container (host shell)
- One Domino-hosted container **per user/project** (D9), running as that user's Domino
  identity. Runs: builder web UI (backend + frontend), the OpenCode agent, the generated
  app's Vite dev server, and the gateway proxy.
- Backend (Python/FastAPI) orchestrates: agent lifecycle, workspace fs, preview proxy,
  gateway proxy, asset explorer, session/phase state, feedback loop.
- **Process supervisor:** process groups + cleanup on parent exit; health-check + auto-restart
  for the Vite dev server with errors surfaced to the preview pane; resource caps (memory/CPU)
  on the generated-app process so a runaway app can't starve the builder.

### 2. Coding agent (OpenCode)
- Drives the edit/run loop over the per-project workspace.
- Emits streaming events (messages, file edits, tool/command runs) into the UI.
- Pointed at the **gateway proxy** (C4) via base-URL config for ALL model calls — never
  directly at a vendor. Its shell-tool allowlist is constrained for v1 (treat "OpenCode can
  run shell" as a threat, not a feature).
- **Spike first (Risk 4):** embed vs subprocess vs server mode; the event-stream contract;
  and proof that all model calls route through the proxy.

### 3. Model control (one control, two modes, one lock)
Replaces the earlier "3 vs 4 triggers" framing. The UI exposes a single **model control**:
- **Mode = Auto:** the pipeline drives phases — **plan** (stronger model) → context reset →
  **implement** (cheaper model). Plan output is persisted as a structured **plan artifact**
  in the workspace and re-injected into implement (handoff is explicit, not a flag).
- **Mode = Manual:** the user drives, via the plan/implement toggle and/or a picked model
  (modal-on-build; remembers last choice, "don't ask again").
- **Sensitivity lock (non-overridable):** if any attached asset carries a sensitivity tag,
  the model is forced to the **sovereign** model above whichever mode is active. Sticky for
  the session (detaching does not silently release it). Recourse: "detach the sensitive
  dataset to choose another model."

`LLMRouter` is a pure function `resolve(session_state) -> model` (unit-tested on the
precedence matrix). Its decision is consulted **inside the gateway proxy per request**, so it
is enforced, not advisory.

### 4. Enforcement shim + GatewayClient (the enforcement seam)
- The Domino AI Gateway already exists, is OpenAI-compatible, and fronts **both** vendor and
  sovereign models (D6). We do **not** build a proxy from scratch. We build a thin
  **enforcement shim**: an OpenAI-compatible endpoint OpenCode targets, which per request calls
  `LLMRouter.resolve(...)` and **overrides the `model` field** when a lock/mode dictates
  (ignoring what OpenCode asked for), then forwards to the gateway. Because both tiers live
  under the gateway, the sovereign switch is exactly "set `model` to the sovereign id."
- **Mandatory tagging:** the shim tags every request with `project` + `phase` (+ model) so the
  gateway's cost/usage attributes it correctly. Untagged → the gateway's "unknown" bucket →
  per-phase savings impossible. Non-negotiable shim responsibility.
- **Container egress allowlist:** the only permitted outbound hosts are the Domino AI Gateway
  and the Domino API. This — not application logic — is what enforces "zero direct-to-vendor"
  and neutralizes credential exfiltration via npm postinstall or the shell tool.
- `GatewayClient` adapter (D6): for the model-call path it is an **OpenAI client pointed at the
  gateway URL**; auth `Bearer <dgw_ token>`; tags via `X-LLM-Tag-*`; cost via `/api/usage/mine`.
- **The gateway itself provides preventive egress guardrails** (input guardrails block/redact the
  prompt before it reaches the provider; output guardrails before the caller reads the response).
  This is defense in depth: our sensitivity lock *routes* tagged data to the sovereign model,
  while the gateway *independently* blocks/redacts sensitive egress per admin rules. Our alarm
  (below) surfaces real `guardrail_blocked` / redaction outcomes — preventive, not detective.

### 5. Live preview
- Vite dev server on an internal port; the proxy **discovers the actual port** (Vite may
  auto-increment) and reverse-proxies HTTP + the **HMR WebSocket** (documented
  `base`/`server.hmr.clientPort`/`server.origin` config). Placeholder app renders before the
  agent writes a line. Generated-app runtime errors shown raw; infra failures shown
  human-readable (see States & Errors).

### 6. Asset explorer (permission-scoped)
- Lists the user's Domino assets scoped to their existing Domino permissions (token
  pass-through under D9). No new permission model.
- **Attach = optional context enrichment**, not the sole build entry. Attaching a dataset
  injects a *reference* (schema, types, row count, sample) and shows a confirmation. Attaching
  a **sensitivity-tagged** dataset activates the sovereign lock — surfaced at attach time.

### 7. Cost view (per project) — reuse the gateway, don't rebuild
- The gateway already has a Usage & Cost page grouped by tag/model/user. **Do not rebuild a
  dashboard from scratch** (DRY). Depending on Step 2.3: if a cost **API** exists, surface a thin
  in-app per-project view (by model + phase) reading it; if UI-only, embed/deep-link the gateway's
  own page filtered to the project tag. Either way the source of truth is the gateway.
- **Hard requirement:** the shim tags every request via `X-LLM-Tag-*` headers (`project`, `phase`,
  `model`), or cost lands in the gateway's "unknown" bucket and per-phase attribution breaks.
- "Savings" is measured against the plan-phase model's rate applied to implement tokens (or drop
  the savings framing and show plain per-phase cost). Actionable empty state; labeled charts.

### 8. IDE-mode escape hatch
- Toggle from agent view into a direct file editor over the same workspace, and back. Framed
  as optional/advanced (progressive disclosure). While in IDE mode the agent is paused; on
  return, a concise **diff** of human changes is injected into the agent's context (not a full
  re-read), and OpenCode's file cache is invalidated at each turn start. User edits are source
  of truth.

### 9. Warm project template (new in v2)
- A fixed, versioned React+Vite starter baked into the container: deps pre-installed, Vite
  running against a placeholder, known-good `package.json`/`vite.config.ts`/`tsconfig.json`.
  The agent fills in `src/` against the skeleton rather than generating config. Ships a
  constrained component set + in-repo golden examples for small models to pattern-match.

### 10. Agent build/typecheck feedback loop (new in v2)
- After every agent edit: run `tsc --noEmit`, capture Vite/HMR compile output, capture
  browser console/runtime errors; inject a structured summary into the agent's next turn
  automatically. Bounded by an iteration/time/cost **circuit breaker** with a "no progress"
  detector (same error N turns) that pauses the agent and surfaces an actionable stuck-state
  tied to IDE mode.

## Information Architecture & layout
- **Two-zone default:** persistent left rail = conversation/agent (primary, always visible)
  with the model badge and model control; right work area = **Preview ⇄ IDE** segmented
  toggle (same workspace, two views).
- Asset explorer = overlay drawer; cost dashboard = drawer or secondary tab — not co-equal
  panels. One primary action per view. State the default view on first open.

## States & Errors
Error taxonomy distinguishes **system errors** (human-readable, problem+cause+fix+retry —
gateway down/slow/429/5xx, model-unavailable, npm-install failure, preview-proxy/port
failure, agent-stuck) from **user-code output** (raw — the generated app's compile/runtime
stderr). Per surface, specify loading / empty / error / partial:
- Agent: thinking indicator; first-run empty state.
- Preview: build/boot progress; empty (no app yet); Vite-crashed with restart.
- Cost dashboard: empty (no turns yet).
- Asset explorer: loading; no-assets.
- **Gateway down:** human-readable system error + retry; "your work is saved, the model
  connection is down"; documented queue-vs-hard-fail policy. Never fall back to a vendor when
  the sovereign lock is active.
- **Sovereign model unavailable:** block the turn with a clear message (sensitive data can't
  be processed until the sovereign model is available); never silently route to a vendor.
- **Guardrail alarm:** state what was detected, blocked-vs-occurred, which asset, the recovery
  action (detach/review), and an acknowledged/resolved state.

## Acceptance criteria (v1)
1. From a fresh container, a user completes the golden path for ≥2 distinct app descriptions
   without leaving the UI — including one that uses **no dataset**.
2. Attaching a sensitivity-tagged dataset activates the sovereign lock on the next turn; the
   UI shows the lock, the reason, and the detach recourse.
3. Auto mode runs plan (stronger model) → context reset via the plan artifact → implement
   (cheaper model); the cost dashboard shows distinct per-phase cost from the gateway.
4. The single model control's Auto/Manual modes behave per the documented state machine; the
   manual plan/implement toggle changes the model on the next turn.
5. Modal-on-build honors the user's pick and remembers it ("don't ask again").
6. **Every model call is recorded by the gateway; N calls per turn reconcile to N gateway
   records; zero requests reach any non-gateway host (verified by the egress policy).**
7. A gateway guardrail event produces a visible, actionable alarm with an acknowledged state;
   UI copy reflects preventive block/redact (the gateway blocks before the provider).
8. Live preview reflects agent edits within one rebuild cycle; generated-app run errors shown
   raw, infra failures shown human-readable.
9. Asset explorer shows only permitted assets (verify with two users of different scopes, each
   in their own container per D9).
10. IDE-mode edits pause the agent and round-trip as a diff into the next turn.
11. The feedback loop surfaces an agent-introduced type/build error to the agent, corrected
    within N turns without human intervention.
12. **Sovereign-path bar:** the sovereign model completes at least one designated app to a
    running preview (proves the differentiator, not just the vendor happy path).
13. No feature regresses the golden path across the multi-app test.

## Testing plan (v1)

| Layer | What | Notes |
|-------|------|-------|
| Unit | `LLMRouter` precedence (sensitivity > auto > manual > modal); `GatewayClient` cost parsing; reference-injection builder; sensitivity detection | Table-driven |
| Integration | proxy overrides model server-side under lock; egress policy blocks non-gateway hosts; auto plan→implement handoff via plan artifact; guardrail event → alarm; Vite-crash recovery + port contention; HMR through proxy | Conformance-tested **fake gateway** for deterministic cost/guardrail |
| E2E | golden path (incl. no-dataset app) via a **record/replay model harness** — assert on flow/control (switches, cost records, preview served, IDE round-trip), not exact generated code; live-model runs are periodic non-gating smoke tests | Handles agent non-determinism |

## Out of scope for v1
- Non-app workflows (e.g. Excel/data analysis).
- Harness auto-adaptation per model; user-configurable harness.
- Publishing/deploying the finished app as a hosted Domino App (P2).
- Sensitivity tagging intelligence (tags assumed pre-existing).
- Dismissible/annotated guardrail alarms and full audit trail (P3).
- Shared multi-user container isolation (D9 uses per-user containers instead).

---

# Later phases (out of v1, sequenced)

**P2 — Deploy as a Domino App (fast-follow).** One-click publish of the finished React app.
Blocked on confirming Domino App hosting for a React static bundle + backend (Risk 2).

**P3 — Governance depth.** Configurable guardrail policies, dismissible/annotated alarms,
auditable trail of switches and leaks, org-level cost/governance view.

**P4 — Broader app surface.** Additional frameworks/backends beyond React+Vite.

**P5 — Harness flexibility.** Per-model harness adaptation and/or user-selectable harness.

## Dependency graph

```
Spike: OpenCode drive + egress proof ──┐
Gateway proxy + egress allowlist ──────┼─> Model control (Auto/Manual + sovereign lock)
GatewayClient adapter seam ────────────┘        │
Warm template + Vite preview proxy ──────────────┼─> Agent feedback loop
Asset explorer (perms, per-user container) ──────┤        │
Cost dashboard (reads gateway) ──────────────────┘        │
IDE mode ── independent                                    │
                                                           └─> P2 deploy (needs hosting confirmation)
```

## Build order (revised per review)
1. **Enforcement seam first:** gateway proxy + container egress allowlist; prove OpenCode
   routes ALL model calls through it and the proxy can override the model server-side. This
   IS the core of the OpenCode spike (Risk 4) — do not build router semantics until proven.
2. Resolve deterministic harnesses: record/replay model harness + conformance-tested fake
   gateway; confirm gateway per-request labeling now (Risk 3).
3. Warm template + workspace + preview proxy (agent-edited React app running live) + process
   supervisor / HMR / port discovery.
4. `LLMRouter` + modal-on-build (simplest mode).
5. Agent feedback loop + circuit breaker.
6. Asset explorer (per-user token) + optional attach + sensitivity lock (with leak-window
   ordering: resolve → switch → reset → inject; serialized turn queue).
7. Auto mode (plan→implement via plan artifact) + manual toggle.
8. Cost dashboard + guardrail alarm.
9. IDE mode + project download.
10. Harden the golden path across multiple apps incl. no-dataset + sovereign-path bar;
    per-user permission tests.
```
