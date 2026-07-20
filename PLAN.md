---
doc: Execution plan — Domino AI App Builder (sage)
status: active
depends_on: SPEC.md (v2), DESIGN.md
date: 2026-07-20
legend: effort shown as (human est / CC-assisted est). [ ] = not started.
---

# Execution plan

Ordered, tasked, gated. Steps 1–2 are **spikes that gate everything else** — do not start
feature work (Step 3+) until the Phase-0 go/no-go passes. Effort is dual-scaled (human /
CC-assisted). Each task lists its exit criteria; each step lists a gate.

Traceability: task IDs map to SPEC.md components (C#), acceptance criteria (AC#), risks (R#),
and DESIGN.md seams (S1 router, S2 proxy, S3 driver).

---

## Phase 0 — Spikes (de-risk before building)

The single question Phase 0 answers: **can we enforce the sovereign / zero-vendor guarantee
through OpenCode, and can we test the whole system deterministically?** If no, the product
premise is at risk and scope changes before we build features.

### Step 1 — Enforcement seam + OpenCode drive spike  (R4, R3, S2, S3, C2, C4)

Goal: prove OpenCode can be pointed at our proxy for **all** model calls, that the proxy can
**override the model server-side**, and that container egress can be locked to gateway-only.

- [x] **1.1 Stand up the enforcement shim in front of the existing Domino AI Gateway** (S2).
  The gateway is already OpenAI-compatible and fronts both vendor + sovereign models, so this is
  a thin shim (accept `/v1/chat/completions` streaming, forward to the gateway URL, log every
  request), NOT a proxy build. (0.25–0.5d / 1–2h)
  - Exit: `curl` through the shim streams a real completion from the gateway.
- [x] **1.2 Point OpenCode at the proxy via `base_url`** and confirm it uses it for **every**
  model call — initial turn, tool-loop sub-calls, retries (S3, C2). (1–2d / 4–8h)
  - Exit: run 3 representative OpenCode tasks; proxy log shows 100% of calls; **zero** calls
    bypass it. Document how OpenCode is driven (embed / subprocess / server mode) — this
    resolves R4's first half.
- [x] **1.3 Server-side model override.** Shim overrides the `model` field when a `locked` flag is
  set, ignoring the body's model (S2, AC2/AC6). Both tiers live under the gateway, so this is just
  setting the field to the sovereign id. (0.25d / 1–2h)
  - Exit: request asks for `vendor-x`, shim forces `sovereign-y`, the gateway serves `sovereign-y`.
- [ ] **1.4 Container egress allowlist.** Configure the container so only the gateway host (and
  Domino API host) are reachable; everything else refused (S2 containment, AC6). (0.5–1d / 2–4h)
  - Exit: from inside the container, a direct `curl` to a vendor API **fails**; through the
    proxy **succeeds**. This is the containment half of "zero direct-to-vendor."
- [ ] **1.5 Egress bypass probe.** Try to defeat 1.4 the ways the eng review named: OpenCode
  shell tool `curl`, an npm `postinstall` script, a subprocess in the generated app (R4, security).
  (0.5d / 2–4h)
  - Exit: all bypass attempts blocked by the allowlist; documented. If any succeed → **red flag,
    raise at the gate.**
- [~] **1.6 Event stream normalization proof** (S3). Capture OpenCode's native event stream and
  map a sample to the `AgentEvent` union (message / file_edit / tool_run / phase / error).
  (0.5–1d / 2–4h)
  - Exit: a recorded OpenCode run replays into `AgentEvent[]`; confirm whether OpenCode exposes
    plan/implement phase natively (decides DESIGN.md Seam-3 option a vs b).

**Step 1 gate:** routing (1.2) + override (1.3) VERIFIED live. Egress allowlist (1.4/1.5) is
**BLOCKED on platform** (we don't control workspace egress) — tracked as a production-readiness
dependency, not a Phase-1 blocker; interim mitigation = OpenCode has only the sage-gateway
provider. Event normalization (1.6) DEFERRED to Phase 1 (UI groundwork). See SPIKE-REPORT.md.
→ Conditional green-light: proceed to build; egress must close before production for regulated use.

### Step 2 — Deterministic test + gateway-contract spike  (R3, S2)

Goal: make the system testable without a live model or a finished gateway, and pin the gateway
contract enough to build the cost dashboard + guardrail alarm for real.

- [x] **2.1 `FakeGatewayClient`** (S2 adapter) — deterministic cost/token records + scriptable
  guardrail events, conformance-tested against the same interface the real client will implement.
  (1d / 3–5h)
  - Exit: proxy runs end-to-end against the fake; integration tests assert override + labeling +
    failure modes with no network.
- [ ] **2.2 Record/replay model harness** — capture real model responses through the proxy, replay
  them for deterministic E2E (AC1/AC13, handles agent non-determinism). (1–2d / 4–8h)
  - Exit: an E2E run asserts on **flow/control** (switch fired, cost recorded, preview served,
    IDE round-trip) not exact generated code; reruns are stable.
- [x] **2.3 Gateway contract** (R3) — RESOLVED from the repo (see MODELS.md): OpenAI base
  `/apps/<id>/v1`, auth `Bearer dgw_`, per-request `X-LLM-Tag-*` (so per-phase cost works), cost
  API `/api/usage/mine`, preventive input/output guardrails. Remaining is not contract but
  access: get the host+app-id, a `dgw_` token (or sidecar), and confirm `/api/usage/mine` shape
  (see gateway-questions.md §"Still needed"). Cost view (C7) = API-read; per-phase savings
  possible; guardrail alarm (C4/AC7) stays in v1 (preventive).

**Step 2 gate:** deterministic proxy + E2E harness exist, and the gateway contract is pinned (or
the dependent ACs are formally deferred with a date). → Only now start feature work.

**Phase-0 go/no-go:** both step gates green. Produce a one-page spike report (what held, what
broke, any scope changes). This is the real exit from "will it work" to "build it."

---

## Phase 1 — Core loop (get an agent-built app running live)

### Step 3 — Warm template + workspace + preview  (C1, C5, C9, R1, R2)
- [x] 3.1 Versioned React+Vite warm template: deps pre-installed, Vite serving a placeholder,
  known-good config, constrained component set + golden examples (C9, R1). (1–2d / 4–8h)
  - Exit: fresh workspace shows a running placeholder app in <90s (TTHW target).
- [x] 3.2 Workspace fs module: per-project dir, plan-artifact location, agent edits land here (S3). (0.5d / 2–3h)
- [x] 3.3 Preview proxy: dynamic Vite port discovery + HTTP + **HMR WebSocket** proxying (C5). (1–2d / 4–8h)
  - Exit: agent edit → preview updates via HMR inside the iframe.
- [ ] 3.4 Process supervisor: spawn/health/restart Vite, cleanup on parent exit, resource caps (C1). (1–2d / 4–8h)
  - Exit: kill Vite → supervisor restarts it, error surfaced to preview pane; runaway app can't
    starve the backend.

**Gate:** the agent (still hand-driven) can edit files and the change shows live.

### Step 4 — Model policy + modal-on-build  (S1, C3, AC5)
- [x] 4.1 `LLMRouter.resolve()` pure fn + table-driven precedence tests (S1, AC-router). (0.5–1d / 2–4h)
- [x] 4.2 `ModelControl` state owner (mode/phase/pick/sticky-lock), single serialized writer (S1). (1d / 3–5h)
- [x] 4.3 Wire `resolve()` into the shim per request (S1×S2). (0.5d / 2–3h)
- [ ] 4.4 Modal-on-build UI + "remember my choice / don't ask again" (C3, AC5). (0.5–1d / 2–4h)

**Gate:** model picked at build is honored and enforced by the proxy (AC5).

### Step 5 — Agent feedback loop + circuit breaker  (C10, AC11, R1)
- [ ] 5.1 After each edit run `tsc --noEmit` + capture Vite/build + browser console; inject
  structured summary into the agent's next turn (C10, AC11). (1–2d / 4–8h)
- [ ] 5.2 Circuit breaker: iteration/time/cost budget + "no progress" detector → pause + actionable
  stuck-state tied to IDE mode (C10, R1). (1d / 3–5h)

**Gate:** an agent-introduced type/build error is auto-surfaced to the agent and fixed within N
turns without a human (AC11).

---

## Phase 2 — Domino integration + switching

### Step 6 — Asset explorer + attach + sensitivity lock  (C6, S1, AC2, AC9, security)
- [ ] 6.1 Permission-scoped asset explorer via user-token pass-through, per-user container (C6, AC9). (1–2d / 4–8h)
- [ ] 6.2 Optional attach → inject a **reference** (schema/types/rowcount/sample), not raw data
  (C6, eng finding). (0.5–1d / 2–4h)
- [ ] 6.3 Sensitivity lock wiring + leak-window ordering: resolve→switch→reset→inject, serialized
  turn queue, sticky session lock (S1, AC2, R-leak). (1–2d / 4–8h)
  - Exit: attach tagged dataset mid-session → next turn is sovereign; no in-flight vendor leak.

**Gate:** AC2 (forced sovereign on next turn, visible + detach recourse) and AC9 (two users, two
containers, correct scoping).

### Step 7 — Auto mode (plan→implement) + manual toggle  (C3, S1, AC3, AC4)
- [ ] 7.1 Plan artifact write/read in workspace as the phase handoff (DESIGN Seam-3 option b). (1d / 3–5h)
- [ ] 7.2 Auto pipeline: plan (strong) → context reset via fresh session seeded with artifact →
  implement (cheap) (C3, AC3). (1–2d / 4–8h)
- [ ] 7.3 Manual plan/implement toggle mapping to the same phase→model logic (C3, AC4). (0.5d / 2–4h)

**Gate:** AC3 (distinct per-phase cost from gateway) + AC4 (toggle changes model next turn).

### Step 8 — Cost dashboard + guardrail alarm  (C4, C7, AC3, AC6, AC7)
- [ ] 8.1 Cost view — reuse the gateway (DRY), don't rebuild (C7). Per Step 2.3: API-read a thin
  in-app per-project view, OR embed/deep-link the gateway's Usage & Cost page filtered to the
  project tag. Depends on the shim tagging every request (project + phase). (0.5–1.5d / 2–6h)
- [ ] 8.2 Guardrail alarm UI: what/blocked-vs-occurred/which asset/recovery/acknowledged, framed as
  detective backstop (C4, AC7). (1d / 3–5h)
- [ ] 8.3 Rewrite/verify AC6 as "N calls reconcile to N records + zero non-gateway egress." (0.5d / 2–3h)

**Gate:** AC6 + AC7 (blocked on Step 2.3 gateway contract go-live).

---

## Phase 3 — Escape hatch + hardening

### Step 9 — IDE mode + project download  (C8, AC10)
- [ ] 9.1 IDE-mode file editor over the workspace; agent paused while editing; on return inject a
  diff (not full re-read); invalidate agent file cache at turn start (C8, AC10). (1–2d / 4–8h)
- [ ] 9.2 Project download (zip the workspace) — cheap escape hatch since deploy is P2 (C8). (0.5d / 1–2h)

**Gate:** AC10 (IDE edits round-trip into next turn).

### Step 10 — Harden the golden path  (AC1, AC8, AC12, AC13)
- [ ] 10.1 Full golden path incl. a **no-dataset** app (AC1). (elapsed — QA)
- [ ] 10.2 **Sovereign-path bar**: sovereign model completes a designated app to running preview
  (AC12) — the differentiator, not just the vendor happy path. (elapsed — QA)
- [ ] 10.3 States/errors matrix: gateway-down, sovereign-unavailable, build/install/port failures,
  agent-stuck (SPEC States & Errors, AC8). (1–2d / 4–8h)
- [ ] 10.4 Per-user permission tests across two containers (AC9). (0.5d / 2–4h)
- [ ] 10.5 Multi-app regression pass (AC13). (elapsed — QA)

**Gate (v1 done):** all ACs 1–13 pass; States & Errors covered; run `/qa` then `/verify`.

---

## Critical path & parallelism
- **Critical path:** 1 → 2 → 3 → 4 → 6 → 7 → 8 → 10. (Enforcement + policy + switching are the spine.)
- **Parallelizable:** Step 5 (feedback loop) after 3; Step 9 (IDE mode) is independent after 3;
  Step 2.3 (gateway contract) runs in the background from day one — it's the longest-pole external
  dependency and gates Step 8.
- **External blockers to start now:** gateway contract (2.3) and React-on-Domino-App hosting
  confirmation (R2, needed only for P2 deploy, not v1).

## Rough sizing
- Phase 0 (spikes): ~1–1.5 weeks human / ~1–2 days CC-assisted + external gateway wait.
- Phases 1–3 (v1 build): ~4–6 weeks human / ~1.5–2.5 weeks CC-assisted, gateway contract permitting.
- These are order-of-magnitude; re-estimate after the Phase-0 spike report.

## Definition of done (v1)
SPEC.md acceptance criteria 1–13 pass, States & Errors matrix covered, `/qa` + `/verify` green,
and the Phase-0 spike report shows the enforcement guarantee held under the bypass probe.
