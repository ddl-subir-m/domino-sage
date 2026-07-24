---
doc: Implementation plan — deploying sage in Domino (Path A, one-project-per-builder)
status: draft (pre-implementation, for review)
depends_on: SPEC.md, DESIGN.md, PLAN.md
branch: feat/domino-workspace-builder
date: 2026-07-23
---

# Deploy plan — sage as a Domino pluggable workspace

## Locked decisions (from brainstorm)

1. **Vehicle = Path A.** Sage is its **own pluggable workspace tool** (custom front door, not
   JupyterLab). One Domino-proxied port serves the Sage UI; the orchestrator reverse-proxies the
   Vite preview under a subpath on that same port. Vite is configured with a runtime `base` so it
   stops emitting root-absolute URLs. (Rejected: Path B / Sage-inside-JupyterLab via
   `jupyter-server-proxy` `/proxy/absolute/` — worse product feel, Jupyter coupling.)
2. **App boundary = Domino Project.** **1 Domino Project = 1 Sage app.** Each running builder is
   scoped to exactly one Domino project; its files live in the project's file volume. The current
   multi-project-per-process design (in-memory registry + `active project` + `x-sage-project`
   header) is **retired**.
3. **Scope = everything, including publish.** Builder-in-workspace → "New app" control plane →
   "Publish as Domino App."
4. **Runtime is invisible-ish.** "New app" and "Open app" spin a workspace behind one button;
   cold-start is acceptable behind a "creating your app…" state. Baked template deps keep the
   *preview* fast once the pod is up.
5. **Persistence = git-based projects.** Each app is its own **private GitHub repo**
   (`github.com/<user>/sage-<slug>`) + a git-based Domino project pointing at it (not DFS). The hub
   auto-creates/seeds the repo using the user's existing Domino GitHub credential (extracted at
   runtime via `git credential fill`; token handled in-memory only, never logged/persisted).
   Repo visibility is **always private**; publishing is a separate Domino App deploy. Seed+push is
   provider-agnostic (works for any Domino git provider/access-type); auto-repo-creation is a
   per-provider adapter (HTTPS-token providers only) with a BYO-repo fallback for SSH keys / "Other".

## Target end-state topology

```
Domino user
  │  (Launch "Sage Builder" workspace, OR "New app" in the hub)
  ▼
Domino proxy  …/{{owner}}/{{project}}/{{session}}/{{runId}}/     ← everything under this prefix
  ▼  (single httpProxy port, e.g. 8888)
Sage orchestrator (FastAPI, ONE port)
  ├─ /                        Sage builder UI (relative asset/API URLs)
  ├─ /api/*, /build/stream    session + model control + SSE   (scoped to ONE project)
  ├─ /v1/chat/completions     enforcement shim  →  Domino AI Gateway (egress choke)
  └─ /preview/*               preview proxy  →  Vite dev server (localhost:auto)
                                               (Vite --base = <prefix>/preview/, HMR ws configured)
Workspace fs = the Domino project's file volume (durable; rehydrate on reopen)

Publish:  POST /modelProducts (app.sh on :8888)  →  shareable Domino App URL
Control plane ("hub"):  POST /projects  →  POST /workspace/.../workspace + /sessions
```

## Domino API surface (verified against Domino Data Lab API v4 swagger)

**Available (endpoint exists):** `POST /projects`; `POST /workspace/project/{projectId}/workspace`;
`POST /workspace/.../workspace/{workspaceId}/sessions` (needs `externalVolumeMounts` query);
`POST /workspace/.../stop`; `POST /environments` + `/environmentRevision`;
`POST /modelProducts` + `/{id}/start`; assets + sidecar token (already in code).

**Spike (documented endpoint, undocumented body/behavior):** request payloads for project-create
and workspace-create/session-start; the pluggable-tool `httpProxy` YAML contract + which env var
yields the run/session id in-container; gateway host/app-id/`dgw_` token/`/api/usage/mine` shape.

---

## Phase 0 — De-risking spike (make-or-break; do first, in a real Domino workspace)

**Goal:** prove Path A is physically possible before building on it.

**STATUS — 2026-07-23, cloud-dogfood: PHASE 0 COMPLETE (0.1–0.4). Proceed to Phase 1.**

- **0.1/0.2 Path A** — Vite renders + HMR fires through the Domino proxy on ONE port (counter
  state preserved across a server-triggered edit). Prefix preserved (`rewrite:false`) =
  `/<owner>/<project>/notebookSession/<runId>`, also in the **`x-script-name`** header;
  `root_path` empty; mount `/mnt/code` (`DOMINO_WORKING_DIR`); public host in
  `x-original-forwarded-host`. Finding: runtime `npm install` caused a first-render gap →
  **bake `node_modules` into the image (Phase 3)**.
- **0.3 control plane** (v4 API, sidecar-auth at `DOMINO_API_PROXY`) — `GET /v4/users/self` →
  caller ObjectId. **`POST /v4/projects` → 200** with `{name, ownerId:<ObjectId>, visibility,
  description, collaborators:[], tags:{tagNames:[]}}`. Workspace create:
  `POST /v4/workspace/project/{id}/workspace` with `{name, environmentId, environmentRevisionId,
  hardwareTierId:{value}, tools:[...], mainGitRepoRef:{type:"branches",value},
  externalVolumeMounts:[]}`. Hardware tiers at `/v4/projects/{id}/hardwareTiers`. Sage project is
  git-based (`mainRepository`). (Sage_Spike env id `6a626bf9…`.)
- **0.4 gateway** — base `https://apps.cloud-dogfood.domino.tech/apps/llm_gateway/v1`, sidecar-JWT
  auth. Models listed; real completions OK for `sonnet` + `gpt-5.4`. Cost/usage at
  `/api/usage/mine/{summary,logs,cost-breakdown}` (per-alias/model tokens + cost). **CARRY-FORWARD:**
  `qwen-2-5` (sovereign) 502s at the provider (empty response) — gateway team's fix, not a sage
  blocker. Anthropic-shape also available at `/anthropic/v1/messages`.

- 0.1 **Base-path + HMR render.** In a stock Domino workspace, run a Vite dev server behind a
  minimal FastAPI reverse-proxy, launched as a pluggable tool, and confirm the app **renders and
  hot-reloads** through the Domino proxy prefix. Nail down: does Domino **strip or preserve** its
  prefix to the port? Which in-container env var(s) reconstruct `…/{{session}}/{{runId}}/`?
  → **verify:** edit a file, see HMR update in the browser through the Domino URL; no 404s on
  `/@vite`, `/src`, or the HMR websocket.
- 0.2 **Pluggable-tool contract.** Capture the exact `httpProxy` YAML (port, `internalPath`
  templating) that lands Sage's UI on launch.
  → **verify:** "Launch Sage" opens our page, not a shell/Jupyter.
- 0.3 **Control-plane payloads.** With the sidecar token, call `POST /projects` and
  `POST /workspace/.../workspace` + `/sessions`; record the real request bodies + required fields.
  → **verify:** a project + a running workspace session created purely via API.
- 0.4 **Gateway live.** Resolve `gateway-questions.md` open items (host, app id, `dgw_`/sidecar
  token, `/api/usage/mine` shape).
  → **verify:** one real completion through the gateway from inside the workspace (`make probe`).

**Gate:** if 0.1 can't be made to work cleanly, revisit Path B before proceeding.

## Phase 1 — Single-port collapse + base-path threading

**STATUS — 2026-07-23, cloud-dogfood: VERIFIED end-to-end on real Domino.** Builder loads under the
prefix; creating a project renders the template app in the preview iframe; editing `src/App.tsx`
hot-reloads live in the preview — all on the one tool port (8888), no `:8090`. Confirmed contracts:
Node ≥20.19 required in the Environment (conda's node shadows nodesource — `run.sh` forces
`/usr/bin` first); template `node_modules` must be reinstalled clean per Node version so rolldown's
platform-native binary is present. Prefix handled by recording `root_path` (NOT rewriting the
path) so the nested `/preview` mount doesn't double-count. **Follow-ups (not Phase-1 blockers):**
(a) builds need the OpenCode binary in the image + gateway wired — Phase 3 / gateway config;
(b) `GET /api/assets` 500s because Domino's `/v4/datasetUi/collections/byProject` returns 400 —
asset-panel fix, tracked separately.

**Goal:** the real builder (orchestrator + preview + template) runs on **one port under the Domino
proxy prefix**, exactly as the Phase-0 spike proved — while behaving identically at naked
`localhost` (empty prefix). No project-model changes here (that's Phase 2).

**Why it's needed — three places assume a naked, two-port world:**
- `orchestrator/app.py:405-421` — a separate `:8090` preview uvicorn server.
- `ui/index.html:384` — `previewBase = …:8090/` (hard port); plus ~18 API calls using
  **leading-slash absolute** paths (`fetch('/api/…')`) that won't carry Domino's prefix.
- `template/react-vite/vite.config.ts` — references `SAGE_HMR_CLIENT_PORT` that **nothing sets**,
  and has no `base`.

**Single source of truth for the prefix.** Compute `SAGE_BASE_PREFIX` once at orchestrator
startup, env-derived like the spike's `run.sh` (`/<owner>/<project>/notebookSession/<runId>` from
`DOMINO_PROJECT_OWNER`/`DOMINO_PROJECT_NAME`/`DOMINO_RUN_ID`), empty locally; cross-check against
the `x-script-name` header. Thread that one value everywhere. **No-strip design is required, not
just tidy:** Vite bakes its `base` into served HTML/JS, so `base` must be the full path the browser
uses, and the proxy must forward that same full path to Vite.

**Change set (5 files):**

| # | File | Change | Verify |
|---|------|--------|--------|
| 1 | `orchestrator/app.py` | ASGI middleware: read prefix, strip from `scope["path"]` so bare routes match + set `root_path`; stash prefix. `run()`: delete the `:8090` server + `SAGE_PREVIEW_PORT`, run one uvicorn on `control_port`. | `<prefix>/api/projects` 200; local `/api/projects` still 200 |
| 2 | `preview/proxy.py` | Register preview under `/preview/{path}` on `control_app`; forwarder **re-adds prefix** building the Vite URL (`{vite}{prefix}/preview/{path}`). Keep the spike's 502-when-Vite-down guard. | preview HTTP + HMR ws reach Vite |
| 3 | `preview/supervisor.py` (+ `service.py:348/383`) | Launch Vite with `--base={prefix}/preview/`, inject HMR env; keep runtime port discovery. | Vite "Local:" line still parsed |
| 4 | `template/react-vite/vite.config.ts` | Match spike: `base = ${prefix}/preview/`; `hmr = {protocol:'wss', clientPort:443, path:base}`; `allowedHosts:true`. Read prefix from env; empty → `/preview/`. | HMR fires through double proxy |
| 5 | `ui/index.html` | `previewBase` → relative `preview/`; **Runtime BASE constant** captured once at load (`const BASE = location.pathname.replace(/\/$/,'')`) prepended in the `api()` helper + the raw `fetch`/`EventSource` sites, so the browser sends fully-prefixed paths. | builder loads + drives a build under a prefix |

→ **verify (the Phase-0 test, now on real code):** launch the actual orchestrator as the pluggable
tool in a Domino workspace → builder UI loads under the prefix, a generated app renders in the
preview iframe, and an OpenCode edit hot-reloads *without* losing preview state — all on one tool
port. Locally, `localhost:8080` unchanged (empty prefix).

**Out of scope (deferred):** multi-project registry / `active-project` / `x-sage-project` teardown
→ Phase 2. Environment/deps baking → Phase 3.

## Phase 2 — Project-model flip (one Domino project per builder) — STATUS: IMPLEMENTED (make test green, 70 passed)

**Goal:** retire multi-project; bind each builder to a single Domino project's file volume.

- 2.1 ✅ `orchestrator/service.py`: `Orchestrator` scoped to one bound project (`project_id` =
  `DOMINO_PROJECT_NAME`), attached lazily via `project()` (memoized). Dropped the registry,
  `create_project`/`open_project`/`get`/`active`/`list_ids`/`list_all_ids`/`delete_project`.
  `history()` reads the volume directly (no attach → a GET never starts Vite).
- 2.2 ✅ `orchestrator/app.py`: removed the `x-sage-project` header and all `/api/projects[...]`
  picker/create/open/delete endpoints; routes are now project-less (`/api/project/...`) and act on
  the single bound project. `.sage/` rehydration (session/history/plan/model-overrides) happens on
  first `project()` attach. `healthz` drops `projects`/`all_projects`.
- 2.3 ✅ `workspace/manager.py`: single-workspace manager keyed on `SAGE_WORKSPACE_DIR` (the
  project's mounted volume; git-based `/mnt/code` in deploy). New `ensure()` **idempotently seeds**
  the template in place only when the volume has no app yet (no `package.json`), never clobbering a
  pre-existing app or its `.git`; warm-`node_modules` symlink kept. Template `.gitignore` now
  ignores `.sage/` so builder bookkeeping isn't committed to the app repo.
- 2.4 ✅ UI: removed the project picker (`<select>`/new-app/delete) + all switching helpers; `active`
  is now a boolean gating starter-screen vs project view. On load `bootProject()` attaches the one
  project and replays its transcript. Orphaned CSS/handlers cleaned up.
- 2.5 ✅ Tests: rewrote `test_workspace.py` (ensure + idempotency + no-clobber), added
  `test_orchestrator.py` (bound-to-volume, seed-in-place, memoized, history-without-attach, and a
  guard asserting the multi-project methods are gone), updated `test_snapshot.py` to `ensure()`.
  → **verify:** `make test` green (70 passed); no cross-project surface remains — with exactly one
  project the "stream bleed"/wrong-preview/wrong-header classes are structurally impossible.
  **Remaining verify (on Domino):** launch via the spike, confirm the builder boots into its bound
  project and HMR/preview still round-trip. Note: the standalone `sage.shim.app` dev harness keeps
  its own `x-sage-project` default — it's not on the deploy path.

**Verification findings (fixed):**
- 2.6 ✅ **Always enter the project view.** With one project the bound project always exists, so
  `bootProject()` now enters the project view unconditionally (preview + model panel + lock live from
  first load) and renders the starter prompts as the empty-chat state (`syncStarters()`), instead of
  a separate "no project" mode. Fixes the dead model panel / lock after a reload — the model panel is
  populated by `refreshStatus()`, which early-returns while `active` is false.
- 2.7 ✅ **Un-ignore `.sage/`** so the transcript + OpenCode session id are committed with the app
  repo and survive a workspace restart (git-based compute is ephemeral; only committed files persist).
  **Prerequisite, not sufficient:** cross-restart persistence only materializes once the post-build
  `git commit && git push` is wired (2.8 below), and is NOT observable on the `/tmp` spike (scratch
  dir is wiped on restart regardless). The spike restart losing history is expected for that reason.
- 2.8 ✅ **Commit + push on clean build** (`make test` green, 75 passed). `sage/workspace/git.py`:
  `is_repo`/`has_remote`/`commit_and_push` via `git`, using Domino's pre-authorized credential helper
  (no token handling; falls back to a sage identity only when none is configured). `build_stream`
  auto-commits+pushes after a turn ends with a passing typecheck (message = first line of the prompt)
  and emits a `saved` SSE event, persisted to history and rendered in the UI; a push failure logs +
  emits `saved:{ok:false}` but never fails the build. No-op (no `saved` line) when the workspace
  isn't a git repo — so the `/tmp` spike stays quiet. **Becomes observable only when the workspace is
  the project's git checkout with a remote** (deploy: `SAGE_WORKSPACE_DIR=/mnt/code`). Auto-*creating*
  the remote for brand-new apps remains separate (Phase 4).
- 2.9 ✅ **Auto build: require edits before "done".** A clean typecheck of the untouched (already
  compiling) template was being mistaken for a finished build, so a plan-only turn reported success
  with no code written. `build_stream` now tracks whether the agent edited/wrote files; a clean turn
  with zero edits nudges it to implement (once), then — if it still writes nothing — ends with an
  honest `done:{ok:false, decision:"planned but wrote no code — try Implement mode"}` instead of a
  false clean. Not unit-tested (the loop needs a live OpenCode server; verified by live re-run).
- **Env packaging (Phase 3) — image verified:** the baked Environment boots clean (Node 22,
  `opencode 1.18.4` resolved, gateway chip `domino`). Build ran end-to-end (agent + typecheck).
- **Env packaging (Phase 3) — spike unblock done:** `spikes/domino-verify/run.sh` now installs the
  repo-root deps so `opencode-ai` (the `opencode` binary) resolves; a real build runs end-to-end
  (verified). Baking this into the Environment image is the remaining Phase 3 work.

## Phase 3 — Environment packaging (the shippable artifact) — STATUS: DRAFTED (files in `environment/`, awaiting on-Domino build)

**Goal:** one Environment image = dev artifact = ship artifact.

**Drafted (`environment/Dockerfile` + `app.sh` + `pluggable-tools.yaml` + `README.md`):** bakes Node 22
(PATH ahead of conda), `uv`, `opencode-ai@1.18.4` (global), and Sage code + warm template (with
baked `node_modules`) into `/opt/sage` via `git clone` (public repo — no build secret) + `npm ci`.
No local build context, so nothing is COPYed. Ship model: **Sage baked in `/opt/sage`, the user's
app on `/mnt/code`** (`SAGE_WORKSPACE_DIR=/mnt/code`); `SAGE_APP_HOME=/mnt/code` gives a fast dev
loop off the mount. Fill-ins before build: base image, gateway creds. Verify = apply + launch on
Domino, debug together.

- 3.1 `environment/Dockerfile`: base image + **Node ≥20.19 (recommend 22)** + `uv` + Python deps +
  OpenCode (pinned) + the React+Vite template **cloned from a baked git URL** so a rebuild pulls
  the latest template (`ARG TEMPLATE_REV` above the `git clone` to bust the layer cache), into
  `/opt/sage/template`, with **baked `node_modules`** (`npm ci` in the same layer; agent-added deps
  install into the project fs on top). Node ≥20.19 is load-bearing — `vite@8`/rolldown hard-fails
  below it, and conda's node shadows nodesource, so ensure the system node wins on PATH.
- 3.2 `environment/pluggable-tools.yaml`: the Path-A `httpProxy` tool entry from 0.2.
- 3.3 `app.sh` / entrypoint: boot the single-port orchestrator (`uvicorn … --host 0.0.0.0 --port
  8888`), resolve the base prefix, start OpenCode server, gateway env wired to real values.
- 3.4 Keep a **fast inner dev loop** (bind-mount / dev override) so we don't rebuild the image per
  change — image is the deliverable, not the edit-test cycle.
  → **verify:** launch "Sage Builder" from the Environment in a real Domino project → describe an
  app → watch it build → private preview renders. End-to-end on Domino.

## Phase 4 — "New app" control plane (the hub) — STATUS: IMPLEMENTED (GitHub v1); one live-verify seam

**Goal:** Lovable/Replit-style "New app" that provisions a **git-based** project + launches the
builder. Each app = its own GitHub repo + a git-based Domino project pointing at it.

**IMPLEMENTED (2026-07-23) — `backend/sage/provision/` + `backend/sage/hub/` (97 tests pass).** Both
the GitHub repo-provisioning contract (`POST /user/repos` → 201) and the v4 project/workspace
contract (§0.3) are confirmed live on cloud-dogfood (`repo_provision_probe.sh` DRY_RUN=0 + the §0.3
probe). Modules, all behind Protocols with in-memory fakes:
- `provision/naming.py` — `sage-<slug>` with `-N` collision candidates.
- `provision/credentials.py` — origin parse + provider detect + `git credential fill` token extract
  (in-memory only, never logged; only the API create needs it).
- `provision/github.py` — `GitHubProvider.create_repo` (422 → `RepoNameConflict` retry) + fake.
- `provision/domino.py` — `DominoControlPlane` (owner_id, create_project w/ `sage` tag +
  `mainGitRepoRef`, create_workspace, list_apps by tag) + fake.
- `provision/seed.py` — template seed + `git push` over **ambient** Domino auth (no token here).
- `provision/service.py` — `HubService.create_app` (repo → seed → project → workspace) /
  `list_apps` / `open_app` (reuse-or-launch); injectable seeder (no-op in fake mode).
- `hub/app.py` + `hub/ui/index.html` — single-page hub (list / create / open), prefix-aware, fake
  mode when off-Domino. Launch via `environment/hub.sh` + the `sageHub` pluggable tool (same image).
- **LIVE-VERIFY (the one open seam):** `service.workspace_open_url()` — which field the v4
  workspace-create response carries the browser URL in (url / notebookUrl / a runId to assemble the
  prefix from). Best-effort now; confirm on launch. Also confirm project-name collision behavior
  (create_app falls back to the unique repo name if the v4 create rejects a duplicate name).

**STATUS — 2026-07-23, cloud-dogfood: git-provisioning capability CONFIRMED (git_discovery.sh).**
Persistence model = **git-based projects** (not DFS). Findings on a git-based workspace:

**STATUS — 2026-07-23, cloud-dogfood: git-provisioning capability CONFIRMED (git_discovery.sh).**
Persistence model = **git-based projects** (not DFS). Findings on a git-based workspace:
- The user's GitHub credential is **extractable at runtime** via `git credential fill`
  (`protocol=https host=github.com`) — Domino returns the PAT (classic `ghp_`). No v4
  git-credential API exists (all candidate paths 404); `credential fill` is the mechanism.
- The PAT carries **`repo` + `delete_repo`** scope → the hub can create/seed/delete repos via the
  GitHub API. (Observed PAT was far broader — `admin:org`/`admin:enterprise`; we only need `repo`.)
- `git push` from the workspace is **already authorized** (Domino injects the creds) — dry-run OK.
- ⚠️ The extracted token is powerful: handle **in-memory only, never log/persist**. GitHub.com only
  for now — detect host from the credential; GHE/GitLab are separate providers.

- 4.1 **Repo provisioning** (per new app, all user-space). Domino supports many providers
  (GitHub / GitHub Enterprise / GitLab / GitLab Enterprise / Bitbucket / Bitbucket DC / Other) and
  access types (PAT / SSH key / App password), so this is layered:
  - **Backbone (universal):** seed + push work for ANY provider + access type — Domino injects the
    creds, `git push` just works (proven §7). This never depends on a provider API.
  - **Auto-create (v1 = all HTTPS-token providers):** detect provider+host from the git remote /
    configured Domino credential → a **provider adapter** creates the repo via its API using the
    token from `git credential fill`. v1 adapters: **GitHub + GitHub Enterprise** (`POST /user/repos`,
    base `api.github.com` vs `<ghe>/api/v3`), **GitLab + GitLab EE** (`POST /projects`, base
    `<host>/api/v4`), **Bitbucket Cloud** (`POST /2.0/repositories/{workspace}/{slug}`) and
    **Bitbucket Data Center** (`POST /rest/api/1.0/projects/{key}/repos` — note: distinct API from
    Cloud). Token is HTTPS-only — **SSH-key creds can't be extracted**, so those hit the fallback.
  - **Fallback (BYO repo):** for SSH-key creds, "Other", or unadapted providers, the user
    creates/picks an empty repo in "New app"; the hub seeds + wires it.
  - **Name/visibility (all paths):** `sage-<slug>` (slug of the display name, host-safe chars,
    lowercased); Domino project keeps the display name; **always private**; collision → `-2`, `-3`.
  - Then: seed from the baked template (`/opt/sage/template`, 3.1) → initial commit → push →
    `POST /v4/projects` with `mainGitRepoRef` → git-based Domino project (body per 0.3).
  - **De-risk before implementing adapters:** `spikes/domino-probes/repo_provision_probe.sh`
    (authored) confirms the create/delete API shape per provider against the LIVE API — `DRY_RUN=1`
    default previews the exact (token-redacted) curl; `DRY_RUN=0` creates + auto-deletes a throwaway
    `sage-probe-<epoch>` private repo (EXIT-trap cleanup). GitHub path is fully wired/tested-first;
    GHE/GitLab/GitLab-EE/Bitbucket-Cloud/Bitbucket-DC are `[UNVERIFIED]` transcriptions to confirm on
    a workspace of that provider (Bitbucket workspace/project-key are guessed from the remote — check
    before a real run). Run this per provider and lock the shape before writing the adapter.
- 4.2 The **hub** (own Domino App): lists the user's Sage apps; "New app" runs 4.1 then
  `POST /workspace/.../workspace` into the Sage Environment (body per 0.3) → redirect into the
  builder. Ephemeral token per call from sidecar `:8899` (re-acquire each call).
- 4.3 UX: "creating your app…" over repo-create + cold-start; "Open" relaunches/reattaches an
  existing app's workspace. Handle repo-name collisions; fall back to a manual-repo path if a
  future user's PAT lacks `repo` scope.
  → **verify:** from the hub, "New app" creates a GitHub repo + git-based project + running builder
  in one flow; OpenCode edits commit+push to that repo; "Open" rehydrates an existing app.

## Phase 5 — Publish as Domino App

**Goal:** turn the generated app into a shareable Domino App.

- 5.1 Build/serve step for the generated app (Vite `build` → static serve, or a minimal app.sh in
  the project) targeting `:8888`.
- 5.2 `POST /modelProducts` in the project + `/{id}/start`; surface the resulting URL.
- 5.3 UX: "Publish" button → progress → shareable link; note it's a separate deployment (its own
  cold-start), distinct from the private in-session preview.
  → **verify:** click Publish → a running Domino App at a shareable URL serving the built app.

---

## Cross-cutting

- **Gateway wiring:** fold 0.4 results into `gateway/factory.py` / env; keep `FakeGatewayClient`
  for local/CI, `DominoGatewayClient` live in-workspace.
- **Egress guarantee:** still infra, not code. Prototype demonstrates routing/labeling/sovereign
  override; hard containment (network allowlist) is called out as prod infra, not claimed as
  enforced.
- **Tests:** router precedence (unchanged, pure); shim policy; single-project rehydration;
  preview-proxy base-path/HMR (integration, fake Vite); control-plane calls (mock Domino API).

## Open risks (ranked)

1. **Base-path/HMR under the double proxy (Phase 0.1)** — the one thing that can sink Path A.
   Spike first.
2. **Cold-start latency** for "New app"/"Open" — acceptable behind a state; no warm-pool in scope.
3. **Project-create/workspace-launch payloads undocumented** — resolved by 0.3, but could surface
   required fields (hardware tier, volume mounts) we must default sensibly.
4. **Generated-app dep drift** — baked baseline covers the template; agent-added libs install into
   project fs (slower reopen). Decide persistence of `node_modules` per app.

## Suggested build order

`Phase 0 (spike)` → `Phase 1` + `Phase 2` (parallelizable) → `Phase 3` → `Phase 4` → `Phase 5`.
