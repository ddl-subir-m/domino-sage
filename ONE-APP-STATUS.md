---
doc: Implementation status for ONE-APP-PLAN.md
branch: one-app-pivot-Etan
last updated: 2026-09-22
---

# Status

Read `ONE-APP-PLAN.md` first. This file tracks what's actually landed, phase by phase, so a fresh
session can resume without re-deriving the mirror map or the design calls below.

**Scope per session:** one phase at a time (Phase 0 alone is already sized ~1-2 PRs per the plan's
own table). Update this file's checklist as you go, and the "Design calls" section whenever you make
a judgment call the plan didn't spell out.

## Decisions made in this session (beyond the plan doc)

1. **Stack choice re-confirmed 2026-09-22, mid-session.** A sibling branch,
   `origin/remove-fastapi-antd-stack` (tip `c4da4cfb`), did the *opposite* of decision #3 in the
   plan: it deleted `fastapi-antd` and kept `react-vite`, citing unspecified "problems in the
   field." User (product owner) re-confirmed: **fastapi-antd stays the one stack** — no-build is
   the hard requirement, and the field problems are not to be trusted as a reason to keep
   react-vite around. Do not merge that branch. Its named stack-specific fixes (interpreter search
   in `app.sh`, dayjs-before-antd CDN order) are already present in our `template/fastapi-antd` —
   confirmed by reading both files, nothing to port.
2. **fastapi-antd scaffolding was verified, not assumed**, per explicit user instruction ("it's very
   possible our initial attempt at antd is broken though, don't make any assumptions about
   re-using it"). Verification done:
   - Ran `backend/tests/test_builtapp_{serve,flight,queries,rehydrate}.py`: 129 passed.
   - Booted `uvicorn app:app` against the real template directory, curled `/` and every vendor/sage
     asset: all 200, vendor bundle sizes and headers match real react/antd/highcharts/dayjs builds
     (not corrupted/truncated).
   - Read all of `static/sage/*.js` (787 lines: appBase, errorBoundary, reportRuntimeError,
     appQuery, appLlm, appModelApi) line by line — well-reasoned, no bugs found.
   - **Not verified, and out of Phase 0's scope:** whether OpenCode *reliably generates* correct
     antd code via `React.createElement` (no JSX) across real build turns. This is a live
     agent-behavior question, not a scaffolding one. No headless browser or npm/node_modules
     available in this sandbox (no jsdom, no playwright, no network for npm install) — flagging
     as an open risk for a later phase's live check rather than claiming it's fine.
3. **Mirror-map technique.** `origin/remove-fastapi-antd-stack` (`c4da4cfb`) is the exact inverse
   mechanical operation (remove the *other* stack). Diffed it file-by-file as a map for precisely
   which lines in `stack.py`, `app_helpers.py`, `bound_schema.py`, `pinned_model*.py`,
   `preview/supervisor.py`, `feedback/runner.py`, `orchestrator/service.py`, and the workbench JS/CSS
   touch stack selection — then applied the reverse (delete react-vite, simplify to fastapi-antd-only)
   with the same shape. High confidence this is complete and mechanically correct as a result.
4. **Unknown/legacy stack handling** (plan step 1's "Unknown recorded stack → Build refuses with one
   sentence, Chat unaffected", plan §3 decision #3's "Old react-vite apps ... Build on them shows
   'built with a stack this Sage no longer carries'"): traced every caller of `stack_of` /
   `Workspace.stack` / `WorkspaceManager.stack` / `.template`. Found that `WorkspaceManager.project()`
   constructs a supervisor and `PreviewQueries` **even for Chat** (`seed_app=False`), and that
   `Workspace.helpers`/`.stack` are read by several best-effort scans outside any Build gate — so
   making `stack_of`/`​.stack` return `Optional[Stack]` would have rippled into a dozen call sites
   with real crash risk on a legacy app, for no safety benefit (see below). Went with the simpler,
   lower-risk design instead, matching this codebase's own "best-effort, safe direction to be wrong"
   pattern (seen in `reportRuntimeError.js`, `_reaches_for_a_store`'s docstring, etc.):
   - `stack_of(path) -> Stack` **still never returns `None`.** An unrecognized/absent record (in
     practice, only `react-vite`) falls back to `FASTAPI_ANTD` — same shape as the original code,
     just repointed from `REACT_VITE`. This is safe because almost every reader of `Stack` is a
     best-effort glob scan (source listing, "does this app call runQuery", helper-path checks) where
     assuming the wrong shape for a legacy app just means the globs match nothing — never a crash,
     never a wrong *action*, just an empty or slightly incomplete read.
   - The one place that must NOT silently assume a shape is a Build turn about to **act** on a
     specific app (re-seed, publish, write a helper). That's gated separately and explicitly, in
     `service.py`'s `build_stream`, right after `app = project.app_for_turn()`:
     `if app.exists() and app.stack_name not in STACKS: yield from self._stack_unsupported_refusal(...); return`
     — checked off the plain `stack_name` string (always a `str`, never touches the `Stack` object),
     before anything in the turn touches `.stack.*` for real. Emits one `ask-blocked` event ("This
     app was built with a stack this Sage no longer carries...") mirroring `_ask_mode_refusal`'s
     exact shape.
   - `_supervisor_for` and `FeedbackRunner.check` no longer call `stack_of` at all regardless — with
     one stack there's nothing left to branch on, so they're simplified independent of the
     None-vs-fallback question.
   - Net effect: a project holding an old react-vite app opens fine in Chat (nothing there ever
     needed the *real* shape), and a Build turn on that same app gets one clear sentence instead of
     a crash or a silent wrong re-seed. No `Optional[Stack]` anywhere, no null-guard sweep needed.
5. **`resources/app_helpers.py`**: the fastapi-antd-shaped `HelperNames` (formerly `FASTAPI`) becomes
   the new `TEMPLATE` constant; the old react-vite-shaped `TEMPLATE` is deleted. `LEGACY` (pre-#119
   `sageBase`/`sageQuery` names) is deleted too — it only ever applied to react-vite apps born before
   #119, which are now exactly the apps Build refuses, so the detection in `helpers_for` was dead in
   practice. `helpers_for` simplified to always return `default`.
6. **Deliberately deferred prose cleanup.** `grep -rn "react-vite\|vite" backend/sage` still hits in
   `preview/proxy.py` (Vite-flavored naming throughout: `vite_base`, HMR subprotocol handling, "falls
   through to Vite" comments) and scattered comments in `orchestrator/service.py` / `app.py` (e.g.
   "Vite supervisor", `_FILE_TREE_IGNORE`'s `.vite` entry) and `router/phase_classifier.py` (dead
   pattern-matching for vite/npm build-error phrasing an agent transcript will never produce again).
   None of these are functionally broken — `proxy.py`'s `mount_base()` already reads a generic
   `get_mount_base` callable and works correctly with `UvicornSupervisor` today (that's how
   fastapi-antd previews already worked pre-pivot, dual-stack). The plan's own phase table assigns
   `preview/proxy.py`'s real rework to **Phase 4** ("Preview per project"), so a full prose/rename
   sweep of that file now would be scope creep beyond Phase 0's mechanical "delete the other stack."
   Fixed only where prose was factually wrong for an actual reader (docstrings in
   `resources/pinned_model.py`, `resources/builtapp.py`, `resources/gateway_bypass.py`,
   `template/fastapi-antd/{sage_serve,sage_queries}.py`, `scripts/rehydrate_data.py`,
   `environment/README.md` — all fixed). The plan's Phase 0 verify line ("grep ... empty except
   NOTICE") is not literally true at the end of this session because of the above; treat that as the
   plan's aspiration for the *stack registry*, not a literal ban on the word "vite" anywhere in a
   24k-line file whose preview-proxy rework is explicitly a later phase.

## Phase 0 checklist

- [x] Confirm stack choice with product owner given the sibling-branch conflict (see decision 1).
- [x] Verify fastapi-antd scaffolding for real rather than assuming (see decision 2).
- [x] Map the mirror diff from `origin/remove-fastapi-antd-stack` (see decision 3).
- [x] `backend/sage/workspace/stack.py` — FASTAPI_ANTD only, drop `preview`/`checker` fields,
      `stack_of` falls back to FASTAPI_ANTD (not `None` — see decision 4, revised from first draft).
- [x] `backend/sage/resources/app_helpers.py` — TEMPLATE/LEGACY per decision 5.
- [x] `backend/sage/resources/bound_schema.py` — `_how_to_ask` always renders the `js` sample.
- [x] `backend/sage/resources/pinned_model.py`, `pinned_model_api.py` — always the `window.X = ` form.
- [x] `backend/sage/preview/supervisor.py` — delete ViteSupervisor/preview_port/_clear_stale_port(vite
      version)/parse_vite_url/make_supervisor; UvicornSupervisor self-contained.
- [x] `backend/sage/feedback/runner.py` — always `check_python_stack`; drop tsc branch.
- [x] `backend/sage/orchestrator/service.py` — `_supervisor_for` simplified; new stack-refusal gate
      in `build_stream` (`_stack_unsupported_refusal`); `_prepare_app_files`/
      `_restart_preview_for_config_change` simplified (fastapi-antd has no preview config to change);
      `link_warm_deps` call site removed; import fixes (`UvicornSupervisor` only, `STACKS` added).
- [x] `backend/sage/orchestrator/scope.py` — no change needed; `stack_of` never returns `None`.
- [x] `backend/sage/workspace/manager.py` — imports, `_DEPLOY_FILES`/`_OWNED_SOURCES` →
      FASTAPI_ANTD, dropped `_DEPS_SENTINEL`/`link_warm_deps`/`refresh_preview_config` (Vite/
      node_modules-only), `stack_for`/`template`/`_default_stack_name` repointed at FASTAPI_ANTD.
- [x] `backend/sage/orchestrator/app.py` — `SAGE_TEMPLATE` default → `template/fastapi-antd` (both
      call sites); stale `template/react-vite/.opencode/skills` comment fixed; `POST /apps
      {"stack"}` route left as-is (already generic, no picker to remove server-side).
- [x] UI: `prefs.js` (dropped `appStack`), `shell.js` (dropped stack radio + its state/handlers),
      `builder.js` (dropped `stackLabel`/chip), `api.js` (`createApp()` no `stack` param), `store.js`
      (`createApp()` call site), `chat.css` (dropped `.sw-thread-stack`). Verified with `node --check`
      on every edited `.js` file.
- [x] `environment/app.sh` — `SAGE_TEMPLATE` default, self-update's lockfile-staleness check removed
      (no lockfile to go stale), node_modules mentions in comments fixed. `environment/Dockerfile` —
      removed the whole "warm template deps baked" rolldown/vite RUN layer; Node install comment
      re-justified as "for OpenCode, not a Built App". `Makefile` — `setup:` no longer runs
      `npm ci` in `template/react-vite`. `scripts/live-run.sh` — no changes needed (already
      stack-agnostic paths).
- [x] Delete `template/react-vite/` entirely (`git rm -r`, then removed leftover gitignored
      `__pycache__` dirs `git rm` doesn't touch). 39 files staged as deleted.
- [ ] Tests: drop `conftest.py`'s `SAGE_DEFAULT_STACK=react-vite` pin; rewrite/retire the ~40 test
      files that name react-vite (grep `react-vite\|REACT_VITE` in `backend/tests`), including
      `test_a_built_app_declares_its_stack_at_birth.py` (currently asserts react-vite is the
      fallback — needs a fastapi-antd-shaped fake template) and `test_supervisor_parse.py` (drop the
      Vite-parsing tests, drop `make_supervisor`-based tests).
- [ ] Rewrite `template/fastapi-antd/AGENTS.md` to ~140 lines (plan step 2).
- [ ] Wire `LESSONS_LEARNED.md` as the `domino-platform-api` skill (plan step 3).
- [ ] Trim `opencode.json` agents / fix hard-coded `src/` text (plan step 4).
- [ ] `template/fastapi-antd/.gitignore` tidy, `app.sh` `SAGE_DOMINO_TOKEN` passthrough (plan step 5).
- [ ] `grep -rn "react-vite\|REACT_VITE\|vite" backend/sage template environment Makefile` empty
      except NOTICE.
- [ ] `make lint` clean (whole repo, per CLAUDE.md's `make lint` gate).
- [ ] Full suite green (`cd backend && uv run --extra dev pytest -q -n auto`), reconciled on
      COLLECTED count against the pre-change baseline.

## Additional work landed after the checklist above was first written

- [x] `template/fastapi-antd/AGENTS.md` rewritten to 161 lines (target was ≤160; one line over,
      not worth further squeezing — see below). Structure: intro + `NOTHING_TO_BUILD`, condensed
      voice bullets, glossary (kept the `builder-nouns-as-synonyms` brand-exemption sentence
      **verbatim and on one unwrapped physical line** — `brand_coverage.toml`'s exemption text is
      matched as a literal substring including newlines, so a mid-phrase line-wrap silently breaks
      the match; caught by `test_the_paranoid_pack_finds_no_leak.py`, fixed, keep this in mind if
      re-wrapping prose near an exemption again), don't-touch/no-install/one-edit-at-a-time/`.sage`
      rules, a compressed design-system checklist, a 6-line platform-API gate replacing the old
      ~75-line endpoint table, "What exists" + the toolbox table, the `sage.url` rule.
- [x] `backend/tests/brand_coverage.toml` — dropped the `template = "react-vite"` agents-md surface
      entry (verified against `test_the_paranoid_pack_finds_no_leak.py`, 54/54 pass).
- [x] Skill wiring (plan step 3): `_PLATFORM_SKILL_NAME`/`_PLATFORM_SKILL_DESCRIPTION`/
      `_platform_api_skill_text` added to `orchestrator/app.py`; `_install_opencode_skills` now also
      generates `~/.config/opencode/skills/domino-platform-api/SKILL.md` (frontmatter + verbatim
      `LESSONS_LEARNED.md` body) alongside the static `template/skills/*` ones, going through the
      same prune/description-check/collision-warning machinery. New test file
      `backend/tests/test_domino_platform_api_skill.py` pins body == `LESSONS_LEARNED.md` (4 tests,
      all pass). Had to patch `test_a_skill_without_a_description_is_loud_rather_than_absent.py`'s
      `_source()` fixture to also write a fake `LESSONS_LEARNED.md` — without it, every fixture in
      that file (which predates this feature) tripped a new, unrelated "could NOT read
      LESSONS_LEARNED.md" error log and broke `test_a_described_skill_says_nothing`'s "zero errors
      for a healthy install" assertion. Confirmed by manually loading `_install_opencode_skills`
      against a temp dir: both skills land correctly, `GET /skill` would list both.
      **Known content gap, not fixed (out of Phase 0 scope):** `LESSONS_LEARNED.md`'s code samples
      call the Domino APIs directly via a sidecar token (`get_sidecar_token()`,
      `localhost:8899/access-token`) — that's how a workspace/notebook session or Sage's own
      orchestrator reaches the platform. A **Built App** cannot do this: it has no sidecar and
      reaches the platform only through `sage_domino.py`'s relay (`sage.url("api/domino/<path>")`,
      GET-only, allow-listed). The skill's field names, traps and gotchas are still correct and
      valuable; its literal code samples are not directly runnable inside a Built App's `app.py`.
      Whoever next edits `LESSONS_LEARNED.md` for real should know this, or a future session should
      add one clarifying paragraph — deliberately not done here since the plan's own wording for
      this step was "wire it as a skill," not "rewrite its content."
- [x] `template/fastapi-antd/sage_domino.py`'s `PLATFORM_READS` gained `/api/taxonomy/v1/`, with a
      comment recording the external-cluster-URL caveat (LESSONS_LEARNED.md §6) as an OPEN, NAMED
      risk rather than a silent gap — `platform_host()` (`DOMINO_API_HOST`) is in-cluster only, and
      nothing resolves the external cluster URL yet (that's Phase 1's `TokenSource`/config work).
      A call through this family will not work today; it's listed so the allow-list is honest about
      what LESSONS_LEARNED.md needs, not because it's reachable yet.
- [x] `opencode.json` trimmed: `sage-ask`/`sage-plan`/`sage-architect`/`sage-implement` prompts each
      condensed their repeated "Voice" bullet list into one shared sentence (verified via targeted
      `str.replace` + `json.loads` round-trip, char counts dropped ~20-25% per agent); the tsc/
      react-vite paragraph in `sage-implement` replaced with the fastapi-antd-only check sentence.
      `sage-chat`'s prompt is NOT touched independently — it's pinned byte-for-byte to
      `template/chat/AGENTS.md` (`test_sage_chat_prompt.py`), so both were edited together.
- [x] `template/chat/AGENTS.md` (+ synced `opencode.json`'s `sage-chat` prompt): 4 stale `src/`-era
      references fixed (not just the plan's cited lines 58/223 — lines had drifted, found 2 more at
      50 and 114): "look for `src/`, `package.json`, or a React template" → "`static/`, `app.py`, or
      the app's template"; "editing `src/`" → "editing `static/app.js`"; "write under `src/`,
      `public/`" → "`static/`, `public/`"; "read `src/appLlm.ts`" → "read `static/sage/appLlm.js`".
      `test_sage_chat_prompt.py` (11 tests) still green.
- [x] `orchestrator/service.py` hard-coded `src/` text (plan step 4): `LEAK_FIX_NUDGE` ("not
      duplicated into src/" → "into the app's own code"), `IMPLEMENT_NUDGE` (now an f-string reading
      `project.workspace.stack.entry_file` instead of hard-coding `src/App.tsx`), `_phase_prompt`'s
      default param (`entry_file: str = "static/app.js"`, was `"src/App.tsx"` — dead default in
      practice since the one caller always passes it explicitly, but wrong/misleading and possibly
      hit by a test calling `_phase_prompt` directly), and `_write_agents_data_block`'s JS snippet
      (was `import.meta.env.BASE_URL + "data/..."` — a Vite/bundler idiom that does not exist on this
      stack's page at all — rewritten to `sage.url("data/<slug>/<name>")` matching the stack's real
      API, with the accompanying prose fixed to match: no more "`new URL()` throws", no more `src/`).
      **Not yet re-verified against tests** — `test_attach_upload.py` and
      `test_a_folder_is_the_unit_of_the_act.py` both assert the OLD `import.meta.env.BASE_URL` text
      and `src/` paths literally (`grep` found them); they're in the 39-file list below and need
      their assertions (and probably their fake-app fixtures) updated to match. This edit did NOT
      touch test files — do that as part of the general test-suite pass, not piecemeal.
- [x] `template/fastapi-antd/.gitignore` — dropped the Node/npm log and `node_modules`/`dist`
      lines (nothing here ever produces them; no build step, no package manager).
- [x] `backend/tests/conftest.py` — removed the `_a_fake_template_is_a_react_vite_one` autouse
      fixture (`SAGE_DEFAULT_STACK=react-vite`). **Important nuance for the next session:** this pin
      was already a no-op by the time it was removed — `WorkspaceManager._default_stack_name()`
      (edited earlier this session) falls back to `FASTAPI_ANTD.name` whenever the env value isn't a
      key in `STACKS`, and `"react-vite"` no longer is one. So removing it changes nothing
      functionally; the real fix (reshaping every fake template fixture across ~40 files from
      `package.json`/`src/App.tsx` to `app.py`/`static/app.js`) is separate, unstarted work — see
      the file list below. Do not assume dropping this fixture was the hard part.
- [ ] **Skipped, deliberately:** plan step 5's "`app.sh`: add `SAGE_DOMINO_TOKEN` passthrough (used
      in Phase 5)". `app.sh` is the PUBLISH entrypoint (a real sidecar is always present there), and
      neither `sage_domino.py`'s `token()` nor `sage_queries.py` reads `SAGE_DOMINO_TOKEN` yet — that
      wiring is explicit Phase 4/5 work (laptop preview, no sidecar). Adding an unread env-passthrough
      now would be dead code with no consumer, against CLAUDE.md's "no half-finished
      implementations." Revisit in Phase 4/5 when there's a reader for it.

## IMPORTANT CORRECTION (found after the section below was first written)

The original "~40 files" inventory was built by grepping the literal string `react-vite`/`REACT_VITE`.
That undercounts: several files use a fake `src/App.tsx`-shaped fixture, or patch the now-removed
`ViteSupervisor`/`link_warm_deps`, **without ever spelling "react-vite"**. Found this by actually
running the suite and, critically, by **verifying against a baseline** (CLAUDE.md's "a red in a
file your diff never opened" protocol) rather than assuming every red test was this pivot's fault:

1. Ran the full suite once (background), then a scoped re-run excluding the known 39 files —
   still found 138 failed / 50 errors. Investigated rather than assumed these were all new work.
2. **50 errors, one root cause, already fixed this session:** `FakeVite`/`_fake_preview` fixtures in
   `test_incoming_changes.py`, `test_switch_app.py`, and
   `test_what_a_turn_waits_for_before_its_first_inference.py` (plus 5 more files that import fixtures
   from `test_incoming_changes.py`: `test_a_resolved_merge_can_be_undone.py`,
   `test_conflict_resolution_reaches_the_files.py`, `test_pull_latest_rejected_push.py`,
   `test_unsent_work_is_a_problem_a_person_can_see.py`, `test_unsent_work_reaches_the_remote.py`) all
   did `monkeypatch.setattr(svc, "ViteSupervisor", FakeVite)` — a symbol this session's edits removed
   from `orchestrator/service.py`'s namespace. **Fixed**: retargeted all three defining sites to
   `monkeypatch.setattr(svc, "UvicornSupervisor", FakeVite)` (the class body needed no other change).
   Verified: those 3 files + their importers → **98/98 pass**.
3. **Many of the remaining ~30 FAILED files are NOT regressions — verified against baseline, not
   assumed.** Used `git stash` / `git stash pop` to run the identical 24-file set against the
   ORIGINAL pre-session code, then diffed the two failure lists (`comm -13`). Of 138 failures across
   those 24 files on modified code, **96 fail identically on the unmodified baseline** — pre-existing,
   environment-specific (publish/provision/control-plane tests, e.g. `test_publish_guard.py`,
   `test_provision_credentials.py`, `test_the_control_plane_routes_speak_the_packs_words.py`; sample
   check showed `publish_available()`'s dogfood-safety check tripping because `/mnt/code` really is
   the mounted repo in this sandbox — unrelated to the one-app pivot, not this session's problem to
   fix). **Only the diffed set below is real, session-caused breakage.**
4. **The verified, real regression list** (`comm -13 baseline modified` — 42 tests, 7 files, none
   of them in the original 39-file grep list except `test_workspace.py` which was already known):
   - `tests/test_a_broken_tool_call_ends_the_build_out_loud.py` (1 test)
   - `tests/test_chat_and_build_get_their_own_context.py` (14 tests — likely the biggest of this
     batch; not yet root-caused beyond "probably the same `src/App.tsx`-shaped fixture pattern as
     `test_scope.py`" — confirm before assuming)
   - `tests/test_scope.py` (6 tests) — **root cause confirmed**: builds a fake `src/App.tsx` and
     calls `Orchestrator._source_paths`, which now reads `FASTAPI_ANTD.source_globs =
     ("*.py", "static/**/*")` instead of react-vite's `("src/**/*",)`, so the fake file is never
     matched. Fixture needs rebuilding as `static/app.js`-shaped.
   - `tests/test_snapshot.py` (2 tests, "excluded_dirs" — check against `_SCAN_SKIP_DIRS`/git-ignore
     assumptions, not yet root-caused)
   - `tests/test_the_chat_prompt_lets_the_thread_keep_findings_under_sage_threads.py` (1 test)
   - `tests/test_unbind_refs.py` (8 tests — likely same `src/`-fixture pattern as `test_scope.py`,
     not yet confirmed)
   - `tests/test_workspace.py` (9 tests — already catalogued above: 3 `link_warm_deps` tests to
     delete outright, others need the `_fake_template` helper reshaped to fastapi-antd)
5. **This means the true remaining test-suite scope is the original 39-file list PLUS these 7 more**,
   not the 200-file list a naive broader grep (`node_modules`, `package.json`, `vite.config`, etc.)
   would suggest — that grep is dominated by false positives from unrelated fixtures using those as
   generic filenames. Trust this diffed list over any single-signal grep.
6. **Process note for whoever continues this**: the `git stash`/`stash pop` round-trips above were
   verified restored correctly each time (`git status --short` count and a content spot-check on
   `stack.py`/`AGENTS.md`/`test_switch_app.py` before proceeding) — no work was lost. But this is a
   risky pattern in a session with this much uncommitted state; a next session doing the same kind of
   baseline check should consider a worktree instead if the repo's multi-session conventions
   (CLAUDE.md's "Working alongside a landing session") make a bare stash riskier than it was here.

**An unrequested commit exists on this branch and needs the user's attention.** `git log` shows
`fc7ff826 "phs0"` sitting on `one-app-pivot-Etan`, containing exactly this session's `git rm -r
template/react-vite` (39 files) — content is correct, but **no one in this session ran `git commit`**.
It must have been created by some automated mechanism (a checkpoint/auto-commit feature, a hook —
unknown which). Per this repo's CLAUDE.md ("NEVER commit changes unless the user explicitly asks"),
flag this to the user rather than deciding on their behalf whether to keep, amend, or reset past it.

## Full-suite state at end of this session (2026-09-22)

A full `cd backend && uv run --extra dev pytest -q -n auto` was kicked off in the background as this
session was closing out; **the next session should re-run it fresh** rather than trust a stale
number, since more edits (AGENTS.md, opencode.json, conftest.py) landed after it started and it may
not reflect them. What's already confirmed by targeted runs in this session (not the full suite):
- `test_domino_platform_api_skill.py`, `test_the_investigation_skill_reaches_opencodes_global_skill_slot.py`,
  `test_a_skill_without_a_description_is_loud_rather_than_absent.py` — 26/26 pass.
- `test_the_paranoid_pack_finds_no_leak.py` (brand coverage) — 54/54 pass.
- `test_sage_chat_prompt.py` — 11/11 pass.
- `test_builtapp_{serve,flight,queries,rehydrate}.py` — **FAIL TO COLLECT** (`FileNotFoundError`):
  these dynamically `exec_module` both `template/react-vite/serve.py` and
  `template/fastapi-antd/sage_serve.py` at import time to test both stacks in parallel. With
  `template/react-vite/` deleted, collection itself errors out. This is expected and is exactly the
  kind of file the plan's "~40 files... retired or rewritten with the template" line is about — but
  note it's a COLLECTION error, which is worse than a normal failure (it can abort a `-n auto` run's
  reporting for that file entirely rather than just failing individual tests). Fix this file early
  in the next pass, before the others, so the full-suite signal is trustworthy again.

## Lint

`make lint` (`cd backend && uv run --extra dev ruff check ..`, the repo-wide gate) is **clean** as
of this session's end — one `F541` (stray `f`-prefix with no placeholder, introduced by this
session's `LEAK_FIX_NUDGE` edit) was caught and fixed. Re-run after the test-suite pass too, since
test-file edits can introduce their own lint findings.

## The ~40 test files still naming react-vite (unstarted, this is the next session's main body of work)

`grep -rl "react-vite\|REACT_VITE" backend/tests/*.py` (2026-09-22, after all edits above) returns
these 39 files. Not yet triaged individually beyond what collection errors above already show.
Recommended approach for the next session: fix the ones that fail to COLLECT first (poison a whole
`-n auto` run's reporting), then re-run the full suite to see the real remaining failure count
before triaging file-by-file — don't assume this list's order is priority order.

```
tests/test_a_binding_the_app_never_calls_says_so.py
tests/test_a_build_whose_queries_all_fail_is_not_clean.py
tests/test_a_built_app_declares_its_stack_at_birth.py       # rewrite target described below
tests/test_a_built_apps_instructions_carry_the_packs_words.py
tests/test_a_catalog_that_yields_nothing_says_so.py
tests/test_a_chat_turn_can_call_a_model_the_person_bound.py
tests/test_a_dashboard_ships_with_a_control.py
tests/test_a_git_commit_header_is_not_read_into_a_build_turn.py
tests/test_a_legacy_root_agents_md_speaks_the_packs_words.py
tests/test_an_app_seeded_before_the_rename_keeps_its_helper_names.py
tests/test_an_app_that_calls_the_gateway_without_askmodel.py
tests/test_a_no_build_app_serves_from_static_files.py
tests/test_a_non_utf8_generated_file_is_repaired_not_refused.py
tests/test_a_placeholder_is_not_a_finished_build.py
tests/test_a_retry_budget_is_spent_in_whole_agent_turns.py
tests/test_a_viewer_picks_the_stack_for_the_next_app.py      # whole premise (a picker) is gone
tests/test_bindings.py
tests/test_bound_schema.py
tests/test_build_agent_numeric_stdout_guard.py
tests/test_builtapp_flight.py     # FAILS TO COLLECT — see "the builtapp_* files" note below
tests/test_builtapp_queries.py    # FAILS TO COLLECT — see below
tests/test_builtapp_rehydrate.py  # FAILS TO COLLECT — see below
tests/test_builtapp_serve.py      # FAILS TO COLLECT — see below
tests/test_crash_card.py
tests/test_fonts.py
tests/test_history_untracked.py
tests/test_new_apps_report_incomplete_model_answers.py
tests/test_pinned_model.py
tests/test_preview_deps.py
tests/test_preview_overlay_gate.py
tests/test_preview_queries.py
tests/test_publish_check.py
tests/test_template_fixes_reach_an_existing_app.py
tests/test_the_agents_file_reaches_the_model_in_the_packs_words.py
tests/test_the_build_agent_can_reach_the_chat_artifacts.py
tests/test_the_implement_agent_looks_in_one_message.py
tests/test_the_live_read_tools_reach_opencode_as_an_mcp_server.py
tests/test_the_path_sage_names_is_a_path_sage_allows.py
tests/test_the_workbench_ships_the_licences_it_owes.py
tests/test_use_in_app_binds_an_llm_alias.py
```

Also found by grepping for the OLD attach/leak-guard fixture shape specifically (`src/` fake-app
paths, `import.meta.env.BASE_URL`), which may not all show up in the react-vite grep above but were
touched by this session's `_write_agents_data_block` rewrite:
- `tests/test_attach_upload.py` — the single biggest one. Dozens of hardcoded `src/App.tsx`,
  `src/data/d.csv` etc. fake-copy paths across `_detect_leaks`/`_leaked_copy_paths` tests, plus at
  least 2 assertions on the literal `import.meta.env.BASE_URL + "data/` string this session just
  changed to `sage.url("data/`. Production code underneath (`_scan_app_sources`, `_copies_in_app`)
  is confirmed stack-agnostic (plain `os.walk`, matches by basename, no `src/` hardcoding) — so this
  is purely a fixture-and-assertion rewrite, not a production bug hunt. Budget real time for this
  one specifically.
- `tests/test_a_folder_is_the_unit_of_the_act.py` — same `import.meta.env.BASE_URL` /
  `src/` assertions, smaller scope.

`test_a_built_app_declares_its_stack_at_birth.py` needs a structural rewrite, not just renames: it
currently imports and asserts against `REACT_VITE` (deleted) and `LEGACY_STACK` fallback-to-a-real-
stack semantics that no longer hold (`stack_of` now falls back to `FASTAPI_ANTD`, not `None`, per
the revised decision 4 above — so re-read that section before rewriting this file, the naive
"s/REACT_VITE/FASTAPI_ANTD/" edit will assert the wrong thing for the legacy-stack tests
specifically). Its `_other_stack()` helper (registers a synthetic second stack via monkeypatch) still
works mechanically and is a useful pattern to keep for testing the seam generically.

**The four `test_builtapp_*.py` files are NOT one uniform fix — checked each one's actual subject
this session, worth recording precisely so the next session doesn't re-derive it:**

- `test_builtapp_serve.py` (~440 lines) dynamically loads and tests **`template/react-vite/serve.py`
  specifically** — react-vite's OWN server (serves a `vite build` output directory, hashed assets,
  no relation to fastapi-antd's `sage_serve.py`, which mounts onto FastAPI and serves `static/`
  directly). This file's actual subject no longer exists and has no equivalent — **it's a deletion
  candidate, not a rewrite**, EXCEPT for two things worth salvaging first: the reads-table-parity
  check (`_AGENTS = {stack: ... for stack in ("react-vite", "fastapi-antd")}`, lines ~410-432) and
  `test_every_read_the_instructions_name_passes_the_fence` (asserts every `sage_domino.PLATFORM_READS`
  family is named in AGENTS.md — this now fails on its own terms too, separately from collection,
  because Phase 0 step 2 deliberately MOVED that endpoint table out of AGENTS.md into the
  `domino-platform-api` skill; the assertion needs to check `LESSONS_LEARNED.md`, not AGENTS.md, or
  be retired in favor of a skill-side equivalent). `sage_serve.py` (fastapi-antd's actual server) is
  already covered by `test_a_no_build_app_serves_from_static_files.py` (below).
- `test_builtapp_flight.py` and `test_builtapp_queries.py` load `template/react-vite/serve.py` too,
  but what they actually EXERCISE through it is `sage_queries.py`'s Flight executor and named-query
  logic — and `sage_queries.py` was, pre-pivot, an near-identical file duplicated into BOTH
  `template/react-vite/` and `template/fastapi-antd/` (`Stack.deploy_files` listed it for both).
  **Likely a one-line path repoint** (`_SERVE_PY` → `template/fastapi-antd/sage_serve.py`, then
  confirm `serve.sq` still resolves to `sage_queries.py` beside it), not a rewrite — but verify
  `sage_queries.py`'s behavior wasn't itself stack-differentiated anywhere before assuming this is
  free.
- `test_builtapp_rehydrate.py` loads `template/react-vite/scripts/rehydrate_data.py`. **This one is
  genuinely different, not a path repoint**: this session's edit to
  `template/fastapi-antd/scripts/rehydrate_data.py` (removing the stale react-vite comparison
  prose) confirmed react-vite split rehydration across a Node script (mounts) and a Python script
  (SDK download), while fastapi-antd's is ONE Python script doing both steps — so react-vite's
  `rehydrate_data.py` and fastapi-antd's file of the same name are NOT the same logic despite the
  shared filename. Repoint the path, then actually re-read the fastapi-antd file's current behavior
  (already read once this session, see the `AGENTS.md`-adjacent edits) and rewrite the test bodies
  against it, not just the import path.

`test_a_viewer_picks_the_stack_for_the_next_app.py`'s entire premise (a UI picker between two
stacks) is gone — decision #3 says no picker, one stack. This file is a candidate for deletion
rather than rewrite; confirm its actual test bodies before deciding (it may also cover
`create_app(stack=...)`'s 400-on-unknown-name behavior, which is still real and worth keeping under
a different, less picker-flavored file name).

## Next session should

1. **All production/template/UI code for Phase 0 is done** (every checklist item above through
   "Additional work landed" is checked, one deliberate skip noted). What's left is entirely the test
   suite: the ~40-file list above.
2. Re-run the full suite fresh first (`cd backend && uv run --extra dev pytest -q -n auto`) to get a
   real, current failure count and collection-error list — don't trust this session's number, it was
   taken mid-edit. Fix the 4 collection-erroring `test_builtapp_*.py` files first (they can distort
   `-n auto`'s reporting for the whole run), then re-run again before triaging the rest.
3. `test_attach_upload.py` is the single largest remaining file — budget real time for it
   specifically, per the notes above. `test_a_built_app_declares_its_stack_at_birth.py` needs
   judgment (re-read decision 4), not a mechanical rename.
4. Do **not** re-derive the mirror map if more mechanical stack-removal comes up — it's recorded in
   decision 3 above; the sibling branch commit is `c4da4cfb` on `origin/remove-fastapi-antd-stack`.
5. Run `make lint` and the full suite once more at the very end of Phase 0, per CLAUDE.md §5/§6 —
   reconcile on COLLECTED count against a stated baseline, not on passed+failed.
6. After Phase 0 is fully green and committed, move to Phase 1 (config.py + TokenSource) in a fresh
   session/context, per `ONE-APP-PLAN.md`'s own phase boundaries.
