---
doc: Implementation plan — Sage as one app container (Domino App or laptop), many project directories
status: draft for review, pre-implementation
branch: one-app-pivot-Etan
date: 2026-09-22
supersedes: DEPLOY-PLAN.md (Path A, one project per workspace), ADR-0004 (Workbench is the door)
reads first: CONTEXT.md, docs/adr/0008, 0010, 0020, 0023, 0040, 0041, 0043, 0067, LESSONS_LEARNED.md
---

# Sage as one app container

Line numbers below were read on 2026-09-22 at `83c8103b` and will drift. Treat them as "where to
start reading", not as coordinates. `origin/main` has since moved to `99598225`; merge it before
Phase 0 (CLAUDE.md: merge `main` BEFORE the suite, `--no-ff`).

## 0. What this pivot is, in one table

| | Today | After |
|---|---|---|
| Where Sage runs | A published Workbench App is the **door**; it provisions a per-user git-based Domino project and a per-user **Sage Builder workspace** (pluggable tool, port 8888), and redirects the browser there. One orchestrator process = one project volume at `/mnt/code`. | **One long-lived orchestrator process** — a Domino App on port 8888, or `make dev` on a laptop — holding **many project directories** under one root, exactly like a laptop with one folder per repo. No workspaces, no door. |
| Identity / tokens | Sidecar JWT at `localhost:8899` in a workspace; `DOMINO_API_KEY` as an optional static override. | One `TokenSource`: **Domino PAT from settings/env** (laptop) or **sidecar JWT re-fetched per call** (in Domino). Same object feeds the platform API, the LLM Gateway listing and the Data SDK. |
| Project = ? | The one mounted checkout. Project switch = leave the container. | A git clone of a `sage-*` Domino project at `$SAGE_HOME/projects/<slug>/`. Switch = change URL prefix `/p/<slug>/`. Domino APIs still create the GitHub repo + Domino project; they no longer create workspaces. |
| Engine | One `opencode serve` per container, sessions keyed by `location.directory`. | **Unchanged.** Same server, same `OpenCodeClient`, same thread/session stores, same shim/router/gateway. |
| Preview | One Vite or uvicorn per process on a fixed port, proxied at `/preview/`, prefix baked from `DOMINO_RUN_ID`. | One **uvicorn** per open project (fastapi-antd only), ephemeral port, proxied at `/p/<slug>/preview/`. Works behind the App proxy because uvicorn serves at root and the page recovers its own base. |
| Resources | Datasets read through `/mnt/data` mounts when present; symlinks into the app. Uploads written into a mounted Dataset. | **API/SDK only.** Dataset files are listed via the snapshots API and **copied** into the app. Drag-in files land in `.sage/scratch/`, and attach to an app as committed copies. No mounts anywhere in Sage. |
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
| Git credential for `sage-*` repos | borrowed from the App's own checkout via `git credential fill` (`provision/credentials.py`, already done for self-update) | `settings.git.token` or `gh auth token` / the user's normal credential helper |
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

### 2.4 Preview

`UvicornSupervisor` only; `ViteSupervisor`, `preview_port()`, `_clear_stale_port` and the `lsof` reaper go. Port is `_free_port()`, `--strictPort` semantics by construction. Mount base is `""` (uvicorn serves at root), so `/p/<slug>/preview/<path>` → `http://127.0.0.1:<port>/<path>`. The page's `sage_serve.py` shim writes `<base href>` from the received path, and every helper uses `sage.url()` — so under `/apps/<uuid>/p/<slug>/preview/` nothing in the served HTML needs rewriting. `PreviewQueries` (the app's own `sage_queries.py` on loopback) stays per project. Supervisors start on a background thread per orchestrator (the #500 rule), never on the request path. The supervisor passes the child `DOMINO_API_HOST` and, on a laptop, `SAGE_DOMINO_TOKEN`, so `sage_queries.py`'s Flight executor and `sage_domino.py` can authenticate without a sidecar (template change in Phase 5).

### 2.5 Resources without mounts

- `assets/provider.py`: drop `resolve_mount_roots`, `_mount_path_for`, `walk_files`; `Asset.mount_path` is removed (or always `None` for one release to keep the UI contract, then removed). Listing = snapshots API (already the unmounted branch). Content = `download_file` via the SDK built from the `TokenSource` (`DatasetClient(token=...)`); if the SDK rejects a PAT on a laptop, a REST fallback `GET /v4/datasetrw/snapshot/{sid}/file/raw?path=` (referenced by the relay allow-list family; verify it exists on the target cluster).
- `attach_file` → always `_download_attachment`: a real copy at `apps/<appId>/public/data/<dataset-slug>/<file>`. `public/data/<dataset-slug>/` stays gitignored; the manifest `.sage/attachments.json` is the source of truth and `scripts/rehydrate_data.py` step 2 (already SDK-only) rebuilds it at publish boot. Step 1 (`link_mounts`) is deleted.
- `attach_folder` → list under the prefix, download each, capped by count and bytes (the comment at `service.py:22245` already names this as the alternative). Refuse over the cap with the count.
- Drag-in files: `upload_scratch` unchanged. **Attaching an upload to an app copies it to `apps/<appId>/public/data/uploads/<name>` and commits it** (`public/data/uploads/` is un-ignored; per-file cap, say 25 MB, refused with a message above it). The published app serves it from the repo; no rehydrate needed. ADR-0023's "an Upload crosses by becoming an Attachment written into a Dataset" is revised: the crossing writes into the app, not a Dataset, until a Dataset write API is adopted (`later`).
- `_default_dataset`, `_resolve_upload_target`, `promote_scratch_to_dataset`, `_cross_chat_upload`, `_delete_upload_bytes`, the `writable` flag and the "Add to <dataset>" menu (`resource-panel.js:260-326`) are removed.
- `fetch_dataset_file_for_chat`: keep only the download branch. Live read `_file_rows` (`liveread/run.py:390`): download the head to `.sage/scratch/` then read, instead of `dataset_root`.
- `shim/chat_paths.py:46-56` `/mnt/code/` prefix stripping, `threads.py:939-957` symlink skip rules, the OpenCode `/mnt/data` read hang guard (`driver/opencode.py:475-481`): delete with their reason.
- Sensitivity (ADR-0043), Data Source cascade, aliases, Model APIs, collaborators: unchanged — already API-only.
- "Datasets from anywhere in the platform": already what `list_datasets` returns; the Browse Domino modal (`resource-catalog.js`) needs no change beyond the `writable` removal.

### 2.6 Publish

`publish_app` (`provision/domino.py:525-552`, `POST /api/apps/beta/apps`, `entryPoint apps/<appId>/app.sh`, `gitRef head`) is unchanged. What changes: `publish_available()` (`preview/prefix.py:29-48`) is replaced by "the project has a `dominoProjectId` and settings have an environment and tier"; the app-mode refusal in `service.py:18831-18839` goes. Pre-publish `_save_to_git` push uses the registry's git credential.

### 2.7 Settings

`backend/sage/config.py` (new): one dataclass loaded from `$SAGE_HOME/settings.json`, env vars overriding (`DOMINO_API_HOST`, `DOMINO_USER_API_KEY`, `GATEWAY_BASE_URL`, `GATEWAY_API_KEY`, `SAGE_MODEL_*`, `DOMINO_ENVIRONMENT_ID`, `DOMINO_HARDWARE_TIER_ID`). `GET/PUT /api/settings` (root scope). UI: a **Connection** section in the existing Account settings drawer (`shell.js:579-747`) with Domino host, token (write-only, shown as set/unset), Gateway URL (prefilled), optional gateway key, publish environment + tier pickers, and a "Test connection" that calls `/api/users/v1/self` and `GET <gateway>/v1/models`. Model-per-slot assignment stays where it is (`.sage/model_overrides.json` per project, `SAGE_MODEL_*` as deployment defaults, now also settable in `settings.json`). First run with no host/token: the Projects home shows the Connection form and nothing else.

## 3. Decisions and assumptions

Numbered so the build sessions can cite them. **Bold** ones want a yes from the product owner before the phase that depends on them.

1. **One user per Sage process.** No door, no per-viewer token juggling. Extended identity is an admin deployment choice, not Sage code. (Phase 1)
2. **Projects are always Domino git-based projects** with a GitHub repo, created through the existing provision code; there is no "local-only" project. A laptop therefore needs a GitHub token in settings. (Phase 3)
3. **`fastapi-antd` is the only stack.** Old `react-vite` apps in re-cloned projects open in Chat; Build on them shows "built with a stack this Sage no longer carries" — no migration. **The sibling branch removing fastapi-antd cites field problems; those need naming before Phase 0.** (Phase 0)
4. **Uploads attach to the app by committed copy, not by writing into a Dataset.** Revises ADR-0023. Dataset write API is `later`. (Phase 5)
5. `$SAGE_HOME` in the App is a mounted Dataset when one is there, else ephemeral. Everything durable is in git anyway (ADR-0006); scratch uploads not yet attached are the only loss on restart. (Phase 7)
6. Routing is `/p/<slug>/…` with the Workbench page at `/p/<slug>/`; no project id in the hash. Reload lands on the same project because the project is in the path. (Phase 2)
7. Per-project gitignored `opencode.json` carries the shim URL. (Phase 2, with the fallback in §2.3)
8. Keep `boot_page.py` (the App proxy also 502s before uvicorn binds), `SAGE_SELF_UPDATE`, the brand system, the Manage / Cost links (hidden when the URLs cannot be built).
9. `LESSONS_LEARNED.md` stays at the repo root as the file people edit; the skill is generated from it at boot (frontmatter + body), pinned by a test the way `test_sage_chat_prompt.py` pins the chat prompt. (Phase 0)
10. Tests: `conftest.py` pins `SAGE_DEFAULT_STACK=react-vite` because every fake template is react-vite shaped; Phase 0 reshapes the fakes to fastapi-antd and drops the pin. ~40 test files name react-vite; they are retired or rewritten with the template, not deselected.

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
4. Git identity: `workspace/git.py` commits as `whoami().fullName <email>`; verify `_save_to_git` push works with the registry credential on both hosts.
   → verify: New project from a laptop creates repo + Domino project and lands in Chat in `/p/<slug>/`; the same project opens in the App container by clone; `git log` on the clone shows Sage's per-turn commits.

### Phase 4 — Preview per project

1. Supervisor per orchestrator on a background thread; `_free_port()`; child env carries host + token; `mount_base=""`.
2. `/p/<slug>/preview/*` through the dispatcher; `PreviewPane` unchanged; `previewStatus` reset to `starting` on project open.
3. Delete `SAGE_PREVIEW_PORT`, the reaper, `SAGE_PROXY_MODE`.
   → verify: behind the App proxy, iframe at `/apps/<uuid>/p/<slug>/preview/` renders with `sage.url()` fetches resolving under the mount; on a laptop the same at `localhost:8080/p/<slug>/preview/`; a preview that cannot start leaves every other route answering (the #500 test, now per project).

### Phase 5 — Resources without mounts

Steps as §2.5, in this order: (1) `assets/provider.py` mount removal + SDK-from-token + REST fallback; (2) `attach_file` copy-only, `attach_folder` bounded download; (3) uploads → committed `public/data/uploads/`, remove the Dataset write paths and the `writable` UI; (4) Chat dataset chip and live-read file head via scratch download; (5) delete the path-stripping and symlink rules; (6) `rehydrate_data.py` step 1 removed, `sage_domino.py`/`sage_queries.py` `token()` honours `SAGE_DOMINO_TOKEN` before the sidecar; (7) ADR-0023 revision + CONTEXT.md Dataset entry ("mounted into the project container" → "reached over the platform API").
→ verify: drag a CSV into Chat, ask about it, hand off to Build, attach it — the app serves it from `public/data/uploads/` and it is in `git status`; attach a file from a Dataset in another project — a copy appears under `public/data/<slug>/`, is gitignored, and `rehydrate_data.py` restores it from an empty dir; sensitivity lock still narrows models for a tagged Dataset.

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
- New tests, one per condition, in the phase that introduces it: dispatcher root_path/path rewriting; ContextVar reaches an SSE generator; registry list merges local and remote; two orchestrators run turns concurrently without sharing a turn lock; preview per project on distinct ports; skill body equals `LESSONS_LEARNED.md`; upload attach commits the file and refuses over the cap; dataset attach is a copy and gitignored; no stack but `fastapi-antd` is seedable; `publish_available` from settings.
- Live checks that cannot be unit-tested are listed in Phase 1 and Phase 6 and must appear in the landing report as measured, not assumed (CLAUDE.md: a report that can be landed on).

## 6. Risks and open questions

| # | Risk | Where it bites | Mitigation |
|---|---|---|---|
| 1 | The SDK rejects a Domino PAT as `token=` off-Domino | Phase 1/5 laptop dataset reads and every Data Source query | REST `file/raw` fallback for Datasets; Data Sources unavailable on laptops until a JWT source exists (acceptable: other resource kinds may be placeholders) |
| 2 | The LLM Gateway does not accept a Domino PAT | Phase 1 laptop model calls | `settings.gateway.apiKey` (`dgw_`) field; already what `.env.example` describes |
| 3 | OpenCode does not deep-merge `provider.options` from a project `opencode.json` | Phase 2 shim routing | Native provider always on + `x-session-id` lookup |
| 4 | `ContextVar` does not reach a streaming generator | Phase 2 | Capture at route entry, pass explicitly |
| 5 | The field problems that motivated `remove-fastapi-antd-stack` | Phase 0 onward | Ask before Phase 0; if they are about the App interpreter search in `app.sh` or CDN scripts, LESSONS §9 already records fixes |
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
