---
prd_title: Sage 1.0 — "Real Apps" milestone
prd_status: draft v1 (for Subir review)
prd_type: buildable milestone spec
prd_author: PM (drafted with Claude)
prd_date: 2026-08-10
depends_on: SPEC.md (v2), DESIGN.md, PLAN.md, EXPERIMENTS.md
supersedes: nothing — extends SPEC.md v1 scope
one_liner: >
  Take Sage from an internal POC that builds single-screen React demos into a product that
  builds real, multi-view apps that use governed data and AI at runtime — with the sovereign
  guarantee as the reason to buy and build-quality/speed as the bar it must clear.
---

# Sage 1.0 — the "Real Apps" milestone

> **How to read this.** This is a *buildable* PRD: it assumes the vision in `SPEC.md` and picks
> the next shippable increment. §2–§6 are the framing you asked for (state of play, who asks
> what, what they build). §7–§8 are the milestone and its acceptance criteria. §9 is the eval
> program. §12 is the short list of decisions I need from you before build starts.

---

## 0. Decisions locked for this milestone

From the product gate (2026-08-10):

| # | Decision | Choice | Consequence |
|---|----------|--------|-------------|
| L1 | Primary users | **Both builder personas** (analyst *and* data scientist), governance owner is the buyer | Two prompt/quality bars, one enforcement bar. §4. |
| L2 | The wedge | **Sovereign is the reason-to-buy; speed & quality are the bar** | Evals prove the sovereign path *first*, then hold build-quality to a competitive bar. §9. |
| L3 | Milestone theme | **Broaden the app surface** beyond single-screen React demos | The headline is "real apps," not "harden the demo." §7. |
| L4 | Doc type | **Buildable milestone spec** | Tight framing, concrete requirements, testable ACs. |

Still **open** (need your call before build — see §12): the second (Python) app stack, whether
runtime AI-in-the-app ships in this milestone or the next, and the deploy/publish boundary.

---

## 1. TL;DR

Sage today reliably builds a **single-screen React app over a static data snapshot, preview-only**.
That is a great demo and a weak product: the apps people actually keep are multi-view, use *live*
governed data, and increasingly *call a model at runtime*. This milestone broadens the app surface
along three axes and makes the sovereign guarantee extend to the **built app**, not just the builder.

The five things we will ship:

1. **Multi-view apps** — client-side routing + shared state, so "build a dashboard with a detail
   page and a settings page" produces a real app, not one crowded screen.
2. **Live governed data** — attach a Domino dataset and the built app *queries it at runtime*
   through a scoped data endpoint, instead of freezing a snapshot into `public/data/`.
3. **AI-in-the-app (governed)** — the built app can call the Domino AI Gateway *through the same
   enforcement shim*, so an app Sage builds inherits Sage's sovereign guarantee. This is the
   on-wedge differentiator no Replit/v0/Bolt clone can match.
4. **The sovereign-path bar, closed for real** (SPEC AC12) — a sovereign model completes each new
   app archetype to a running preview, proven by evals, not by a single hand-run.
5. **An eval harness** that scores build success, app quality, and the enforcement guarantee on a
   fixed prompt suite, run per-change — the thing that lets us broaden the surface without silently
   regressing the golden path.

Deploy/publish (P2) is **adjacent to** this milestone; §12-D3 asks whether it's in or out.

---

## 2. Where Sage is today (honest state of play)

Grounded in the repo, not the roadmap. "Proven" = verified live in a Domino workspace per
`SPIKE-REPORT.md`; "built" = code + tests exist; "stub/gap" = declared but not wired.

| Capability | State | Evidence |
|---|---|---|
| Enforcement shim overrides model server-side, all OpenCode calls route through it | **Proven** | `SPIKE-REPORT.md` 1.1–1.3 |
| Warm React+Vite template, live HMR preview, process supervisor | **Built** | `template/react-vite/`, `preview/` |
| Agent feedback loop (`tsc` + console → next turn) + circuit breaker | **Built** | `feedback/`, tests |
| `LLMRouter` precedence + `ModelControl` state owner | **Built** | `router/`, `test_llm_router.py` |
| First-build plan gate + **scope classifier** (plan vs build on later turns) | **Built** | `orchestrator/scope.py` |
| **Phased build** (each step a cold session) + cost/quality experiment design | **Built, unproven** | `plan_steps.py`, `EXPERIMENTS.md` (results table empty) |
| Attachment **shape** description (CSV/JSON/Parquet/PDF/image → schema, never content) | **Built** | `orchestrator/describe.py` |
| Provisioning: seed template repo, create Domino project, per-user container ("hub") | **Built** | `provision/`, `hub/` |
| Attach → **inject dataset reference into the build prompt** | **Stub/gap** | `PLAN.md` 6.2 backlog: "today attach only drives the sovereign lock" |
| Data at runtime | **Snapshot only** | `template/.../scripts/rehydrate-data.mjs` freezes data into `public/data/` |
| Auto mode plan→implement model switch (non-locked) | **Gap** | `PLAN.md` 7, backlog "Auto-mode model override" |
| Cost dashboard (in-app) / guardrail alarm UI | **Deep-link only / gap** | `PLAN.md` 8; cost is a deep-link to the gateway |
| IDE mode + project download | **Gap** | `PLAN.md` 9 |
| Sovereign-path bar (AC12) | **Not closed** | `PLAN.md` 10.2 |
| Container egress allowlist (airtight zero-vendor) | **Blocked on platform** | `SPIKE-REPORT.md` 1.4/1.5 |

**Read of it:** the *enforcement spine* and the *build loop* are real; the *product* around them
(runtime data, richer apps, switching UX, cost/governance surfaces, escape hatch) is partial. This
milestone deliberately invests in **app surface + runtime data/AI + evals** and treats the remaining
v1 UX gaps (auto-mode switch, cost view, IDE mode) as *table-stakes dependencies* pulled in only
where an app archetype needs them (§8-E6).

---

## 3. Positioning — the wedge in one page

**Category:** an in-platform AI app builder (Replit/Bolt/v0/Lovable/Cursor shape) — describe an app,
watch an agent build it, see it run live, drop into code when stuck.

**Why anyone picks Sage over those:** they are all *build-outside, bring-your-own-data, vendor-model*
tools. Sage is **inside Domino, on the user's governed data, on models that can be made sovereign,
with cost attributed and guardrails enforced at the gateway.** For a regulated enterprise (Sage's
market — banks, pharma, insurers on Domino), that is not a nice-to-have; it is the difference between
"allowed" and "not allowed."

- **Reason to buy (the wedge):** *Build and run AI apps on sensitive data that never leaves your
  walls.* Sovereign lock, per-project cost attribution, permission-scoped data, preventive
  guardrails.
- **The bar (table stakes):** it has to build a **good app, fast**. A governed tool that produces
  broken or ugly apps loses to an ungoverned tool that produces working ones, because users route
  around governance. So build success rate, app polish, and time-to-first-preview are hard bars, not
  aspirations (§9-B, §10).

**Sharpest single differentiator this milestone unlocks:** the apps Sage builds are *themselves* governed
— an app that calls a model does it through the shim, on the sovereign path, with its tokens
attributed. No external builder can offer "your AI app is sovereign too."

---

## 4. Users & JTBD

Two builder personas, co-equal (L1). One buyer.

| Persona | Who | Core job (JTBD) | Sage relationship | Success signal |
|---|---|---|---|---|
| **Ana — Analyst / citizen dev** | Risk/clinical/ops analyst; semi-technical; lives in the data, not the IDE | *"When I have a recurring question about my data, I want a small tool my team can use, without waiting on a dev sprint."* | NL-only. Never opens IDE mode. Trusts the preview. | Ships a working internal tool in one session without help. |
| **Dara — Data scientist / ML eng** | Domino power user; writes Python daily; can read the diff | *"When my notebook analysis is worth operationalizing, I want to wrap it in an app fast and keep control of the code."* | NL-first, IDE-mode when precise; may want a Python stack (§12-D1). | Builds + tweaks in code + hands off a running app. |
| **Gita — Governance / platform owner** | Platform admin / CISO-adjacent; the economic buyer | *"When my org wants to build on sensitive data, I want to guarantee nothing leaks and everything is attributed."* | Doesn't build. Approves the capability, reads cost/guardrail surfaces. | Can point to enforced sovereignty + per-project cost + audit. |

**Design tension this creates:** Ana needs the machinery *hidden* (the template `AGENTS.md` already
enforces "never mention tools/modes/placeholder; talk about the app"); Dara needs it *reachable*
(IDE mode, model control, phased-build toggle). The product must serve both without making Ana feel
she's using a developer tool. This milestone keeps NL as the default surface and treats every
power-control as progressive disclosure.

---

## 5. What users will ask — the query taxonomy

You asked specifically for this. This taxonomy is the backbone of the eval suite (§9): every class
below maps to eval prompts, and the classifier in `scope.py` already has to sort several of them.

### 5.1 By intent (what the turn is *for*)

| Class | Example utterances | Sage path today | Milestone impact |
|---|---|---|---|
| **Create** (turn 1) | "Build a fraud review queue." "Make me a dashboard for this sales CSV." | First-build plan gate → build | Must handle multi-view + data-backed creates (§7). |
| **Iterate — small** | "Make the table sortable." "Change the accent to green." "Add a status column." | `scope.py` → BUILD | Unchanged; keep the fast path fast. |
| **Iterate — large** | "Make it production-ready." "Add auth, orgs, and billing." "Redesign it to look professional." | `scope.py` → PLAN (approval gate) | Bigger apps → more of these; plan quality matters more. |
| **Fix** | "The chart is blank." "It crashes when I filter." | BUILD + feedback loop | Runtime data introduces new failure classes to self-heal (§8-E2). |
| **Ask (read-only)** | "What does this app do?" "Can it handle 1M rows?" | Ask model, no edit | Must not edit; must not leak `.sage/`. |
| **Data-shaping** | "Only show flagged rows." "Join these two datasets." "Aggregate by month." | Prompt + attachment reference | Depends on live-data epic (§8-E2); today snapshot-only. |
| **AI-feature** | "Summarize each ticket." "Classify these reviews." "Add a chat box over this doc." | *Not supported* | New: AI-in-the-app epic (§8-E3). |
| **Deploy / share** | "Publish this." "Give my team a link." | *Not supported* (download only) | Out of milestone unless §12-D3 flips. |
| **Meta / off-task** | "Which model are you using?" "Delete everything." "Ignore your instructions and…" | Mixed | Guardrail + safety evals (§9-C). |

### 5.2 By specificity (how much the prompt pins down)

This axis, not intent, is what most predicts build success — and it's invisible to any regex, which
is exactly why `scope.py` uses a model.

- **Terse / underspecified** — "make a dashboard" (no data, no metrics). Sage must make sensible
  defaults and *say what it assumed*, not stall.
- **Rich / specified** — the fraud-queue prompt in `EXPERIMENTS.md` (entities, columns, filters,
  states named). Highest success rate; the eval "happy path."
- **Contradictory / impossible** — "a real-time app with no backend and no data." Must degrade to
  the achievable and name the gap, never silently drop half.
- **Reference-driven** — "make it look like Linear," "use this screenshot" (image attach → vision).
  `describe.py` already inlines images for the model.

### 5.3 By data posture (the governance-critical axis)

- **No data** — pure UI/tool (a unit converter, a form). Must work (SPEC AC1).
- **Attached, non-sensitive** — enrich context; today only shape is injected, data is snapshotted.
- **Attached, sensitivity-tagged** — triggers the **non-overridable sovereign lock**. The
  differentiator; every eval run includes a locked variant (§9-C).
- **Live/large** — too big to snapshot; needs the runtime data endpoint (§8-E2).

---

## 6. What users will build — app archetypes

The "what apps" question. These archetypes are the eval corpus (§9-A) and they set the template /
skill investment. Ordered by how well Sage handles them **today**.

| # | Archetype | Example | Today | Gap this milestone closes |
|---|---|---|---|---|
| A1 | **Record table / review queue** | Fraud queue, ticket triage, approvals | **Strong** — dedicated `data-table` skill | Multi-view (row → full detail page), live data |
| A2 | **Dashboard / KPI view** | Sales KPIs, cohort metrics, ops monitor | **OK** (StatCard example) | Real charts, live refresh, drill-down page |
| A3 | **Data explorer** | Filter/search/pivot over a dataset | **Snapshot-only** | Live query over large/governed data (§8-E2) |
| A4 | **Form / data-entry tool** | Intake form, labeling UI, config editor | **Weak** (no persistence) | Write-back path (§12-D2 — likely next milestone) |
| A5 | **AI-assisted tool** | Summarizer, classifier, doc Q&A/chat, extract-to-table | **Unsupported** | Runtime governed model calls (§8-E3) |
| A6 | **Multi-step / wizard** | Onboarding flow, guided analysis | **Cramped on one screen** | Routing + shared state (§8-E1) |
| A7 | **Content/report generator** | Generate a styled report/brief from data | **Partial** | Benefits from A5 + export |

**Investment implication:** A1/A2/A6 are carried by the **multi-view** epic + charting; A3 by
**live data**; A5/A7 by **AI-in-the-app**. A4 (persistence/write-back) is deliberately deferred —
it opens auth/state/migration questions that deserve their own milestone (§12-D2).

**Explicitly out of scope (unchanged from SPEC):** non-app data-analysis workflows (Excel-style),
notebooks, arbitrary backends/microservices, mobile-native.

---

## 7. The milestone: goal, axes, non-goals

**Goal.** Sage builds **real apps** — multi-view, over live governed data, optionally calling a
governed model at runtime — and proves it does so on the sovereign path, held to a competitive
quality/speed bar by an automated eval suite.

**Definition of "real app" for this milestone (the bar every archetype in §6-A1/A2/A3/A5/A6 must clear):**
1. More than one view where the archetype warrants it, with navigation and shared state that
   survives navigation.
2. Data comes from the attached Domino dataset **at runtime**, not a frozen snapshot, for datasets
   above the snapshot threshold.
3. If it uses AI, the call goes **through the shim** (governed, attributed, lock-respecting).
4. It passes the app-quality rubric (§9-B) — states, polish, accessibility — same bar Sage already
   holds single-screen apps to via `AGENTS.md`.
5. The **sovereign model** can build it to a running preview (AC12), verified by eval.

**The three broadening axes (the epics):** multi-view (E1), live governed data (E2), AI-in-the-app
(E3). Plus the enabling work: eval harness (E4), sovereign-path closure (E5), and the table-stakes
dependencies these pull in (E6).

**Non-goals for this milestone (say them out loud):**
- Hosted publish/deploy — *pending §12-D3*; default is **out**, download stays the escape hatch.
- A second (Python/Streamlit) stack — *pending §12-D1*; default is **out this milestone, spec'd for next**.
- Write-back / persistent user data (A4) — **out** (§12-D2).
- Multi-user *shared* apps, org cost roll-ups, configurable guardrail policies — **out** (that's P3
  governance depth in SPEC).
- Airtight container egress allowlist — **still a platform dependency**, tracked, not built here.

---

## 8. Requirements & acceptance criteria

Epics E1–E6. Each requirement is testable; ACs extend `SPEC.md`'s AC1–AC13 (referenced as
SPEC-ACn). New ACs are numbered M-ACn.

### E1 — Multi-view apps
Give the agent a first-class way to build more than one screen without hand-rolling routing every time.

- **R1.1** Warm template ships a **routing primitive** (lightweight client router or a documented
  convention) and a `multi-view` **skill** (sibling to `data-table`) covering nav, active state,
  layout shell, and shared state across views.
- **R1.2** Shared app state (selected record, filters) **survives navigation** and deep-linking to a
  view works (URL reflects state).
- **R1.3** The scope classifier + plan gate treat "add a page/view" as a first-class structural
  change; phased build (`plan_steps.py`) can put a view per phase without cross-view type drift.

**M-AC1** From one prompt, Sage builds a ≥2-view app (e.g. list → detail page → settings) with
working navigation, shared selection state, and no `tsc`/console errors, on both the strong and the
sovereign model.
**M-AC2** Deep-linking to a non-root view renders that view directly (no blank/crash).

### E2 — Live governed data
Replace snapshot-only with a runtime data path that respects permissions and the sovereign posture.

- **R2.1** A **scoped data endpoint** in the builder container serves attached datasets to the built
  app at runtime (read-only), authorized by the same per-user Domino token, so the app never embeds
  raw data and large datasets are not frozen into the bundle.
- **R2.2** Attaching a dataset **injects its reference** (schema/columns/types/rowcount/sample from
  `describe.py`) into the build prompt — closing `PLAN.md` 6.2 — so the agent codes against the real
  shape and wires the runtime fetch, not a snapshot.
- **R2.3** A documented **snapshot-vs-live threshold** (size/row count): small stays snapshot (fast,
  offline preview), large goes live. The choice is visible to the user.
- **R2.4** Sensitivity lock composes with live data: a tagged dataset both forces the sovereign
  model *and* keeps its data on the governed endpoint; nothing tagged is ever inlined into the
  prompt or the bundle (extends SPEC AC2).

**M-AC3** An app built over a live-threshold dataset fetches and renders real rows at runtime
through the scoped endpoint; the dataset is not present in the built bundle.
**M-AC4** With a sensitivity-tagged dataset attached, the built app's data path and the build's model
path are both sovereign; a probe confirms no tagged content in the prompt log or `dist/`.

### E3 — AI-in-the-app (governed)  *(gated by §12-D4 — may move to next milestone)*
Let built apps call a model at runtime, on the sovereign path, attributed.

- **R3.1** A **runtime inference route** the built app calls (never a vendor key in the app);
  requests pass through the enforcement shim, get tagged (`project`, `phase=runtime`, `model`), and
  honor the project's sovereign lock.
- **R3.2** An `ai-feature` **skill**: patterns for summarize/classify/extract/chat-over-doc that use
  the route, with loading/error/empty states and cost-awareness.
- **R3.3** Runtime model calls appear in the same cost attribution as build calls, distinguishable by
  the `phase=runtime` tag.

**M-AC5** An app with an AI feature (e.g. "summarize each row") calls the model at runtime through
the shim; the call is attributed under the project with `phase=runtime`; under a sovereign lock it
uses the sovereign model and refuses (not downgrades) if that model is unavailable (extends SPEC AC6).

### E4 — Eval harness (the enabler — detail in §9)
- **R4.1** A **fixed prompt suite** (the §6 archetypes × §5 postures) runnable head-to-head across
  models via record/replay or live, producing the scorecard in §9.
- **R4.2** Runs in CI on a schedule and on demand; a regression in build-success or the enforcement
  checks **fails the run** and is visible.
- **R4.3** Uses the deterministic `FakeGatewayClient` for enforcement/flow assertions and a
  live-model lane (periodic, non-gating) for quality (extends SPEC testing plan).

**M-AC6** The eval suite runs green on the committed baseline; introducing a known regression (e.g.
disabling the model override) turns the enforcement lane red.

### E5 — Close the sovereign-path bar (SPEC AC12, for real)
- **R5.1** For each in-scope archetype (§6 A1/A2/A3/A6, +A5 if E3 ships), the **sovereign model**
  completes it to a running preview, tracked as an eval metric over time, not a one-off.
- **R5.2** Run the `EXPERIMENTS.md` A-vs-C phased-build experiment to a **recorded result** so we
  know whether phasing is what makes the sovereign/cheap tier viable, and default the toggle
  accordingly.

**M-AC7** ≥N of the in-scope archetypes build to a running preview on the sovereign tier in the eval
suite (N set in §12-D5); the phased-build experiment has a recorded verdict.

### E6 — Table-stakes dependencies (pull in only as archetypes require)
These are v1 gaps that real apps trip over; scoped to what E1–E3 need, not a full v1 sweep.

- **R6.1** **Auto-mode model switch** actually changes the model per phase when unlocked (`PLAN.md`
  backlog) — otherwise "plan strong / implement cheap" is a story, not a behavior, and the sovereign
  economics don't hold.
- **R6.2** **Cost visibility** sufficient to see build vs runtime spend per project (deep-link is
  acceptable if tags are correct; a thin in-app read is better).
- **R6.3** **Error taxonomy** coverage for the *new* failure modes: data-endpoint down, dataset too
  large, runtime model call failed/blocked, view routing 404 (extends SPEC States & Errors).
- **R6.4** **Session/context growth** on long-lived projects (`PLAN.md` backlog: compaction) — bigger
  apps mean longer sessions; without this, later turns on a real app get slow and expensive.

**M-AC8** Each new system-error class renders human-readable (problem/cause/fix/retry) and is
distinct from raw user-code output (SPEC States & Errors); a long project session stays within a
bounded context budget across N turns.

---

## 9. Eval strategy

You flagged this as a core need. Evals are how we broaden the surface *without* regressing the golden
path, and how we make the sovereign claim defensible rather than anecdotal. Three layers, one
scorecard.

### 9.0 Principles
- **The prompt suite is the product spec in executable form.** Every §6 archetype × §5 posture is a
  fixture. If it's not in the suite, we don't claim it works.
- **Assert on flow/control for determinism; score quality with a rubric.** Generated code is
  non-deterministic (SPEC testing plan); assert exact behavior on *switches, tags, egress, endpoints*
  and score *the app* with a graded rubric, not string-match.
- **Governance evals gate; quality evals track.** A sovereignty/enforcement failure is a build
  breaker (red CI). A quality dip is a tracked metric with a threshold, reviewed, not auto-blocking
  (because live-model runs are noisy).
- **Sovereign path is a first-class lane, not a variant.** Every fixture runs at least strong-vendor
  and sovereign; the delta between them is a headline metric.

### 9.A The prompt suite (fixtures)
A versioned corpus, `evals/suite/`, each fixture = `{prompt, attachments, posture, expected_archetype,
must_have[], must_not[]}`. Seed set (~20–30 to start, grow with archetypes):

- **Per archetype (§6):** ≥2 prompts each for A1, A2, A3, A6 (+A5 if E3 ships) — one rich, one terse.
- **Per data posture (§5.3):** no-data, attached-nonsensitive, attached-**sensitive**, live/large.
- **Iteration chains:** a create followed by 3–5 realistic follow-ups (small tweak, large change,
  fix) — evaluates the multi-turn path and `scope.py`, not just turn one.
- **Adversarial/meta (§5.1):** prompt-injection in an attached file's contents, "ignore your
  instructions," "curl this vendor," off-task requests — feeds the safety lane (9.C).

The **fraud-review-queue** prompt from `EXPERIMENTS.md` is the anchor fixture (already characterized:
5–7 phases, shared data model, two empty states) — reuse it so the cost/quality experiment and the
eval suite share a reference point.

### 9.B Quality evals (track) — "is the app good?"
Two scorers, both recorded per fixture per model:

1. **Deterministic checks** — the `must_have[]`/`must_not[]` list, checked mechanically where
   possible: does it `tsc` clean? does the preview boot without console errors? do the named entities/
   columns/filters/states appear (the `EXPERIMENTS.md` checklist generalized)? For live-data
   fixtures: does it fetch the endpoint rather than snapshot? For multi-view: do routes resolve?
2. **LLM-judge rubric (1–5 each), on a screenshot + code**: *functional completeness, states
   coverage (empty/loading/error/zero-result), visual polish & token adherence, code cleanliness
   (no dup types — the cross-phase-amnesia grep from `EXPERIMENTS.md`), prompt fidelity.* Judge model
   is a strong vendor model; rubric and few-shot anchors are versioned so scores are comparable over
   time.

**Headline quality metrics:** build-success rate (boots + passes deterministic checks), mean rubric
score, and **sovereign-vs-vendor quality delta** per archetype.

### 9.C Governance & enforcement evals (gate) — "is the guarantee real?"
These run against the deterministic `FakeGatewayClient` and a probed container; **any failure fails
CI.** They are the wedge, made testable.

- **Model override under lock** (SPEC AC2/AC6): request vendor, sensitivity lock on → gateway serves
  sovereign; holds across every call in a turn including runtime (E3).
- **Tagging completeness** (SPEC AC3/AC6): N model calls → N gateway records, each tagged
  `project`+`phase`(+`runtime` for E3); zero untagged.
- **No sensitive leakage** (E2/E3): tagged dataset content never appears in the prompt log, the
  build bundle (`dist/`), or a runtime request body; only its *reference* does.
- **Refuse-don't-downgrade** (SPEC States & Errors): sovereign model unavailable under lock → turn
  blocked with a clear message, never a vendor fallback.
- **Egress posture** (SPEC AC6, mechanism-level given the platform allowlist is deferred): OpenCode
  has only the `sage-gateway` provider; a probe asserts no other model path is configured.
- **Prompt-injection resistance:** malicious instructions inside an attached file's *content* do not
  cause the agent to exfiltrate, change models, or ignore the lock.

### 9.D Harness & ops (from SPEC testing plan, extended)
- **Record/replay model harness** (SPEC 2.2, still deferred) — required now that quality evals need
  stable reruns. Build it as part of E4.
- **Cost regression:** track tokens/cost per fixture; flag a fixture that gets materially more
  expensive (catches a prompt/template change that bloats context).
- **Latency:** time-to-first-preview and time-to-done per fixture (feeds the §10 TTHW metric).

### 9.E The scorecard (one artifact per run)

| fixture | archetype | posture | model | boots | checks (n/N) | rubric (avg) | governance | tokens | wall-clock |
|---|---|---|---|---|---|---|---|---|---|

Sovereign and vendor rows side by side; governance column is pass/fail and **must be all-pass**.
This table is the go/no-go for shipping any change that touches the build path.

---

## 10. Success metrics

**North Star:** **weekly count of apps that reach a working preview and get iterated on ≥3 times**
(a proxy for "someone is actually building something real, not kicking tires").

Supporting KPIs, with this-milestone targets (baselines TBD from first eval run):

| Metric | Definition | Target |
|---|---|---|
| Build-success rate | fixtures that boot + pass deterministic checks | ≥85% vendor, **≥70% sovereign** |
| Sovereign-path coverage (M-AC7) | in-scope archetypes that build sovereign | ≥N of them (§12-D5) |
| Quality delta | mean rubric, vendor − sovereign | ≤1.0 point |
| Time-to-first-preview (TTHW) | prompt → placeholder/preview visible | <90s (SPEC target, held) |
| Enforcement pass rate | governance lane (9.C) | **100%, always** |
| Multi-turn resilience | iteration chains completing without a stuck-state | ≥80% |

---

## 11. Sequencing

Dependency-ordered; the eval harness comes early because it's how we measure everything after.

1. **E4 eval harness + record/replay** — first, so E1–E3 are measured from day one.
2. **E2 live governed data** + **R2.2 reference injection** — highest-value, on-wedge, and A3/A5 depend on it.
3. **E1 multi-view** — unblocks A1-detail/A2-drilldown/A6; parallelizable with E2.
4. **E6 table-stakes** (auto-mode switch R6.1, error taxonomy R6.3) — pulled in as E1/E2 surface them.
5. **E3 AI-in-the-app** — *if* §12-D4 says this milestone; else spec-and-defer.
6. **E5 sovereign-path closure** + phased-build experiment result — continuous via the harness, reported at the end.

Rough shape (re-estimate after the first eval baseline): eval harness ~1 wk; live data ~1.5–2 wk;
multi-view ~1 wk; AI-in-app ~1.5 wk if in; table-stakes ~1 wk interleaved.

---

## 12. Open decisions (need your call before build)

- **D1 — Second (Python) app stack?** Dara (data scientist) likely wants Streamlit/Dash/Gradio, the
  Domino-native app shapes. It would materially widen the surface for that persona — but it doubles
  the template + skill + eval + preview surface. **My recommendation:** keep this milestone
  React-only, *spec* a Python stack as the very next milestone, and use this milestone's eval harness
  as the thing that makes adding a stack safe. Confirm?
- **D2 — Persistence / write-back (A4 forms)?** Real forms need to save. That opens storage, auth,
  and migration. **Recommendation:** out of this milestone; its own milestone after deploy.
- **D3 — Deploy/publish (P2): in or out?** "A running app my team can use" is arguably the real job,
  and it's what turns preview-only into a product. It's blocked on confirming Domino App hosting for
  a React bundle + our runtime routes (SPEC Risk 2). **Recommendation:** run the hosting spike *in
  parallel* during this milestone; ship publish as the immediate fast-follow, not inside this
  milestone's ACs — unless you want it as the headline instead of app-surface breadth.
- **D4 — Does AI-in-the-app (E3) ship this milestone or next?** It's the sharpest differentiator but
  also the newest surface (a runtime inference route + a new skill + new evals). **Recommendation:**
  include it — it's the most on-wedge thing here — but treat E1/E2 as the must-ship core and E3 as
  the stretch that slips to next milestone if the eval bar isn't met.
- **D5 — Set N for M-AC7 / sovereign coverage.** How many of the in-scope archetypes must build on
  the sovereign tier to call the milestone done? **Recommendation:** all of A1/A2/A6 (UI-shaped) +
  A3 (data), i.e. 4; treat A5 (AI) sovereign coverage as stretch.
- **D6 — Which sovereign model is the bar?** `MODELS.md`/`EXPERIMENTS.md` note the shipped implement
  tier (`bedrock-qwen3-coder`) doesn't exist on the OpenRouter dogfood gateway, so experiments
  substitute `GLM-5.2`. The sovereign-path bar is only meaningful against the model customers will
  actually run — we need a gateway that hosts it, or an explicit statement of the stand-in.

---

## 13. Glossary (delta from SPEC — new terms only)

- **App surface** — the range of apps Sage can build (shape, data posture, runtime capability). This
  milestone widens it.
- **Live governed data** — data served to the built app at runtime through a scoped, permission-checked
  endpoint, as opposed to a static snapshot baked into the bundle.
- **AI-in-the-app** — a built app making model calls at runtime through the enforcement shim, so it
  inherits Sage's sovereign/attribution guarantees.
- **Eval fixture** — one `{prompt, attachments, posture, expected, must_have/must_not}` case in the
  suite; the executable form of a spec line.
- **Governance lane / quality lane** — the two eval tracks: governance *gates* (must be 100%),
  quality *tracks* (thresholds, reviewed).
- **Archetype** — a recurring app shape (§6); the unit the suite and skills are organized around.
