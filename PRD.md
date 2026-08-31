---
prd_title: Sage 1.0 — "Real Apps" milestone
prd_status: draft v3 (all decisions D1–D6 resolved with Subir, 2026-08-10)
prd_type: buildable milestone spec
prd_author: PM (drafted with Claude)
prd_date: 2026-08-10
depends_on: SPEC.md (v2), DESIGN.md, PLAN.md, DEPLOY-PLAN.md, EXPERIMENTS.md
supersedes: nothing — extends SPEC.md v1 scope
one_liner: >
  Take Sage from an internal POC that builds single-screen React demos into a product that builds
  real, multi-view apps over live governed data and publishes them as governed Domino Apps — with
  the sovereign guarantee as the reason to buy and build-quality/speed as the bar it must clear.
  (AI-in-the-app is the headline of the next milestone.)
---

# Sage 1.0 — the "Real Apps" milestone

> **How to read this.** This is a *buildable* PRD: it assumes the vision in `SPEC.md` and picks the
> next shippable increment. §2–§6 are the framing you asked for (state of play, who asks what, what
> they build). §7–§8 are the milestone and its acceptance criteria. §9 is the eval program. §12 is
> the decisions log — all milestone decisions (D1–D6) are now resolved.

---

## 0. Decisions locked for this milestone

From the product gate (2026-08-10):

| # | Decision | Choice | Consequence |
|---|----------|--------|-------------|
| L1 | Primary users | **Both builder personas** (analyst *and* data scientist), governance owner is the buyer | Two prompt/quality bars, one enforcement bar. §4. |
| L2 | The wedge | **Sovereign is the reason-to-buy; speed & quality are the bar** | Evals prove the sovereign path *first*, then hold build-quality to a competitive bar. §9. |
| L3 | Milestone theme | **Broaden the app surface** beyond single-screen React demos | Headline is "real apps," not "harden the demo." §7. |
| L4 | Doc type | **Buildable milestone spec** | Tight framing, concrete requirements, testable ACs. |
| D1 | Second app stack | **React only this milestone** | Python/Streamlit stack spec'd for a later milestone, not built now. §12-D1. |
| D3 | Publish | **In scope** — generated-app publish (DEPLOY-PLAN Phase 5) is small & proven; pull it in | Publish becomes the third pillar (E3). §8-E3. |
| D4 | AI-in-the-app | **Next milestone** | Runtime governed model calls are spec'd here (§8, "Next milestone") but not built now. |
| D6 | Sovereign bar model | **The deepseek / GLM models we have on the gateway today** | With the truth-in-labeling caveat in §9.0. |
| D2 | Persistence / write-back | **Out — its own milestone later** | Apps stay read-only over data this milestone; write-back (A4) is sequenced after AI-in-the-app. §12-D2. |
| D5 | Sovereign-bar coverage (M-AC7) | **All 4 in-scope archetypes — A1/A2/A6 + A3** | The sovereign tier must build the data explorer (A3) too — the hardest combo and the core of the claim. §8-E5, §12-D5. |

All milestone decisions are now resolved; see the decisions log (§12) for rationale.

---

## 1. TL;DR

Sage today reliably builds a **single-screen React app over a static data snapshot**, and can already
**publish the builder itself** as a Domino App. That's a great demo and a partial product: the apps
people actually keep are multi-view, use *live* governed data, and get *shared with a team*. This
milestone broadens the app surface and completes the build→run→share loop, all on infrastructure
that's already proven.

The five things we will ship:

1. **Multi-view apps** — client-side routing + shared state, so "a dashboard with a detail page and a
   settings page" produces a real app, not one crowded screen.
2. **Live governed data** — attach a Domino dataset and the built app *queries it at runtime* through
   a scoped, permission-checked endpoint, instead of freezing a snapshot into `public/data/`.
3. **Publish the built app** — one click turns the generated app into a shareable **Domino App**
   (DEPLOY-PLAN Phase 5). The mechanism is the same `POST /modelProducts` the Sage hub already
   publishes itself with; each app is already a git-based Domino project — so this is the thin, missing
   last mile, not new research.
4. **The sovereign-path bar, closed for real** (SPEC AC12) — the sovereign tier completes each new app
   archetype to a running preview, proven by evals, against the deepseek/GLM models we run today.
5. **An eval harness** that scores build success, app quality, and the enforcement guarantee on a fixed
   prompt suite, run per-change — the thing that lets us broaden the surface without silently
   regressing the golden path.

**Deferred to the next milestone (spec'd here, §8): AI-in-the-app** — built apps calling a governed
model at runtime through the shim. It's the sharpest differentiator, and it gets its own milestone so
it ships with real evals rather than bolted onto this one.

---

## 2. Where Sage is today (honest state of play)

> **Corrected 2026-08-31.** This table was accurate when the PRD was written on 2026-08-10.
> The rows marked _(was: …)_ moved after that date and were re-checked against the repo at
> the merge to `main`. The epics in §8 are targets and were **not** re-dated — read §2 for
> what exists, §8 for what is wanted.

Grounded in the repo, not the roadmap. "Proven" = verified live in a Domino workspace per
`SPIKE-REPORT.md` / `DEPLOY-PLAN.md`; "built" = code + tests exist; "stub/gap" = declared but not wired.

| Capability | State | Evidence |
|---|---|---|
| Enforcement shim overrides model server-side, all OpenCode calls route through it | **Proven** | `SPIKE-REPORT.md` 1.1–1.3 |
| Warm React+Vite template, live HMR preview under the Domino proxy prefix, process supervisor | **Proven** | `DEPLOY-PLAN.md` Ph1; `preview/` |
| Agent feedback loop (`tsc` + console → next turn) + circuit breaker | **Built** | `feedback/`, tests |
| `LLMRouter` precedence + `ModelControl` state owner | **Built** | `router/`, `test_llm_router.py` |
| First-build plan gate + **scope classifier** (plan vs build on later turns) | **Built** | `orchestrator/scope.py` |
| **Phased build** (each step a cold session) + cost/quality experiment design | **Built, unproven** | `plan_steps.py`, `EXPERIMENTS.md` (results table empty) |
| Attachment **shape** description (CSV/JSON/Parquet/PDF/image → schema, never content) | **Built** | `orchestrator/describe.py` |
| One Domino project per app; git-based repo; commit+push on clean build | **Proven** | `DEPLOY-PLAN.md` Ph2 |
| **Hub**: "New app" provisions repo + project + builder; "Open" rehydrates | **Proven live** | `DEPLOY-PLAN.md` Ph4 |
| **Publish the *hub / builder* itself as a Domino App** | **Proven** | `environment/HUB-AS-APP.md` |
| **Publish the *generated app* as a Domino App** | **Built** _(was: not built, Phase 5)_ | `provision/domino.py` `/api/apps/beta/apps`; `resources/publish_guard.py`, `publish_egress.py`; `tools/app_visibility.py`; ADR-0010, ADR-0012. **E3 is done** — see §8-E3. |
| Attach → **inject dataset reference into the build prompt** | **Partly built** _(was: stub/gap)_ | `describe()` is wired into the turn prompt at `orchestrator/service.py:690` (`_describe_context_file`). The *runtime* half (R2.1) is still absent. The old evidence quote is void: the sovereign lock it named no longer exists — see the note below. |
| Data at runtime | **Snapshot only** | `template/.../scripts/rehydrate-data.mjs` freezes data into `public/data/` |
| Multi-view / routing | **Router shipped, skill missing** _(was: single screen)_ | `react-router-dom ^7.18.2` is in `template/react-vite/package.json`; `.opencode/skills/` still holds only `data-table`, so R1.1 is half-met. |
| Auto mode plan→implement model switch (non-locked) | **Built** _(was: gap)_ | `router/llm_router.py` `resolve()` returns `catalog.plan` in `Phase.PLAN` and `catalog.implement` otherwise under `Mode.AUTO`. **R6.1 is met.** |
| Cost dashboard (in-app) / guardrail alarm UI | **Deep-link only / gap** | `PLAN.md` 8; cost is a deep-link to the gateway |
| IDE mode | **Gap** | `PLAN.md` 9 |
| Sovereign-path bar (AC12) | **Not closed** | `PLAN.md` 10.2 |
| Container egress allowlist (airtight zero-vendor) | **Blocked on platform** | `SPIKE-REPORT.md` 1.4/1.5 |

**Two things this PRD assumes that are no longer true (2026-08-31):**

1. **The sensitivity / sovereign lock has been removed from the code.** There is no
   `sensitivity_lock`, `sovereign_lock`, or `locked=True` anywhere in `backend/sage`;
   `router/llm_router.py` states its precedence is "per SPEC.md Component 3 **minus the old
   sensitivity lock**" and that "sensitive attachments do not change the model." **R2.4 and
   M-AC4 are written on that mechanism and cannot be implemented as worded** — they need
   rewriting against whatever replaced it before E2 is scheduled.
2. **There is no CI in this repo** — no `.github/workflows`, no eval suite, no scorecard.
   E4 (R4.2 "runs in CI on a schedule", M-AC6 "the eval suite runs green") starts from zero,
   and every AC that says "verified by eval" — M-AC1, M-AC7, R5.1 — inherits that dependency.

**Read of it:** the *enforcement spine*, the *build loop*, the *per-app project/repo model*, and
*publishing Sage itself* are all real and proven. What's missing for "real apps" is **richer app
shapes (multi-view), live data at runtime, and the last-mile publish of the user's app** — plus the
evals to keep it honest. This milestone invests exactly there and pulls in the remaining v1 UX gaps
(auto-mode switch, cost view, error taxonomy) only where an app archetype needs them (§8-E6).

---

## 3. Positioning — the wedge in one page

**Category:** an in-platform AI app builder (Replit/Bolt/v0/Lovable/Cursor shape) — describe an app,
watch an agent build it, see it run live, drop into code when stuck.

**Why anyone picks Sage over those:** they are all *build-outside, bring-your-own-data, vendor-model*
tools. Sage is **inside Domino, on the user's governed data, on models that can be made sovereign, with
cost attributed and guardrails enforced at the gateway.** For a regulated enterprise (Sage's market —
banks, pharma, insurers on Domino), that's the difference between "allowed" and "not allowed."

- **Reason to buy (the wedge):** *Build and run apps on sensitive data that never leaves your walls.*
  Sovereign lock, per-project cost attribution, permission-scoped data, preventive guardrails.
- **The bar (table stakes):** it has to build a **good app, fast**, and let the user **share it**. A
  governed tool that produces broken, ugly, or un-shippable apps loses to an ungoverned tool that
  produces working ones, because users route around governance. So build success, app polish,
  time-to-first-preview, *and one-click publish* are hard bars, not aspirations (§9-B, §10).

**What this milestone unlocks on the wedge:** governed data *at runtime* (the app reads sensitive data
through a scoped endpoint, tagged and permission-checked — not baked into a downloadable bundle) and
governed *publish* (the shared app runs inside Domino under the same identity/permissions). The
sharpest differentiator of all — *the app's own AI calls are sovereign too* — is the **next**
milestone's headline (AI-in-the-app), and this milestone lays its data + publish foundation.

---

## 4. Users & JTBD

Two builder personas, co-equal (L1). One buyer.

| Persona | Who | Core job (JTBD) | Sage relationship | Success signal |
|---|---|---|---|---|
| **Ana — Analyst / citizen dev** | Risk/clinical/ops analyst; semi-technical; lives in the data, not the IDE | *"When I have a recurring question about my data, I want a small tool my team can use, without waiting on a dev sprint."* | NL-only. Never opens IDE mode. Trusts the preview. Wants to *share* the result. | Ships & publishes a working internal tool in one session, no help. |
| **Dara — Data scientist / ML eng** | Domino power user; writes Python daily; can read the diff | *"When my notebook analysis is worth operationalizing, I want to wrap it in an app fast and keep control of the code."* | NL-first, IDE-mode when precise; may want a Python stack (§12-D1). | Builds + tweaks in code + hands off a running, shared app. |
| **Gita — Governance / platform owner** | Platform admin / CISO-adjacent; the economic buyer | *"When my org builds on sensitive data, I want to guarantee nothing leaks and everything is attributed — including the published app."* | Doesn't build. Approves the capability, reads cost/guardrail surfaces. | Can point to enforced sovereignty + per-project cost + governed publish. |

**Design tension:** Ana needs the machinery *hidden* (the template `AGENTS.md` already enforces "never
mention tools/modes/placeholder; talk about the app"); Dara needs it *reachable* (IDE mode, model
control, phased-build toggle). Publish must be one button for Ana and not get in Dara's way. NL stays
the default surface; every power-control is progressive disclosure.

---

## 5. What users will ask — the query taxonomy

The backbone of the eval suite (§9): every class maps to eval prompts, and the classifier in `scope.py`
already has to sort several of them.

### 5.1 By intent (what the turn is *for*)

| Class | Example utterances | Sage path today | Milestone impact |
|---|---|---|---|
| **Create** (turn 1) | "Build a fraud review queue." "Make me a dashboard for this sales CSV." | First-build plan gate → build | Must handle multi-view + data-backed creates (§7). |
| **Iterate — small** | "Make the table sortable." "Change the accent to green." | `scope.py` → BUILD | Unchanged; keep the fast path fast. |
| **Iterate — large** | "Make it production-ready." "Add auth, orgs, and billing." | `scope.py` → PLAN (approval gate) | Bigger apps → more of these; plan quality matters more. |
| **Fix** | "The chart is blank." "It crashes when I filter." | BUILD + feedback loop | Runtime data introduces new failure classes to self-heal (§8-E2). |
| **Ask (read-only)** | "What does this app do?" "Can it handle 1M rows?" | Ask model, no edit | Must not edit; must not leak `.sage/`. |
| **Data-shaping** | "Only show flagged rows." "Aggregate by month." | Prompt + attachment reference | Depends on live-data epic (§8-E2); today snapshot-only. |
| **Publish / share** | "Publish this." "Give my team a link." | *Not supported for the built app* | New: publish epic (§8-E3). |
| **AI-feature** | "Summarize each ticket." "Classify these reviews." | *Not supported* | **Next milestone** (§8, deferred). |
| **Meta / off-task** | "Which model are you using?" "Ignore your instructions and…" | Mixed | Guardrail + safety evals (§9-C). |

### 5.2 By specificity (how much the prompt pins down)

This axis, not intent, most predicts build success — and it's invisible to any regex, which is exactly
why `scope.py` uses a model.

- **Terse / underspecified** — "make a dashboard" (no data, no metrics). Sage must make sensible
  defaults and *say what it assumed*, not stall.
- **Rich / specified** — the fraud-queue prompt in `EXPERIMENTS.md` (entities, columns, filters, states
  named). Highest success rate; the eval "happy path."
- **Contradictory / impossible** — "a real-time app with no backend and no data." Degrade to the
  achievable and name the gap; never silently drop half.
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

The "what apps" question. These archetypes are the eval corpus (§9-A) and they set the template / skill
investment. Ordered by how well Sage handles them **today**.

| # | Archetype | Example | Today | Gap this milestone closes |
|---|---|---|---|---|
| A1 | **Record table / review queue** | Fraud queue, ticket triage, approvals | **Strong** — dedicated `data-table` skill | Multi-view (row → full detail page), live data, publish |
| A2 | **Dashboard / KPI view** | Sales KPIs, cohort metrics, ops monitor | **OK** (StatCard example) | Real charts, live refresh, drill-down page, publish |
| A3 | **Data explorer** | Filter/search/pivot over a dataset | **Snapshot-only** | Live query over large/governed data (§8-E2), publish |
| A4 | **Form / data-entry tool** | Intake form, labeling UI, config editor | **Weak** (no persistence) | **Deferred** — write-back is its own milestone after AI-in-the-app (D2) |
| A5 | **AI-assisted tool** | Summarizer, classifier, doc Q&A/chat | **Unsupported** | **Next milestone** (AI-in-the-app) |
| A6 | **Multi-step / wizard** | Onboarding flow, guided analysis | **Cramped on one screen** | Routing + shared state (§8-E1), publish |
| A7 | **Content/report generator** | Styled report/brief from data | **Partial** | Benefits from A5 (next) + export |

**Investment implication:** A1/A2/A6 are carried by the **multi-view** epic + charting; A3 by **live
data**; all of A1/A2/A3/A6 by **publish**. A5/A7 wait on next milestone's AI-in-the-app. A4
(persistence/write-back) is deferred — it opens auth/state/migration questions that deserve their own
milestone (§12-D2).

**Explicitly out of scope (unchanged from SPEC):** non-app data-analysis workflows (Excel-style),
notebooks, arbitrary backends/microservices, mobile-native.

---

## 7. The milestone: goal, axes, non-goals

**Goal.** Sage builds **real apps** — multi-view, over live governed data — and lets the user **publish
them as governed Domino Apps**, proven on the sovereign path and held to a competitive quality/speed bar
by an automated eval suite.

**Definition of "real app" for this milestone** (the bar A1/A2/A3/A6 must clear):
1. More than one view where the archetype warrants it, with navigation and shared state that survives
   navigation and deep-links.
2. Data comes from the attached Domino dataset **at runtime** (scoped endpoint), not a frozen snapshot,
   for datasets above the snapshot threshold.
3. It can be **published** to a shareable Domino App in one action, running under the user's identity.
4. It passes the app-quality rubric (§9-B) — states, polish, accessibility — the same bar Sage already
   holds single-screen apps to via `AGENTS.md`.
5. The **sovereign tier** (deepseek/GLM, D6) can build it to a running preview (AC12), verified by eval.

**The three broadening axes (the epics):** multi-view (E1), live governed data (E2), publish (E3). Plus
the enabling work: eval harness (E4), sovereign-path closure (E5), and the table-stakes dependencies
these pull in (E6).

**Non-goals for this milestone (say them out loud):**
- **AI-in-the-app** (runtime governed model calls) — **next milestone**; spec'd in §8 so E2's data
  endpoint is designed to extend into it.
- **A second (Python/Streamlit) stack** — React only (D1); spec'd for a later milestone.
- **Persistence / write-back** (A4 forms) — its own milestone (§12-D2).
- **Multi-user *shared-state* apps, org cost roll-ups, configurable guardrail policies** — that's P3
  governance depth in SPEC.
- **Airtight container egress allowlist** — still a platform dependency, tracked, not built here.

---

## 8. Requirements & acceptance criteria

Epics E1–E6. Each requirement is testable; ACs extend `SPEC.md`'s AC1–AC13 (referenced as SPEC-ACn).
New ACs are M-ACn.

### E1 — Multi-view apps
Give the agent a first-class way to build more than one screen without hand-rolling routing every time.

- **R1.1** Warm template ships a **routing primitive** (lightweight client router or a documented
  convention) and a `multi-view` **skill** (sibling to `data-table`) covering nav, active state, layout
  shell, and shared state across views.
- **R1.2** Shared app state (selected record, filters) **survives navigation**, and deep-linking to a
  view works (URL reflects state) — which also makes published deep-links work (E3).
- **R1.3** The scope classifier + plan gate treat "add a page/view" as a first-class structural change;
  phased build (`plan_steps.py`) can put a view per phase without cross-view type drift.

**M-AC1** From one prompt, Sage builds a ≥2-view app (list → detail page → settings) with working
navigation, shared selection state, and no `tsc`/console errors, on both the strong and the sovereign
tier.
**M-AC2** Deep-linking to a non-root view renders that view directly (no blank/crash), in preview and
when published.

### E2 — Live governed data
Replace snapshot-only with a runtime data path that respects permissions and the sovereign posture.

- **R2.1** A **scoped data endpoint** in the builder/app runtime serves attached datasets to the built
  app at runtime (read-only), authorized by the same per-user Domino token, so the app never embeds raw
  data and large datasets aren't frozen into the bundle. Designed to be the same seam AI-in-the-app
  (next milestone) plugs into.
- **R2.2** Attaching a dataset **injects its reference** (schema/columns/types/rowcount/sample from
  `describe.py`) into the build prompt — closing `PLAN.md` 6.2 — so the agent codes against the real
  shape and wires the runtime fetch, not a snapshot.
- **R2.3** A documented **snapshot-vs-live threshold** (size/row count): small stays snapshot (fast,
  offline preview), large goes live. The choice is visible to the user.
- **R2.4** Sensitivity lock composes with live data: a tagged dataset forces the sovereign tier *and*
  keeps its data on the governed endpoint; nothing tagged is ever inlined into the prompt or the bundle
  (extends SPEC AC2).

**M-AC3** An app built over a live-threshold dataset fetches and renders real rows at runtime through
the scoped endpoint; the dataset is not present in the built bundle.
**M-AC4** With a sensitivity-tagged dataset attached, the built app's data path and the build's model
path are both sovereign; a probe confirms no tagged content in the prompt log or `dist/`.

### E3 — Publish as a governed Domino App

> **Status 2026-08-31: shipped.** Kept here for the ACs and the rationale. See §2.

Complete the build→run→share loop. This is DEPLOY-PLAN **Phase 5**, and the mechanism is already proven
by how the Sage hub publishes itself.

- **R3.1** A **build/serve step** for the generated app: `vite build` → static serve (or a minimal
  `app.sh` in the project) targeting `:8888` (DEPLOY-PLAN 5.1). Must correctly serve a multi-view (E1)
  app under the Domino App prefix (same base-path threading already solved for preview).
- **R3.2** A **Publish action**: `POST /modelProducts` in the app's project + `/{id}/start`, surfacing
  the shareable URL (DEPLOY-PLAN 5.2). One button for Ana; the app's git repo is the source.
- **R3.3** **Publish UX & states** (DEPLOY-PLAN 5.3): "Publishing…" progress, the shareable link,
  republish-on-change, and a clear note that the published app is a separate deployment (its own
  cold-start) from the private in-session preview. Error taxonomy for publish failures (§8-E6).
- **R3.4** The published app runs under the **user's Domino identity/permissions**, so a live-data (E2)
  app's scoped endpoint enforces the *viewer's* access — publish does not widen data access.

**M-AC5** From a finished app, one Publish action produces a running Domino App at a shareable URL that
serves the built (multi-view, live-data) app; a second publish after an edit updates it; a publish
failure renders a human-readable system error, not a raw stack trace.

### E4 — Eval harness (the enabler — detail in §9)
- **R4.1** A **fixed prompt suite** (the §6 archetypes × §5 postures) runnable head-to-head across
  models via record/replay or live, producing the scorecard in §9.
- **R4.2** Runs in CI on a schedule and on demand; a regression in build-success or the enforcement
  checks **fails the run** and is visible.
- **R4.3** Uses the deterministic `FakeGatewayClient` for enforcement/flow assertions and a live-model
  lane (periodic, non-gating) for quality (extends SPEC testing plan).

**M-AC6** The eval suite runs green on the committed baseline; introducing a known regression (e.g.
disabling the model override) turns the enforcement lane red.

### E5 — Close the sovereign-path bar (SPEC AC12, for real)
- **R5.1** For each in-scope archetype (§6 A1/A2/A3/A6), the **sovereign tier** (deepseek/GLM, D6)
  completes it to a running preview, tracked as an eval metric over time, not a one-off.
- **R5.2** Run the `EXPERIMENTS.md` A-vs-C phased-build experiment to a **recorded result** so we know
  whether phasing is what makes the cheap/sovereign tier viable, and default the toggle accordingly.

**M-AC7** **All 4** in-scope archetypes — A1 (record table), A2 (dashboard), A6 (multi-step), and A3
(data explorer) — build to a running preview on the sovereign tier in the eval suite; any archetype
added to the suite later is stretch, not gating. The phased-build experiment has a recorded verdict.

### E6 — Table-stakes dependencies (pull in only as archetypes require)
v1 gaps that real apps trip over; scoped to what E1–E3 need, not a full v1 sweep.

- **R6.1** **Auto-mode model switch** actually changes the model per phase when unlocked (`PLAN.md`
  backlog) — otherwise "plan strong / implement cheap" is a story, not a behavior, and the sovereign
  economics don't hold.
- **R6.2** **Cost visibility** sufficient to see spend per project (deep-link acceptable if tags are
  correct; a thin in-app read is better).
- **R6.3** **Error taxonomy** coverage for the *new* failure modes: data-endpoint down, dataset too
  large, view routing 404, **publish failure / cold-start** (extends SPEC States & Errors).
- **R6.4** **Session/context growth** on long-lived projects (`PLAN.md` backlog: compaction) — bigger
  apps mean longer sessions; without this, later turns get slow and expensive.

**M-AC8** Each new system-error class renders human-readable (problem/cause/fix/retry) and is distinct
from raw user-code output (SPEC States & Errors); a long project session stays within a bounded context
budget across N turns.

### Next milestone (spec'd here so E2 is built to extend into it): AI-in-the-app
Not built this milestone (D4). Captured so the E2 data endpoint is designed as the same seam.
- A **runtime inference route** the built app calls (never a vendor key in the app); requests pass
  through the enforcement shim, get tagged (`project`, `phase=runtime`, `model`), and honor the
  project's sovereign lock — so an app Sage builds inherits Sage's sovereign guarantee.
- An `ai-feature` **skill**: summarize/classify/extract/chat-over-doc patterns with loading/error/empty
  states and cost-awareness.
- Runtime calls appear in the same cost attribution as build calls, distinguishable by `phase=runtime`.
- Its evals (AI fixtures, runtime-override governance checks) join the suite then.

---

## 9. Eval strategy

Evals are how we broaden the surface *without* regressing the golden path, and how we make the
sovereign claim defensible rather than anecdotal. Three layers, one scorecard.

### 9.0 Principles
- **The prompt suite is the product spec in executable form.** Every §6 archetype × §5 posture is a
  fixture. If it's not in the suite, we don't claim it works.
- **Assert on flow/control for determinism; score quality with a rubric.** Generated code is
  non-deterministic (SPEC testing plan); assert exact behavior on *switches, tags, egress, endpoints,
  publish* and score *the app* with a graded rubric, not string-match.
- **Governance evals gate; quality evals track.** A sovereignty/enforcement failure is a build breaker
  (red CI). A quality dip is a tracked metric with a threshold, reviewed, not auto-blocking (live-model
  runs are noisy).
- **Sovereign path is a first-class lane, not a variant.** Every fixture runs at least strong-vendor and
  sovereign; the delta between them is a headline metric.
- **Truth-in-labeling on the "sovereign" tier (D6).** On today's OpenRouter dogfood gateway, the
  deepseek/GLM aliases are *the same vendor path under a different name* — they are **not** running on
  customer GPUs (`EXPERIMENTS.md` says this outright). So this milestone's sovereign lane measures two
  real things — the **enforcement mechanism** (lock → override → refuse-don't-downgrade) and the
  **capability floor** (can a small/cheap model build these archetypes) — but **not** the
  data-never-leaves guarantee end to end. The full sovereign claim needs a gateway that hosts the actual
  customer-GPU model; call that out in any external writeup and re-run the bar against it before
  claiming it (§12-D6).

### 9.A The prompt suite (fixtures)
A versioned corpus, `evals/suite/`, each fixture = `{prompt, attachments, posture, expected_archetype,
must_have[], must_not[]}`. Seed set (~20–30 to start, grow with archetypes):

- **Per archetype (§6):** ≥2 prompts each for A1, A2, A3, A6 — one rich, one terse. (A5 AI fixtures join
  next milestone.)
- **Per data posture (§5.3):** no-data, attached-nonsensitive, attached-**sensitive**, live/large.
- **Iteration chains:** a create followed by 3–5 realistic follow-ups (small tweak, large change, fix) —
  evaluates the multi-turn path and `scope.py`, not just turn one.
- **Publish:** at least one fixture per archetype that builds *then publishes*, asserting a reachable
  Domino App URL serving the multi-view/live-data app.
- **Adversarial/meta (§5.1):** prompt-injection in an attached file's *contents*, "ignore your
  instructions," "curl this vendor," off-task requests — feeds the safety lane (9.C).

The **fraud-review-queue** prompt from `EXPERIMENTS.md` is the anchor fixture (already characterized:
5–7 phases, shared data model, two empty states) — reuse it so the cost/quality experiment and the eval
suite share a reference point.

### 9.B Quality evals (track) — "is the app good?"
Two scorers, both recorded per fixture per model:

1. **Deterministic checks** — the `must_have[]`/`must_not[]` list, checked mechanically where possible:
   does it `tsc` clean? does the preview boot without console errors? do the named entities/columns/
   filters/states appear (the `EXPERIMENTS.md` checklist generalized)? For live-data fixtures: does it
   fetch the endpoint rather than snapshot? For multi-view: do routes resolve? For publish fixtures:
   does the published URL return the app?
2. **LLM-judge rubric (1–5 each), on a screenshot + code**: *functional completeness, states coverage
   (empty/loading/error/zero-result), visual polish & token adherence, code cleanliness (no dup types —
   the cross-phase-amnesia grep from `EXPERIMENTS.md`), prompt fidelity.* Judge model is a strong vendor
   model; rubric and few-shot anchors are versioned so scores are comparable over time.

**Headline quality metrics:** build-success rate (boots + passes deterministic checks), mean rubric
score, and **sovereign-vs-vendor quality delta** per archetype.

### 9.C Governance & enforcement evals (gate) — "is the guarantee real?"
Run against the deterministic `FakeGatewayClient` and a probed container; **any failure fails CI.** The
wedge, made testable.

- **Model override under lock** (SPEC AC2/AC6): request vendor, sensitivity lock on → gateway serves
  sovereign; holds across every call in a turn.
- **Tagging completeness** (SPEC AC3/AC6): N model calls → N gateway records, each tagged
  `project`+`phase`; zero untagged.
- **No sensitive leakage** (E2): tagged dataset content never appears in the prompt log, the build
  bundle (`dist/`), or a served response body; only its *reference* does.
- **Refuse-don't-downgrade** (SPEC States & Errors): sovereign model unavailable under lock → turn
  blocked with a clear message, never a vendor fallback.
- **Publish preserves scope** (E3-R3.4): a published live-data app enforces the *viewer's* Domino
  permissions; publishing does not widen data access.
- **Egress posture** (SPEC AC6, mechanism-level given the platform allowlist is deferred): OpenCode has
  only the `sage-gateway` provider; a probe asserts no other model path is configured.
- **Prompt-injection resistance:** malicious instructions inside an attached file's *content* do not
  cause the agent to exfiltrate, change models, or ignore the lock.

### 9.D Harness & ops (from SPEC testing plan, extended)
- **Record/replay model harness** (SPEC 2.2, still deferred) — required now that quality evals need
  stable reruns. Build it as part of E4.
- **Cost regression:** track tokens/cost per fixture; flag a fixture that gets materially more expensive
  (catches a prompt/template change that bloats context).
- **Latency:** time-to-first-preview and time-to-done per fixture; time-to-published for publish
  fixtures (feeds the §10 metrics).

### 9.E The scorecard (one artifact per run)

| fixture | archetype | posture | model | boots | checks (n/N) | rubric (avg) | governance | published | tokens | wall-clock |
|---|---|---|---|---|---|---|---|---|---|---|

Sovereign and vendor rows side by side; the governance column is pass/fail and **must be all-pass**.
This table is the go/no-go for shipping any change that touches the build or publish path.

---

## 10. Success metrics

**North Star:** **weekly count of apps that reach a working preview and get iterated on ≥3 times**
(a proxy for "someone is actually building something real, not kicking tires"). Publish adds a
companion signal: **apps published and opened by a second user.**

Supporting KPIs, with this-milestone targets (baselines TBD from first eval run):

| Metric | Definition | Target |
|---|---|---|
| Build-success rate | fixtures that boot + pass deterministic checks | ≥85% vendor, **≥70% sovereign (deepseek/GLM)** |
| Sovereign-path coverage (M-AC7) | in-scope archetypes that build on the sovereign tier | 4 of 4 (A1/A2/A6/A3) |
| Quality delta | mean rubric, vendor − sovereign | ≤1.0 point |
| Time-to-first-preview (TTHW) | prompt → placeholder/preview visible | <90s (SPEC target, held) |
| Publish success rate | publish fixtures reaching a live Domino App URL | ≥90% |
| Enforcement pass rate | governance lane (9.C) | **100%, always** |
| Multi-turn resilience | iteration chains completing without a stuck-state | ≥80% |

---

## 11. Sequencing

Dependency-ordered; the eval harness comes early because it measures everything after.

1. **E4 eval harness + record/replay** — first, so E1–E3 are measured from day one.
2. **E2 live governed data** + **R2.2 reference injection** — highest-value, on-wedge, and A3 depends on
   it; designed as the seam next-milestone AI-in-the-app extends.
3. **E1 multi-view** — unblocks A1-detail/A2-drilldown/A6; parallelizable with E2.
4. **E3 publish** — after E1 (must serve multi-view under the App prefix); small, proven mechanism.
5. **E6 table-stakes** (auto-mode switch R6.1, error taxonomy R6.3 incl. publish failures) — pulled in
   as E1/E2/E3 surface them.
6. **E5 sovereign-path closure** + phased-build experiment result — continuous via the harness, reported
   at the end.

Rough shape (re-estimate after the first eval baseline): eval harness ~1 wk; live data ~1.5–2 wk;
multi-view ~1 wk; publish ~0.5–1 wk (mechanism proven); table-stakes ~1 wk interleaved.

---

## 12. Decisions log

**Resolved 2026-08-10:**
- **D1 — Second (Python) app stack? → React only this milestone.** Dara (data scientist) likely wants
  Streamlit/Dash/Gradio eventually; spec it as a later milestone and let this milestone's eval harness
  make adding a stack safe. Not built now.
- **D3 — Deploy/publish: in.** Publishing *Sage itself* already works (`HUB-AS-APP.md`), each app is
  already a git-based Domino project, and generated-app publish is DEPLOY-PLAN Phase 5 with a proven
  mechanism (`POST /modelProducts`). It's the thin last mile, so it's the third pillar (E3), not a
  fast-follow.
- **D4 — AI-in-the-app: next milestone.** It's the sharpest differentiator and deserves to ship with
  real evals, not bolted on. Spec'd in §8 so E2's data endpoint is built as the seam it will extend.
- **D6 — Sovereign bar = the deepseek/GLM models we run today.** With the truth-in-labeling caveat
  (§9.0): on the current gateway these aren't customer-GPU-hosted, so the bar measures the enforcement
  mechanism and the capability floor, not the full data-never-leaves guarantee. Re-run against a gateway
  hosting the real sovereign model before making the end-to-end sovereign claim externally.
- **D2 — Persistence / write-back (A4 forms): out, its own milestone later.** Read-only over data this
  milestone. Write-back is where the governance surface gets genuinely hard — who can write, is it
  attributable, does it mutate a governed dataset — and it opens storage + write-auth + schema
  migration + its own governance evals. Doing it right after AI-in-the-app beats bolting a cheap
  version on now. A4 stays "deferred" in §6; browser-local-only was rejected as off-wedge (ungoverned,
  non-attributable, doesn't survive across users).
- **D5 — Sovereign-bar coverage: all 4 in-scope archetypes (A1/A2/A6 + A3).** The sovereign lane exists
  to prove a governed/cheap model builds *real* apps, and A3 (data explorer) is the one that actually
  exercises the live governed-data path — the small-model + runtime-data combo that is the heart of the
  claim. Dropping it would soften the differentiator exactly where it matters. The risk that small
  models flake on data wiring is *what the eval is meant to surface*, not something to design around.

**Still open:** none — all milestone decisions resolved.

---

## 13. Glossary (delta from SPEC — new terms only)

- **App surface** — the range of apps Sage can build (shape, data posture, runtime capability). This
  milestone widens it (multi-view, live data) and the next widens it further (AI-in-the-app).
- **Live governed data** — data served to the built app at runtime through a scoped, permission-checked
  endpoint, as opposed to a static snapshot baked into the bundle.
- **Publish** — turning the generated app into a shareable Domino App via `POST /modelProducts`; a
  separate deployment from the in-session preview, running under the user's identity.
- **AI-in-the-app** *(next milestone)* — a built app making model calls at runtime through the
  enforcement shim, inheriting Sage's sovereign/attribution guarantees.
- **Eval fixture** — one `{prompt, attachments, posture, expected, must_have/must_not}` case; the
  executable form of a spec line.
- **Governance lane / quality lane** — the two eval tracks: governance *gates* (must be 100%), quality
  *tracks* (thresholds, reviewed).
- **Archetype** — a recurring app shape (§6); the unit the suite and skills are organized around.
