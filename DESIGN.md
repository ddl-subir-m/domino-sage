---
doc: Module design for sage's three risky seams
status: draft
depends_on: SPEC.md (v2)
date: 2026-07-20
---

# Module design — the three risky seams

Deep modules: a lot of behaviour behind a small interface, placed at a clean seam, tested
through that interface. The three seams from SPEC.md v2, in dependency order:

1. **Model policy** — `LLMRouter` (pure) + the `ModelControl` state it reads.
2. **Enforcement seam** — the `GatewayProxy` (OpenAI-compatible, egress choke) + `GatewayClient`.
3. **Harness seam** — the `AgentDriver` (spawn/drive OpenCode, stream events, point it at the proxy).

The load-bearing rule that ties them together: **the router decides, the proxy enforces, the
driver is dumb about both.** If OpenCode-specific facts leak into the router or the proxy, the
enforcement guarantee stops being testable in isolation and the harness stops being swappable.

---

## Seam 1 — Model policy: `LLMRouter` + `ModelControl`

### What varies (why a seam exists)
The *decision* of which model a request uses varies along four axes (sensitivity lock, Auto
plan/implement phase, Manual toggle, modal pick) but must collapse to one answer per request.
That decision is pure and belongs behind the narrowest possible interface.

### Interface
```
LLMRouter.resolve(state: SessionState) -> ModelDecision

SessionState {
  sensitivityLocked: bool          // any attached asset carries a sensitivity tag
  mode: "auto" | "manual"
  phase: "plan" | "implement"      // meaningful in auto; the current toggle in manual
  pickedModel: ModelId | null      // modal-on-build choice
}
ModelDecision {
  model: ModelId                   // sovereign id when locked
  reason: "sensitivity" | "auto-plan" | "auto-implement" | "manual" | "modal-default"
  locked: bool                     // proxy must not let this be overridden downstream
}
```

### Why it's deep
Four inputs, one output, the entire precedence policy (`sensitivity > auto > manual > modal`)
hidden inside. Callers learn 4 fields; they get the whole switching product. It is a **pure
function** — no I/O, no clock, no OpenCode, no HTTP. The `reason` field is what powers the UI
model badge and the cost dashboard's per-phase attribution, so one function pays back across
the proxy, the UI, and the dashboard.

### Seam placement
`resolve()` is called **inside the proxy per request** (Seam 2), not at the UI or the driver.
That is the only way "locked, non-overridable" can be enforced rather than advised.

### `ModelControl` (the state owner, separate from the router)
`ModelControl` owns the mutable `SessionState` and its transitions; `LLMRouter` only reads a
snapshot. Splitting them keeps the router pure and testable and puts all the race-prone mutation
(manual toggle vs auto phase transition, attach/detach) behind one serialized owner.
```
ModelControl.setMode(mode) ; .setPhase(phase) ; .pick(model)
ModelControl.onAssetsChanged(assets)   // recomputes sensitivityLocked; sticky per session
ModelControl.snapshot() -> SessionState
```
Sticky-lock rule from the spec lives here (detach does not clear `sensitivityLocked` for the
session). One writer, so the manual-vs-auto race has a single arbiter.

### Test surface
Table-driven over the 4×2×2×N precedence matrix — pure inputs, pure outputs, zero mocks. This
is the highest-value unit test in the system and it needs no infrastructure.

### Deletion test
Delete `LLMRouter` and the precedence logic reappears smeared across the proxy, the UI badge,
and the dashboard, each drifting independently. It earns its keep.

---

## Seam 2 — Enforcement: `EnforcementShim` + `GatewayClient`

This is where the sovereign / zero-vendor guarantee actually holds. Two modules, one seam.

> **Note (existing gateway):** the Domino AI Gateway already exists, is OpenAI-compatible, and
> fronts **both** vendor and sovereign models selectable by the `model` field. So we build a
> thin **enforcement shim** in front of it, not a full proxy. The shim's job shrinks to: consult
> the router, override the `model` field, be the egress choke, forward to the gateway. The
> sovereign switch is literally "set `model` to the sovereign id." `GatewayClient` for the
> model-call path is just an OpenAI client pointed at the gateway URL; the only unknown left is
> the cost/guardrail surface.

### `EnforcementShim` — the deep module callers never see directly
It presents an **OpenAI-compatible HTTP surface** (`/v1/chat/completions`, etc.) inward to
OpenCode, and enforces policy on the way out. Its "interface" in our vocabulary is not just the
HTTP shape — it's the guarantees:

- Interface (the facts a caller/operator must know):
  - OpenCode is configured with `base_url = proxy`; it speaks vanilla OpenAI.
  - On every request the proxy calls `router.resolve(control.snapshot())` and, when
    `decision.locked`, **overwrites the requested model server-side** regardless of what the
    body asked for.
  - It is the container's **only permitted egress** (enforced by network allowlist, not code —
    see below). Direct-to-vendor is impossible, not discouraged.
  - It records every upstream call to the gateway (cost/tokens/labels) — N calls per turn map
    to N records; there is no "1:1 per turn" claim.
  - Failure modes are explicit: gateway down/slow/429/5xx surface as typed errors; when
    `locked` and the sovereign upstream is unavailable, the request is **refused, never
    downgraded to a vendor**.

- Implementation (hidden): request rewriting, per-request labeling (project/phase/model),
  retry/backoff, guardrail-event demux, streaming pass-through of tokens.

### `GatewayClient` — the adapter at the upstream seam
The shim talks to Domino's AI Gateway through a narrow adapter:
```
GatewayClient.route(req: ModelRequest, labels: CostLabels) -> ModelResponse (streamed)
GatewayClient.costs(window) -> CostRecords          // surface TBD (Step 2.3)
GatewayClient.guardrailEvents() -> stream<GuardrailEvent>   // surface TBD (Step 2.3)
```
`route()` is now known — `DominoGatewayClient` is a thin OpenAI client at the gateway URL. The
`costs()`/`guardrailEvents()` surface is the only unconfirmed part; keeping it behind this
adapter means the fake covers it until Step 2.3 confirms the real shape. Two adapters make the
seam real: `FakeGatewayClient` (conformance-tested, deterministic cost + guardrail events for
integration/E2E) and `DominoGatewayClient` (real).

### The egress guarantee is infrastructure, not a method
Critical design point from the eng review: "zero direct-to-vendor" cannot be a property of
application code, because OpenCode's shell tool or an npm postinstall can `curl` a vendor
directly. So the guarantee is split:
- **Policy** (which model) → `LLMRouter`, enforced by `GatewayProxy` rewriting the request.
- **Containment** (nothing escapes) → **container network egress allowlist**: only the gateway
  host and the Domino API host are reachable. This is deployment config the proxy assumes, and
  the integration test asserts (attempt a non-gateway host → blocked).

Keeping these two apart is what lets AC6 be tested: the proxy test proves override + labeling;
the egress test proves containment. Neither depends on OpenCode internals.

### Deletion test
Delete the proxy and every model call site would need to re-implement router consultation +
model override + labeling + failure handling, and the lock guarantee would depend on OpenCode
behaving. It's the definition of a deep module.

---

## Seam 3 — Harness: `AgentDriver`

### What varies
The coding harness (OpenCode today; possibly others in P5). Everything OpenCode-specific must
live behind this one adapter and **nowhere else**.

### Interface
```
AgentDriver.start(workspace: Path, config: AgentConfig) -> Session
AgentDriver.send(session, message) -> void
AgentDriver.events(session) -> stream<AgentEvent>       // normalized, harness-agnostic
AgentDriver.stop(session) -> void

AgentConfig {
  modelBaseUrl: Url        // ALWAYS the proxy — the only model path the driver knows
  toolAllowlist: Tool[]    // constrained shell for v1
  workspace: Path
}
AgentEvent =
  | { kind: "message", text }
  | { kind: "file_edit", path, diffSummary }
  | { kind: "tool_run", tool, status }
  | { kind: "phase", phase }        // if the harness exposes plan/implement natively
  | { kind: "error", detail }
```

### Why it's deep, and where the leak risk is
The driver hides how OpenCode is driven (embed vs subprocess vs server mode — the open spike,
Risk 4) and normalizes its native event shape into the `AgentEvent` union the UI and the
feedback loop consume. The UI never learns OpenCode's log format; it learns `AgentEvent`.

**Leak flags (the thing you asked me to watch for):**
- The router (`SessionState`) contains **no** OpenCode concepts — only `mode`/`phase`/`lock`/
  `pick`. Good: OpenCode could be swapped and `resolve()` wouldn't change.
- The proxy sees a **vanilla OpenAI request**, not an OpenCode request. It must not branch on
  `User-Agent`, OpenCode headers, or OpenCode session ids. If it ever needs to, that's the leak
  — push it back behind `AgentConfig`.
- `phase` is the one genuine coupling risk. Two clean options:
  a) The harness reports phase natively → `AgentDriver` emits `{kind:"phase"}` and
     `ModelControl` consumes it. Harness detail stays behind the driver.
  b) The harness does NOT model phases → **we** own the plan→implement boundary: `ModelControl`
     sets phase, drives a context reset by starting a fresh `Session` for implement, and hands
     over via the plan artifact in the workspace (not via OpenCode session state).
  Prefer (b) as the default assumption — it keeps phase logic in `ModelControl`, out of the
  harness, and makes the plan→implement handoff a workspace artifact both harnesses and IDE
  mode can see. Revisit only if the spike shows (a) is materially cheaper.

### The context-reset handoff is a `ModelControl` + workspace concern, not a driver flag
"Reset context between plan and implement" is not `driver.reset()`. It is: persist plan artifact
→ `ModelControl.setPhase("implement")` (router now resolves the cheaper model) → `AgentDriver.
start()` a fresh session seeded with the plan artifact. State lives in the workspace, not inside
the harness — so it survives a harness swap and is inspectable in IDE mode.

### Deletion test
Delete `AgentDriver` and OpenCode's process management + event parsing spread into the backend,
the UI, and the feedback loop, and swapping harnesses (P5) becomes a cross-cutting rewrite.

---

## How the three seams compose (one request)

```
UI intent ─▶ ModelControl (mutate state)          [Seam 1 state owner]
AgentDriver.send ─▶ OpenCode ─▶ OpenAI call to proxy base_url   [Seam 3, knows only the proxy]
EnforcementShim: router.resolve(control.snapshot()) ─▶ decision  [Seam 1 pure fn, called in Seam 2]
             override model field if decision.locked
             GatewayClient.route(req, labels) ─▶ existing Domino AI Gateway ─▶ model  [Seam 2 adapter]
             (egress allowlist makes this the ONLY way out)      [infra, not code]
guardrail/cost events ─▶ backend ─▶ UI badge + cost dashboard (keyed on decision.reason)
```

Each seam is crossed by both callers and tests at the same place:
- Router: pure unit tests.
- Proxy: integration tests with `FakeGatewayClient` (override + labeling + failure); egress test
  for containment.
- Driver: integration tests with a fake harness process emitting a recorded event stream;
  record/replay model responses through the real proxy for E2E.

## Design rules to hold the line
1. `LLMRouter` stays pure and OpenCode-free. Any harness concept appearing in `SessionState` is
   a design bug.
2. `GatewayProxy` sees only vanilla OpenAI requests. No branching on harness identity.
3. All OpenCode specifics live in `AgentDriver` + `AgentConfig`. One adapter now, but the
   interface is drawn so a second harness needs no changes upstream.
4. The egress allowlist, not code, provides containment. The proxy assumes it; a test asserts it.
5. Phase/context-reset is `ModelControl` + workspace, not a harness flag (unless the spike proves
   native phase support is clearly better).
```
