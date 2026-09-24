---
doc: Investigation + fix plan for a live bug found during Phase 4 (preview per project) verification
status: unconfirmed — reported by the product owner from a real laptop, not yet reproduced or root-caused
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

# Step 2: Hypotheses, ranked, each with how to falsify it

None of these are confirmed. Each names the exact code to look at and a concrete way to rule it in or
out — do that before changing anything, and update this section with what you found (strike through a
ruled-out hypothesis rather than deleting it, so the next reader doesn't re-check it).

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
