---
doc: Implementation plan — Sage as one app container (Domino App or laptop), many project directories
status: draft for review, pre-implementation
branch: one-app-pivot-Etan
date: 2026-09-22
supersedes: DEPLOY-PLAN.md (Path A, one project per workspace), ADR-0004 (Workbench is the door)
reads first: CONTEXT.md, docs/adr/0008, 0010, 0020, 0023, 0040, 0041, 0043, 0067, LESSONS_LEARNED.md
---

# Sage as one app container

**Purpose.** Rule out Domino workspace and Environment lifecycle as a source of Sage's instability
by removing them from the path entirely, and give the coding agent one simple app stack it is
biased towards, with the least context that still teaches it Domino. Everything else in Sage —
the engine, sessions, model routing, resources, publish — is kept and made to work from one
process on either host.

**Ownership model (confirmed 2026-09-22).** Every person runs their own Sage: either they publish
the Sage App themselves, or they run it on a laptop. Sage acts as that person for every Domino call
— listing resources, creating the `sage-*` project and its repo, reading Datasets, publishing Built
Apps into that project — using their sidecar JWT in the App and their PAT on a laptop. There is no
shared Sage and no impersonation. Sage's own Domino API use stays in the backend (the control plane
and providers) behind UI actions; the **agent** does not get a Domino API tool for Sage's business.
What the agent gets is the `domino-platform-api` skill, for the apps it builds.

Line numbers below were read on 2026-09-22 at `83c8103b` and will drift. Treat them as "where to
start reading", not as coordinates. `origin/main` has since moved to `99598225`; merge it at the
start of Phase 0 (CLAUDE.md: merge `main` BEFORE the suite, `--no-ff`) and adapt this plan if the
merge moves anything named here.

## 0. What this pivot is, in one table

| | Today | After |
|---|---|---|
| Where Sage runs | A published Workbench App is the **door**; it provisions a per-user git-based Domino project and a per-user **Sage Builder workspace** (pluggable tool, port 8888), and redirects the browser there. One orchestrator process = one project volume at `/mnt/code`. | **One long-lived orchestrator process** — a Domino App on port 8888, or `make dev` on a laptop — holding **many project directories** under one root, exactly like a laptop with one folder per repo. No workspaces, no door. |
| Identity / tokens | Sidecar JWT at `localhost:8899` in a workspace; `DOMINO_API_KEY` as an optional static override. | One `TokenSource`: **Domino PAT from settings/env** (laptop) or **sidecar JWT re-fetched per call** (in Domino). Same object feeds the platform API, the LLM Gateway listing and the Data SDK. |
| Project = ? | The one mounted checkout. Project switch = leave the container. | A git clone of a `sage-*` Domino project at `$SAGE_HOME/projects/<slug>/`. Switch = change URL prefix `/p/<slug>/`. Domino APIs still create the GitHub repo + Domino project; they no longer create workspaces. |
| Engine | One `opencode serve` per container, sessions keyed by `location.directory`. | **Unchanged.** Same server, same `OpenCodeClient`, same thread/session stores, same shim/router/gateway. |
| Preview | One Vite or uvicorn per process on a fixed port, proxied at `/preview/`, prefix baked from `DOMINO_RUN_ID`. | One **uvicorn** per open project (fastapi-antd only), ephemeral port, proxied at `/p/<slug>/preview/`. Works behind the App proxy because uvicorn serves at root and the page recovers its own base. |
| Resources | Datasets read through `/mnt/data` mounts when present; symlinks into the app. Uploads written into a mounted Dataset. | **API/SDK only.** Dataset files are listed via the snapshots API and **copied** into the project (`data/<slug>/`) or the app (`public/data/<slug>/`). Drag-in files are plain committed files at `uploaded_files/`. The agent reaches a Dataset only as a local file or through a Sage-backed tool (`dataset_fetch`, `live_read_*`), never with a token of its own (§2.8). No mounts anywhere in Sage. |
| Stacks | `react-vite` (legacy default) and `fastapi-antd`. | **`fastapi-antd` only.** Node is needed for OpenCode only. |
| Agent context | 389-line AGENTS.md per app + 5 agent prompts repeating the same voice rules + a Domino API section that duplicates `LESSONS_LEARNED.md` (which is not wired in). | ~140-line AGENTS.md; **`LESSONS_LEARNED.md` becomes an on-demand skill** (`domino-platform-api`) the agent loads when a request names snapshots, tags, governance or "which datasets exist". |
| Theme | Domino theme + Google Bloom toggle, per person, `SAGE_BRAND_OVERRIDE`. | Unchanged. Default override path moves under `$SAGE_HOME` so it survives on both hosts. |

## 1. Findings that shape the design (what the code does today)

Six exploration passes over the tree. Only the facts the plan leans on are kept here.

**Single-project by construction.** `Orchestrator(workspace_dir, ...)` (`backend/sage/orchestrator/service.py:5478`) builds one `WorkspaceManager` and memoises one `Project` in `project()` (`service.py:6310`). `app.py:325` reads one `SAGE_WORKSPACE_DIR`; 128 routes in `app.py` call the module-level `orchestrator`. `WorkspaceManager`'s docstring says "Per D9 one container hosts one project". `DEPLOY-PLAN.md` §2 records that a multi-project-per-process registry once existed and was retired — this plan reinstates that shape with the current, far richer `Project`.

**The engine is already directory-agnostic.** `driver/server.py:3-5`: one `opencode serve` per container, "sessions are scoped by location.directory". `OpenCodeClient.create_session(directory=...)`, `GET /session/status?directory=`, `GET /event?directory=` (`driver/opencode.py:368, 561, 245`). Build sessions run in `apps/<appId>/`, Chat sessions in `<root>/.sage/chat-work/`. `ThreadStore`, `ProjectRecord`, `Workspace` all take one root path. `Orchestrator.__init__` already accepts an injected `opencode_client`, `gateway`, `catalog`, `assets`, `resources`, `control_plane`. OpenCode reads project config (`opencode.json`, `AGENTS.md`, `.opencode/skills`) from the **git root of the session directory**, not the server cwd (`server.py:93-105`).

**Identity is process-global and env-derived.** `_build_control_plane` uses one `sidecar_token` (`app.py:243`); `/api/me` and `_viewer_id()` fall back to `DOMINO_USER_ID` (`app.py:1141`, `service.py:4263`); the token provider is a bare `Callable[[], str]` chosen at each build site (`gateway/factory.py:58`, `app.py:211-219, 425-428`). No class, no host derivation from the token. `whoami()` = `GET /api/users/v1/self` (`provision/domino.py:730-756`). The Data SDK is built with no args and reads `DOMINO_API_PROXY` (`assets/provider.py:334-353`); an account **API key** as `token=` is rejected — a **PAT/JWT** has not been tried from Sage (LESSONS_LEARNED §4 says `DatasetClient(token=...)` accepts a valid token).

**Preview and proxy.** `ViteSupervisor`/`UvicornSupervisor` (`preview/supervisor.py`); Vite needs an absolute `base` baked at spawn and a shared port 5173 with an `lsof` reaper that kills sibling instances (`supervisor.py:41-53, 170-190`); uvicorn takes `_free_port()` and serves at root, and `sage_serve.py:41-64` recovers the browser-visible prefix by subtracting the received path from `location.pathname`. The App proxy strips the mount and exposes **one port**; the workspace proxy exposes no extra ports (`LESSONS_LEARNED.md:195`). So on Domino, path-based routing under one port is the only option, and it is only easy with the no-build stack. The Workbench UI is entirely relative (`api.js:5` `BASE='./api'`, `store.js:391` `previewSrc:'./preview/'`, hash router) — it works under any prefix as long as the page URL ends in `/`. Commit #500 (`service.py:6330-6355`) is the rule that a supervisor start must never hold the request thread pool.

**Resources.** Listing is API-only already: datasets `GET /api/datasetrw/v2/datasets?minimumPermission=ReadDatasetRwV2` (cross-project, `assets/provider.py:370`), files via `GET /v4/datasetrw/snapshots/{id}` + `snapshot/{sid}/files/recursive` (`:552,575`); Data Sources via `/api/datasource/v1/datasources` + the SDK's Arrow Flight (`resources/provider.py:2143, 2285`); aliases via the gateway. Content reads go through `DatasetClient().download_file` (`assets/provider.py:608-621`). Mount dependence is concentrated in `service.py`: `attach_file` symlink branch (`:22379`), `attach_folder` (`:22244`, refuses unmounted), `fetch_dataset_file_for_chat` (`:22534`), and **every upload→Dataset path** (`:22814-23100`, `_cross_chat_upload :10121`). There is **no Dataset write API in use anywhere**; the relay deliberately excludes the upload-session endpoints (`template/fastapi-antd/sage_domino.py:52-54`). Drag-in already lands in `.sage/scratch/` (`upload_scratch`, `service.py:22903`) with no mount.

**Agent context.** `template/fastapi-antd/AGENTS.md` (389 lines, ~8k tokens every turn) and `template/react-vite/AGENTS.md` share 200 identical lines; lines 284-358 (Domino API table + gotchas) restate `LESSONS_LEARNED.md`. `opencode.json` inlines `template/chat/AGENTS.md` verbatim (pinned by `backend/tests/test_sage_chat_prompt.py`); the four other agent prompts repeat the voice bullets; `sage-implement` carries both stacks' check commands. Skills already have two slots: global (`template/skills/*` → `~/.config/opencode/skills/`, `app.py:4823-4918`) and project (`template/<stack>/.opencode/skills/`, seeded with the app). AGENTS.md managed blocks are spliced by `_splice_instructions`, `_write_agents_data_block` (react-vite-specific JS snippet at `service.py:23641-23652`), `_write_app_model*`, `bound_schema`.

**A sibling branch goes the other way.** `origin/remove-fastapi-antd-stack` (`c4da4cfb`) removes fastapi-antd "because it is causing problems in the field" and keeps react-vite. This plan does the opposite on the product owner's instruction. Do not merge that branch; find out what the field problems were before Phase 0 lands, because they become this stack's problems (see §6).

## 2. Target architecture

```
Browser ──► Domino App proxy (strips /apps/<id>/, one port 8888)      or   ──► localhost:8080
             │
             ▼
   Sage orchestrator (FastAPI, ONE process, ONE port)
   ├─ /                         Projects home: list, open, new, settings    (root scope)
   ├─ /api/projects*            registry: list / create / clone / forget     (root scope)
   ├─ /api/settings, /api/me, /api/brand, /healthz                           (root scope)
   ├─ /css /js /img /vendor /fonts /brand                                    (static, root)
   └─ /p/<slug>/…               ONE PROJECT, dispatched to its Orchestrator
        ├─ /                    Workbench shell (index.html, hash routes #/chat, #/build…)
        ├─ /api/*               every existing project route, unchanged paths
        ├─ /v1/*                shim + native routes, dialled by OpenCode for THIS project
        ├─ /mcp/*               live-read / delegated tools (token → orchestrator)
        └─ /preview/*           reverse proxy → this project's uvicorn (ephemeral port)

   Shared, process-wide: TokenSource · DominoControlPlane · GatewayClient · ModelCatalog defaults
                         AssetProvider · ResourceProvider · OpenCodeServer + OpenCodeClient

   $SAGE_HOME/
     settings.json              host, token, gateway, publish env/tier, model defaults
     brand.json                 Appearance override (ADR-0044)
     projects/<slug>/           git clone of Domino project `sage-…` (repo root = Project volume)
        .sage/                  threads/ plan-docs/ settings.json model_overrides.json …  (as today)
        apps/<appId>/           Built Apps (as today, fastapi-antd)
        examples/               Chat artifacts (as today)
        opencode.json           gitignored; points sage-gateway at /p/<slug>/v1
```

### 2.1 Two hosts, one code path

| Concern | In a Domino App | On a laptop |
|---|---|---|
| `SAGE_HOME` | `/domino/datasets/local/sage-home/<user-id>/` if that Dataset is mounted into the Sage App, else `/tmp/sage-home` (ephemeral; projects re-clone on open) | `~/.sage/` |
| Domino host | `DOMINO_API_HOST` (injected) | `settings.domino.host` |
| Token | sidecar `http://localhost:8899/access-token`, fetched per call (5-min JWT) | `settings.domino.token` (PAT) or `DOMINO_USER_API_KEY` env |
| Gateway | `GATEWAY_BASE_URL` (baked ENV) else derived `https://apps.<host>/apps/llm_gateway/v1` | `settings.gateway.baseUrl`, default derived from host; optional `settings.gateway.apiKey` (`dgw_`) if the gateway does not accept the Domino PAT |
| Git credential for `sage-*` repos | `git credential fill` for `github.com`, which reaches the credential Domino wired into the App's own checkout (`provision/credentials.py`, already done for self-update) | `git credential fill` for `github.com` against the laptop's own helper (gh, keychain, manager); `settings.git.token` as the fallback when the helper answers nothing. **Domino does not hand out the secret of a stored git credential** — `GET /api/users/beta/credentials/{uid}` returns ids and names only — so the laptop cannot borrow the Domino-stored one; the same GitHub account behind both is what makes them "the same" |
| Publish env + tier | the App's own `DOMINO_ENVIRONMENT_ID` / `DOMINO_HARDWARE_TIER_ID` (the Sage image has fastapi, uvicorn, `domino_data`) | picked once in Settings from `/v4/environments` and `/v4/hardwareTier` listings |
| Proxy prefix | empty (nginx stripped the mount); page recovers it client-side | empty |
| Control port | 8888 | 8080 |

**Identity is single-user per process.** In an App the sidecar token is the publisher's; each person publishes their own Sage App from the Sage project, or an admin enables extended identity propagation (`DATA-SOURCES-RESEARCH.md:25`). The door, `whoami` caching per token and every `DOMINO_USER_*` fallback go away; identity is `GET /api/users/v1/self` with the one token.

### 2.2 The project registry

`backend/sage/projects/registry.py` (new, small):

- `ProjectRegistry(home, services)` scans `projects/*/.sage/project.json` — a new 6-key file written at clone/create: `{slug, dominoProjectId, dominoProjectName, ownerName, repoUrl, createdAt}`. Directory scan, no index (ADR-0008's rule).
- `list()` merges local clones with `sage-*` Domino projects the token can see (`GET /api/projects/beta/projects`, filter on `mainRepository.uri` as `door.py:135-145` does today) → rows `{slug, name, local: bool, current: bool}`.
- `open(slug)` → get-or-build `Orchestrator(workspace_dir=projects/<slug>, project_id=slug, ...shared services...)`. Lazy, cached in a dict. `close(slug)` stops its supervisors and drops it.
- `create(name)` → today's `ProvisionService.create_app` minus the workspace: GitHub repo (`provision/github.py`), seed + push (`provision/seed.py`, seeding only `.sage/settings.json` and `.gitignore` — apps are seeded on first handoff as today), Domino project with `mainRepository` (`provision/domino.py:302-329`), then the seeded dir **is** the project dir (move it into place; no second clone).
- `clone(dominoProjectId)` → `git clone` `mainRepository.uri` into `projects/<slug>` with the git credential.
- `orchestrator_for_session(sid)` / `orchestrator_for_live_token(tok)` — for the shared loopback routes.
- Shared `OpenCodeServer` hoisted out of `Orchestrator._ensure_opencode` (`service.py:7150`); `revoice()` becomes registry-level (restart once, all projects).

Each `Orchestrator` keeps its own turn lock, `ModelControl`, shim, supervisors, caches — one turn per project, projects in parallel, like several OpenCode windows on a laptop.

### 2.3 Routing

An ASGI dispatcher in `app.py` in front of `control_app`: for `path = /p/<slug>/<rest>` it appends `/p/<slug>` to `scope["root_path"]`, rewrites `path` to `/<rest>`, resolves `registry.open(slug)` and binds it in a `ContextVar`. Routes read `current_orchestrator()` instead of the module global; a thin proxy object named `orchestrator` keeps the 128 call sites textually unchanged (or, if the proxy reads as too clever, a FastAPI dependency and a one-line signature change per route — the reviewer's call at Phase 2). `_PrefixMiddleware` (`app.py:741-786`, notebookSession prefix) is deleted; `SAGE_BASE_PREFIX` stays as an optional override for a reverse proxy on a laptop.

Verify early (Phase 2, step 1): the `ContextVar` survives into `StreamingResponse` generators and `run_in_threadpool` for the SSE-over-POST routes. If not, capture the orchestrator at route entry and pass it explicitly to the generator — that is what the shim route already does with `project`.

**OpenCode → shim.** Each project's git root gets a gitignored `opencode.json`:
```json
{"provider":{"sage-gateway":{"options":{"baseURL":"http://127.0.0.1:<port>/p/<slug>/v1"}}}}
```
OpenCode merges project config over the global one, so every model call for a session in that repo arrives under `/p/<slug>/v1/…` and the existing `/v1/*` handlers run against the right orchestrator with no lookup. Verify the deep-merge in Phase 2 (OpenCode 1.18.4). Fallback if `provider.options` does not merge: force the native provider (`driver/provider.mjs`, which sends `x-session-id`) and resolve via `orchestrator_for_session`. The diagnostic that flags a project `opencode.json` as a foreign shadow (`app.py:1522-1560`) is inverted to expect ours.

**Phase 2 spike addendum (2026-09-23) — both open questions above answered from this codebase's own
prior research, not by running a live OpenCode server (no network in this sandbox for `npx`):**

1. **Project config lands at the right place already, by construction.** `driver/server.py` and
   `orchestrator/app.py:_install_opencode_config`'s own docstrings record hard-won findings from an
   earlier investigation (#199/#202, "measured on opencode-ai@1.18.4"): OpenCode resolves PROJECT
   config off the **git root of the SESSION directory** (`location.directory`), not the server's
   cwd, and project config outranks both `OPENCODE_CONFIG` and the global config. Today that slot is
   deliberately left unfilled — Sage's one workspace volume is the session directory for every
   session, and writing a config file into it would dirty `git status` in a single-project world. A
   per-project **gitignored** `opencode.json` at `projects/<slug>/opencode.json` fills exactly the
   slot this research already proved wins, with none of the reason they left it unfilled (gitignored
   files don't show up in `git status`).
2. **Don't rely on a partial-stub deep merge — write the full voiced config per project instead.**
   Nothing in this codebase's history tests whether OpenCode deep-merges a **partial** provider
   config (just `options.baseURL`) against the global config's `npm`/`models`/other keys for the
   same provider id, or replaces the whole `sage-gateway` entry wholesale (which would silently drop
   `npm` and `models` and break the provider). The one place this repo DID consider filling the
   project slot, it wrote the **entire** transformed config, not a stub (`_install_opencode_config`'s
   own comment: "the voiced blob still goes to `_opencode_project_dir()` as well... it costs nothing
   to have the file beside it be a voiced one rather than the template"). Phase 2 should follow that
   precedent: `registry.open(slug)` calls the same transform `_install_opencode_config` already
   applies (parameterized by target path and this project's shim port) to write the FULL resolved
   config to `projects/<slug>/opencode.json`, not the plan's original two-line stub. This removes
   risk #3 by construction instead of gambling on unverified merge semantics — refactor
   `_install_opencode_config` to take a `(source_dir, control_port, dest_path)` so both the global
   slot and every project slot go through one tested code path.
3. **The `ContextVar`/proxy question is not actually a risk in this codebase.** Checked every
   SSE route in `app.py` (`build_stream`, `chat_stream`, `build_approve`, `decline_handoff`,
   `_install_native_routes(control_app, lambda: orchestrator)`) and grepped every non-attribute use
   of the `orchestrator` name (163 plain `orchestrator.<attr>` reads, zero `isinstance` checks, zero
   places that store the module global by reference for later reuse outside the request). Every
   streaming route already calls `orchestrator.<method>(...)` **synchronously, in the route
   handler**, before building the `StreamingResponse` — the generator that results closes over the
   already-bound method of a concrete `Orchestrator` instance; it never re-reads the name later. A
   `current_orchestrator()` ContextVar lookup only has to be correct at that one synchronous call
   site, which is no different from today's plain global read. **The thin proxy object is safe as
   originally proposed** — no FastAPI-dependency rewrite of 128 signatures needed.
4. **A real risk the plan didn't name: ~267 test files monkeypatch `app_module.orchestrator`'s
   attributes directly** (`monkeypatch.setattr(app_module.orchestrator, "_wm", fake)`, etc.), relying
   on it being the SAME concrete `Orchestrator` object for the life of the test process (it's built
   once at import time and Python caches the module). A naive `__getattr__`-only proxy breaks this
   silently: `setattr(proxy, "_wm", fake)` would shadow the proxy's own `__dict__` instead of reaching
   the real object, since `__getattr__` is only consulted on lookup miss. **Fix, decided here:** the
   proxy also implements `__setattr__`/`__delattr__`, forwarding to whatever `current_orchestrator()`
   resolves to; and `current_orchestrator()` itself is `_CURRENT.get() or _DEFAULT`, where `_DEFAULT`
   is a plain module-level `Orchestrator` built exactly as today (not a ContextVar `.set()` at import
   time, which would depend on context-inheritance semantics across worker threads/tasks — an
   explicit `or _DEFAULT` fallback needs none of that). The dispatcher's per-request `ContextVar.set()`
   is then a pure OVERLAY used only inside a real `/p/<slug>/...` dispatch; every existing test and
   every pre-Phase-2 root-scope route keeps resolving to `_DEFAULT`, unchanged, with zero of the
   267 files touched.
5. **Naming collision caught before it was written:** `sage.workspace.manager.ProjectRecord` already
   owns that class name for a different record (a Project's plan/settings/session bookkeeping at the
   volume root, ADR-0008). The registry's own 6-key file (§2.2) is implemented as `RegistryEntry` in
   `sage/projects/registry.py`, landed this session — see ONE-APP-STATUS.md.

### 2.4 Preview

`UvicornSupervisor` only; `ViteSupervisor`, `preview_port()`, `_clear_stale_port` and the `lsof` reaper go. Port is `_free_port()`, `--strictPort` semantics by construction. Mount base is `""` (uvicorn serves at root), so `/p/<slug>/preview/<path>` → `http://127.0.0.1:<port>/<path>`. The page's `sage_serve.py` shim writes `<base href>` from the received path, and every helper uses `sage.url()` — so under `/apps/<uuid>/p/<slug>/preview/` nothing in the served HTML needs rewriting. `PreviewQueries` (the app's own `sage_queries.py` on loopback) stays per project. Supervisors start on a background thread per orchestrator (the #500 rule), never on the request path. The supervisor passes the child `DOMINO_API_HOST` and, on a laptop, `SAGE_DOMINO_TOKEN`, so `sage_queries.py`'s Flight executor and `sage_domino.py` can authenticate without a sidecar (template change in Phase 5).

### 2.5 Resources without mounts

- `assets/provider.py`: drop `resolve_mount_roots`, `_mount_path_for`, `walk_files`; `Asset.mount_path` is removed (or always `None` for one release to keep the UI contract, then removed). Listing = snapshots API (already the unmounted branch). Content = `download_file` via the SDK built from the `TokenSource` (`DatasetClient(token=...)`); if the SDK rejects a PAT on a laptop, a REST fallback `GET /v4/datasetrw/snapshot/{sid}/file/raw?path=` (referenced by the relay allow-list family; verify it exists on the target cluster).
- `attach_file` → always `_download_attachment`: a real copy at `apps/<appId>/public/data/<dataset-slug>/<file>`. `public/data/<dataset-slug>/` stays gitignored; the manifest `.sage/attachments.json` is the source of truth and `scripts/rehydrate_data.py` step 2 (already SDK-only) rebuilds it at publish boot. Step 1 (`link_mounts`) is deleted.
- `attach_folder` → list under the prefix, download each, capped by count and bytes (the comment at `service.py:22245` already names this as the alternative). Refuse over the cap with the count.
- Drag-in files (confirmed: plain files in the project directory, no Dataset for now). `upload_scratch` writes to **`<project>/uploaded_files/<name>`**, committed with the next save, not to the gitignored `.sage/scratch/`. The name matches what `LESSONS_LEARNED.md` §0 already tells the agent to read (`pd.read_csv("uploaded_files/…")`). **Attaching an upload to a Built App copies it to `apps/<appId>/uploaded_files/<name>`**, also committed, so the published app has it with no rehydrate and the agent's cwd-relative path in the lessons holds. Per-file cap (25 MB) refused with a message above it. `public/data/` stays for Dataset copies only. ADR-0023's "an Upload crosses by becoming an Attachment written into a Dataset" is revised: the crossing is a committed copy into the app; a Dataset write API is `later`.
- `_default_dataset`, `_resolve_upload_target`, `promote_scratch_to_dataset`, `_cross_chat_upload`, `_delete_upload_bytes`, the `writable` flag and the "Add to <dataset>" menu (`resource-panel.js:260-326`) are removed.
- `fetch_dataset_file_for_chat`: keep only the download branch. Live read `_file_rows` (`liveread/run.py:390`): download the head to `.sage/scratch/` then read, instead of `dataset_root`.
- `shim/chat_paths.py:46-56` `/mnt/code/` prefix stripping, `threads.py:939-957` symlink skip rules, the OpenCode `/mnt/data` read hang guard (`driver/opencode.py:475-481`): delete with their reason.
- Sensitivity (ADR-0043), Data Source cascade, aliases, Model APIs, collaborators: unchanged — already API-only.
- "Datasets from anywhere in the platform": already what `list_datasets` returns; the Browse Domino modal (`resource-catalog.js`) needs no change beyond the `writable` removal.

### 2.8 How a Domino resource reaches the agent (the judgment call)

Today a Dataset reaches OpenCode by three different routes depending on where Sage runs: a symlink
into a mount (`attach_file`, `service.py:22379`), a downloaded copy (`_download_attachment`), or a
prompt line telling the model to build `DatasetClient()` itself and download into `.sage/scratch/`
(`service.py:3855-3891`). Data Sources reach it through Sage-backed custom tools (`live_read_table`,
`live_read_query`, `live_read_files`, `backend/sage/liveread/tools/`) that call back into
`/mcp/live-read` where Sage holds the token (ADR-0041). The working set is orientation only
(ADR-0020); the Session-context chip is the door into a conversation (ADR-0021).

**Rule after the pivot: if the model can use it, it is a file in the project directory or it is
behind a Sage tool. The agent never holds a Domino token.**

- **Files in the directory.** Drag-in uploads at `uploaded_files/` (§2.5). A Dataset file the person
  picks in the panel is downloaded by Sage to `data/<dataset-slug>/<path>` at the project root
  (gitignored, re-fetchable, recorded in a manifest) for Chat, and copied to
  `apps/<appId>/public/data/<dataset-slug>/<path>` when attached to a Built App, exactly as the
  copy branch does today. The chip's prompt line becomes one sentence: the local path.
- **Sage-backed tools, unchanged in shape.** `live_read_*` and `delegated_model_call` stay; the
  `live_read_files` file-head arm downloads to `data/<dataset-slug>/` instead of reading a mount.
  One tool is added, `dataset_fetch(dataset, path) → local path`, so the model can pull a whole
  file it needs from a Dataset the person already put in the working set (the case the
  "run `DatasetClient()` yourself" prompt covered). It downloads through Sage's `TokenSource`,
  writes under `data/<dataset-slug>/`, records it in the manifest, and returns the path.
- **Removed.** The `DatasetClient()` prompt lines; any thought of putting `DOMINO_USER_API_KEY` or
  the sidecar URL into the OpenCode server's environment (`sage-chat` runs `bash: allow`, and
  `environment/app.sh` already goes to lengths to keep tokens out of that env).
- **No general Domino API tool for the agent.** Sage's own platform work — listing resources,
  creating the project and repo, publishing — stays in the backend behind UI actions. The apps the
  agent builds read the platform at runtime through `sage_domino.py` and `sage_queries.py`, and the
  `domino-platform-api` skill teaches that. If a later need for the agent to, say, list Datasets
  itself appears, it is one more narrow Sage-backed tool in the same pattern, not a token.
- **Working set semantics stay** (ADR-0018/0020/0021) and `.sage/project-resources.json` keeps its
  schema — `LESSONS_LEARNED.md` §2 tells built apps to read it for Dataset ids.

### 2.6 Publish

`publish_app` (`provision/domino.py:525-552`, `POST /api/apps/beta/apps`, `entryPoint apps/<appId>/app.sh`, `gitRef head`) is unchanged. What changes: `publish_available()` (`preview/prefix.py:29-48`) is replaced by "the project has a `dominoProjectId` and settings have an environment and tier"; the app-mode refusal in `service.py:18831-18839` goes. Pre-publish `_save_to_git` push uses the registry's git credential.

### 2.7 Settings

`backend/sage/config.py` (new): one dataclass loaded from `$SAGE_HOME/settings.json`, env vars overriding (`DOMINO_API_HOST`, `DOMINO_USER_API_KEY`, `GATEWAY_BASE_URL`, `GATEWAY_API_KEY`, `SAGE_MODEL_*`, `DOMINO_ENVIRONMENT_ID`, `DOMINO_HARDWARE_TIER_ID`). `GET/PUT /api/settings` (root scope). UI: a **Connection** section in the existing Account settings drawer (`shell.js:579-747`) with Domino host, token (write-only, shown as set/unset), Gateway URL (prefilled), optional gateway key, publish environment + tier pickers, and a "Test connection" that calls `/api/users/v1/self` and `GET <gateway>/v1/models`. Model-per-slot assignment stays where it is (`.sage/model_overrides.json` per project, `SAGE_MODEL_*` as deployment defaults, now also settable in `settings.json`). First run with no host/token: the Projects home shows the Connection form and nothing else.

## 3. Decisions and assumptions

Numbered so the build sessions can cite them. Items 1-4 were **confirmed by the product owner on 2026-09-22**; the rest are the planner's calls, open to change.

1. **One user per Sage process** (confirmed). Each person publishes their own Sage App or runs Sage locally; Sage uses their token for every Domino call and publishes into the `sage-*` project it created for them. No door, no per-viewer token juggling; extended identity is not Sage's concern. (Phase 1)
2. **Projects are always Domino git-based projects** with a GitHub repo, created through the existing provision code; there is no "local-only" project (confirmed). Git credential on both hosts is `git credential fill` for `github.com`; a settings token is the fallback, because Domino never returns a stored credential's secret. (Phase 3)
3. **`fastapi-antd` is the only stack** (confirmed: one simple stack the agent is biased towards, no picker). Old `react-vite` apps in re-cloned projects open in Chat; Build on them shows "built with a stack this Sage no longer carries" — no migration. The sibling branch `origin/remove-fastapi-antd-stack` goes the other way and is not merged; another developer is untangling field issues on the current shape, so coordinate on that branch's findings rather than re-deriving them. (Phase 0)
4. **Uploads are plain files in the project directory, no Dataset** (confirmed). `uploaded_files/` at the project root, copied into the app on attach, both committed. Revises ADR-0023. (Phase 5)
5. `$SAGE_HOME` in the App is a mounted Dataset when one is there, else ephemeral. Everything durable is in git anyway (ADR-0006); scratch uploads not yet attached are the only loss on restart. (Phase 7)
6. Routing is `/p/<slug>/…` with the Workbench page at `/p/<slug>/`; no project id in the hash. Reload lands on the same project because the project is in the path. (Phase 2)
7. Per-project gitignored `opencode.json` carries the shim URL. (Phase 2, with the fallback in §2.3)
8. Keep `boot_page.py` (the App proxy also 502s before uvicorn binds), `SAGE_SELF_UPDATE`, the brand system, the Manage / Cost links (hidden when the URLs cannot be built).
9. `LESSONS_LEARNED.md` stays at the repo root as the file people edit; the skill is generated from it at boot (frontmatter + body), pinned by a test the way `test_sage_chat_prompt.py` pins the chat prompt. (Phase 0)
10. Tests: `conftest.py` pins `SAGE_DEFAULT_STACK=react-vite` because every fake template is react-vite shaped; Phase 0 reshapes the fakes to fastapi-antd and drops the pin. ~40 test files name react-vite; they are retired or rewritten with the template, not deselected.
11. **Resources reach the agent as files in the directory or through Sage-backed tools, never via a token in the agent's shell** (§2.8). One new tool, `dataset_fetch`; no general Domino API tool for the agent. Left to the planner by the product owner on 2026-09-22. (Phase 5)

## 4. Work plan

Each phase lands on `main` green and usable on its own. Verification lines are the "done" test. Phases 0 and 1 can run in parallel worktrees; 2 depends on 1; 3-6 depend on 2; 7 and 8 close.

### Phase 0 — One stack, one small context (independent of the pivot; do first)

Removes Vite, which is what makes multi-preview and the App proxy hard, and shrinks the surface every later phase touches.

1. **Delete `template/react-vite/`** and the `REACT_VITE` entry: `workspace/stack.py` (`REACT_VITE`, `LEGACY_STACK`, `stack_of` fallback), `workspace/manager.py` (`_DEPS_SENTINEL`, `link_warm_deps`, `refresh_preview_config`, `stack_for`'s `SAGE_TEMPLATE` override), `preview/supervisor.py` (`ViteSupervisor`, `preview_port`, `_clear_stale_port`), `feedback/runner.py` (tsc branch), `resources/app_helpers.py` (`TEMPLATE`, `LEGACY`), `app.py:301,443` (`SAGE_TEMPLATE` default), `environment/app.sh:16`, `Dockerfile` `npm ci` block, `Makefile:15`, `scripts/live-run.sh:72`. UI: `prefs.js:37` `appStack`, `shell.js:659-661` radio, `builder.js:357-359` label, `POST /api/apps {"stack"}` (`app.py:3359-3372`). Unknown recorded stack → Build refuses with one sentence, Chat unaffected.
   → verify: `grep -rn "react-vite\|REACT_VITE\|vite" backend/sage template environment Makefile` is empty except NOTICE; `make lint`; suite green with the conftest pin removed.
2. **Rewrite `template/fastapi-antd/AGENTS.md`** to ~140 lines: intro + `NOTHING_TO_BUILD`; ≤8 voice bullets; glossary tokens; the rules that matter (don't-touch list, no installs, one edit per file, `.sage/` + `public/data/` rules, runtime data, plain scripts, antd); design system as a tokens/states checklist; "What exists" table; `sage.url` rule; a 6-line **Domino API gate**: "only when the request names a snapshot, version, approval, policy, tag or which datasets exist — load the `domino-platform-api` skill; the relay allow-list in `sage_domino.py` is authoritative". Delete lines 284-358.
   → verify: `wc -l` ≤ 160; `brand_coverage.toml` updated (drop `react-vite`); brand lint passes.
3. **Wire `LESSONS_LEARNED.md` as a global skill.** `_install_opencode_skills` (`app.py:4823`) writes `~/.config/opencode/skills/domino-platform-api/SKILL.md` = frontmatter (`name: domino-platform-api`, a ≤300-char `description` that is the gate above) + the body of `LESSONS_LEARNED.md`. Add `/api/taxonomy/v1/` to `PLATFORM_READS` in `sage_domino.py` (LESSONS §6 needs it) and record the external-host caveat from §6 beside it.
   → verify: new test pins skill body == `LESSONS_LEARNED.md`; `GET /skill?directory=<app>` from OpenCode lists it (the diag at `app.py:1301` already reads this).
4. **Trim `opencode.json` agents**: remove the tsc paragraph from `sage-implement`; move the repeated voice bullets into one shared sentence per agent; fix `template/chat/AGENTS.md:58,223` (`src/`). Fix `service.py` hard-coded `src/` text: `IMPLEMENT_NUDGE`, `LEAK_FIX_NUDGE`, `_phase_prompt` default entry, `_write_agents_data_block` JS snippet (`:23641-23652`) → `Stack.entry_file` and `sage.url("data/…")`; `driver/opencode.py:64`, `bound_schema.py:325`.
   → verify: `test_sage_chat_prompt.py` still equal; grep `src/` in `backend/sage` and `opencode.json` returns only stack-neutral hits.
5. Tidy `template/fastapi-antd/.gitignore` (drop Node lines); `template/fastapi-antd/app.sh`: interpreter search stays, add `SAGE_DOMINO_TOKEN` passthrough (used in Phase 5).

### Phase 1 — Config and one TokenSource

1. `backend/sage/config.py`: `Settings` dataclass + `load(home)`; env overrides; `derive_gateway_url(host)`; `$SAGE_HOME` resolution (§2.1).
2. `backend/sage/platform/auth.py`: `TokenSource` with `static(pat)` and `sidecar(url)`; `.bearer()`; `.whoami()` cached for the token's life. Replace every build-site choice: `gateway/factory.py:58`, `app.py:211-219, 243, 425-428, 4519`, `tools/app_visibility.py:105`. Remove `DOMINO_USER_ID/NAME/STARTING_USERNAME` fallbacks (`app.py:1141`, `service.py:4263, 7108`); `_hydrate_untitled` (`service.py:7096`) asks `whoami`.
3. SDK construction from the token: `assets/provider.py:_sdk_dataset`, `resources/provider.py:2315, 2401` → `DatasetClient(token=source.bearer())` / `DataSourceClient(token=...)`, built per call as today.
4. `GET/PUT /api/settings` + Connection section in the settings drawer + first-run gate.
   → verify: on a laptop with a PAT, `/api/me`, `/api/assets`, `/api/resources` answer; `make probe` completes one gateway call. **Live checks recorded in the report:** (a) PAT as Bearer on `/api/users/v1/self`; (b) `DatasetClient(token=PAT).get_dataset(...).download_file(...)` — if refused, note it and Phase 5 uses the REST fallback; (c) whether the LLM Gateway accepts the Domino PAT, else the `dgw_` key field is required on laptops.

### Phase 2 — Registry and per-project routing

1. Spike (½ day): the dispatcher + `ContextVar` + proxy against the SSE routes and the `/preview` mount; the OpenCode project-config merge (§2.3). Both answers written into this file.
2. `projects/registry.py` (§2.2), `project.json`, hoisted `OpenCodeServer`; `Orchestrator` gains nothing new except reading `opencode_client` it already accepts. `_gateway_ui_url`, `cost_project_label`, `manage_url` become per project (`app.py:328-385` → registry).
3. Dispatcher in `app.py`; delete `_PrefixMiddleware`, `preview/prefix.py` (`domino_base_prefix`, `proxy_is_app`, `publish_available`, `domino_project_label`) and every `DOMINO_PROJECT_OWNER/NAME/RUN_ID` read. Root-scope routes: `/`, `/api/projects*`, `/api/settings`, `/api/me`, `/api/brand`, `/healthz`, statics.
4. Loopback: per-project `opencode.json` written by `registry.open`; `/mcp/live-read` and `/mcp/delegated-model` resolve by token across open orchestrators.
5. Projects home page (`workbench/home.html`, reusing `door.html`'s shell): rows from `/api/projects`, Open → `/p/<slug>/`, New, Settings. Scope picker (`scope-picker.js:50-53`) → `location.assign('../<slug>/')`; `SW.api.projects()` (`api.js:353-376`) → one `GET ../api/projects`; `handOver()` / `attachProject` / provisioning polls removed.
   → verify: two projects open in two tabs, a Chat turn in each concurrently; `/p/a/preview/` and `/p/b/preview/` serve different apps; reload keeps the project; the js harness for scope-picker updated.

### Phase 3 — Project lifecycle over Domino APIs (no workspaces)

1. `registry.create(name)` = `ProvisionService.create_app` minus `create_workspace`; the seeded dir becomes `projects/<slug>` (`.sage/project.json` written before the first push so a half-finished create is recognisable).
2. `registry.clone(dominoProjectId)` for a `sage-*` project seen in the listing but not on disk; the git credential from `settings.git.token` or `credentials.extract_token`.
3. Delete: `provision/door.py`, `door.html`, `/api/door*`, `/api/projects/{id}/open`, `/api/projects/status`, `create_workspace`/`start_workspace_session`/`stop_workspace`/`delete_workspace`/`workspace_status`/`workspace_http_ready`/`save_workspace_work` in `provision/domino.py` and `provision/service.py`, `Orchestrator.stop()` + `/api/stop` + `_resolve_workspace_id` (`service.py:19384-19422`), `environment/pluggable-tools.yaml`, `SAGE_BUILDER_TOOL`.
4. Git credential: one resolver, `credentials.extract_token("github.com")` generalised to run `git credential fill` from the project dir, then `$SAGE_HOME`, then the App checkout; `settings.git.token` last. Domino project creation keeps choosing a `gitCredentialId` from the person's Domino credential list as today (ADR-0033: proven by use).
5. Git identity: `workspace/git.py` commits as `whoami().fullName <email>`; verify `_save_to_git` push works with the resolved credential on both hosts.
   → verify: New project from a laptop creates repo + Domino project and lands in Chat in `/p/<slug>/`; the same project opens in the App container by clone; `git log` on the clone shows Sage's per-turn commits.

### Phase 4 — Preview per project

1. Supervisor per orchestrator on a background thread; `_free_port()`; child env carries host + token; `mount_base=""`.
2. `/p/<slug>/preview/*` through the dispatcher; `PreviewPane` unchanged; `previewStatus` reset to `starting` on project open.
3. Delete `SAGE_PREVIEW_PORT`, the reaper, `SAGE_PROXY_MODE`.
   → verify: behind the App proxy, iframe at `/apps/<uuid>/p/<slug>/preview/` renders with `sage.url()` fetches resolving under the mount; on a laptop the same at `localhost:8080/p/<slug>/preview/`; a preview that cannot start leaves every other route answering (the #500 test, now per project).

### Phase 5 — Resources without mounts

Steps as §2.5, in this order: (1) `assets/provider.py` mount removal + SDK-from-token + REST fallback; (2) `attach_file` copy-only, `attach_folder` bounded download; (3) uploads → committed `uploaded_files/` at the project root and in the app on attach, remove the Dataset write paths and the `writable` UI; (4) Chat dataset chip → download to `data/<dataset-slug>/` and a one-line path in the prompt; live-read file head via the same download; the `DatasetClient()` prompt lines (`service.py:3855-3891`) deleted; (4b) new `dataset_fetch` custom tool beside `backend/sage/liveread/tools/`, served from `/mcp/live-read` (or a sibling route) with the same per-conversation token, and its two sentences in `template/chat/AGENTS.md` and the build AGENTS.md; (5) delete the path-stripping and symlink rules; (6) `rehydrate_data.py` step 1 removed, `sage_domino.py`/`sage_queries.py` `token()` honours `SAGE_DOMINO_TOKEN` before the sidecar; (7) ADR-0023 revision + CONTEXT.md Dataset entry ("mounted into the project container" → "reached over the platform API").
→ verify: drag a CSV into Chat, ask about it, hand off to Build, attach it — it sits at `apps/<appId>/uploaded_files/` and is in `git status`, and the agent reads it by the path `LESSONS_LEARNED.md` §0 names; attach a file from a Dataset in another project — a copy appears under `public/data/<slug>/`, is gitignored, and `rehydrate_data.py` restores it from an empty dir; in Chat, ask about a Dataset in the working set with no file picked and the model calls `dataset_fetch` and reads the returned path; `env | grep -i domino` from a `sage-chat` bash call shows no token; sensitivity lock still narrows models for a tagged Dataset.

### Phase 6 — Publish from the container

`publish_available` replacement; settings pickers for environment and tier (`/v4/environments`, `/v4/hardwareTier` — confirm paths live); `_refuse_unsafe_publish` unchanged.
→ verify: publish a fastapi-antd app from a laptop and from the App; the published app rehydrates its attachments with the sidecar and serves the committed uploads.

### Phase 7 — Packaging: App and laptop

1. `environment/app.sh` → the App entrypoint: `SAGE_HOME` resolution, boot page, `exec uv run --extra domino python -m sage.orchestrator.app`. Root `app.sh` no longer sets `SAGE_PROXY_MODE` or a scratch workspace. `Dockerfile`: drop the template `npm ci`, keep Node for OpenCode, add nothing.
2. `make dev` (laptop): `uv sync --extra domino`, `npm ci`, run on 8080 with `SAGE_HOME=~/.sage`. `README.md` rewritten around the two hosts; `environment/README.md` loses the door and pluggable tool.
3. Brand override default → `$SAGE_HOME/brand.json`.
→ verify: fresh laptop clone → `make setup && make dev` → Settings → New project → Chat turn → Build → preview → publish, no Domino workspace involved; the App boots from the image and the same steps pass behind the proxy.

### Phase 8 — Close out

ADRs: a new ADR "Sage is one process holding many project directories" superseding ADR-0004 and DEPLOY-PLAN.md's decision 2; revise ADR-0023 (uploads), ADR-0040 (one current app per project still holds, now per project), ADR-0067 (one stack). `CONTEXT.md`: Dataset, Working set, Resource Browser entries. Delete `DEPLOY-PLAN.md`'s workspace sections or mark superseded. `make lint` on `main` after every landing.

## 5. Verification and gating

- Per CLAUDE.md §5/§6: targeted tests while iterating, the full suite once per landing on the merged tree, `-rs` in worktrees, reconcile on COLLECTED. One suite slot on the machine; claim on the issue.
- New tests, one per condition, in the phase that introduces it: `dataset_fetch` writes under `data/<slug>/`, records the manifest and refuses a path outside the Dataset; no Domino token variable reaches the OpenCode server env; dispatcher root_path/path rewriting; ContextVar reaches an SSE generator; registry list merges local and remote; two orchestrators run turns concurrently without sharing a turn lock; preview per project on distinct ports; skill body equals `LESSONS_LEARNED.md`; upload attach commits the file and refuses over the cap; dataset attach is a copy and gitignored; no stack but `fastapi-antd` is seedable; `publish_available` from settings.
- Live checks that cannot be unit-tested are listed in Phase 1 and Phase 6 and must appear in the landing report as measured, not assumed (CLAUDE.md: a report that can be landed on).

## 6. Risks and open questions

| # | Risk | Where it bites | Mitigation |
|---|---|---|---|
| 1 | The SDK rejects a Domino PAT as `token=` off-Domino | Phase 1/5 laptop dataset reads and every Data Source query | REST `file/raw` fallback for Datasets; Data Sources unavailable on laptops until a JWT source exists (acceptable: other resource kinds may be placeholders) |
| 2 | ~~The LLM Gateway does not accept a Domino PAT~~ **RESOLVED, live-verified 2026-09-23 by the product owner from their own laptop**: `curl -H "Authorization: Bearer $DOMINO_PAT" $GATEWAY_URL/v1/chat/completions` succeeded against a real Domino gateway. Matches `gateway_bearer()`'s no-`dgw_`-key branch (`platform/auth.py`) exactly — a bare account PAT as `Authorization: Bearer` is enough; the `settings.gateway.apiKey` (`dgw_`) field stays only as an override for a gateway that genuinely needs one, not a default requirement. | Phase 1 laptop model calls | none needed |
| 3 | OpenCode does not deep-merge `provider.options` from a project `opencode.json` | Phase 2 shim routing | **RESOLVED by the Phase 2 spike (2026-09-23), not by testing the merge — by not needing it.** Write the FULL voiced config per project (the same transform `_install_opencode_config` already applies for the global slot), not a `{"provider":{"sage-gateway":{"options":{"baseURL":...}}}}` stub. See §2.3 addendum below. |
| 4 | `ContextVar` does not reach a streaming generator | Phase 2 | **RESOLVED by the Phase 2 spike (2026-09-23): not a risk in this codebase's shape.** Every SSE route (`build_stream`, `chat_stream`, `build_approve`, `decline_handoff`) already calls `orchestrator.<method>(...)` *synchronously in the route handler*, before constructing the `StreamingResponse` — the returned generator is a bound method closing over the already-resolved `Orchestrator` instance, not a re-lookup. A ContextVar-backed accessor only ever needs to resolve correctly at that one synchronous call point, which is exactly where a normal attribute read already happens today. No capture-and-pass-explicit rewrite needed. See §2.3 addendum. |
| 5 | The field problems that motivated `remove-fastapi-antd-stack` | Phase 0 onward | Another developer is on them; read that branch's commit body and issues before Phase 0 and carry any fix that is about the stack itself (interpreter search in `app.sh`, CDN script order — LESSONS §9) rather than about workspaces |
| 6 | 24k-line `service.py` and 128 routes: the multi-project change is mechanical but wide | Phase 2 | Proxy object keeps call sites unchanged; land the spike first; one PR for the dispatcher, one for the registry |
| 7 | App container restarts lose un-attached scratch uploads | Phase 7 | Mounted `sage-home` Dataset when available; say so in the UI ("not saved until attached") |
| 8 | A user with many `sage-*` projects: cloning on open is slow the first time | Phase 3 | Clone is on-demand, `--depth 50`; show progress on the home page |
| 9 | Taxonomy API needs the external cluster URL (LESSONS §6) while the relay uses `DOMINO_API_HOST` | Phase 0 step 3 | Relay tries `DOMINO_API_HOST`, then the host from settings; record which worked |
| 10 | Two Sage processes on one laptop | Phase 4 | Ephemeral ports everywhere; `SAGE_HOME` and control port per instance |

## 7. Size

| Phase | Touches | Deletes (approx.) | Shape |
|---|---|---|---|
| 0 | stack registry, supervisor, feedback, templates, opencode.json, ~40 tests | template/react-vite (~5,900 lines incl. lockfile), Vite paths | 2 PRs |
| 1 | config, auth, 6 build sites, SDK ctor, settings UI | env fallbacks | 1-2 PRs |
| 2 | app.py dispatcher, registry, home page, scope picker | prefix.py, `_PrefixMiddleware` | spike + 2 PRs |
| 3 | provision/service.py, registry | door, workspace API, pluggable tool | 1 PR |
| 4 | supervisor wiring | reaper, proxy mode | 1 PR |
| 5 | service.py attach/upload, assets provider, liveread, template scripts, ADR-0023 | mount code | 2-3 PRs |
| 6 | publish availability, settings pickers | app-mode refusal | 1 PR |
| 7 | app.sh, Dockerfile, Makefile, READMEs | — | 1 PR |
| 8 | ADRs, CONTEXT.md | DEPLOY-PLAN sections | 1 PR |
