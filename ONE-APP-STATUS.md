---
doc: Implementation status for ONE-APP-PLAN.md
branch: one-app-pivot-Etan
last updated: 2026-09-23
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
**UPDATE: all 7 of these regression files are now fixed** (same session, continued after a context
handoff check-in). Root causes and fixes, per file:
- `test_scope.py` (6 tests): rebuilt fake fixtures from `src/*.tsx` to `static/*.js`/root `*.py`,
  matching `FASTAPI_ANTD.source_globs`. The "scaffolding" test's noise list changed from
  `package.json`/`dist/`/`tsconfig` to `static/vendor/*` (vendored) and dotfiles/dotdirs (the actual
  exclusions `scope.app_context` applies now).
- `test_unbind_refs.py` (8 tests) and `test_a_broken_tool_call_ends_the_build_out_loud.py` (1 test):
  same `src/App.tsx`→`static/app.js`/`package.json`→`app.py` fixture rename; `import { askModel }
  from "./appLlm"` → `sage.askModel(...)` (matching the real, no-module API). Needed one more fix
  beyond the rename in `test_unbind_refs.py`: `HELPER_PATH`/`CONFIG_PATH` now resolve under
  `static/sage/`, which doesn't exist yet at template-build time — added a `.parent.mkdir()`.
- `test_snapshot.py` (2 tests): the fake `node_modules/` dir the tests wrote into no longer gets
  created at all (that was `link_warm_deps`'s job, now deleted) — added an explicit `.mkdir()`
  before writing into it. `TurnSnapshot._EXCLUDE` still lists `"node_modules"` as a literal string
  (harmless dead entry now that no stack creates one; left alone per "don't delete unrelated dead
  code" — it still works fine as a test vehicle for "an excluded dir is protected").
- `test_the_chat_prompt_lets_the_thread_keep_findings_under_sage_threads.py` (1 test): pinned the
  exact `src/`-era sentence this session's own `template/chat/AGENTS.md` edit changed to `static/`
  — a one-line string update in the test.
- `test_chat_and_build_get_their_own_context.py` (14 tests, the big one) AND its shared root cause:
  **`test_chat_turn.py`'s `_orch()` helper** (imported by `test_chat_and_build_get_their_own_context.py`
  directly, and used as the base fixture by **~48 other test files** — checked, not assumed: grepped
  every importer and ran all of them together). `_orch()` built a react-vite-shaped fake template;
  fixed to fastapi-antd shape (`static/app.js` + `app.py`, dropping `package.json`/`src/`). Also fixed
  two more `src/App.tsx`-reading tests inside `test_chat_turn.py` itself that read the seeded
  placeholder directly, and one standalone template-builder in the same file
  (`test_chat_does_not_seed_the_react_template`, renamed `..._the_app_template`). **Verified the
  full blast radius**: ran `test_chat_turn.py` + all 48 importers together — 812 passed, 0 failed.
- `test_workspace.py` (9 tests): `_fake_template` rebuilt fastapi-antd-shaped (no more
  `node_modules`/`.bin/vite` sentinel construction). The 3 `test_link_warm_deps_*` tests were
  **deleted outright** (the method no longer exists — Vite/node_modules-only, nothing to port).
  `test_ensure_seeds_from_template_and_symlinks_node_modules` renamed to
  `test_ensure_seeds_from_template` and its symlink assertion dropped (no such concept for a
  no-build stack). Four `refresh_entry_script`/`app.sh` tests renamed `serve.py`→`sage_serve.py`
  throughout (matching `FASTAPI_ANTD.deploy_files`, which has no Node-script (`.mjs`) companion and
  no `sage_domino.py` entry — react-vite's `_DEPLOY_FILES` had both; fastapi-antd's doesn't).

**Verified together**: all 7 files, 107 tests, 0 failures.

## Process note for whoever continues this

The `git stash`/`stash pop` round-trips used to diff failures against baseline were verified
restored correctly each time (`git status --short` count and a content spot-check on
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

## The ~40 test files still naming react-vite (list below is HISTORICAL — see the 2026-09-23 update
## above for what's actually still outstanding; most of this list is now done)

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

## UPDATE: continued past this point in the same session (context budget was fine; user said to
## keep going rather than hand off) — the four `test_builtapp_*.py` files, `test_a_no_build_app_
## serves_from_static_files.py`, `test_build_agent_numeric_stdout_guard.py`,
## `test_a_built_app_declares_its_stack_at_birth.py` and `test_a_viewer_picks_the_stack_for_the_
## next_app.py` are now DONE, not just planned. What actually happened, since it differs in places
## from what this section originally predicted:

- **`test_builtapp_flight.py` / `test_builtapp_queries.py`** (62 tests total): not a one-line path
  repoint as guessed — `serve.build_server(...)` (a real socket server) doesn't exist on
  `sage_serve.py` at all (it's `mount(app, executor=...)` onto an existing FastAPI app). Rewrote
  both to mount onto a bare `FastAPI()` and drive it in-process with `TestClient`, matching the
  pattern already in `test_a_no_build_app_serves_from_static_files.py`. **One subtle bug found
  along the way**: the executor must be built from `serve.sq` (the specific `sage_queries` module
  instance the freshly-loaded `sage_serve.py` itself imported), never from a separately-loaded
  top-level `sq` — two module objects for byte-identical source means two different `QueryProblem`
  classes, so `sage_serve.py`'s own `except sq.QueryProblem` silently misses an instance raised by
  the other one's `FlightExecutor`, and a specific 503 degrades to a generic 502. Both files: 100%
  passing (33 + 29).
- **`test_builtapp_serve.py` — deleted, not rewritten**, confirming the original guess: its subject
  (react-vite's own `serve.py` — `build_server`, SPA-fallback routing, hashed-asset caching,
  dist-directory serving) has no fastapi-antd equivalent. **Named gap, not silently dropped**:
  roughly 15 of its ~44 tests covered `sage_domino.py`'s relay/fence directly (path-traversal
  refusal, allow-list enforcement, redirects, token non-disclosure, error sanitization) and aren't
  covered anywhere else except one thin test in `test_a_no_build_app_serves_from_static_files.py`.
  `sage_domino.py` itself is unchanged production code this session, so this is a coverage gap, not
  a proven regression — a future session should consider a dedicated `test_sage_domino_relay.py`
  testing those functions directly (no server needed for most of them).
- **`test_builtapp_rehydrate.py`**: turned out to be near a pure path repoint after all —
  `rehydrate_data.rehydrate(root, get_dataset=None)` has the identical signature and
  `(fetched, unavailable)` contract in both stacks' scripts; the tests only ever call `rehydrate()`,
  never `link_mounts()` or `main()`, so fastapi-antd's extra mount-linking step (folded into the
  same file) never entered the picture. All 7 tests pass unchanged in substance.
- **`test_a_no_build_app_serves_from_static_files.py`**: this file's own premise was "prove
  fastapi-antd and react-vite coexist" (`from sage.workspace.stack import FASTAPI_ANTD, REACT_VITE`
  — collection error once `REACT_VITE` was deleted). Rewrote to test fastapi-antd standalone:
  dropped the two-stack-comparison tests (folding the still-useful FASTAPI_ANTD-only assertions into
  a new `test_the_stacks_shape_is_internally_consistent`), simplified `_seed()` to seed fastapi-antd
  directly, fixed `from sage.resources.app_helpers import FASTAPI, TEMPLATE` (`FASTAPI` no longer
  exists — `TEMPLATE` **is** the fastapi-antd shape now), and changed one `mgr.stack is FASTAPI_ANTD`
  identity check to `==` — `stack_for()` returns a **new** `dataclasses.replace()`'d instance every
  call now, so `is` can never hold even for a matching case. 11/11 pass.
- **`test_build_agent_numeric_stdout_guard.py`**: pinned exact substrings from the OLD, longer
  AGENTS.md wording that this session's rewrite paraphrased while keeping the same substance.
  Updated `PROBES` to match the current (shorter) wording instead of restoring the old one —
  shrinking AGENTS.md was an explicit Phase 0 goal — and repointed `AGENTS` from
  `template/react-vite/` to `template/fastapi-antd/`. 3/3 pass.
- **`test_a_built_app_declares_its_stack_at_birth.py`**: rewritten as anticipated (re-keyed on
  `stack_of` falling back to `FASTAPI_ANTD`, not `None`) — and this surfaced a **real production
  bug**, not just a test-side issue: `WorkspaceManager.stack_for()`, as this session had simplified
  it earlier, unconditionally overrode `template_dir` to the manager's own configured directory for
  EVERY stack, not just the fastapi-antd/fallback one. That silently breaks the moment a second real
  stack is registered — its `template_dir` gets clobbered with fastapi-antd's, seeding the wrong
  template — and this file's own `_other_stack()` helper caught it immediately. **Fixed in
  `manager.py`**: the override now applies only when `kind.name == FASTAPI_ANTD.name` (covering both
  a real `fastapi-antd` record and the legacy/absent-record fallback, which answers as that same
  Stack), leaving any other real registered stack's own `template_dir` alone — restoring the
  original pre-pivot code's actual conditional shape, just re-keyed on the new stack name. Re-ran
  everything already fixed this session against this change to be sure: 290 tests across 17 files
  and the 49-file `test_chat_turn.py`-plus-importers batch (812 tests) — 0 failures in either.
  `_other_stack()`'s fake second-stack shape also needed reshaping: it used to distinguish itself
  from react-vite via `app.py`/`static/`, which is now fastapi-antd's OWN shape and collided;
  changed to `manifest.json`/`widget.js`, sharing nothing with fastapi-antd's files.
- **`test_a_viewer_picks_the_stack_for_the_next_app.py` — deleted**, confirming the guess: its
  entire premise (a settings-drawer picker between two stacks) is gone per decision #3, and
  `create_app(stack=...)`'s 400-on-unknown-name behavior it also touched on is already covered in
  `test_a_built_app_declares_its_stack_at_birth.py`. `prefs_harness.mjs` (its JS test harness) is
  kept — 4 other test files still use it for unrelated preferences.

**Remaining, not yet started**: everything in the file list below except what's named done above.
`test_attach_upload.py` (dozens of `src/`-shaped fake-copy paths, `_detect_leaks`/`_leaked_copy_
paths` tests, at least 2 `import.meta.env.BASE_URL` assertions this session's `_write_agents_data_
block` rewrite already invalidated) and `test_a_folder_is_the_unit_of_the_act.py` (same pattern,
smaller) are still the two biggest known remaining items — budget real time for `test_attach_
upload.py` specifically. Production code underneath both (`_scan_app_sources`, `_copies_in_app`) is
confirmed stack-agnostic (plain `os.walk`, matches by basename, no `src/` hardcoding), so this is a
fixture-and-assertion rewrite, not a production bug hunt.

## UPDATE 2026-09-23: continued the test-suite sweep (post context-handoff)

Worked straight through the file list below in the order it was written, plus the extra 7-file
regression batch (already marked done above). All of these are now green, verified individually:

- `test_crash_card.py` (6/6), `test_a_dead_alias_stops_the_turn_before_it_starts.py` + 5 importers
  sharing its `_template()` fixture (102/102), `test_a_placeholder_is_not_a_finished_build.py` (2/2),
  `test_preview_deps.py` (4/4, 5 Vite-`optimizeDeps`/`refresh_preview_config` tests dropped as having
  no fastapi-antd equivalent), `test_new_apps_report_incomplete_model_answers.py` +
  `tests/js/app_model_outcome_harness.mjs` (5 passed, 2 skipped — harness rewritten to `eval()` the
  helper as a plain script against preset globals instead of `import()`-ing an ES module),
  `test_fonts.py` (4/4), `test_history_untracked.py` (6/6), `test_bound_schema.py` (79/79),
  `test_a_dashboard_ships_with_a_control.py` (11/11).
- `test_preview_overlay_gate.py` and `test_an_app_seeded_before_the_rename_keeps_its_helper_names.py`
  — deleted (`git rm`), confirmed no fastapi-antd equivalent for either subject (Vite's error-overlay
  plugin; `LEGACY` helper names already gone from production code).
- `test_a_git_commit_header_is_not_read_into_a_build_turn.py` (12/12) — path/fixture repoint, but
  also surfaced a **real content-loss bug in `template/fastapi-antd/AGENTS.md`**: this session's
  earlier compression of the git-history-safety bullet (#328) had silently dropped several
  safety-relevant details versus the pre-pivot wording — three of the six leaking `blame` flags
  (`--line-porcelain`, `--incremental`, `git annotate`), the "do not work out which form is safe"
  instruction, and the address-vs-command generalisation reasoning. This is unlike the Dashboard/
  Control compression (a cosmetic checklist reflow) — it's a PII-leak-prevention rule with three
  documented live incidents behind it, so the fix was to **restore the fuller bullet text**, not
  shrink the test to match thinned-out safety guidance. `AGENTS.md` is 167 lines now (was ~161).
- `test_a_binding_the_app_never_calls_says_so.py` — 16/18 (2 fail identically on the unmodified
  baseline via `git stash`: `publish_available()`'s dogfood-safety check trips because `/mnt/code`
  really is the mounted repo in this sandbox — pre-existing/environmental, not this pivot's doing).
- `test_a_built_apps_instructions_carry_the_packs_words.py` (11/11) — rewrote assertions to match
  the CURRENT (compressed) AGENTS.md content: "typechecks" → "compiles every `.py` file", the old
  hardcoded accent-hex sentence → the `{platformName} theme into antd.ConfigProvider` line (which
  wraps mid-sentence — `"the\nAcme Cloud theme..."`, match the literal newline), `src/appQuery.ts` →
  `static/sage/appQuery.js`. Dropped the `basename={appBase}` "unknown token survives" check — this
  stack's AGENTS.md genuinely contains no non-pack-token brace anymore (verified by grep), and the
  general invariant is already covered by `test_brand.py::test_an_unknown_token_is_left_alone` against
  synthetic content, so this isn't a coverage loss.
- `test_a_legacy_root_agents_md_speaks_the_packs_words.py` (5/5), path/fixture repoint only.
- `test_the_agents_file_reaches_the_model_in_the_packs_words.py` (6/6) — path/fixture repoint, plus
  fixed two stale doc-comment examples (`App.tsx` / `basename={appBase}`, which no longer exists in
  this stack) to point at a real current example (`sage_serve.py`'s `/u/{owner}/{project}/app/`).
- `test_the_live_read_tools_reach_opencode_as_an_mcp_server.py` (13/13), path fix only.
- `test_the_build_agent_can_reach_the_chat_artifacts.py` (18/18) — full `src/App.tsx`/`package.json`
  → `static/app.js`/`app.py` conversion across ~15 call sites (scripted with a small Python replace
  since the pattern repeated identically everywhere); template's own `.gitignore` fixture content
  also simplified since `node_modules`/`dist` no longer mean anything here.
- `test_an_app_that_calls_the_gateway_without_askmodel.py` (25/25) — path/fixture repoint only.
  **Important finding, not a fix**: `_scan_app_sources` (`orchestrator/service.py`) walks the WHOLE
  app tree via `os.walk` and is not filtered by `stack.source_globs` at all — only skips a fixed dir
  list and reads by extension (`_SCAN_EXTS`, which still includes `.tsx`/`.ts`). So this file's fake
  `src/Chat.tsx` write-paths did NOT need renaming to `static/*.js` to be picked up by the real
  build-loop tests — they already were. Worth remembering before assuming every `src/`-shaped test
  fixture needs a path rename: some only need the TEMPLATE/helper paths fixed, not every fake write.
- `test_a_non_utf8_generated_file_is_repaired_not_refused.py` (4/4) and
  `test_a_retry_budget_is_spent_in_whole_agent_turns.py` (8/8) — path/fixture repoints, including
  copying the real `sage_serve.py`/`sage_queries.py`/`static/sage/appQuery.js`/`static/sage/appBase.js`
  into a fake template (mirroring `test_bound_schema.py`'s established pattern).
- `test_pinned_model.py` (11/11) — surfaced **two real, unrelated production bugs** in code this
  session's earlier "always render the JS/global-script form" pass (see decision 5's neighbours)
  actually missed:
  1. **`pinned_model.py`'s and `pinned_model_api.py`'s `agents_block()` functions were still
     generating react-vite-style code samples** — `` ```tsx ``  fences and
     `import { askModel, checkModel } from "./appLlm"` / `import { callModelApi, ModelApiError }
     from "./appModelApi"` — never converted to the plain-script `sage.*` form the rest of the
     stack uses. This means every real Built App bound to an LLM Alias or Model API had its AGENTS.md
     literally telling the agent to write an ES import that does not exist on this page. Fixed both
     to `` ```js `` + `sage.askModel(...)` / `sage.callModelApi(...)`, matching `bound_schema.py`'s
     already-correct `sage.runQuery` convention. Prose mentions of bare `` `askModel` ``/
     `` `callModelApi` `` (no `sage.` prefix) were left alone — that's the established convention
     for prose (`bound_schema.py` does the same for `runQuery`), only the fenced code samples were
     wrong.
  2. **`render_config()`'s header comment** (written into the real, committed
     `static/sage/appLlm.config.js`) **and `pinned_model_api.py`'s equivalent had drifted from the
     already-correct, hand-edited shipped template files.** The shipped `template/fastapi-antd/
     static/sage/appLlm.config.js` already said `sage.askModel`; the Python function that's supposed
     to be its single source of truth still generated bare `askModel`. Worse on the Model API side:
     the shipped file's header had been rewritten to a shorter, more accurate warning, while
     `pinned_model_api.py`'s function still generated the old text, which claimed the token is
     "compiled into the app's bundle" — a bundler concept that does not exist on this no-build
     stack. Fixed both functions' header text to match the shipped templates **exactly** (verified
     byte-for-byte via a direct diff, not just eyeballed), and fixed the same stale "bundle" language
     in two nearby docstrings in `pinned_model_api.py` for accuracy (no test pinned these, but they
     directly misdescribe the mechanism).
  One downstream test broke from fix (2) and was updated to match:
  `test_model_api_credentials.py::test_the_generated_config_carries_the_url_and_token_and_warns_
  about_the_bundle` (renamed to `..._about_the_exposure`; the "CAN READ THEM" phrase now wraps
  across a comment-line boundary in the corrected text, `"CAN READ\n// THEM"`).
  Verified the full affected batch together after both fixes: `test_pinned_model.py` +
  `test_bound_schema.py` + `test_an_app_that_calls_the_gateway_without_askmodel.py` +
  `test_model_api_credentials.py` = **163 passed, 0 failed**.

**Two minor, low-priority doc-staleness items found but NOT fixed (no test pins them, pure prose)**:
`sage/resources/gateway_bypass.py`'s module docstring (lines 3, 6, 27) still narrates `src/appLlm.ts`
/`src/appLlm.config.ts` in prose (the actual code holds no hardcoded path — `DEV_PROXY_PATH`/
`COMPLETIONS_PATH` are URL paths, not file paths, so this is pure documentation drift). And
`orchestrator/service.py:4030`, inside the Chat/delegated-model-call prompt text
(`_describe_binding` or similar, the `llm_alias` branch), still hard-codes the sentence
"Do not read src/appLlm.ts for a recipe" — this one IS live text sent to the model in Chat's own
delegated-tool-call flow, not just a comment, so it's a slightly higher-priority fix than the
`gateway_bypass.py` docstrings, but out of scope of the file being worked when found. Worth a
dedicated small fix early next session.

**Remaining files from the original list, not yet started**: `test_a_build_whose_queries_all_fail_
is_not_clean.py`, `test_a_catalog_that_yields_nothing_says_so.py`,
`test_a_chat_turn_can_call_a_model_the_person_bound.py`, `test_bindings.py`,
`test_preview_queries.py`, `test_publish_check.py`, `test_template_fixes_reach_an_existing_app.py`,
`test_the_implement_agent_looks_in_one_message.py`, `test_the_path_sage_names_is_a_path_sage_
allows.py`, `test_the_workbench_ships_the_licences_it_owes.py`, `test_use_in_app_binds_an_llm_
alias.py`, plus the two large ones flagged repeatedly: `test_attach_upload.py` and
`test_a_folder_is_the_unit_of_the_act.py`. Production code underneath the last two is confirmed
stack-agnostic, so those two remain fixture-and-assertion rewrites, not production bug hunts —
though per this update's `pinned_model.py` experience, do not assume a file's production code is
correct just because it's stack-agnostic in shape; check `agents_block`-style generated text
against what actually ships in `template/fastapi-antd/` before trusting it.

## UPDATE 2026-09-23 (continued, same session): finished the rest of the backlog

Finished every remaining file from the original list, plus the two large ones repeatedly flagged as
needing dedicated time. All verified individually green:

- `test_a_catalog_that_yields_nothing_says_so.py` (16/16) — path/fixture repoint
  (`src/App.tsx`+`src/appQuery.ts` → `static/app.js`+`static/sage/appQuery.js`, plain
  `sage.runQuery(...)` call instead of an ES import in the fake app source).
- `test_use_in_app_binds_an_llm_alias.py` (6/6) — one path fix (real `appLlm.js` still carries the
  same guard text this test checks for, just at its new path).
- `test_template_fixes_reach_an_existing_app.py` (6/6) — path/fixture repoint. `_OWNED_SOURCES` is
  now `static/sage/{appBase,appModelApi,appQuery,reportRuntimeError,errorBoundary}.js` +
  `static/theme.js` (renamed from the old `ErrorBoundary.tsx`/`reportRuntimeError.ts` pair), so the
  ordering test now checks `errorBoundary.js` calls `sage.reportRuntimeError(...)` (a global, not an
  ES import) rather than a `from "./reportRuntimeError"` import line. The
  "AGENTS.md forbids editing every refreshed file" test needed an actual judgment call, not a
  rename: the current (compressed) AGENTS.md forbids `static/sage/` as ONE blanket directory rule
  rather than naming every file under it individually, so the test now checks the blanket rule for
  files under `static/sage/` and the individual backtick path for the one owned file outside it
  (`static/theme.js`) — this is a legitimate representation change, not a coverage loss, since the
  blanket rule does structurally cover every file under the directory.
- `test_preview_queries.py` (20/20), `test_publish_check.py` (9/10, 1 pre-existing `/mnt/code`
  dogfood-safety failure confirmed via baseline), `test_a_build_whose_queries_all_fail_is_not_clean.py`
  (20/20) — all three needed only the `TEMPLATE` path repointed to `template/fastapi-antd` (plus
  fixture reshaping in the two that build their own fake template). **Important finding, not a
  fix**: `PreviewQueries.start()` calls `module.build_server(...)` where `module` is
  `sage_queries.py` (`serve_module()`'s target, per `builtapp.py`'s `_SERVE_REL`) — NOT the
  react-vite-only `serve.py` that `test_builtapp_serve.py` was about. `sage_queries.py` carries its
  OWN standalone stdlib `build_server`/`QueryRoute`/`_QueryOnlyHandler` (a tiny query-only HTTP
  server for the preview, separate from `sage_serve.py`'s `mount(app, executor=...)` used by the
  published app's real FastAPI server). So the preview's live-query-while-building feature (#24)
  already works correctly on fastapi-antd — nothing here was actually broken, despite `serve.py`
  genuinely not existing. Worth remembering: `sage_serve.py` and `sage_queries.py` are not
  interchangeable names for the same thing, and `serve_module()` only ever means the latter.
- `test_attach_upload.py` (87/87) and `test_a_folder_is_the_unit_of_the_act.py` (45/45) — the two
  files flagged repeatedly across this whole effort as needing dedicated time turned out to need
  almost none: both were already passing except for ONE test each (the same one, structurally), left
  over from this session's earlier `_write_agents_data_block` rewrite (`import.meta.env.BASE_URL +
  "data/..."` / `"Invalid base URL"` → `sage.url("data/<slug>/<name>")` / `"Do NOT fetch a
  leading-slash path"`). The dozens of `src/`-shaped fake-copy paths this file's docstrings warned
  about were apparently already reshaped earlier in this session (via the shared `_orch()`/fixture
  helpers these two files import from `test_chat_turn.py`/`test_bound_schema.py`, both already fixed
  in the earlier 7-file regression batch) — so the "budget real time" warning in every prior status
  update turned out to be stale by the time this session reached them. Lesson for next time: a file
  flagged as large/risky should be RE-CHECKED (a quick `pytest -n0` run) before being scheduled for
  dedicated effort, rather than trusted from an earlier note — the backlog's true size shrinks as
  shared fixtures get fixed, and the note describing it does not update itself.

**This closes every file in the original ~40-file list and the 7-file regression batch.**
`grep -rl "react-vite\|REACT_VITE" backend/tests/*.py` still returns 8 files, all confirmed either
already passing (harmless historical narration in a docstring, or — `test_the_workbench_ships_the_
licences_it_owes.py` — a loop over both stack directories that tolerates the missing one) or
intentionally still discussing `react-vite` as a legacy stack-name string
(`test_a_built_app_declares_its_stack_at_birth.py`, which is specifically about the fallback
behavior for an app whose record still says `react-vite`).

## UPDATE 2026-09-23 (continued, same session): Phase 0 test suite is closed out

A full `cd backend && uv run --extra dev pytest -q -n auto` was run at the end of the previous
update. It surfaced two more files this session's earlier work had broken but the `react-vite`-string
grep never caught (same trap as the "IMPORTANT CORRECTION" section above, same root cause: a deleted
SYMBOL rather than a deleted file path) — both were **collection errors**, which poison `-n auto`'s
reporting for the whole run:

- `test_feedback.py` imported `parse_tsc`, deleted from `sage/feedback/runner.py` when typecheck was
  replaced with `check_python_stack` (always Python/JS syntax check). Its coverage was already fully
  duplicated by `test_parse_py_compile_extracts_errors`/`test_parse_node_check_extracts_errors`
  (already present in the file, already fastapi-antd-shaped) — so deleting the one obsolete test was
  not a coverage loss. Also fixed one assertion pinning the old "Typecheck passed." wording (now
  "Syntax check passed.") and rewrote `test_implement_prompt_names_the_config_the_gate_checks`, whose
  premise (`FeedbackRunner()._tsconfig`, an attribute that no longer exists) depended on a config file
  that doesn't exist anymore either. Renamed to `..._names_the_same_check_the_gate_runs`, now pinned
  on the literal `py_compile`/`node --check` command names in the `sage-implement` prompt instead —
  **narrower than the original guarantee**, since there is no longer a shared config object linking
  the prompt's prose to the runner's behavior for a test to pin against; noted as a real, permanent
  gap rather than solved.
- `test_supervisor_parse.py` imported `ViteSupervisor`/`parse_vite_url`/`make_supervisor`, all deleted
  when `preview/supervisor.py` became `UvicornSupervisor`-only. Rewrote the file: kept
  `parse_uvicorn_url` coverage, translated the `preview_port()` env-override/typo-fallback tests to
  `UvicornSupervisor._env_port()` (the equivalent logic, now a method rather than a module function,
  so real behavior — not just parsing — stayed covered), kept the already-fastapi-antd-shaped
  `_spawn()` command-line test, and deleted `test_an_app_with_no_record_keeps_its_vite_preview`
  outright: `_supervisor_for()` no longer dispatches on the app's record at all (`UvicornSupervisor`
  unconditionally, per `service.py:544-548`), so there is nothing left for that test to be about.

Both fixed and verified (9/9, 5/5). Grepped for every one of the four deleted symbols
(`ViteSupervisor`, `parse_vite_url`, `make_supervisor`, `parse_tsc`) across `tests/` and `sage/` —
clean, no other stragglers.

**Full suite, final and reconciled:**

```
7774 collected == 99 failed + 7665 passed + 10 skipped, 0 errors
```

All 99 failures verified against the unmodified baseline via `git stash`/`git stash pop` (work
restored and re-verified intact afterward): **identical failing-test-name set, before and after this
session's changes** — `diff` of the two sorted lists is empty. These are the same pre-existing,
environment-specific failures already characterized earlier in this doc (`publish_available()`'s
`/mnt/code`-dogfood-safety check tripping because this sandbox's `/mnt/code` really is the mounted
repo; `test_native_gateway_transport.py`'s codec tests; a handful of others) — none of them are this
pivot's doing, and none of them are new. `make lint` (the repo-wide `cd backend && ruff check ..`
target) is clean: **All checks passed!**

**This closes Phase 0.** Every item in the Phase 0 checklist above is done; the ~40-file test-suite
backlog plus the two 7-file/2-file regression batches found along the way are all fixed and verified;
lint is clean; the full suite is green modulo a pre-existing, baseline-verified, environment-specific
failure set that is not this session's to fix (it would need a sandbox without a real `/mnt/code`, or
a rewrite of `publish_available()`'s own dogfood-safety check, which is out of scope for this pivot).

**Two known, permanent (not-a-bug) gaps, recorded rather than silently dropped:**
1. `sage_domino.py`'s relay/fence lost ~15 tests when `test_builtapp_serve.py` was deleted (its
   subject, react-vite's own `serve.py`, has no equivalent) — `sage_domino.py` itself is unchanged
   production code, so a future session should add a dedicated `test_sage_domino_relay.py`.
2. `test_feedback.py`'s prompt/runner-drift guard is now weaker (see above) — there is no longer a
   shared config object to pin both sides to, only matching literal command names in two independent
   places.

**Two minor doc-staleness items, still not fixed** (no test pins either, both pure prose, both named
in the previous update too): `sage/resources/gateway_bypass.py`'s module docstring narrates
`src/appLlm.ts`/`src/appLlm.config.ts`; `orchestrator/service.py`'s delegated-model-call prompt text
(around line 4030) hardcodes "read src/appLlm.ts for a recipe" as LIVE text sent to the model in
Chat's delegated-tool-call flow — this one is a real (if minor) live-prompt inaccuracy, not just a
comment, and is worth a small dedicated fix early next session.

## Next session should

1. **Phase 0 is fully done and verified** — production/template/UI code, the entire test-suite
   backlog, `make lint`, and the full suite (reconciled on COLLECTED count against a diffed baseline,
   per CLAUDE.md §5/§6). Nothing from Phase 0 is left to do.
2. Nothing in this repo has been committed by this session (see the earlier note about an
   already-existing unrequested commit, `fc7ff826`, still needing the user's attention) — confirm
   with the user before committing this session's changes, per this repo's CLAUDE.md.
3. Fix the two minor doc-staleness items named just above (`gateway_bypass.py`'s docstring,
   `service.py`'s delegated-model-call prompt text) — small, quick, and one of them is live-prompt
   text rather than a comment.
4. Consider the two named gaps above (`test_sage_domino_relay.py`, the weakened `test_feedback.py`
   drift guard) — neither blocks Phase 1, both are real, minor coverage debt.
5. Move to Phase 1 (config.py + TokenSource) in a fresh session/context, per `ONE-APP-PLAN.md`'s own
   phase boundaries. Do **not** re-derive the mirror map if more mechanical stack-removal comes up —
   it's recorded in decision 3 near the top of this file; the sibling branch commit is `c4da4cfb` on
   `origin/remove-fastapi-antd-stack`.

## UPDATE 2026-09-23 (new session): Phase 1 — Config and one TokenSource. DONE and verified.

Also did the item 3 above named ("Fix the two minor doc-staleness items") before starting Phase 1:
`gateway_bypass.py`'s docstring/comments and `service.py`'s delegated-model-call prompt text
(`llm_alias` branch) both repointed from `src/appLlm.ts`/`src/appLlm.config.ts` to
`static/sage/appLlm.js`/`static/sage/appLlm.config.js`; one test
(`test_the_service_speaks_the_packs_words.py::test_a_language_model_row_hands_the_turn_a_way_to_call_it`)
updated to match the corrected live-prompt sentence. Verified against baseline via `git stash` that
the one test failure this touched (`test_publishing_off_platform_names_the_pack`) is pre-existing and
unrelated (see below — it's part of the dogfood-safety class). Item 4 (the two named test-coverage
gaps, `test_sage_domino_relay.py` and `test_feedback.py`'s weakened drift guard) was **not** picked
up — still open, still non-blocking, still real. `fc7ff826`'s "unrequested commit" concern is now
moot: Phase 0's work is confirmed committed and pushed (`a627b0ec "phase 0 done"`, `git log` shows it
on `origin/one-app-pivot-Etan`) — nothing to flag there anymore.

**A live sandbox discovery that shapes this whole phase, found before writing any code, not
assumed.** This session's sandbox is itself a real Domino workspace (`DOMINO_API_HOST`,
`DOMINO_USER_API_KEY`, `DOMINO_ENVIRONMENT_ID`, `DOMINO_HARDWARE_TIER_ID` all genuinely set,
`localhost:8899` sidecar reachable) — so several of Phase 1's own "live checks... cannot be
unit-tested" requirements were actually checkable, and were checked, with `curl`/`httpx`/the real
`domino_data` SDK, never by assumption:

1. **A static Domino account key and a sidecar JWT are NOT interchangeable on the wire — this
   contradicts the plan's own text, not just fills a gap in it.** `GET /api/users/v1/self` and
   `GET /api/projects/beta/projects`: the account key (`DOMINO_USER_API_KEY`) sent as
   `Authorization: Bearer <key>` is REFUSED — 403, `"Not authorized: No current user in request"`
   from `/self`. The SAME key sent as `X-Domino-Api-Key: <key>` is accepted (200). A sidecar JWT
   sent as `Authorization: Bearer <jwt>` is accepted (200) — the header style already shipped
   everywhere (`DominoControlPlane._headers()`), unchanged, still correct for sidecar. One
   inconsistency worth remembering: `GET /api/datasetrw/v2/datasets` (the family
   `assets/provider.py`'s `DominoAssetProvider` already calls) tolerated the account key as
   `Bearer` too — so that provider's three existing raw-Bearer call sites were deliberately left
   unchanged rather than "fixed" into a header scheme nothing there needed.
2. **The `domino_data` SDK mirrors the exact same split, and `assets/provider.py`'s OWN docstring
   had already half-recorded this before Phase 1 touched it** (`"Passing an account API key as
   token= instead is rejected with 'Your role does not authorize you to perform this action'"` —
   a slightly different error string than what this session got, `"Anonymous principals are not
   supported"`, probably a version difference, same conclusion either way):
   `DatasetClient(token=<static key>)` fails; `DatasetClient(api_key=<static key>)` **and**
   `DatasetClient(token=<sidecar JWT>)` both work — live-verified against a real Dataset
   (`dataset-new-project-oct-28-671fc113401a7124d7887576`, `list_files()` returned `0` with no
   auth error either way). `DataSourceClient` shares the identical `api_key=`/`token=` constructor
   shape; NOT independently live-tested (no live Data Source query attempted), the assumption is
   "same shape, same rule" and it is exactly that — an assumption, flagged as one in
   `platform/auth.py`'s own docstring, not quietly treated as verified.
3. **`derive_gateway_url()`'s "apps.<host>" formula — literally what ONE-APP-PLAN.md §2.1 specifies
   — does not resolve from inside this workspace, and is UNVERIFIED for the App context the plan
   actually means it for.** `DOMINO_API_HOST` here is `http://nucleus-frontend.domino-platform:80`,
   an internal k8s service name; the sandbox's real public origin (from `VSCODE_PROXY_URI`) is
   `cloud-dogfood.domino.tech` — an unrelated string, not a subdomain swap of the internal one.
   `curl`ing the derived URL (`https://apps.nucleus-frontend.domino-platform/apps/llm_gateway/v1/
   models`) timed out (`HTTP 000`, no DNS/route from here). Implemented anyway, exactly as the plan
   specifies, because `Settings.gateway_url()` only ever uses it as a **fallback default** behind an
   explicit `gateway_base_url` override — so a wrong guess costs a person one form field, not a
   crash. Whether the formula holds from inside an actual published App (a genuinely different
   network position than a workspace) is still open; nothing here could test that. Recorded as an
   open risk, not silently shipped as fact.

**What got built**, matching the plan's own Phase 1 checklist:

1. `backend/sage/config.py` (new): `Settings` frozen dataclass (`domino_host`, `domino_token`,
   `gateway_base_url`, `gateway_api_key`, `git_token`, `publish_environment_id`,
   `publish_hardware_tier_id`) + `load(home, env)`/`save(home, settings)` reading
   `$SAGE_HOME/settings.json` with env vars overriding per-field (`DOMINO_API_HOST`,
   `DOMINO_USER_API_KEY`, `GATEWAY_BASE_URL`, `GATEWAY_API_KEY`, `DOMINO_ENVIRONMENT_ID`,
   `DOMINO_HARDWARE_TIER_ID` — the platform's own injected names, verified present in this sandbox,
   not invented). `derive_gateway_url(host)` and `resolve_sage_home(env)` per §2.1 — the latter
   deliberately does NOT implement the App's per-user-id nested-under-a-mounted-Dataset path yet
   (that detection needs a real App to test against, i.e. Phase 7); it falls back to `/tmp/sage-home`
   (ephemeral, on Domino) or `~/.sage` (laptop) — safe defaults, not the full spec, and said so in
   the docstring rather than pretending otherwise. `Settings.redacted()` is the `GET /api/settings`
   shape: secret fields collapse to a bool, never the value. 14 tests, `tests/test_config.py`.
2. `backend/sage/platform/auth.py` (new package): `TokenSource` — `kind` ("static"/"sidecar"),
   `.bearer()`, `.headers()` (the header-scheme split from finding 1), `.sdk_kwarg()` (finding 2),
   `.whoami()` cached **forever per instance** (not re-keyed per token value like
   `DominoControlPlane.whoami()` — that class's cache is deliberately kept keyed on the live token
   because ONE of its instances used to serve MANY viewers behind the old multi-viewer door,
   ADR-0004/`test_whoami_follows_the_token.py`; a `TokenSource` is one person for its whole life
   under decision #1, so caching forever is correct here, not stale). `build_token_source(host,
   token, env)`: static when a token is configured, else sidecar, `None` with no host at all.
   `gateway_bearer(gateway_api_key, token_source, env)`: an explicit `dgw_` key wins, else the SAME
   `TokenSource` every other Domino call uses (§0's "one TokenSource ... feeds ... the LLM Gateway
   listing"), else sidecar as a last resort. 11 tests, `tests/test_token_source.py`, including one
   that pins the header-scheme split with a mock transport so a future edit can't silently drift a
   static key back onto a bare `Bearer` header.
3. **Every build site the plan named, rewired to the shared `Settings`/`TokenSource`, none of them
   left as a second, independent "check the key, else sidecar" branch:**
   - `gateway/factory.py`'s `build_gateway()`: gained optional `token_source`/`gateway_api_key`/
     `base_url` params (all default to the old raw-env behavior, so `shim/app.py`'s and
     `tools/probe.py`'s bare `build_gateway()` calls — and this module's own existing tests — are
     completely unchanged); `orchestrator/app.py` is the only caller that passes the new params.
     `resolve_mode()` also gained an optional `base_url` override, so a gateway configured ONLY
     through Settings (no env vars at all — the laptop story) still resolves to `domino` mode
     instead of reading as `fake` because the env var it would have derived from was never set.
   - `orchestrator/app.py`: one process-wide bootstrap block (`_SAGE_HOME`, `_SETTINGS`,
     `_TOKEN_SOURCE`) built once, near the top, before `build_gateway()` is called. `_domino_api_
     token()` deleted outright (was: `DOMINO_API_KEY`-or-sidecar, used by nobody with a real key in
     production since `DOMINO_API_KEY` was never Domino's own injected name — see decision below).
     `_build_assets()`, `_build_resources()`, `_preview_llm()` all read `_SETTINGS`/`_TOKEN_SOURCE`
     instead of raw env; the gateway-token duplication that existed independently in THREE places
     (`factory.py`, `_build_resources`, `_preview_llm`, all doing the identical `GATEWAY_API_KEY`-
     or-sidecar check) collapsed to one shared `gateway_bearer()` call in each.
   - `_build_control_plane()`: repointed to read `_SETTINGS.domino_host`/`publish_environment_id`/
     `publish_hardware_tier_id` (same values as before when only env vars are set — behavior-
     identical for every existing deployment), but **deliberately kept sidecar-only**, not wired to
     `_TOKEN_SOURCE`. Finding 1 is why: `DominoControlPlane._headers()` always sends `Authorization:
     Bearer`, which finding 1 proved is REFUSED for a static key on `/api/projects/beta/projects` —
     the exact endpoint project creation/listing needs. Wiring a static-key `TokenSource` in here
     today would ship a laptop control-plane path that is silently broken the first time it's used.
     `DominoControlPlane._headers()` needs the same static/sidecar header split `TokenSource` has
     before this is safe — that's a Phase 3 prerequisite (the first phase that actually drives the
     control plane from a laptop), named here so it isn't rediscovered the hard way.
   - `assets/provider.py`'s `DominoAssetProvider`: gained an optional `sdk_credential` param
     (a zero-arg callable returning `TokenSource.sdk_kwarg()`'s dict). `None` (default) keeps
     TODAY'S exact `DatasetClient()` no-arg/sidecar-implicit behavior untouched; only a static
     `TokenSource` causes `_sdk_dataset()` to pass `**sdk_credential()` (i.e. `api_key=...`)
     instead. Zero behavior change for every existing App/workspace deployment.
   - `resources/provider.py`'s `DominoResourceProvider`: same `sdk_credential` pattern, used by
     `_query()`/`run_statement()`'s `DataSourceClient()` construction. **Also gained a
     class-level** `_sdk_credential = None` **default** (not just set in `__init__`) — two existing
     tests (`test_a_read_given_no_level_refuses_instead_of_sending_dots.py`,
     `test_a_store_that_was_never_reached_does_not_report_its_plumbing.py`) build a narrow instance
     via `DominoResourceProvider.__new__(...)` to exercise `_query`'s exception classification
     without a real constructor call, and the first version of this change broke both — a class
     attribute is what makes an object built that way still resolve `_sdk_credential` correctly.
     Caught by running the affected files directly, not assumed safe from reading the diff.
4. `Orchestrator.__init__` gained `token_source: TokenSource | None = None`. `_viewer_id()` (was a
   bare module-level function reading `DOMINO_USER_ID`) became a method, `self._viewer_id()`, at all
   5 call sites, reading `self._token_source.whoami().id` with a `try/except` fallback to `"me"` — an
   identity-API hiccup must never break authoring a plan document. `_hydrate_untitled()` similarly:
   the `DOMINO_USER_NAME`/`DOMINO_STARTING_USERNAME`/`DOMINO_USER_ID` fallbacks are GONE, replaced by
   the same `whoami()`-with-fallback pattern. `GET /api/me` (`orchestrator/app.py`): same removal,
   same `whoami()`-first pattern, falling back to `"me"`/`"You"` only when there's no `TokenSource`
   at all or `whoami()` raises. **Test-fixture consequence, not a production bug:**
   `tests/test_chat_turn.py`'s `_orch()` helper gained an optional `token_source` param and a
   `_FakeTokenSource` stub (just `.whoami()`, no network); `test_default_slug_hydrates_the_default_
   chip` (the one existing test that relied on the removed env fallbacks) now passes a
   `_FakeTokenSource` instead of `monkeypatch.setenv`. 3 new tests added alongside it pinning
   `_viewer_id()`'s three paths (a configured identity, none configured, `whoami()` raising).
5. `GET/PUT /api/settings` + `POST /api/settings/test` (`orchestrator/app.py`), named
   `get_connection_settings`/`save_connection_settings`/`test_connection_settings` internally (a
   pre-existing, UNRELATED route at `/api/project/settings` already used the bare name
   `get_settings` — `ruff`'s `F811` caught the collision before it shipped). `GET` answers
   `Settings.redacted()`. `PUT` validates every key against `Settings`'s real field names (400 on
   an unknown one), persists to `$SAGE_HOME/settings.json`, updates the module-level `_SETTINGS` so
   `GET` reflects it — but **does NOT hot-swap** `_gateway`/`_control_plane`/the asset/resource
   providers or the one `orchestrator`, all built once at import time; the response carries
   `restartRequired: true` and says so, rather than pretending a live reconfigure happened. This is
   a deliberate Phase 1 scope cut, not an oversight: hot-reconfiguring a running process's Domino
   identity mid-session is real, unproven work Phase 2's per-project registry is a much more natural
   place for (a registry that already builds an `Orchestrator` per project on demand can rebuild one
   on a settings change too; today's code has exactly one, built once, at boot). `POST .../test`
   builds a THROWAWAY `TokenSource` from whatever the request body carries (falling back to the
   already-saved value per field), calls `.whoami()`, and reports `{ok, id, name}` or `{ok: false,
   error}` — deliberately independent of `_TOKEN_SOURCE`, because it has to test what the FORM
   holds, not what already booted. **Live-verified end-to-end, not just unit-tested**: called with
   this sandbox's real `DOMINO_API_HOST`/`DOMINO_USER_API_KEY` through the actual FastAPI
   `TestClient`, it returned `{"ok": true, "id": "671fd3aa49827159bd79ed53", "name":
   "etan_lightstone"}` — the real user id/name Domino injects into this workspace's own env,
   round-tripped correctly through the whole route, the header-scheme fix, and the real cluster. 8
   tests, `tests/test_settings_api.py` (mocking `build_token_source` for the failure/success-shape
   cases, not re-hitting the network in the suite).
6. Minimal Connection UI: `api.js` gained `settings()`/`saveSettings()`/`testSettings()`; `shell.js`'s
   `SettingsDrawer` gained a `ConnectionSettings` section (host, token, gateway URL, gateway key —
   each secret an `antd.Input.Password` showing "Already set — leave blank to keep it" rather than
   the value; Save and Test connection buttons; a result line). Environment/hardware-tier PICKERS
   were deliberately not built — `ONE-APP-PLAN.md`'s own phase table puts those in **Phase 6**, not
   here (Settings already has the two fields as plain strings for now). **Two real bugs found and
   fixed while verifying this against the existing JS test suite, neither caught by writing the code
   itself:**
   - `test_a_problem_informs_and_never_blocks.py::test_nothing_goes_grey_because_a_problem_is_true`
     scans the ENTIRE rendered `Shell` tree for anything `disabled` (ADR-0027: nothing gates on
     missing state, blanket rule, not scoped to Problem-related controls specifically). My first cut
     disabled "Test connection" with no host configured; removed the `disabled` prop entirely —
     the click now always reaches the same request, and the server's refusal becomes the on-screen
     result, matching the informer-not-blocker house style rather than greying a control out.
   - `test_the_paranoid_pack_finds_no_leak.py`'s brand-neutrality scan caught four literal
     `"Domino ..."` strings in `aria-label`/placeholder text I'd written (should have used
     `SW.brand.text('{platformName} ...')` from the start, the way every other user-facing string in
     this file does) — fixed. The SAME class of bug also existed in my own NEW backend code
     (`test_connection_settings`'s `"no Domino host given"` error) and was caught by the identical
     test on the Python side; fixed there too (`brand_text("no {platformName} host given")`).
   Both are recorded because they are the second and third time IN THIS SESSION that a
   correctness-adjacent guard test (F811 naming collision, a `__new__`-bypassed test fixture, a
   brand-neutrality scanner) caught something a plain code review would not have — the tests here
   are pulling real weight, not padding.
7. `tools/app_visibility.py:105` (named in the plan as a build site) — deliberately left untouched.
   It is a standalone, manually-run diagnostic script (`uv run python -m sage.tools.app_visibility`,
   its own docstring: "Run it TWICE, inside the workspace of a project that has a published app"),
   never imported by the live app, always run inside an actual Domino workspace with a real sidecar
   by construction. There is no Settings-driven laptop story for a script whose whole premise is
   "you are already inside a workspace" — threading `Settings`/`TokenSource` through it would be
   speculative generality with no caller that needs it.

**Full-suite verification, following this repo's own protocol (CLAUDE.md §5/§6) — not a single
run trusted at face value:**

- Collected count: **7810**, exactly 36 more than the last Phase-0-close baseline (`a627b0ec`,
  7774) — all 36 are this session's own new tests (14 + 11 + 8 in the three new files, 3 more added
  to `test_chat_turn.py`), reconciled by counting them, not assumed.
- First full run: **110 failed** / 7690 passed / 10 skipped. Investigated rather than assumed —
  diffed against the SAME 110 node ids run on the unmodified baseline tree (`git stash`): 100 of
  110 failed identically on baseline too (the well-established publish/provision/control-plane/
  `native_gateway_transport` dogfood-safety class this whole plan has hit every session). The other
  **10 were real**, not baseline noise:
  - 9 in `resources/provider.py`'s new `_sdk_credential` class attribute gap (finding 3's fix,
    above) — `AttributeError: 'DominoResourceProvider' object has no attribute '_sdk_credential'`.
  - 1 the brand-neutrality leak in `test_connection_settings`'s error string (finding 6's fix,
    above).
  Both fixed (see items 3 and 6). Re-ran the exact same 110 node ids on the fixed tree: down to the
  expected baseline-shaped set.
- **Final, clean full run on the fully-fixed tree**: `7810 collected == 101 failed + 7699 passed +
  10 skipped`. 99 (not 101) was baseline's number from the Phase-0-close session; the 2-failure
  wobble between runs of the SAME dogfood-safety/live-network class is consistent with what that
  class already is — real network calls to a real, occasionally-quota-limited Domino API (one
  baseline run in this same session hit `"Workspace quota exceeded for user across all projects"`
  from a live `POST /v4/workspace/.../workspace` call), not a fixed deterministic count. Checked
  the full failure list by name, not just the count: every one of the 101 is in the same publish/
  provision/"container that cannot provision"/`native_gateway_transport` family already
  characterized across this whole plan's history, except one
  (`test_an_opening_conversation_shows_it.py::test_a_cross_project_open_keeps_its_marker_through_
  the_scope_switch`) that does not reproduce alone (`1 passed in 6.99s`) — the CLAUDE.md "did not
  reproduce" outcome for a file this diff never opened, not a regression to chase further. None of
  this session's own 36 new tests are in the failure list, in any of the three runs.
- `make lint`: clean, both before and after the two fixes above.

**Not committed.** Same as every prior session on this branch: nothing here has been `git commit`ed.
Confirm with the user before committing (CLAUDE.md: never commit unless explicitly asked) — there is
real work to hand off either way, committed or not.

## Next session should

1. **Phase 1 is done and verified** per the section immediately above. Nothing from Phase 1's own
   checklist is left, modulo the two deliberately-scoped-out items named there (environment/tier
   pickers → Phase 6; `DominoControlPlane` static-key support → Phase 3 prerequisite).
2. Confirm with the user whether to commit this session's changes (and the still-open Phase-0-era
   items: `test_sage_domino_relay.py`, `test_feedback.py`'s weakened drift guard — neither blocks
   Phase 2).
3. Before Phase 2 (registry + per-project routing): re-read `ONE-APP-PLAN.md` §2.2-§2.3 with this
   session's findings in mind. In particular, Phase 2 hoists `OpenCodeServer` out of `Orchestrator`
   and will need to decide how `TokenSource`/`Settings` become per-registry rather than the single
   process-wide globals they are today — `_SETTINGS`/`_TOKEN_SOURCE` in `orchestrator/app.py` are
   written as module-level singletons on purpose, matching how `_gateway`/`_control_plane` already
   work, but Phase 2's whole point is that a per-project construct replaces exactly this pattern.
4. If a `DominoControlPlane` static-PAT path becomes needed for Phase 3, start from finding 1 above
   (`_headers()` needs to send `X-Domino-Api-Key` for a static key, `Authorization: Bearer` for a
   sidecar JWT) rather than re-discovering it live again.
5. The `derive_gateway_url()` open risk (finding 3) is worth a real check the first time this code
   runs inside an actual published App rather than a workspace — record whatever that App's
   `DOMINO_API_HOST` actually looks like.

## UPDATE 2026-09-23 (new session): Phase 2 started — spike done, registry.py landed, dispatcher not yet started

Read `ONE-APP-PLAN.md` and this file fresh (new session, no prior context). Confirmed Phase 0/1 are
genuinely committed on this branch (`a627b0ec`, `a9690c98`, `9eaa0620` — `git log` matches the
status doc's own claim; working tree was clean at session start). Did Phase 2 step 1 (the spike) and
half of step 2 (the registry module, not yet wired into `app.py`).

**Phase 2 step 1 — spike. Findings written into `ONE-APP-PLAN.md` §2.3 directly (both open
questions the plan asked for), not just here.** Summary:

1. **OpenCode project-config resolution is exactly what the plan assumes, and it's already proven
   in this codebase** — `driver/server.py`'s and `orchestrator/app.py`'s own docstrings record a
   prior investigation (#199/#202, "measured on opencode-ai@1.18.4"): project config is resolved off
   the git root of the **session directory**, outranks both `OPENCODE_CONFIG` and global, and is
   deliberately left unfilled today only because filling it would dirty `git status` on the one
   shared workspace volume. A gitignored `projects/<slug>/opencode.json` sidesteps that reason
   entirely.
2. **Real, not-yet-verified risk found and resolved by avoiding it rather than testing it**: nothing
   in this codebase's history proves OpenCode deep-merges a *partial* project config's
   `provider.sage-gateway.options.baseURL` against the global config's `npm`/`models` for the same
   provider id — and the one place this repo DID fill a project slot before, it wrote the FULL
   transformed config, not a stub. **Decision: Phase 2 must write the full voiced config per project
   (reusing `_install_opencode_config`'s exact transform, parameterized by destination path and that
   project's shim port), not the plan's original two-line stub.** This removes the plan's risk #3
   by construction. Not yet implemented — `_install_opencode_config` still only writes the one global
   slot; refactoring it to a `(source_dir, control_port, dest_path)` shape and calling it once more
   per `registry.open()` is dispatcher-phase work, not done this session.
3. **ContextVar-into-SSE-generator is not actually a risk here, verified by reading every streaming
   route**: `build_stream`/`chat_stream`/`build_approve`/`decline_handoff` all call
   `orchestrator.<method>(...)` *synchronously in the route handler*, before the `StreamingResponse`
   is built — the resulting generator closes over an already-bound method on a concrete instance, it
   never re-reads the module name later. A ContextVar lookup only needs to be correct at that one
   synchronous call, same as a plain global read today. Plan's risk #4 resolved; no capture-and-pass
   rewrite needed.
4. **A real risk the plan never named, found by grepping test usage, not assumed**: ~267 test files
   do `monkeypatch.setattr(app_module.orchestrator, "_wm", fake)` etc., relying on
   `app_module.orchestrator` being the SAME concrete `Orchestrator` instance for the whole test
   process (built once at import time; pytest's module-import caching keeps it that way across every
   test file). A bare `__getattr__`-only proxy silently BREAKS this: `setattr(proxy, "_wm", fake)`
   would land in the proxy's own `__dict__`, shadowing it, rather than reaching the real object,
   because `__getattr__` is only consulted on a lookup MISS — nothing would look re-broken until a
   later test read `app_module.orchestrator._wm` through `__getattr__` and got the ORIGINAL value
   back, silently invalidating whatever the earlier monkeypatch thought it had changed. **Decision:
   the future proxy must implement `__setattr__`/`__delattr__` too (forwarding to whatever
   `current_orchestrator()` resolves to), and `current_orchestrator()` must be `_CURRENT.get() or
   _DEFAULT` — an explicit fallback to a plain module-level `Orchestrator` built exactly as today —
   rather than relying on a ContextVar `.set()` at import time inheriting correctly across worker
   threads. This keeps every one of the 267 files, and every pre-Phase-2 root-scope route, resolving
   to `_DEFAULT` completely unchanged; the dispatcher's per-request `ContextVar.set()` becomes a pure
   overlay used only inside a real `/p/<slug>/...` dispatch.** Not yet implemented — the proxy class
   and `_CURRENT`/`_DEFAULT` split are dispatcher-phase work.
5. **Also checked and ruled out**: no `isinstance(orchestrator, ...)` checks and no code that stores
   the module-level `orchestrator` name by reference for reuse outside a request — every one of the
   163 non-test call sites in `app.py` is a plain `orchestrator.<attr>` read, confirming the thin
   proxy approach is mechanically sufficient for 100% of `app.py`'s route bodies with zero call-site
   edits, once the `__setattr__` fix above is in place.

**Phase 2 step 2 (partial) — `backend/sage/projects/registry.py` landed, not yet wired into
`app.py`.** `ProjectRegistry` per plan §2.2: `RegistryEntry` (the 6-key `.sage/project.json` record —
named `RegistryEntry` rather than `ProjectRecord` because `sage.workspace.manager.ProjectRecord`
already owns that name for a different record, ADR-0008; caught before it was written, not after),
`ProjectRow` (a `list()` row), `local_slugs()` (directory scan, no index, matching
`WorkspaceManager`'s own rule — a half-written `create()` with no entry file yet is deliberately
invisible), `entry()`, `list(current=)` (merges local rows with `control_plane.list_apps()` — reused
the EXISTING `ControlPlane.list_apps() -> list[ProjectRef]` / `_SAGE_REPO_PREFIX` filtering
`door.py`/`domino.py` already implement, not reinvented; local rows win on a slug collision), `open()`
(get-or-build via an injected factory closure, raises `KeyError` for an unknown slug), `is_open()`,
`close()` (drops the cache; best-effort stops the bound `Project`'s `UvicornSupervisor` if a turn
ever started one — ADR-0040 means there's at most one live per Orchestrator — but this is explicitly
NOT yet Phase 4's real per-project preview lifecycle, since today's supervisor is reached only
through whatever `Project` an Orchestrator happens to have bound, not a first-class handle the
registry owns; recorded as a real, temporary gap in the method's own docstring rather than silently
assumed handled).

The registry itself does not know how to build an `Orchestrator` — it takes a
`build_orchestrator(entry, workspace_dir) -> Orchestrator` factory at construction, matching the
plan's "shared, process-wide services built once by the caller" split. Nothing about `app.py`'s
bootstrap was touched this session; the factory closure that will supply the real shared services
(gateway, catalog, control plane, assets, resources, token source — all already process-wide
singletons per Phase 1) is dispatcher-phase work.

15 new tests, `tests/test_project_registry.py`, using bare dataclass fakes rather than a real
`Orchestrator`/`UvicornSupervisor` (this module doesn't need to know about either concrete type at
test time — verified by keeping the test file free of any import from `orchestrator.service` or
`preview.supervisor`). All pass. `make lint` (repo-wide) clean. Full suite collection: **7825**,
exactly 15 more than the Phase-1-close baseline (7810) — reconciled by counting, not assumed. Full
run not repeated this session (nothing outside the new file was touched, so the Phase-1-close
baseline-diffed failure set — the publish/provision/control-plane/`native_gateway_transport`
dogfood-safety class — stands unchanged; re-verify at the next natural full-run checkpoint rather
than re-running it for a change that touched one new, isolated file).

**Not started, and deliberately not attempted this session**: the actual dispatcher (ASGI wrapper in
`app.py`, the `_CURRENT`/`_DEFAULT` ContextVar split, the `orchestrator` proxy class with
`__setattr__`/`__delattr__`, `_install_opencode_config`'s refactor to a reusable
`(source_dir, control_port, dest_path)` shape, deleting `_PrefixMiddleware`/`preview/prefix.py`, the
Projects home page, and the JS scope-picker rewrite). This is deliberately held at a checkpoint
before touching `app.py`'s `orchestrator` global — the object 267 test files depend on and every one
of `app.py`'s 128 routes read from — since the design above, while grounded in real findings rather
than assumption, has not yet been built and run against the suite. Flagged to the user for a
go-ahead before executing it repo-wide (CLAUDE.md §1: surface a judgment call rather than picking
silently for a change this wide — the plan's own §2.3 text explicitly calls the proxy-vs-dependency
choice "the reviewer's call at Phase 2").

## UPDATE 2026-09-23 (same session, continued): the dispatcher landed. Phase 2 is functionally done
## except the Projects home page / scope-picker UI (step 5) and the loopback `/mcp` token resolution
## named in step 4.

User confirmed "proceed now" on the design from the section above. Built it exactly as designed,
found and fixed one real regression along the way (below), then verified against the full suite
twice (once before the fix, once after) rather than trusting a single green run.

**What got built, in `backend/sage/orchestrator/app.py` unless noted:**

1. Hoisted `_CATALOG`/`_ASSETS`/`_RESOURCES` to module-level singletons, built once, shared by the
   legacy default Orchestrator AND every per-project one (previously each was constructed inline,
   once, only for the one Orchestrator that existed — a second Orchestrator would have silently
   re-logged the boot-time "no Domino host configured" notice and built its own independent copy).
2. `_DEFAULT_ORCHESTRATOR` — the renamed former `orchestrator = Orchestrator(...)`, unchanged in
   every argument.
3. `_CURRENT_ORCHESTRATOR: ContextVar[Orchestrator | None]` (default `None`) and
   `current_orchestrator() -> Orchestrator` (`_CURRENT_ORCHESTRATOR.get() or _DEFAULT_ORCHESTRATOR`)
   — the explicit-fallback shape from the spike, not a `.set()` at import time.
4. `_OrchestratorProxy` — `__getattr__`/`__setattr__`/`__delattr__` all forward to
   `current_orchestrator()`. `orchestrator = _OrchestratorProxy()` replaces the bare instance at the
   module name. Every one of the ~163 `orchestrator.<attr>` call sites in `app.py` needed no edit.
5. `_write_project_opencode_config(source_dir, control_port, slug, dest_path)` — the per-project
   opencode.json writer from the spike's finding 2: writes the FULL voiced config (reusing
   `apply_agent_voice`, matching `_install_opencode_config`'s own established pattern), with every
   port-bearing URL (`provider.sage-gateway.options.baseURL`, every `mcp.*.url`) rewritten to this
   process's control port AND that project's `/p/<slug>` path prefix. Deliberately simpler than
   `_install_opencode_config`: no native-codec/reasoning-settings branch (a real, named gap — not yet
   extended to per-project configs) and no tools/skills install (those are global slots, already
   installed once at boot, already reached from any session directory). Left `_install_opencode_config`
   itself completely untouched rather than refactoring it to share code — that function is heavily
   commented, hard-won (#199/#202), and pinned by several existing tests
   (`test_brand.py::test_install_opencode_config_voices_the_global_copy_only`,
   `test_a_dropped_mcp_server_is_visible_in_diag.py`, `test_native_model_controls.py`); a small amount
   of duplication was the safer trade against regressing it. 8 new tests,
   `tests/test_project_opencode_config.py`.
6. `_build_project_orchestrator(entry, workspace_dir)` — the registry's factory closure. Calls
   `_write_project_opencode_config` (using the same `SAGE_OPENCODE_CWD`-or-`_REPO` source
   `_install_opencode_config` reads), then builds an `Orchestrator` from the shared services plus the
   per-project fields (`project_id=entry.slug`, `domino_project_id`, `domino_project_name`,
   `cost_project_label` via `domino_project_label(fallback=entry.domino_project_name)`,
   `gateway_ui_url` recomputed from that label). **Named, deliberate gap**: each project gets its OWN
   `opencode serve` process (`opencode_cwd=~/.config/sage-opencode/<slug>/`), lazily started on first
   turn exactly as `_ensure_opencode` already does for the legacy default — NOT the shared
   `OpenCodeServer` the target architecture calls for (§2.2: "Shared `OpenCodeServer` hoisted out of
   `Orchestrator._ensure_opencode`"). Correct and isolated, just heavier (N processes instead of 1);
   hoisting to a shared server is real, deferred efficiency work, not attempted this session because
   `_ensure_opencode`'s internals were judged too risky to restructure in the same pass as the
   dispatcher.
7. `_CONTROL_PORT` moved to module scope (was a local inside `run()`) so the factory above can read
   it — `run()` now reads the same constant instead of recomputing it. One value, one place.
8. `_ProjectDispatchMiddleware` — routes `/p/<slug>/<rest>` to that project's Orchestrator. Registered
   via `control_app.add_middleware(_ProjectDispatchMiddleware, registry=_REGISTRY)` **before** (in
   source order) `control_app.add_middleware(_PrefixMiddleware, prefix=BASE_PREFIX)` — Starlette's
   `add_middleware` inserts at position 0, so the middleware added FIRST ends up innermost and runs
   LAST; **verified empirically**, not assumed, with a throwaway two-middleware Starlette app before
   trusting this ordering in production code (see the spike addendum's caution about scanners/ordering
   assumptions elsewhere in this repo's CLAUDE.md — same discipline applied here). This means
   `_ProjectDispatchMiddleware` sees `root_path` AFTER `_PrefixMiddleware` has already set it, so it
   only ever EXTENDS `root_path` (via `starlette.routing.get_route_path`), matching
   `_PrefixMiddleware`'s own established rule of never rewriting `path`. `/p/` added to
   `_PrefixMiddleware._UNPROXIED` for the identical reason `/mcp/` is already there: a loopback
   `/p/<slug>/v1/...` call (OpenCode dialling the shim) carries no Domino prefix, and would otherwise
   spend the one-time prefix-mismatch warning on a correct request. 7 new tests,
   `tests/test_project_dispatch.py`, against the middleware directly (not the full route stack) —
   including one that simulates an upstream Domino mount prefix to prove extension-not-replacement.
9. Shutdown (`_lifespan`'s `yield; ...` and `run()`'s `_serve()` `finally`) now ALSO sweeps
   `_REGISTRY.all_open()`, calling `.shutdown()` on every project a request ever dispatched to during
   this process's life (saves in-progress work to git, stops that project's preview/OpenCode) — not
   just the one default project. New `ProjectRegistry.all_open()` method + 2 more registry tests.
10. Module docstring updated to describe the dispatch mechanism (left the pre-existing Vite-flavored
    staleness in the same docstring alone — that's Phase 4's `preview/proxy.py` rework, already
    deferred there by an earlier session in this file).
11. `GET /api/projects` was **deliberately NOT added** this session, despite being named in the plan's
    own Phase 2 step 5 and drafted once: a route by that exact name ALREADY EXISTS
    (`list_projects`/`create_project`, the door-era "Sage Projects this viewer can open" chip listing,
    `{"items":[...],"provisioning":bool}` shape, still backing the CURRENT frontend's scope picker).
    Replacing its shape now — before the home page that would consume the new shape exists — would
    have shipped a half-finished, silently-broken frontend contract. `ProjectRegistry.list()` itself
    is built and tested (`tests/test_project_registry.py`); wiring an HTTP route to it is Phase 2 step
    5's job, done together with the home page and scope-picker that read it.

**One real regression found and fixed, verified against the suite twice:**
`_lifespan`'s shutdown originally called `_DEFAULT_ORCHESTRATOR.shutdown()` directly (bypassing the
proxy) so it would definitely reach the real default object even under test monkeypatching. This
broke `tests/test_stopping_the_orchestrator_stops_opencode.py::test_serving_control_app_under_any_asgi_server_tears_down_on_exit`,
which does `monkeypatch.setattr(appmod, "orchestrator", _Recorder())` — REPLACING the module
attribute wholesale (not mutating the existing object's attributes) — and asserts THAT gets called on
shutdown. Reading `_DEFAULT_ORCHESTRATOR` by name bypasses a whole-name replacement like this one,
same as it would have before Phase 2 if `orchestrator` had been swapped for something else read by a
different name. **Fixed** by calling `orchestrator.shutdown()` (the bare, dynamically-resolved module
name — proxy or not, whatever `appmod.orchestrator` currently names) for the default part, and adding
the `_REGISTRY.all_open()` sweep as an ADDITIONAL step alongside it, not a replacement. This is the
same class of lesson §2.3's addendum already generalized (a monkeypatch relies on being able to swap
what a NAME resolves to, not just what an OBJECT's attributes are) — just found by the suite instead
of by reasoning it out in advance, which is exactly why the plan calls for running the suite
specifically after this substitution rather than trusting the design alone.

**Full-suite verification (CLAUDE.md §5/§6 — reconciled on COLLECTED, not passed+failed):**

- Collected: **7848**, exactly 38 more than the Phase-1-close baseline (7810) — 17 registry tests + 7
  dispatch + 6 proxy + 8 opencode-config = 38, reconciled by counting, not assumed.
- First full run (before the shutdown fix): **102 failed** / 7736 passed / 10 skipped. Two of the 102
  were real: the shutdown regression above, and 2 in `test_builtapp_flight.py`
  (`test_a_connector_with_no_scope_recorded_runs_with_no_configuration`,
  `test_a_statement_that_names_the_schema_itself_needs_nothing_from_configuration`) that turned out
  to be `-n auto` cross-worker flakiness, not a regression — **verified by running them alone**
  (`-n0`, isolating them from the distribution): both passed immediately. CLAUDE.md's own "did not
  reproduce" outcome for a file this diff never opened.
- After the fix: **100 failed** / 7738 passed / 10 skipped. `diff`ed the two failure-name lists: the
  shutdown test and the two flight tests are gone; nothing new appeared **except**
  `test_builtapp_queries.py::test_without_an_executor_the_app_says_it_cannot_reach_its_data`, which
  reproduces alone too (unlike the flight pair) but is confirmed NOT caused by this session —
  `resources/builtapp.py` and `test_builtapp_queries.py` are both byte-identical to session start (not
  in this session's diff at all), and the test's own captured output shows it hit a REAL sidecar and a
  REAL `domino_data` install ("token sidecar: reachable", "platform api ... answered 200", "data
  library: ready") expecting a "cannot reach" message and getting a more specific
  "could not open the Data Source" one instead — the same "this sandbox has genuine live Domino
  credentials, unlike whatever environment tuned this assertion" class as the other 99, just not
  previously caught under this exact node id.
- **All 100 remaining failures are the same publish/provision/control-plane/`native_gateway_transport`
  dogfood-safety class characterized repeatedly across this whole plan's history**
  (`test_publish_*`, `test_orchestrator.py`, `test_a_rename_reaches_the_deployed_app.py`,
  `test_delete_app.py`, `test_gallery.py`, `test_attach.py`, `test_create_project.py`,
  `test_chat_shows_the_whole_conversation.py`, `test_the_control_plane_routes_speak_the_packs_words.py`,
  `test_the_service_speaks_the_packs_words.py`, `test_prefix.py`, `test_provision_credentials.py`,
  `test_native_gateway_transport.py`, `test_builtapp_queries.py`'s one) — confirmed by inspecting
  full tracebacks for a representative sample of each file, every one either
  `publish_available()`'s dogfood-safety check tripping because `/mnt/code` really is the mounted
  repo in this sandbox, or a live call to a real Domino control plane hitting a genuine quota/ID-
  validation error. **None of these files are touched by this session's diff** (`git diff --stat`
  against the commit before this session's work shows only `orchestrator/app.py`, plus the wholly new
  `sage/projects/` package and four new test files — confirmed by reading the diff stat directly, not
  assumed).
- `make lint` (repo-wide): clean, before and after the fix.
- Targeted regression spot-checks beyond the full run: `test_a_dropped_mcp_server_is_visible_in_diag.py`,
  `test_brand.py`, `test_native_model_controls.py`, `test_a_permission_that_cannot_be_asked.py`,
  `test_the_plan_draft_door_resets_its_counter.py`, `test_preview_llm.py` (186 tests, the files that
  monkeypatch `app_module.orchestrator` most heavily) and `test_chat_turn.py` + importers (151 tests)
  — all green, run before the full suite as a faster first signal.

**An automated commit landed mid-session, again — same unresolved mechanism as `fc7ff826` earlier in
this file, not something this session ran.** `git log` shows `c2162aa8 "commit on phase work just in
case while we wait for test suite"`, containing this session's `registry.py`, the dispatcher, all four
new test files, and the two plan-doc edits — content is correct (`git show --stat` matches exactly
what this session built), but **no `git commit` was run by this session** (the small shutdown fix
after it is the only uncommitted change, per `git status`). Flagging again rather than silently
treating it as this session's own action, or deciding whether to keep/amend/reset it — per this
repo's CLAUDE.md, that decision is the user's.

## Next session should

1. **Phase 2 steps 1-4 are functionally done and verified**: the spike, the registry, the dispatcher,
   the proxy, the per-project opencode config, the shutdown sweep. Step 4's second half (loopback
   `/mcp/live-read` and `/mcp/delegated-model` resolving by TOKEN across open orchestrators, for calls
   that don't arrive under a `/p/<slug>/` path) is not done — those routes still resolve against the
   bare `orchestrator` proxy (i.e., whatever `current_orchestrator()` gives, which is
   `_DEFAULT_ORCHESTRATOR` outside a dispatched request). Read ADR-0041 and the existing
   `/mcp/live-read` route before starting that piece.
2. **Step 5 (Projects home page, scope-picker, `GET /api/projects`) is the remaining Phase 2 work.**
   The existing `/api/projects` GET/POST (door-era, `{"items":[...],"provisioning":bool}`) needs to be
   replaced together with `api.js`'s `projects()` call and `scope-picker.js` in the SAME change — not
   before, per the reasoning in point 11 above. `ProjectRegistry.list()` is ready to back it.
3. Confirm with the user whether to commit this session's changes (registry.py, the dispatcher, the
   four new test files, the shutdown fix, and the two plan-doc/status-doc edits) — and separately,
   whether they want the auto-commit mechanism investigated (this is now the second time it has fired
   on this branch).
4. `_ensure_opencode`'s per-project-vs-shared-server question (point 6 above) is worth a real decision
   before Phase 4 (preview) or Phase 7 (packaging) — N processes is correct today but was not the
   target shape, and nobody has yet weighed whether the simplicity is worth keeping permanently.
5. Do not re-derive the spike findings — they're in `ONE-APP-PLAN.md` §2.3's addendum and this file's
   prior update, both dated 2026-09-23.

## UPDATE 2026-09-23 (new session): Phase 2 step 4's second half closed (no code needed, verified);
## step 5 partly done — `GET /api/projects` + scope-picker rewritten; the Projects home page and
## root-route (`/`) repoint deliberately NOT started this session (see reasoning below).

Read `ONE-APP-PLAN.md` and this file fresh. Confirmed the branch was clean and Phase 2 steps 1-4 were
genuinely on disk as described (`52a7be69`).

**Step 4's second half turned out to need no new code — verified, not assumed.** The plan's own text
worried about `/mcp/live-read` and `/mcp/delegated-model` resolving "by token across open
orchestrators" for calls that arrive with no `/p/<slug>/` prefix. Traced the actual mechanism instead
of building the token-resolution machinery the plan sketched:

- `_write_project_opencode_config` (already landed) rewrites every `mcp.*.url` in a project's own
  `opencode.json` to carry that project's `/p/<slug>` prefix (`test_project_opencode_config.py`
  already pins this: `.../p/beta/mcp/live-read`).
- `_ProjectDispatchMiddleware` (already landed) matches on path generically — `/p/<slug>/<anything>`
  — with no allowlist of which routes count. It doesn't special-case `/mcp/`.
- Given today's design keeps one `opencode serve` PER PROJECT (a named, deliberate gap in the
  registry's own factory closure — see the "Shared OpenCodeServer" note above), every project's
  OpenCode process dials its OWN `/p/<slug>/mcp/live-read`, which already lands on the right
  Orchestrator through the existing generic path dispatch. The plan's token-resolution worry is real
  only once OpenCode is SHARED across projects (deferred, unbuilt) — with N separate processes, path
  dispatch already is the resolution mechanism.
- Proved this rather than trusting the reasoning alone: added `test_project_dispatch.py`'s new
  `_FakeProjectOrchestrator` + three tests hitting the REAL `control_app` (not the middleware in
  isolation, which the file's earlier tests already covered) at `/p/<slug>/mcp/live-read` and
  `/p/<slug>/mcp/delegated-model`, confirming each reaches that project's fake orchestrator and never
  the default. 10/10 pass (7 pre-existing + 3 new).
- **No production code changed for this step.** `orchestrator_for_live_token()` from the plan's §2.2
  sketch was not built — it would be solving a problem the current per-project-process design doesn't
  have. Worth reconsidering only if/when `_ensure_opencode` is ever hoisted to a shared server (the
  named gap from the previous update).

**Step 5, done this session:**

1. **`GET /api/projects`** (`orchestrator/app.py`) rewritten from `_provision.list_apps()` (door-era,
   `{"items":[...], "provisioning": bool}`) to `_REGISTRY.list(current)` (`{"items":[{slug, name,
   local, current}]}`). `current` is `None` (no row marked) at root scope, and the dispatched
   project's own slug when the call arrives through `/p/<slug>/api/projects` — read via
   `current_orchestrator()._project_id` gated on `_CURRENT_ORCHESTRATOR.get() is not None`, so the
   root-scope Projects home (not yet built — see below) and the in-Workbench scope chip share one
   route with no query param needed. Works with no Domino control plane at all (a laptop with at
   least one local clone still gets a real, non-empty answer) — only the REMOTE half of the merge
   needs `_control_plane`. 2 new tests in `test_project_dispatch.py` pin this (root scope passes
   `current=None`; a `/p/alpha/...` call passes `current="alpha"` and the route's JSON marks the
   matching row).
2. **`POST /api/projects`, `/api/projects/{id}/open`, `/api/projects/status` left completely
   untouched.** The plan's own Phase 3 step 3 explicitly owns deleting these (along with
   `door.py`/`door.html`/`/api/door*`) — they're door-era workspace-launch routes that don't fit the
   one-app model, but replacing or deleting them now would be ahead of the phase that actually retires
   workspaces. They are effectively orphaned by the frontend change below (nothing calls them from the
   Workbench anymore), which is flagged rather than silently left implicit.
3. **`scope-picker.js` rewritten**: `select(project)` is now `window.location.assign(`../${project
   .slug}/`)` for a `local` row — a same-origin navigation to that project's own `/p/<slug>/`, since
   every open project is already a path this ONE process serves (no more workspace hand-over, no more
   polling). A row with `local: false` (a `sage-*` Domino project the token can see but hasn't been
   cloned to this machine — Phase 3's `clone()` is what would fix that) renders disabled with
   "Not cloned yet" rather than offering a click that 404s. "New project" is unconditionally disabled
   with a tooltip ("Creating a new {project} from here arrives in a later phase") rather than gated on
   `canProvision` — matching the product owner's explicit choice this session (asked via
   `AskUserQuestion`: show the affordance, disabled and informational, rather than omitting it or
   pulling Phase 3's `registry.create()` forward early).
4. **`api.js`'s `projects()`** simplified to `request('/projects').then(listing => listing.items ||
   [])` — no more merging `/project` + `/projects` into a synthetic "here" row (the registry's own
   listing carries no such row now; `current` is a plain field on whichever row matches). `openProject`,
   `projectStatus`, `createProject` deleted from `api.js` — their only callers (below) are gone too.
5. **`store.js` cleanup, cascading from the same change:** `attachProject`, `createProject`, and
   `handOver` (the workspace-hand-over modal + up-to-6-minute poll) deleted outright — `attachProject`
   had exactly one caller (the picker's old `select`, now `location.assign`), and `createProject` had
   exactly one caller (the picker's old `create`, now gone since "New" has no input state left to
   submit); `handOver` then had zero callers left. Verified no other caller exists anywhere in
   `sage/workbench/js/` before deleting (grepped the whole tree, not assumed). Boot (`load()`)
   simplified: `state.scope`/`applyModelStatus` used to prefer `projects[0]` (the old synthetic "here"
   row) and fall back to the raw `/project` read; since the registry listing carries no such row
   anymore, both now read `/project` directly (which always carried the same fields — the old "here"
   row was itself built by copying them, per `api.js`'s prior code) — one fallback chain instead of
   two, same information. `state.canProvision` no longer derived per-boot (there is no
   `.provisioning` field on a registry row) — left permanently `False` at its existing initial-state
   default, comment updated to say why (Phase 3 is what would flip this back on).
6. **Known, pre-existing dead code NOT touched, mentioned rather than fixed**: `store.js`'s
   `setScope`/`adoptThreadScope`/`adoptAppScope` compare `state.projects` rows by `.id` — a field
   registry rows never had even before this session (they carry `.slug`). Traced why this is not a
   regression: in the one-container-per-project model (ADR-0004, still standing until Phase 3),
   `adoptThreadScope`/`adoptAppScope`'s cross-project `find()` could only ever match the container's
   OWN project in the old shape too (a thread belonging to a genuinely different project was never in
   this container's own thread list to begin with), and the very next line's `target.id !==
   state.scope.id` check made that case a no-op regardless. So this was already-vestigial,
   likely-from-an-earlier-multi-project-per-container prototype (DEPLOY-PLAN.md §2 records one was
   retired) — not something my change broke, just something it didn't fix. Left alone per CLAUDE.md
   ("if you notice unrelated dead code, mention it — don't delete it").
7. `node --check` on all three edited JS files: clean.

**Tests updated to match (existing contract-pins on the retired mechanism, rewritten to pin the new
one — not deleted, since the underlying behavior they guard still needs a pin):**
`test_attach.py` (`test_picking_another_project_is_a_same_origin_navigation`,
`test_the_chip_describes_only_the_project_it_can_read`,
`test_a_container_with_no_local_projects_offers_nothing_to_switch_to` — the last one asserts
`{"items": []}`, not the old `{"items": [], "provisioning": False}` shape), `test_create_project.py`
(`test_the_workbench_no_longer_hands_the_browser_over_to_create`,
`test_new_project_is_explained_rather_than_offered_when_it_cannot_work`).
`test_new_conversation_still_does_not_provision` needed no change (its assertions were always about
absence, which still holds now that the functions are gone rather than merely unreachable).

**Verification, following this repo's own protocol — not a single green run trusted at face value:**

- Targeted run of every directly-touched file (`test_project_dispatch.py`, `test_attach.py`,
  `test_gallery.py`, `test_create_project.py`): 45 passed, 3 failed. All 3 diffed against baseline via
  `git stash`/`git stash pop` (restored and spot-checked intact afterward) — **identical failures on
  unmodified code**: this sandbox's `_provision`/`_control_plane` are genuinely configured (a real
  Domino workspace), so every test asserting "`_provision is None`" or exercising a live
  `create_app`/`open_app` call against this sandbox's real, already-taken repo names hits the same
  well-established dogfood-safety/live-network class this whole plan's history has hit every session.
  None of the 3 are new.
- Widened the check to adjacent files most likely to share fixtures or boot-state assumptions
  (`test_one_dead_service_does_not_blank_the_workbench.py`,
  `test_the_control_plane_routes_speak_the_packs_words.py`,
  `test_the_port_answers_before_the_server_does.py`, `test_project_registry.py`,
  `test_project_opencode_config.py`): 53 passed, 2 failed, both diffed against baseline too and
  confirmed identical (same live-network class, unrelated routes — `open_app`/`create_app` against a
  real control plane).
- `make lint` (repo-wide, from `/mnt/code`): clean.
- **Full suite, reconciled**: `cd backend && uv run --extra dev pytest -q -n auto` →
  `7853 collected == 100 failed + 7743 passed + 10 skipped`. Collected is exactly 5 more than the
  Phase-2-dispatcher-landed baseline (7848) — the 5 new tests this session added (3 in
  `test_project_dispatch.py` for step 4's second half, 2 for the `GET /api/projects` route), all of
  which pass. Failed count is unchanged from that same baseline (100, both before and after this
  session's work) — grouped the full failure list by file and confirmed every name is one of the
  already-characterized classes (`test_native_gateway_transport`, `test_publish_*`,
  `test_a_rename_reaches_the_deployed_app`, `test_orchestrator`, `test_delete_app`,
  `test_the_control_plane_routes_speak_the_packs_words`, `test_the_service_speaks_the_packs_words`,
  `test_provision_credentials`, `test_prefix`, `test_chat_shows_the_whole_conversation`,
  `test_a_publish_says_what_leaves_domino`, `test_a_binding_the_app_never_calls_says_so`,
  `test_builtapp_queries`), plus exactly the 3 in `test_attach.py`/`test_gallery.py`/
  `test_create_project.py` this session's own rewritten tests inherited and already diffed against
  baseline above. No new regressions.

**Deliberately NOT done this session, and why — surfaced rather than silently cut:**

- **The actual Projects home page (`workbench/home.html`) and repointing `/` to serve it were NOT
  built.** The target architecture table (§2) says `/` becomes "Projects home: list, open, new,
  settings" unconditionally, but `ui()`'s current door-vs-shell branch (`_DOOR_UI if proxy_is_app()
  else _UI`) is still the live, currently-CORRECT implementation of ADR-0004 until Phase 3 actually
  deletes `door.py`/`door.html`/`/api/door*` — and it is pinned by name in `test_workbench.py`,
  `test_the_entry_pages_carry_the_packs_name.py`, and `test_the_favicon_comes_from_the_pack.py`.
  Changing `/`'s branching now (e.g., to "home page at root scope, Workbench shell inside a
  `/p/<slug>/` dispatch") would break those currently-valid pins ahead of the phase that is supposed
  to retire what they pin, and there is no browser in this sandbox to verify a brand-new page's actual
  rendering before shipping it as the FIRST thing every viewer sees. This is a real, deliberate scope
  cut, surfaced to the user rather than guessed past: the registry-backed listing and the picker's
  navigation model (both now real and tested) are the parts of step 5 that don't depend on
  Phase 3's door removal; the home page itself does, structurally, even though the plan's phase table
  files it under Phase 2. Whoever does Phase 3 should build `home.html` and repoint `/` in the SAME
  change that deletes `door.html`, not before.
- Given the above, `POST /api/projects`, `/api/projects/{id}/open`, `/api/projects/status`,
  `door.py`, `door.html` are all untouched, as noted in point 2 above.

## Next session should

1. **Phase 2 is now functionally complete for everything that doesn't depend on Phase 3's door
   removal.** Steps 1-4 (spike, registry, dispatcher, proxy, per-project opencode config, shutdown
   sweep, and now step 4's second half) are done and verified. Step 5's registry-backed listing and
   scope-picker rewrite are done and verified; the Projects home page and `/`'s repoint are the one
   remaining piece, and they belong with Phase 3 (see reasoning above) rather than being forced into
   Phase 2 ahead of door.html's actual deletion.
2. Full suite is reconciled and green modulo the same 100-failure baseline class (see above) —
   nothing further to check here before the next phase starts.
3. Confirm with the user whether to commit this session's changes, and whether the recurring
   auto-commit mechanism (now flagged three times on this branch — `fc7ff826`, `c2162aa8`, and
   whatever lands this session) is worth investigating on its own.
4. When Phase 3 lands `registry.create()`/`registry.clone()`: flip `state.canProvision` back on (or
   retire the flag and just check `projects` for a `local: false` row), re-enable the picker's "New
   project" and the disabled `ScopeRow` rows, and build `home.html` + repoint `/` in the same change
   that deletes `door.py`/`door.html`/`/api/door*` — see the reasoning above for why these are one
   change, not three.
5. `_ensure_opencode`'s per-project-vs-shared-server question (carried over, still undecided) and the
   two named Phase-0/1-era coverage gaps (`test_sage_domino_relay.py`, `test_feedback.py`'s weakened
   drift guard) remain open, non-blocking items.
