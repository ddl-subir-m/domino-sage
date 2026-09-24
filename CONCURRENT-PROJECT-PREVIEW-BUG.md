---
doc: Investigation + fix plan for a live bug found during Phase 4 (preview per project) verification
status: RESOLVED (2026-09-24) — reproduced deterministically, root-caused, fixed, and unit-tested.
  See `## Reproduced` and `## Root cause & fix` below. The hypotheses are kept with their verdicts.
branch: one-app-pivot-Etan
date: 2026-09-24
reads first: ONE-APP-PLAN.md §2.3 (dispatcher), §2.4 (preview), ONE-APP-STATUS.md's last "Where things
  stand — start here" section (Phase 4, including its `## CORRECTION` — read that one carefully, it's
  about a different bug in the same area that was already found and fixed today)
---

# The bug, as reported

Product owner's own words, live-testing Phase 4 on a laptop (`make orchestrator`):

> I can open two projects with two previews in two tabs, but if I first open one and show preview,
> then open just the home in the other and create a new project, the preview will be broken in that
> exact scenario.

**Not yet confirmed which side breaks, or what "broken" looks like.** The report doesn't say whether
it's the FIRST project's preview (already showing, in the first tab) that breaks once the second
project is created, or the NEWLY CREATED project's own preview (once its tab lands on it) that never
comes up, or something else entirely (blank page, a 502, a stuck spinner, a wrong app rendering, a
browser console error). **Step 1 of this plan is nailing that down** — everything after it depends on
knowing which one it is.

This machine (the one that did today's Phase 4 work) has no browser and no live Domino sandbox
reachable from it, which is exactly why this is a plan for a session that DOES have both, not a fix
attempt from here. Do not skip straight to a fix — the hypotheses below are informed guesses from
reading the code, not confirmed root causes, and more than one of them could be wrong.

# Step 1: Reproduce and characterize, before touching any code

1. Start fresh: `make orchestrator` (or however you normally launch), confirm no projects are open,
   confirm `env | grep -i domino` shows what you expect for a laptop run (a host + token, no sidecar).
2. Tab 1: open the Projects home (`/`), open an EXISTING project (call it A), navigate to Build or
   Chat so its preview starts, and confirm the preview pane actually renders A's app. Note A's slug
   and, from the URL or the page, whatever you can see about its preview (iframe src, any visible
   port in a diagnostic view).
3. Tab 2: open the Projects home (`/`) — the SAME origin, a different browser tab. **Before creating
   anything**, open the browser's Network tab (or equivalent) and watch it for the next step.
4. In tab 2, create a new project (call it B) through the home page's New Project form. Watch what
   tab 2's network log shows for the `POST /api/projects` call and the redirect that follows
   (`home.html`'s `createProject()` does `window.location.assign('p/' + slug + '/')` on success —
   confirm this actually happens and where it lands).
5. **Now check BOTH tabs, and write down exactly what you see in each:**
   - Tab 1 (project A): is the preview still showing what it was? Reload it — does it still work?
     Check the browser console and network tab for the preview iframe's requests (they should be
     `GET/POST .../p/<A-slug>/preview/...`) — status codes, any 502s, any requests going to the WRONG
     slug's path.
   - Tab 2 (now on project B, freshly created and freshly seeded): does ITS preview come up at all?
     A brand-new project may not have a built app yet — is Build/Chat itself broken, or specifically
     the preview pane, or is there nothing to preview yet and that's expected?
   - Hit `GET /p/<A-slug>/api/diag` and `GET /p/<B-slug>/api/diag` (from a THIRD tab, or curl) for
     both projects at this point. Compare `ports` (`base_port` vs `control_port`), and anything else
     that looks per-project. Save both JSON blobs — they're the most useful artifact for whoever reads
     this next if the bug turns out to be timing-sensitive and hard to reproduce twice.
6. Write the exact observed symptom into this file (a new `## Reproduced` section below) before doing
   anything else: which tab, what the page/console/network actually showed, and the two `/api/diag`
   blobs. If you cannot reproduce it on the first try, try the exact sequence 2-3 more times before
   concluding it's not reproducible — the report describes it as consistent ("will be broken in that
   exact scenario"), which suggests it isn't a rare race, but confirm rather than assume either way.

# Reproduced (2026-09-24, live laptop against dogfood, on branch `one-app-pivot-Etan`)

Ran the exact sequence: tab 1 opened project A (`sage-sage-etan-del-again2`), went to Build, its
preview rendered the Hello-World app. Tab 2 opened the home and created project B
(`sage-delme-preview-bug`). Both `/p/<slug>/preview/` still answered `200` — but the fresh project B
answered in **0.4s**, far too fast for a cold `uvicorn` start, which was the tell.

Inspecting the running preview processes showed the real symptom: **project A had THREE live
`uvicorn app:app` servers**, two of them spawned in the *same second* (ports 61415 + 61416, both
children of the orchestrator pid), where there should be exactly one. Two more servers were orphans
with `ppid=1`, left by *previous* orchestrator processes (a separate leak-on-exit gap, see H4 below).

Then reproduced it cleanly and controllably, no new Domino project needed: fired **6 concurrent**
`GET /p/<slug>/preview/` at a local project whose supervisor was not yet running
(`sage-hithere-etan-sagetst`). Result: **6 separate `uvicorn --reload` servers** spawned (6 distinct
ports, all children of the orchestrator), only one of them tracked. The other five are orphaned —
their `stop()` is never called, they hold ports and memory until the process dies.

So it is genuinely a concurrency bug, consistent ("that exact scenario"): concurrent first-preview
requests to one project each spawn a preview server. The two-tabs-plus-create scenario is just an
easy way to drive several near-simultaneous preview requests at a project whose supervisor is cold.

# Root cause & fix

The preview startup path had **no synchronization**. `_preview_upstream` (`orchestrator/app.py`)
calls `Orchestrator._ensure_seeded()` → `project()` and then `_ensure_preview_running()`, and none of
those held a lock:

- `project()` is get-or-attach with a bare `if self._project is not None: return`. Two threads that
  both see `None` each run the whole attach — each builds a `Project` **and its own
  `UvicornSupervisor`** via `_supervisor_for`, and only the last assignment to `self._project` is
  kept; the earlier supervisors are orphaned. (They also race on the *same* workspace dir — the unit
  test reproduces the resulting `FileExistsError` from two concurrent `copytree` seeds.)
- `_ensure_preview_running()` did `try: supervisor.upstream() except RuntimeError: supervisor.start()`.
  Two threads both see "not ready" and both `start()`. `UvicornSupervisor` keeps a single
  `_proc`/`_upstream`, so the second `_spawn()` orphans the first, and `_proc`/`_upstream` can end up
  pointing at *different* servers — so the proxy forwards to a port whose process no thread owns, and
  when a leaked server later dies its `_read_output` fires the shared restart logic, eventually
  exhausting `_restarts` and leaving `upstream()` raising → the proxy returns 502 → **broken preview**.

**What made it live now**: Phase 4 (2026-09-24) wrapped `get_upstream` in `run_in_threadpool` (to stop
a cold start freezing the shared event loop). Before that, `_preview_upstream` ran inline on the one
event loop, so these sync sections were effectively serialized and could not race into `start()` at
the same instant. Offloading to real threadpool workers is correct — but it turned a benign
serialized path into a genuinely concurrent one, and the missing lock became a live bug. This is why
H2's instinct ("newest, riskiest code in this area") pointed at the right file; the *mechanism* is a
missing lock, not a ContextVar leak.

**Fix** (`sage/orchestrator/service.py`): one per-orchestrator `threading.RLock` (`_preview_lock`),
held on the cold path only (the `self._project is not None` fast path never takes it, so a
started project's requests never wait):
- `project()` double-checks `self._project` under the lock, then builds once (body extracted to
  `_build_project`), so the attach happens exactly once.
- `_ensure_preview_running()` re-reads `upstream()`/`queries.port` under the lock, so the supervisor
  and the query server each start exactly once. The loser blocks at most one cold start, then reads
  the ready upstream — the single-flight *is* the wait.

The lock is per-`Orchestrator` (one per project), so project B's cold start never waits on project A's.

**Test**: `backend/tests/test_concurrent_preview_starts_are_single_flighted.py` — fires 8 concurrent
threads at `_ensure_preview_running` and at `project()`, asserts one `start()` and one supervisor.
Verified to fail without the fix (N starts / `FileExistsError`) and pass with it.

**Not fixed here (named, not silently skipped)**: the `ppid=1` orphan servers from *previous*
orchestrator processes are a distinct leak-on-exit gap — the orchestrator's shutdown stops only
`self._project.supervisor`, and a hard kill leaves the `uvicorn --reload` group behind. Separate from
the reported symptom; left for its own change.

# Step 2: Hypotheses, ranked, each with how to falsify it

None of these are confirmed. Each names the exact code to look at and a concrete way to rule it in or
out — do that before changing anything, and update this section with what you found (strike through a
ruled-out hypothesis rather than deleting it, so the next reader doesn't re-check it).

**Verdicts (2026-09-24):** H1 ruled out — a fresh project B previews fine (200), so this is not a
"nothing built yet" UX gap. H2 **correct file, wrong mechanism** — the `run_in_threadpool` change is
what made the bug live, but via a missing lock on the concurrent start path, not a ContextVar leak
(dispatch resolves correctly per project). H3 ruled out — not the shared module cache. H4 mostly ruled
out as the *cause* (no `_free_port` collision), but its observation is real: the removed reaper no
longer sweeps orphaned servers, and `ppid=1` orphans from prior runs were found. H5 ruled out —
`_ProjectDispatchMiddleware`'s per-request `root_path`/ContextVar is correct and does not leak.

### H1 — `home.html`'s auto-navigate lands tab 2 on a project with no app yet, and "broken" is actually "nothing to preview yet, but the UI doesn't say so"

`sage/workbench/home.html`'s `createProject()` (and `cloneProject()`) call `window.location.assign('p/'
+ slug + '/')` the instant the API call returns — i.e., the SAME tab is now looking at project B before
anyone has run a Build turn on it. Check what `ProjectRegistry.create()` (`sage/projects/registry.py`)
actually leaves on disk for a brand-new project: does it seed a full app (so a preview SHOULD be
possible immediately), or does it leave the project appless until the first Build turn? If the latter,
"the preview is broken" in tab 2 might just be the ordinary "nothing built yet" state rendering as if
it were broken — check what the Build/preview pane actually shows for a genuinely fresh project opened
on its own (no tab-1-with-a-different-project involved) and compare.

**Falsify**: open a brand-new project ALONE (no other tab, no other project open at all) and see if
its preview pane looks the same as what was reported. If yes, this isn't a concurrency bug at all —
it's a pre-existing "fresh project" UX gap, unrelated to having a second tab open, and is a much
smaller, calmer bug to fix. If tab 2's brand-new project looks FINE in isolation but broken only when
tab 1 is also open with a different project's preview running, this hypothesis is ruled out and the
bug is genuinely about the two coexisting.

### H2 — The `run_in_threadpool` wrap added earlier today (2026-09-24) doesn't propagate `ContextVar` correctly under real concurrent load

`sage/preview/proxy.py`'s `http_proxy`/`ws_proxy` now call `await run_in_threadpool(get_upstream)`
instead of `get_upstream()` directly (today's fix for a real event-loop-blocking bug — see
`ONE-APP-STATUS.md`'s Phase 4 update). `get_upstream` is `_preview_upstream` in `app.py`, which reads
`orchestrator` — a module-level proxy that resolves via `current_orchestrator()`, an
`asyncio.ContextVar` set per-request by `_ProjectDispatchMiddleware`. Python's `contextvars` ARE
designed to propagate correctly across `anyio`/`asyncio` thread-offload (this is the documented,
intended use case), and a unit test today confirmed the offload itself works — but that test used a
SINGLE project's `get_upstream`, never two DIFFERENT projects' requests genuinely concurrent on the
threadpool. If this hypothesis is right, the SYMPTOM would be: tab 1's request occasionally reaches
project B's Orchestrator (or vice versa) — e.g., tab 1 briefly shows B's app in A's iframe, or gets a
404/wrong content instead of a clean 502.

**Falsify**: add a temporary log line in `_preview_upstream` printing `orchestrator._project_id` (or
equivalent) and the request path, reproduce the bug, and check the server log for whether a request to
`/p/<A-slug>/preview/...` was ever answered using project B's Orchestrator (or vice versa). If the log
shows every request resolved to the correct project throughout, this hypothesis is ruled out. This is
the single most important thing to check FIRST if H1 is ruled out, because it's the newest, riskiest
piece of code in this exact area and was added the same day this bug was found.

### H3 — The preview relay's shared, cross-project module cache (`domino_module`/`serve_module`) races when a second project is created around the same time

`sage/resources/builtapp.py`'s `domino_module()`/`serve_module()` cache the loaded `sage_domino.py`/
`sage_queries.py` modules keyed by `template_dir` (`_load()`, `_loaded: dict[str, Any] = {}`, one
process-wide dict with a `threading.Lock`). Because every project uses the SAME stack's template
directory (there is only one stack, fastapi-antd), EVERY open project's preview relay/queries share
the literal SAME loaded module objects, process-wide. This is fine for read-only relay behavior (see
the `## CORRECTION` in `ONE-APP-STATUS.md` for why today's static-token override patches this SAME
shared module and considers that safe) — but check whether anything in the CREATE path (seeding a
brand-new project's own copy of `sage_domino.py`/`sage_queries.py` from the template) triggers a
RELOAD of this cache, or whether `PreviewQueries`'s per-project state (`_stamp`, `executor`,
`self._server`) could be confused by two `PreviewQueries` instances (one per project) sharing the
same underlying module object's globals.

**Falsify**: check whether `_loaded`'s cache key (`f"{rel}@{template_dir}"`) is IDENTICAL for project
A and project B (it should be, since `template_dir` is the shared stack template, not the per-project
seeded copy — confirm this by printing the key both projects resolve to). If identical, check whether
`sage_queries.py`'s `CachingExecutor`/`FlightExecutor` or any other module-level state (not
instance-level) could leak between two `PreviewQueries` objects that share the loaded module. If
nothing module-level is mutated per-project, this hypothesis is ruled out.

### H4 — A port collision or a leftover process from removing today's port-reaper

Today's Phase 4 work deleted `SAGE_PREVIEW_PORT` and the `lsof`-based `_clear_stale_port` reaper from
`sage/preview/supervisor.py`, reasoning that `_free_port()`'s OS-assigned ephemeral ports make a
collision "negligible." Check whether that reasoning actually holds under this specific sequence —
e.g., does creating project B cause its own preview supervisor to start EAGERLY (not lazily on first
preview request), close to in time to when A's supervisor is doing something (a `--reload` restart,
say)? A `_free_port()` race window exists between the test-bind-and-close and the real `uvicorn --port
N` bind; it's meant to be exceedingly rare, but this is literally the newest deletion in this exact
code path and deserves a direct look before being ruled out on reasoning alone.

**Falsify**: check server logs for a `uvicorn` bind failure (`OSError`/`Address already in use`)
around the time of reproduction, and check whether B's supervisor actually starts eagerly at
create-time at all (it shouldn't, per `ProjectRegistry.create()` — confirm it truly doesn't touch
`Orchestrator`/supervisor machinery, only writes files and a registry entry). If B's supervisor never
starts until someone actually opens Build/Chat on it and asks for a preview, this hypothesis likely
does not apply to the CREATE step itself, though it could still apply once tab 2 actually opens B's
preview for the first time.

### H5 — Something about `_ProjectDispatchMiddleware`'s `root_path` extension breaks the SECOND concurrently-dispatched project's routing

`sage/orchestrator/app.py`'s `_ProjectDispatchMiddleware` extends `scope["root_path"]` per-request and
sets a `ContextVar` for the request's lifetime, reset in a `finally`. This is designed to be per-request
and shouldn't leak across concurrent requests to different projects — but it has apparently never been
exercised by two DIFFERENT real browser tabs driving two DIFFERENT real projects' Orchestrators through
two DIFFERENT real preview supervisors AT THE SAME TIME, only by the unit tests in
`test_project_dispatch.py` (synthetic, sequential, single-threaded `TestClient` calls) and by two
manually-opened tabs in an earlier session's smoke test (unclear from `ONE-APP-STATUS.md` whether that
smoke test ever had one tab's preview ACTIVELY STREAMING/POLLING while the other tab did something
else at the same instant).

**Falsify**: same log-and-check approach as H2 — confirm every request's resolved `root_path` and
bound Orchestrator match the URL it actually came in on, across the whole reproduction sequence.

# Step 3: once root-caused

- Write a NEW test that actually reproduces the bug's mechanism (not just "two orchestrators exist" —
  the existing `test_project_dispatch.py` and `test_preview_proxy_does_not_block_the_event_loop.py`
  already cover pieces of this area and evidently did not catch whatever this is). If the root cause
  is a genuine concurrency issue, the test should drive it the way
  `test_preview_proxy_does_not_block_the_event_loop.py` does (`asyncio.gather` on ONE shared event
  loop, not two threads/two `TestClient`s — see that file's own docstring for why the threaded version
  was a false negative) rather than trust a slower, real-server integration test alone.
- Fix, then re-run the full suite (`cd backend && uv run --extra dev pytest -q -n auto`, redirected to
  an explicit file — a background-task capture silently truncated a large run earlier today, see
  `ONE-APP-STATUS.md`) and reconcile against the ~97-failure pre-existing baseline the same way every
  other change today did: diff failing test NAMES against a `git stash`-restored baseline on exactly
  the files that show red, not just the count.
- Update `ONE-APP-STATUS.md` with what was found (root cause, fix, verification) in its own dated
  `## UPDATE` section, following the style already established there — and update THIS file's
  `## Reproduced` / hypothesis sections with the actual answer rather than leaving them as open
  guesses, so a future reader doesn't have to redo Step 1.
- Delete this file, or mark it resolved at the top, once the fix is verified and merged into
  `ONE-APP-STATUS.md`'s own record — this file is scaffolding for the investigation, not a permanent
  doc.
