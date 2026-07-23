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

**Goal:** the builder renders correctly under the Domino proxy on one port.

- 1.1 `template/react-vite/vite.config.ts`: add `base` (injected at spawn), `server.hmr`
  (`clientPort`/`path`/`protocol` derived from the Domino prefix), `server.allowedHosts`, and
  `server.fs` as needed for the proxied host.
- 1.2 `preview/supervisor.py`: spawn Vite with `--base=<prefix>/preview/` (prefix computed from
  Domino env resolved in 0.1) and pass HMR config; keep runtime port discovery.
- 1.3 `orchestrator/app.py` `run()`: collapse two uvicorn servers → **one**; mount the preview
  proxy (`make_preview_app`) under `/preview` on the control app. Remove the `:8090` server.
- 1.4 `preview/proxy.py`: serve under the `/preview` sub-mount; ensure the browser-visible prefix
  is preserved end-to-end (HTTP + HMR ws).
- 1.5 `ui/index.html`: audit for absolute URLs; make all asset/API/SSE calls relative.
  → **verify:** launch the built image locally emulating the proxy prefix; UI loads, a build runs,
  the preview renders in the iframe, HMR live-reloads. All on one port under a subpath.

## Phase 2 — Project-model flip (one Domino project per builder)

**Goal:** retire multi-project; bind each builder to a single Domino project's file volume.

- 2.1 `orchestrator/service.py`: `Orchestrator` scoped to a single project derived from
  `DOMINO_PROJECT_ID`/`DOMINO_PROJECT_NAME`; drop the registry, `active()`, `list_ids()`.
- 2.2 `orchestrator/app.py`: remove `active-project`/`x-sage-project` header logic and the
  project-picker endpoints; routes act on the single bound project. Keep `open_project`'s
  **reattach-from-disk** rehydration as the *normal* startup path (session/history/plan/model
  overrides from `.sage/`).
- 2.3 `workspace/manager.py`: workspace root = the Domino project's mounted file volume
  (git-based: `/mnt/...`; dataset-backed: `/domino/datasets/local/...` — confirm in 0.1), not
  `backend/workspaces/<id>`. Keep warm-`node_modules` symlink from the baked template.
- 2.4 Update tests: remove multi-project cases; add single-project-scoped + rehydration tests.
  → **verify:** `make test` green; a builder opened in project A only ever sees project A's files;
  no cross-project surface remains (the "stream bleed" class is structurally impossible).

## Phase 3 — Environment packaging (the shippable artifact)

**Goal:** one Environment image = dev artifact = ship artifact.

- 3.1 `environment/Dockerfile`: base image + Node ≥20 + `uv` + Python deps + OpenCode (pinned) +
  the warm React+Vite template with **baked `node_modules`** (template baseline; agent-added deps
  install into the project fs on top).
- 3.2 `environment/pluggable-tools.yaml`: the Path-A `httpProxy` tool entry from 0.2.
- 3.3 `app.sh` / entrypoint: boot the single-port orchestrator (`uvicorn … --host 0.0.0.0 --port
  8888`), resolve the base prefix, start OpenCode server, gateway env wired to real values.
- 3.4 Keep a **fast inner dev loop** (bind-mount / dev override) so we don't rebuild the image per
  change — image is the deliverable, not the edit-test cycle.
  → **verify:** launch "Sage Builder" from the Environment in a real Domino project → describe an
  app → watch it build → private preview renders. End-to-end on Domino.

## Phase 4 — "New app" control plane (the hub)

**Goal:** Lovable/Replit-style "New app" that provisions a project + launches the builder.

- 4.1 A small **hub** (own Domino App, or a landing route): lists the user's Sage apps; "New app"
  → `POST /projects` (from a Sage project template) → `POST /workspace/.../workspace` +
  `/sessions` (into the Sage Environment) → redirect the user into the builder. Payloads per 0.3.
- 4.2 Token: acquire the ephemeral token per call from sidecar `:8899` (re-acquire each call).
- 4.3 UX: "creating your app…" state over the cold-start; "Open" relaunches/reattaches an existing
  project's workspace.
  → **verify:** from the hub, "New app" yields a fresh project with a running builder in one flow;
  "Open" on an existing app rehydrates it.

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
