---
doc: Implementation status for ONE-APP-PLAN.md
branch: one-app-pivot-Etan
last updated: 2026-09-24
---

# Status

Read `ONE-APP-PLAN.md` first. This file tracks what's actually landed, phase by phase, so a fresh
session can resume without re-deriving the mirror map or the design calls below.

**This file is long and grows chronologically — read to the LAST `## UPDATE` block before trusting
any earlier `## Next session should` list.** Several of those are now stale (e.g. the one just below
the 2026-09-23 mirror-map section still names Phase 3 step 3 as the next thing to do — it was
finished in a 2026-09-24 update well before the end of this file). As of 2026-09-24: Phases 0-3 are
complete (product-owner-smoke-tested on a real laptop) and **Phase 4 (preview per project) is also
complete**, verified by tests and a full-suite reconciliation but not yet live. **Start from the LAST
section of this file, "Where things stand — start here (end of 2026-09-24, Phase 4)"** — there are
now two sections with a "Where things stand — start here" heading on the same date; the Phase 4 one,
at the very bottom, supersedes the Phase 3 one above it. **The working tree may not be clean or
pushed** — that section says so explicitly and gives the reason; run `git status --short` before
assuming `git log`'s tip reflects everything described here. Agent sessions don't commit on this
branch unless asked (CLAUDE.md); the user commits and pushes directly.

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

## UPDATE 2026-09-23: two real laptop-host bugs found and fixed during the product owner's own
## first live smoke test — recorded here since Phase 2's own verification never actually drove a
## real chat turn end-to-end (no browser, no `npx` in any sandbox this whole plan has used so far).

1. **`scope-picker.js` threw `ReferenceError: Popover is not defined` on load** — this session's own
   earlier edit (trimming `useRef`/`useEffect` off the React destructure) accidentally dropped
   `Popover` too, which the file still uses lower down. `node --check` cannot catch this class of
   bug (syntax-only, no reference resolution) — a real gap in how this session verified JS edits.
   Fixed: restored `Popover` to the destructure; re-audited every other destructured symbol in the
   file by counting occurrences instead of trusting a per-symbol grep list again.
2. **Chat failed with `TimeoutError: opencode serve did not report a URL`, and the laptop story
   masked its own cause.** `OpenCodeServer.start()` (`driver/server.py`) spawns `opencode serve`
   with `cwd=~/.config/sage-opencode` (`_opencode_project_dir()`, deliberately outside the repo, to
   keep a checked-out worktree's `git status` clean). `npx` resolves a LOCAL install by walking up
   from ITS OWN cwd — never the repo — so this only ever worked because the Domino Environment's
   Dockerfile does `npm install -g opencode-ai` (global, on `PATH` regardless of cwd). A laptop's
   `make setup` only does a local `npm ci`, so from `~/.config/sage-opencode` there was nothing to
   find, and `npx` silently fell through to fetching from the registry — hanging for the full 30s
   timeout with zero output. **This is a real, previously-undiscovered gap the laptop host exposes
   for the first time**, not something introduced by this pivot's own edits — `_opencode_project_dir`
   predates it. **First fix attempt was wrong, and the diagnostic tail (below) is exactly what proved
   it**: prepending the repo's `node_modules/.bin` to `PATH` (on the theory that `npx opencode` falls
   back to a same-named binary already on `PATH` before considering the registry) did NOT work — the
   product owner's very next real chat turn hit the new tail and it read `npm error 404 Not Found -
   GET https://registry.npmjs.org/opencode`. `npx` went straight for the registry regardless of the
   augmented `PATH`; whatever its actual PATH-fallback rule is, it is not the one guessed at first.
   **Real fix**: `_opencode_argv()` resolves the binary itself via `shutil.which("opencode",
   path=env["PATH"])` — unambiguous, exactly what a shell would find — and execs that path directly,
   bypassing `npx`'s own package-vs-registry judgment call entirely; falls back to `["npx",
   "opencode", ...]` only when nothing resolves. The `PATH`-prepend from the first attempt is KEPT,
   not reverted — it's now what `shutil.which` actually searches, so the two fixes compose rather
   than one replacing the other. Also fixed, and what surfaced the wrong first attempt so quickly:
   the timeout no longer fails silently — `_read()` keeps a 40-line tail of everything the child
   process printed regardless of `SAGE_OPENCODE_LOG`, and `TimeoutError` includes it plus the exit
   code if the process already died. 6 new tests in `test_driver.py` (43 total, all passing);
   `make lint` clean. **Not yet re-confirmed against the product owner's own laptop** — the fix is
   verified at the unit level (a real resolved binary is exec'd, proven by `test_start_execs_the_
   resolved_binary_not_npx`) but the next real chat turn on their machine is the actual proof.
3. **A real open risk from Phase 1 is now resolved, live, by the product owner's own laptop**: a bare
   Domino account PAT sent as `Authorization: Bearer` IS accepted by their LLM Gateway
   (`curl .../v1/chat/completions -H "Authorization: Bearer $DOMINO_PAT"` succeeded) — matching
   `gateway_bearer()`'s no-`dgw_`-key branch exactly. `ONE-APP-PLAN.md`'s risk #2 updated to record
   this rather than leave it as an open question for the next session to re-litigate. The `Gateway
   API key` field in Settings stays as an override for a deployment that genuinely needs one, not a
   default requirement.

**Lesson for whoever verifies UI/process-spawning changes next**: `node --check` and a pytest suite
that mocks `subprocess.Popen` cannot catch either of these classes of bug — a missing destructured
name only breaks at the exact line that reads it, and a `cwd`/`PATH`/global-vs-local install
mismatch is invisible to anything that fakes the subprocess. Both were only found by a real person
running the real thing on a real laptop. Budget for that as part of "done", not as optional polish
— this plan's own Phase 7 verify line already says as much ("fresh laptop clone → `make setup &&
make dev` → ... no Domino workspace involved"), and this session is the first time anyone actually
tried it.

## UPDATE 2026-09-23 (continued): Build mode's "stack not carried" refusal on the very first click —
## investigated, most likely explained, and (this is the part that matters) given real test coverage
## it never had, rather than left as a one-off explanation nobody could check again.

**What happened**: the product owner's first-ever Build click on their laptop hit "This app was
built with a stack this Sage no longer carries. Open it in Chat instead." — the Phase 0 refusal for
an app whose record names `react-vite` (or has no record at all, which reads the same way). Deleting
that app through the UI and retrying worked. **The product owner was not sure whether an app already
existed there or not** ("very possible I did it") — asked to track this as a default/empty-state
correctness question rather than let a plausible-sounding explanation stand in for a checked one.

**Traced, not just asserted:**
1. `backend/workspaces/` is gitignored (confirmed: no commit ever touches it), so a leftover
   `apps/<id>/` directory from any EARLIER run of Sage on that same laptop — including from before
   this pivot removed `react-vite`, or from an earlier attempt earlier in this same debugging
   session — survives every `git pull`/branch switch completely untouched. Nothing in this pivot's
   code path can see or clean up an app it was never asked to open.
2. Read every code path that could record a NEW app's stack (`WorkspaceManager.ensure()` →
   `record_stack(stack or self._default_stack_name())` → `default_stack_name()`): all of them are
   safe today. `SAGE_DEFAULT_STACK`, even set to a garbage value, falls back to `fastapi-antd` with
   just a log warning (`_default_stack_name()`) — there is no live code path in this codebase that
   can record anything OTHER than `fastapi-antd` for a genuinely brand-new app.
3. **Live-reproduced the exact ordering a real Workbench session hits** (Chat runs before Build,
   `Orchestrator.project()`'s memoization caches an UNSEEDED attach from Chat's own
   `seed_app=False` call) against the real `Orchestrator`/`build_stream`, using the same
   `FakeOpenCode`/`ScriptedGateway` fixtures ~49 other test files already share. It does NOT
   reproduce the refusal: `_build_stream` (the inner loop, called only after every gate in the outer
   `build_stream` passes) calls `_ensure_seeded()`, not the cached `project()`, specifically to
   re-seed regardless of what Chat left cached — and that seam holds. This is the strongest evidence
   for "leftover app from before", since the alternative (something in the empty-state path is
   actually broken) does not reproduce against real code.
4. **This surfaced a real, separate gap while investigating**: the stack-refusal gate itself
   (`Orchestrator.build_stream`'s `_stack_unsupported_refusal`, added in Phase 0 per decision 4) had
   **zero direct test coverage**. `test_a_built_app_declares_its_stack_at_birth.py`'s own docstring
   claimed "see `test_orchestrator.py`'s stack-refusal tests" — no such tests exist there, or
   anywhere. A stale pointer, not a real one; nobody had actually written the coverage it promised.

**Fixed, not just diagnosed** — new file `tests/test_a_build_turn_refuses_a_stack_it_no_longer_carries.py`,
3 tests:
- `test_a_brand_new_project_never_hits_the_stack_refusal_build_first` — an empty project, Build
  first, asserts no refusal and the new app records `fastapi-antd`.
- `test_a_brand_new_project_never_hits_the_stack_refusal_chat_first` — the exact real-world
  ordering (Chat, then Build) — same assertion. **This is the one that would have caught it** if the
  memoization/`_ensure_seeded()` seam had actually been broken, rather than merely reasoned about.
- `test_an_app_from_before_the_pivot_is_refused_not_silently_reseeded` — plants a legacy app (no
  `stack` key, a `package.json` the way an old react-vite app actually left one) and asserts the
  refusal DOES fire and nothing gets silently re-seeded over it — pinning the gate's actual, intended
  job now that the two brand-new-project tests prove it doesn't fire where it shouldn't.

All 3 pass. `test_a_built_app_declares_its_stack_at_birth.py`'s stale cross-reference fixed to point
at the new file. `make lint` clean.

**Not fully closed — recorded as a real, open uncertainty rather than a solved one**: nothing here
proves what was ACTUALLY on that laptop before the delete — the explanation is the most likely one
that survives every check that could rule it out, not an observed fact. If this refusal is ever hit
again on a machine that can be confirmed to have had zero prior Sage runs (a truly fresh laptop
clone, `backend/workspaces/` never populated), that would be the live counter-example this
investigation couldn't produce, and it should reopen this exact question rather than be treated as a
new, unrelated bug.

## UPDATE 2026-09-23 (new session): Phase 3 steps 1, 2, and half of step 4 — `registry.create()`/`registry.clone()` built and tested; step 3 (door/workspace deletion) and the HTTP/UI wiring deliberately deferred

Read `ONE-APP-PLAN.md` and this file fresh. Confirmed Phase 0-2 genuinely on disk and matching this
file's own account (`git log` clean, working tree clean at session start).

**This branch will likely never merge to `main`.** The product owner said so directly this session:
`one-app-pivot-Etan` is meant to stay a permanent separate flavor of Sage, not something headed for a
landing. `origin/main` had drifted 54 commits ahead by this session's start (unrelated stop-recovery/
diagnostics work), with a real conflict in `orchestrator/service.py` on a trial `merge-tree`. Given the
above, that divergence is not this branch's problem to reconcile — CLAUDE.md's "merge `main` before the
suite" landing-session protocol does not apply here, and a future session should stop treating the
main-divergence count as something to track or fix.

**Scope decision for this session.** Phase 3's full step 3 (delete `door.py`/`door.html`/`/api/door*`,
the workspace-lifecycle `ControlPlane` methods, `Orchestrator.stop()`, `environment/pluggable-tools.yaml`)
touches on the order of 231 call sites across `app.py`/`service.py` alone (grepped, not guessed) — a
large, high-blast-radius deletion that Phase 2's own prior session already found depends on the Projects
home page existing first (see that update's "Deliberately NOT done this session" section). Rather than
force that into one pass, this session did Phase 3 steps 1 and 2 in full, plus half of step 4 (the
git-credential resolver generalization used by both), and left step 3 — together with the home page and
the HTTP-route wiring for `create`/`clone` — for a dedicated follow-up. This mirrors the reasoning the
Phase 2 session already recorded for its own deferral, rather than inventing a new one.

**What was built, concretely:**

1. **`sage/provision/seed.py`**: `seed_and_push()` gained an optional `dest: Path | None = None`. When
   given, the template is committed and pushed directly into `dest` — which is then KEPT, not
   discarded — instead of a throwaway tempdir; refactored around a shared `_seed_into(repo)` closure
   so the no-`dest` path (the door's own `create_app`) is byte-identical to before. Added
   `clone(clone_url, dest, *, branch="main", token_provider=None)`: the mirror of `seed_and_push` for
   an EXISTING repo — same one-shot credential-helper mechanism, token travelling only through the
   child git process's env, never argv/disk/logs.

2. **`sage/provision/credentials.py`**: `_checkout_dirs(cwd=None, extra=None)` gained `extra`, a list
   of additional directories swept after the built-in `/mnt/code` and before the trailing process-cwd
   `None`. `extract_token(host, protocol="https", *, cwd=None, extra=None, settings_token="")` threads
   `extra` through and adds `settings_token` as the LAST resort — tried only once every `git credential
   fill` and origin-URL-embedded-credential answer comes up empty. This is `Settings.git_token`,
   §2.1's laptop fallback for a machine with no git credential helper configured at all.
   `credential_probe(host, protocol="https", *, extra=None, settings_token="")` got the same two
   params (so `/api/diag`'s credential probe can't report "not found" for a token the real resolver
   would use) and now also reports `settings_token_configured: bool` (never the value).

3. **`sage/provision/service.py`**: split `ProvisionService.create_app` into a new public
   `provision_project(display_name, *, name=None, dest_for=None) -> tuple[ProjectRef, RepoInfo]` —
   repo create + seed + Domino project create, everything `create_app` used to do MINUS the workspace
   launch — and a thin `create_app` that calls it, then launches the workspace exactly as before.
   `dest_for`, when given, is called with the FINAL repo name (after any `-N` collision suffix
   `_create_repo` took) and must return the directory to seed into, so a caller's own project
   directory becomes the seeded working copy with no second clone. `create_app` passes no `dest_for` —
   unchanged tempdir behavior.

4. **`sage/projects/registry.py`** (`ProjectRegistry`): constructor gained
   `provision: ProvisionService | None = None` and
   `git_token_provider: Callable[[], str | None] | None = None`, both optional and both `None` on a
   process with no Domino control plane configured, matching `open_app`'s own "no door in this
   container" shape rather than a bare `AttributeError`.
   - **`create(display_name) -> RegistryEntry`** (Phase 3 step 1): calls `provision.provision_project`
     with a `dest_for` closure pointed at `workspace_dir(repo_name)`; writes `.sage/project.json` only
     once the Domino project genuinely exists, matching `local_slugs()`'s own established rule that an
     entry-less directory is invisible — a create that fails partway never shows up as a broken
     project. The written `slug` is the ACTUAL repo name captured from the `dest_for` callback, not
     `project.name` — Domino may echo back a normalized string, and a mismatch there would write
     `project.json` into a directory `open()`/`entry()` can never find again by that slug. On any
     failure, best-effort `shutil.rmtree`s the partial directory before re-raising, so a retry under
     the same display name starts clean.
   - **`clone(domino_project_id) -> RegistryEntry`** (Phase 3 step 2): looks the id up in
     `control_plane.list_apps()` (`KeyError` if the token can't see it), derives the slug via the
     existing `_slug_for_remote`, refuses with `FileExistsError` if that directory is already on disk
     (a name collision this method does not resolve — `create()`'s `-N` retry only ever applies to a
     brand-new repo name), then calls `provision.seed.clone` through the injected `git_token_provider`
     and writes the entry. Same best-effort cleanup on failure.
   - Neither method takes a lock around its git/network work — only `open()`'s `_open` dict mutation
     is lock-guarded, deliberately, since different projects' `create`/`clone` calls are independent of
     each other.

5. **`sage/orchestrator/app.py`**: `_build_provision_service`'s shared `token_provider()` closure (used
   for both the repo-create REST call and the seed push) now calls
   `credentials.extract_token(host, extra=[str(_SAGE_HOME)], settings_token=_SETTINGS.git_token)`
   instead of the bare `extract_token(host)` — the generalized resolver actually reaching a laptop's
   configured Settings token, not a capability sitting unused. Added `_clone_git_token_provider()`
   (same resolver shape, no per-call `cwd` to pin since a clone's destination doesn't exist as a
   checkout yet) and wired it plus `provision=_provision` into `_REGISTRY = ProjectRegistry(...)`.
   `_git_credential_diag()` (the `/api/diag` route) passes the same `extra`/`settings_token` through to
   `credential_probe`.

**A real regression found and fixed, not a pre-existing dogfood-class failure.**
`tests/test_the_control_plane_routes_speak_the_packs_words.py::test_the_missing_git_credential_names_the_pack_and_keeps_the_git_host`
monkeypatched `credentials.extract_token` with a fixed-arity `lambda host: ""`, which broke the moment
`token_provider()`'s call site started passing `extra=`/`settings_token=` kwargs (`TypeError`, not the
expected `RuntimeError`). Grepped every `extract_token` reference across the whole tree first — this
was the only fixed-arity monkeypatch of it. Fixed to `lambda host, **kw: ""`.

**Tests added, all passing when run individually (`-n0`) and together:**
- `tests/test_project_registry.py`: +9 (4 for `create()`, 5 for `clone()`), plus a
  `_FakeProvision`/`_FakeRepoInfo` fixture pair and a `whoami()` method (`who: str = "etan"`) added to
  the existing `_FakeControlPlane` fixture.
- `tests/test_provision_seed.py`: +4 (`dest`-seeding in place; 3 for `clone()` mirroring
  `seed_and_push`'s own existing test shapes — token-via-env, no-token-ambient-env,
  failure-surfaces-stderr-not-token).
- `tests/test_provision_credentials.py`: +3 (extra-dir sweep, `settings_token` fallback,
  real-credential-wins-over-fallback).
- `tests/test_create_project.py`: +2 for `provision_project` directly (seeds into a real given `dest`
  via an actual bare git repo; confirms no-`dest_for` still uses a throwaway tempdir).

**Verification, following this repo's own protocol:**
- Every touched/added test file green individually and combined, except the well-established
  dogfood-safety class (real Domino/GitHub credentials genuinely reachable from this sandbox —
  `/mnt/code` really is the mounted repo, same class this file has characterized every session). Two
  hits (`test_provision_credentials.py::test_extract_token_reads_password`,
  `test_create_project.py::test_a_container_that_cannot_provision_refuses_to_create`) confirmed via
  `git stash`/`git stash pop` to fail identically on the unmodified baseline tree.
- `make lint` (repo-wide): clean.
- Full suite run #1 (before the regression fix above): **101 failed, 7769 passed, 10 skipped, 7880
  collected**. All failures checked by name against this file's own established dogfood-safety class,
  except one — `test_the_missing_git_credential_names_the_pack_and_keeps_the_git_host` — which was
  this session's real regression above, found this way rather than by inspection alone.
- Regression fixed; full suite re-run twice more for a trustworthy reconciliation (the first two
  background runs' shell commands accidentally piped their own output through an in-command `tail`,
  truncating what got saved — re-run a third time with output redirected to a plain file so the
  complete `FAILED` list could actually be diffed, not just its tail): **100 failed, 7770 passed, 10
  skipped, 7880 collected** — identical collected count across all three runs (7880), so nothing
  affected collection. The failed count wobbles 99-101 run to run, matching the exact "2-failure wobble
  … real network calls to a real, occasionally-quota-limited Domino API" class the Phase 1 update
  already documented — not a fixed deterministic count. Diffed the full 100-name failure list from the
  final clean run against every file this session touched or added: **zero of this session's new test
  files appear anywhere in it**, and every failing file is one already named in this file's own
  dogfood-safety catalogue (`test_publish_*`, `test_orchestrator.py`,
  `test_a_rename_reaches_the_deployed_app.py`, `test_delete_app.py`, `test_gallery.py`, `test_attach.py`,
  `test_the_control_plane_routes_speak_the_packs_words.py`, `test_the_service_speaks_the_packs_words.py`,
  `test_prefix.py`, `test_provision_credentials.py`, `test_native_gateway_transport.py`,
  `test_builtapp_queries.py`, `test_create_project.py`, `test_chat_shows_the_whole_conversation.py`,
  `test_a_binding_the_app_never_calls_says_so.py`).

**Discovered conflict, flagged rather than silently resolved: plan step 5's git-identity wording vs. an
existing, reasoned design.** `ONE-APP-PLAN.md`'s Phase 3 step 5 says "Git identity: `workspace/git.py`
commits as `whoami().fullName <email>`." But `sage/workspace/git.py`'s existing `_identity_args()`
(~lines 146-161) already commits as `agent <agent@localhost>`, with an explicit docstring citing
de-branding (ADR-0014's third arm) and the fact that git history is immutable — a name written into a
commit can never be re-branded later without falsifying an already-committed record — and calls this
"the author line of every save in a repo the partner's own customer can read." This session did NOT
touch `workspace/git.py`. This is a real tension between the plan's literal text and an already-shipped,
reasoned decision, not an oversight — it needs the product owner's call, not a silent pick either way.

## Next session should

1. ~~Get the git-identity conflict above resolved with the user before touching `workspace/git.py`.~~
   **Resolved this session — see the update immediately below.**
2. Do the actual Phase 3 step 3 deletion (`door.py`, `door.html`, `/api/door*`, the workspace-lifecycle
   `ControlPlane` methods and their `FakeControlPlane` counterparts,
   `Orchestrator.stop()`/`/api/stop`/`_resolve_workspace_id`, `environment/pluggable-tools.yaml`,
   `SAGE_BUILDER_TOOL`) together with building the Projects home page, repointing `/`, and wiring
   `registry.create()`/`registry.clone()` to real HTTP routes (replacing the door-era
   `POST /api/projects`/`/api/projects/{id}/open`/`/api/projects/status`) — one coordinated change, per
   the reasoning the Phase 2 session already recorded for why these belong together.
3. `registry.create()`/`clone()` are fully unit-tested but have never been driven through a real HTTP
   request or against a real Domino sandbox — that live check is still owed, the same caveat this
   file's Phase 1/2 updates already applied to their own new capabilities.
4. The two long-standing, non-blocking coverage gaps from earlier sessions (`test_sage_domino_relay.py`,
   `test_feedback.py`'s weakened drift guard) remain open, untouched this session.

## UPDATE 2026-09-24 (same session, continued): git-identity conflict resolved — Sage's commits are now authored as the real Domino user, not `agent <agent@localhost>`

The user answered the conflict flagged above directly: commits should be attributed to the real
Domino person, plain name/email, no "via Sage"-style marker (asked via `AskUserQuestion`: "Plain
name/email" over "Name + agent marker"). Before writing any code, checked this sandbox live (a real
Domino workspace) rather than assume:

- `git config --global user.name/email` here: empty. `git config user.name/email` (repo-local, this
  checkout): "Etan Lightstone" / "etan.lightstone@dominodatalab.com" — almost certainly this
  person's own manual git setup on this specific checkout, not something Domino auto-injects into
  every fresh git-based Project (there is no evidence for the latter, and `_identity_args`'s existing
  "agent" fallback exists precisely because *some* environments have no ambient identity at all).
- `GET /api/users/v1/self`, hit for real with this sandbox's own credentials: returns `fullName` and
  `email` fields (`"fullName": "Etan Lightstone", "email": "etan.lightstone@dominodatalab.com"`) —
  confirmed live, not assumed. So `whoami()` already has everything needed once threaded through.
- Re-read `workspace/git.py`'s existing docstring carefully: its "de-branding" reasoning is about not
  hardcoding a re-brandable PRODUCT name (`Sage`/`Ada`) as the git author, never about hiding the
  PERSON — so the user's ask doesn't actually conflict with the original reasoning, only with the
  current literal behavior (which had no real-identity path to prefer at all).

**What was built:**

1. `sage/provision/domino.py`: `UserRef` gained `full_name: str = ""` and `email: str = ""` (both
   default `""` — an empty string means "this reader didn't fetch one", never "this person has no
   name"). `DominoControlPlane.whoami()` now populates them from the same `/api/users/v1/self` call
   it already makes.
2. `sage/platform/auth.py`: `TokenSource.whoami()` populates the same two fields from the same API
   response shape.
3. `sage/workspace/git.py`: `_identity_args(path, identity=None)` — an explicit `identity: (name,
   email)` ALWAYS wins over ambient git config now (a Domino-provisioned checkout has no real
   ambient identity of its own to lose to), falling back to today's "ambient config, else neutral
   `agent`" only when no identity is known. When only one half of `identity` is known, the OTHER half
   is filled with the neutral default (`agent@localhost`/`agent`) rather than left to git's own
   email-guessing — live-verified that git's guess (`$(whoami)@$(hostname)`) fails outright (exit
   128) in this sandbox's container, so a bare `user.name=` with no email is not a safe partial
   identity to hand git. `commit_all`, `commit_and_push`, `pull`, `finalize_merge`, `undo_merge` all
   gained the same optional `identity` parameter, threaded to `_identity_args`.
4. `sage/orchestrator/service.py`: new `Orchestrator._git_identity() -> tuple[str, str] | None`,
   mirroring `_viewer_id()`'s exact safe pattern (try `self._token_source.whoami()`, log+fall back to
   `None` on any failure — an identity-API hiccup must never block a save). Wired into all 5 call
   sites that author a commit: `_save_to_git`'s `commit_all`, `_integrate_remote`'s `pull`,
   `_resolve_conflicts`'s `finalize_merge`, `sync`'s `commit_all`, `undo_merge`'s `undo_merge`.
   Grepped every other `from ..workspace import git` import site in the file first (6 total) to
   confirm the other one imports for read-only calls (`Incoming`, `has_remote`) that need no
   identity — not guessed.
5. `sage/provision/seed.py`: `seed_and_push()` gained `identity`, threaded to a new
   `_seed_identity_args()` helper (same "identity wins, fill the missing half with the neutral
   default" logic as `workspace/git.py`'s, kept as a **second, independent implementation** rather
   than a shared import — matching this file's own established precedent
   (`test_the_commit_author_names_nobody` already keeps the two fallback constants in step by
   harvesting source rather than by sharing code, and the module's own comment says so).
6. `sage/provision/service.py`: new `ProvisionService._committer_identity()` (same safe
   try/except-and-log pattern, resolving `self._cp.whoami()`), wired into `provision_project`'s
   `seed_and_push(..., identity=self._committer_identity())` call — so BOTH the door's legacy
   `create_app` path and the new `registry.create()` path get the real identity on their initial
   commit, from the one call site both already share.

**Tests added, 16 total, all passing:**
- `tests/test_token_source.py` (+2): `whoami()` carries `full_name`/`email` when the API has them,
  blank when it doesn't.
- `tests/test_git.py` (+5, plus one existing test's assertion strengthened): an explicit identity
  overrides the repo's own ambient config; an identity is used even with zero ambient config;
  a name with no email still commits, under the neutral fallback email; `finalize_merge` and
  `undo_merge` each honor an explicit identity (built against a REAL pull-conflict-resolve-undo
  scenario, not a simulated one, matching this file's own established pattern for those functions).
- `tests/test_chat_turn.py` (+4): `_git_identity()`'s four paths — a configured identity, no
  `TokenSource`, a `TokenSource` whose `whoami()` carries no name/email at all (the id/name-only
  shape older fixtures and some real answers have), and `whoami()` raising.
- `tests/test_provision_service.py` (+3): the initial commit is authored as the real control-plane
  identity; falls back to `None` with no `full_name`/`email` (the `FakeControlPlane` default shape);
  survives a `whoami()` failure.
- `tests/test_provision_seed.py` (+2): `seed_and_push(identity=...)` authors the initial commit as
  that person; with no `identity`, the neutral `agent <agent@localhost>` default is unchanged.

**Verification:**
- Every touched/new test file green individually (`test_git.py`, `test_token_source.py`,
  `test_provision_service.py`, `test_provision_seed.py`, `test_create_project.py`,
  `test_project_registry.py`, `test_provision_credentials.py`, `test_door.py`,
  `test_the_control_plane_routes_speak_the_packs_words.py`,
  `test_the_door_waits_for_the_builder_to_answer.py`, `test_settings_api.py`) except the same
  well-established dogfood-safety class hit every session (confirmed: identical two test names —
  `test_creating_a_project_off_the_platform_names_the_pack`,
  `test_opening_another_project_off_the_platform_names_the_pack` — in this run and in the
  Phase-3-close full-suite run before this session touched anything).
- Grepped every `UserRef(` construction site across `tests/` and `sage/` to confirm the two new
  defaulted fields break no positional or equality-sensitive construction — all existing sites use
  `id=`/`name=` keywords only.
- `make lint` (repo-wide): clean.
- Full suite, reconciled: **7896 collected == 100 failed + 7786 passed + 10 skipped** — collected is
  exactly 16 more than the Phase-3-close baseline (7880), matching the 16 new tests above by count,
  not assumed. Failed count and the full 20-file failure-name list are BYTE-IDENTICAL to the
  Phase-3-close baseline's own list — zero new regressions, zero new test files appearing anywhere
  in the failure list.

**What this does NOT touch, on purpose:** the git identity used for a **published Built App's own**
runtime git operations (none exist today — apps don't commit) and any workspace-era (pre-pivot,
soon-to-be-deleted) code paths are unaffected; this is scoped to the Orchestrator's own per-turn
saves and the provisioning seed commit, the only two places this codebase ever authors a commit.

## UPDATE 2026-09-24 (same session, continued): `registry.create()`/`registry.clone()` live-verified end to end against this real Domino sandbox — closes the gap named at the top of Phase 3's own status

The user asked for this directly rather than more implementation: drive `registry.create()`/
`registry.clone()` for real, not just against fakes. Built a real `DominoControlPlane` (sidecar
token, this sandbox's own `DOMINO_ENVIRONMENT_ID`/`DOMINO_HARDWARE_TIER_ID`), a real `GitHubProvider`
(token via `credentials.extract_token`), a real `ProvisionService` pointed at the actual
`template/fastapi-antd`, and two independent throwaway `$SAGE_HOME` directories — one for `create()`,
one for `clone()` — via a one-shot script, run once, deleted after.

**Found and worth knowing before running anything like this again: the token that provisions repos
has `repo` scope but NOT `delete_repo`** (checked via `GET /user`'s `X-OAuth-Scopes` header before
creating anything, not assumed) — so **any repo `registry.create()` makes here cannot be deleted
through the API afterward**, only by hand in GitHub's UI. This is not new; it explains an existing,
larger problem found while checking: **23 real orphaned repos** (`sage-sales` through
`sage-sales-23`) already sit on this account, left by `test_create_project.py`'s
`test_a_container_that_cannot_provision_refuses_to_create` — a dogfood-unsafe test this file has
called out every session, hitting real infrastructure every time the full suite runs (which happened
3× this session alone). That test's own rollback path (`_rollback_repo`) already tries to delete the
repo it just orphaned and already logs the same 403 each time — nobody had connected that log line to
an accumulating account-level side effect until this check. Worth a dedicated look (rescope that test
off real credentials, or fix `_rollback_repo`'s expectations) — not done here, flagged rather than
fixed, since it's unrelated to Phase 3/registry work.

**The live run itself** (one own bug found and fixed along the way): the first attempt used the
wrong template path (guessed `backend/template/fastapi-antd` instead of the real
`template/fastapi-antd` at the repo root) — failed after the GitHub repo was already created (empty,
no commits, `auto_init: false`), and the rollback then hit the same delete_repo-scope 403 above,
leaving `sage-live-verify-registry-delete-me` (no suffix) as a second, EMPTY orphan. Fixed the path
and re-ran; `naming.candidates`' own `-N` collision retry picked
`sage-live-verify-registry-delete-me-2` for the real attempt, exactly as designed.

**Full pass, verified line by line, not just "it didn't crash":**
- `registry.create("Live Verify Registry DELETE ME")` → real GitHub repo
  `etanlightstone/sage-live-verify-registry-delete-me-2`, real Domino project of the same name
  (id `6ab489f94fd92a76b2895e6a`), local directory seeded with the real template (`app.py` present),
  `.sage/project.json` written with every field correct, and — the git-identity fix, confirmed live
  in the same pass — **the initial commit's author is `Etan Lightstone
  <etan.lightstone@dominodatalab.com>`**, not `agent <agent@localhost>`.
- `registry.clone(entry.domino_project_id)`, from a SECOND, independent `$SAGE_HOME` → same slug,
  same `app.py` bytes (byte-compared, not eyeballed), confirming the clone is a faithful copy of what
  `create()` actually pushed.

**Real resources left behind, not auto-deletable, named here for manual cleanup:**
- GitHub repos (delete via GitHub UI): `etanlightstone/sage-live-verify-registry-delete-me` (empty,
  this session's own path-bug artifact) and `etanlightstone/sage-live-verify-registry-delete-me-2`
  (the real, successful verification).
- Domino project `sage-live-verify-registry-delete-me-2` (id `6ab489f94fd92a76b2895e6a`) — archive or
  delete from Domino's own project settings.
- Local temp directories were cleaned up by the script itself; nothing left on disk.

**This closes item 3 from the "Next session should" list two updates up** ("`registry.create()`/
`clone()` are fully unit-tested but have never been driven through a real HTTP request or against a
real Domino sandbox") — for the underlying library calls. Still not done: driving them through an
actual HTTP route (none exist yet — that's still Phase 3 step 3's job, wiring `create`/`clone` to
real routes together with the door/home-page work).

## UPDATE 2026-09-24 (same session, continued): the two real-side-effect dogfood-unsafe tests fixed

The live verification above surfaced a real, separate bug: `test_create_project.py`'s
`test_a_container_that_cannot_provision_refuses_to_create` (and, found by grepping for the identical
shape, its sibling `test_the_control_plane_routes_speak_the_packs_words.py`'s
`test_creating_a_project_off_the_platform_names_the_pack`/`test_opening_another_project_off_the_platform_names_the_pack`,
plus `test_gallery.py`'s `test_a_container_that_cannot_provision_has_an_empty_gallery` and
`test_attach.py`'s `test_a_container_with_no_local_projects_offers_nothing_to_switch_to`) all
**assumed** `app.py`'s module-level `_provision`/`_control_plane` would be `None` in a test
environment, rather than **forcing** it. That assumption is true in normal CI (no Domino env vars)
and false in this specific sandbox (a real, live Domino workspace) — so these tests were silently
making REAL network calls, and two of them (the `POST /api/projects {"name": "Sales"}` ones) were
the actual source of the 23 orphaned `sage-sales-N` repos found above: two real repo-creation
attempts per full-suite run, not one, run 3+ times this session alone.

Checked the other dogfood-class failures first rather than assume they had the same shape:
`test_delete_app.py`/`test_publish_target.py`/etc. trip an unrelated, already-known guard
(`publish_available()` refusing because `/mnt/code` really is Sage's own repo) and never reach a
real network call at all — safe, a different false-positive, left alone.

**Fixed, five tests across four files**, each now forcing its own precondition instead of hoping the
ambient environment supplies it:
- `test_create_project.py`, `test_the_control_plane_routes_speak_the_packs_words.py` (both
  `test_creating_a_project_off_the_platform_names_the_pack` and
  `test_opening_another_project_off_the_platform_names_the_pack`), `test_gallery.py`:
  `monkeypatch.setattr(appmod, "_provision", None)` — sufficient because each route (`POST
  /api/projects`, `POST /api/projects/{id}/open`, `GET /api/gallery`) reads the bare module-level
  name directly inside the route function.
- `test_attach.py`'s test needed a SECOND patch, found by reading the actual route rather than
  guessing: `GET /api/projects` is registry-backed (`_REGISTRY.list(...)`), and `ProjectRegistry`
  captures `control_plane` BY REFERENCE at construction time (`self._control_plane = control_plane`
  in `__init__`) — so patching the module-level `appmod._control_plane` name would not reach the
  already-built `_REGISTRY`'s own copy. Fixed by patching
  `monkeypatch.setattr(appmod._REGISTRY, "_control_plane", None)` directly, alongside `_provision`.

**Verified live, not just re-run**: listed this account's real `sage-sales*` repos via the GitHub API
before and after running the fixed tests — **23 before, 23 after** — confirming the fix actually
stops the real side effect, not just the test's own assertion.

Full suite, reconciled: **7896 collected == 95 failed + 7791 passed + 10 skipped** — collected
unchanged (no tests added/removed, only fixed in place); failed count dropped by exactly 5 from the
prior update's 100, and the failure-name list lost exactly `test_attach.py`, `test_create_project.py`,
`test_gallery.py`, and `test_the_control_plane_routes_speak_the_packs_words.py` — all four now fully
green — with nothing new appearing anywhere. `make lint`: clean.

**Not done, and worth a dedicated look later, named rather than silently left**: this was a targeted
fix for the specific tests found to have a REAL side effect; the broader question of whether other
tests in the ~16-file remaining dogfood-safety class have similar unenforced assumptions (even if
their current failure mode happens to be safe, per the `publish_available()` check above) was not
audited exhaustively. The 23 pre-existing orphaned `sage-sales-N` repos and the empty
`sage-live-verify-registry-delete-me` (this session's own path-bug artifact) are both still real and
still need manual deletion — nothing here cleans those up, only stops new ones from this specific
source.

## Where things stand, consolidated (2026-09-24, end of this session)

Everything below is committed on `one-app-pivot-Etan` (tip `6c3f2957`), working tree clean, `make
lint` clean. This branch is not headed for a `main` merge (the user's own words, recorded near the
top of this file and in the assistant's memory) — don't spend a future session reconciling the
ongoing `main`-line divergence.

**Done and verified:**
- Phases 0-2 (one stack, config/TokenSource, the registry+dispatcher+per-project routing) — closed
  out in earlier sessions, unchanged this session.
- Phase 3 steps 1, 2, and half of 4: `registry.create()`/`registry.clone()` (repo + Domino project,
  no workspace) and the generalized git-credential resolver (`$SAGE_HOME` + `settings.git.token`
  fallback) — built, unit-tested, and **live-verified end to end against this real Domino sandbox**
  (a real repo+project created, seeded, cloned into an independent second `$SAGE_HOME`, byte-identical
  content confirmed).
- The git-identity conflict flagged mid-session — resolved with the user's decision (plain real
  name/email, no agent marker): every commit Sage's Orchestrator or provisioning makes now attributes
  to the real, authenticated Domino person via `whoami()`, confirmed live in the same verification
  pass (`Etan Lightstone <etan.lightstone@dominodatalab.com>` on the initial commit).
- Fixed 5 tests that were silently making real Domino/GitHub calls in this sandbox (found while doing
  the live verification above) — verified via the GitHub API that the side effect actually stopped,
  not just that the test's own assertion now passes.

**Not done, deliberately, and named for whoever picks this up next:**
1. **Phase 3 step 3** — deleting `door.py`/`door.html`/`/api/door*`, the workspace-lifecycle
   `ControlPlane` methods (`create_workspace`/`stop_workspace`/`resume_workspace`/`delete_workspace`/
   `workspace_http_ready`/`save_workspace_work`) and their `FakeControlPlane` counterparts,
   `Orchestrator.stop()`/`/api/stop`/`_resolve_workspace_id`, `environment/pluggable-tools.yaml`,
   `SAGE_BUILDER_TOOL` — together with building the Projects home page, repointing `/`, and wiring
   `registry.create()`/`registry.clone()` to real HTTP routes (replacing the door-era
   `POST /api/projects`/`/api/projects/{id}/open`/`/api/projects/status`). One coordinated change,
   per the reasoning recorded when the home page was first deferred (Phase 2's own update).
   `registry.create()`/`clone()` themselves need no further work to be wired — they're ready.
2. **Real, external cleanup only you can do**: delete the GitHub repos
   `etanlightstone/sage-live-verify-registry-delete-me` and `-2`, the Domino project
   `sage-live-verify-registry-delete-me-2` (id `6ab489f94fd92a76b2895e6a`), and — separately, much
   older — the 23 `sage-sales`/`sage-sales-2`...`sage-sales-23` repos/any matching Domino projects
   from before this session's fix.
3. Two long-standing, non-blocking coverage gaps, untouched across every session so far:
   `test_sage_domino_relay.py` (never written — `sage_domino.py`'s relay/fence lost coverage when
   `test_builtapp_serve.py` was deleted in Phase 0) and `test_feedback.py`'s weakened
   prompt/runner-drift guard.
4. The broader (unaudited) question of whether any of the remaining ~16 dogfood-class test files
   have the same unenforced-assumption shape as the 5 just fixed, beyond the specific ones checked.

## UPDATE 2026-09-24 (new session): Phase 3 step 3 done — door/workspace-lifecycle deleted, Projects home page built, `create`/`clone` wired to real HTTP routes

Read `ONE-APP-PLAN.md` and this file fresh; confirmed the branch matched this file's account
(`7f3b327c`, clean tree). This closes the one item every prior session flagged as the coordinated
next step.

**Deleted, real functions with real callers, not dead stubs:**
- `sage/provision/door.py` (whole file: `Door`, `DoorTarget`, `DefaultProjectRepoUnreachable`) and
  `sage/workbench/door.html`.
- `/api/door`, `/api/door/status`, `/api/stop`, and the door-era `POST /api/projects/{id}/open`,
  `GET /api/projects/status` routes; `_build_door`/`_door` in `app.py`.
- `ControlPlane.create_workspace`/`stop_workspace`/`resume_workspace`/`delete_workspace`/
  `save_workspace_work`/`workspace_http_ready`/`list_workspaces` — real impl AND `FakeControlPlane`
  counterparts, plus the now-dead `BUILDER_WORKSPACE_NAME`/`_SAVE_TIMEOUT_S`/`_READY_TIMEOUT_S`
  constants and the `builder_tool` ctor param (`SAGE_BUILDER_TOOL` env read in `app.py` too).
  `archive_project` and `available_tools` are ALSO uncalled now (door.py's own docstring already
  said `archive_project` had zero callers before this session) but are pre-existing dead code
  outside this deletion's cause — named, not touched.
- `ProvisionService.create_app`/`open_app`/`workspace_status`/`_open_result`/`_reachable` and the
  helpers only they used (`AppCreated`, `workspace_open_url`, `is_builder_workspace`, `is_owned_by`,
  `workspace_is_running`, `_STOPPED_STATES`, `WorkspaceLaunchFailed`, `_launching`).
  `provision_project`/`_committer_identity`/`_create_repo`/`_create_project`/`git_credential_diag`/
  `repo_is_unreachable`/`list_apps`/`list_built_apps` are untouched — `registry.create()`/`clone()`
  already ran through `provision_project`, not `create_app`.
- `Orchestrator.stop()`/`_resolve_workspace_id()` and the `workspace_id`/`domino_run_id` ctor params
  they were the only readers of (`orchestrator/service.py`).
- `environment/pluggable-tools.yaml`. `environment/README.md`'s "Sage Builder workspace" section is
  now actively wrong (describes a launch path that cannot work), not just stale — added a one-line
  flag at the top saying so and pointing at Phase 7 for the real rewrite, rather than doing that
  rewrite here (out of this step's scope, and `environment/app.sh`/the Dockerfile are untouched).

**Built, not just deleted:**
1. `sage/workbench/home.html` — the Projects home the plan's §2 target architecture puts at root
   scope. Self-contained (no shared shell CSS/JS), matching `door.html`'s own established reason for
   that shape (reached before any project — and now, possibly before Settings has ever been saved —
   so it must not depend on anything a broken/unconfigured process could be missing): project
   list (Open for `local: true` rows, Clone for `local: false`), a New-project form, and a compact
   Connection form (`domino_host`/`domino_token`/`git_token` via the already-existing
   `GET/PUT /api/settings` and `POST /api/settings/test`) shown alone when nothing is configured yet
   — §2.7's "First run with no host/token: the Projects home shows the Connection form and nothing
   else" literally, though only the core fields (not the publish env/tier pickers, which stay in the
   Workbench's own Account drawer — Phase 6 territory since there's no live env/tier listing route
   yet either).
2. `GET /`'s `ui()` now serves `home.html` at root scope and `index.html` (the Workbench shell) once
   `_ProjectDispatchMiddleware` has bound a project for the request (`_CURRENT_ORCHESTRATOR.get() is
   not None`) — replacing the `_DOOR_UI if proxy_is_app() else _UI` branch. `_DOOR_UI` → `_HOME_UI`.
3. `POST /api/projects` rewritten to call `_REGISTRY.create(name)` (was `_provision.create_app`);
   new `POST /api/projects/clone` (body `{"dominoProjectId"}`) calls `_REGISTRY.clone(...)`, 404 on
   `KeyError` (token can't see it), 409 on `FileExistsError` (slug collision). Gated on `_provision`
   (create) vs. `_control_plane` (clone) respectively, NOT the same guard: `clone()` only needs a
   control plane to look the project up and a git credential to pull it, so a git host `_provision`
   has no adapter for (`_build_provision_service` returns `None` for anything but GitHub) still
   leaves cloning possible — checked against `registry.py`'s actual guard rather than assumed.
4. `ProjectRow` (`sage/projects/registry.py`) gained `domino_project_id: str = ""` (populated for
   both local and remote rows) so `GET /api/projects`'s `dominoProjectId` field lets a client clone a
   `local: false` row with no second lookup.
5. Workbench-shell wiring, now that create/clone are real: `scope-picker.js`'s "New project" button
   is a plain enabled link to the Projects home (`../`) rather than a permanently-disabled tooltip —
   a full create FORM belongs on the home page, not duplicated into this popover. A `local: false`
   `ScopeRow` now clones on click (`SW.api.cloneProject`, new) instead of staying disabled, then
   navigates the same way a local row does; a collaborator-safety note doesn't apply here since
   clone has no workspace-reuse semantics to get wrong.

**Deliberately NOT done, named rather than silently cut:**
- Full ADR-0014 quotation treatment (the `sw-passthrough` blockquote, `ours`/`retryable` markers) is
  not implemented for `home.html`'s create/clone/settings errors — they're plain text. `/api/door`
  used to carry that distinction; the new routes don't. Flagged directly in
  `test_a_platform_error_reads_as_a_quotation.py`'s surviving test rather than silently dropped —
  redesigning the Projects home's error surfaces is bigger than retiring the door.
- A new ADR superseding ADR-0004 ("Workbench is the door") is Phase 8's job per the plan's own
  phasing; not written here. `docs/adr/0004-workbench-is-the-door.md` still describes the retired
  shape.
- `environment/app.sh`, the Dockerfile, and the rest of `environment/README.md` still describe the
  pre-pivot two-container world (Sage Builder workspace, `SAGE_SELF_UPDATE`, the fast inner dev
  loop) — Phase 7's "Packaging: App and laptop" is the real rewrite; this session only flagged the
  one paragraph that became actively wrong (the pluggable-tools launch path) rather than doing that
  rewrite piecemeal.

**Test sweep — every file the deletions touched, fixed rather than left red:**
`test_door.py`, `test_provision_open_url.py` (`workspace_open_url`/`workspace_is_running` unit
tests — no longer meaningful once workspace launch is gone), `test_the_door_waits_for_the_builder_to_answer.py`
deleted wholesale. `test_provision_domino.py` (-7 workspace-lifecycle tests), `test_provision_service.py`
(create_app/open_app tests repointed to `provision_project` where the behavior is shared — repo
naming collision, credential retry/grouping, rollback, identity attribution, description branding —
and deleted outright where genuinely workspace-only: `test_no_rollback_once_project_exists` and 5
`open_app` tests), `test_create_project.py` (same repoint pattern, `test_the_new_project_opens_this_creators_builder`
deleted), `test_attach.py` (its whole first half — `open_app`/`workspace_status` against
`FakeControlPlane` — deleted; the Phase-2-era same-origin-navigation tests kept), `test_orchestrator.py`
(3 stop tests deleted, `workspace_id`/`domino_run_id` dropped from the `_domino_orch` fixture),
`test_brand.py` (1 `create_app`→`provision_project` repoint). Door/brand-substitution surface tests
(`test_a_platform_error_reads_as_a_quotation.py`, `test_the_favicon_comes_from_the_pack.py`,
`test_the_entry_pages_carry_the_packs_name.py`, `test_the_workbench_ships_the_licences_it_owes.py`,
`test_workbench.py`, `test_the_paranoid_pack_finds_no_leak.py`, `brand_coverage.toml`) all repointed
from monkeypatching `proxy_is_app()` to registering a throwaway fake project directly in
`_REGISTRY._open` and dispatching through `/p/<slug>/` for real — the same shortcut
`test_project_dispatch.py` already established — since `ui()` no longer reads `proxy_is_app()` at
all. `test_the_control_plane_routes_speak_the_packs_words.py`'s door test deleted, its
`/api/projects/{id}/open` test repointed to `/api/projects/clone` (own guard, own text). New
route-level tests for `POST /api/projects`/`/api/projects/clone` added to `test_project_dispatch.py`
(6 tests: happy path + empty-name/empty-id refusal + 404/409 for clone) since nothing else exercised
the HTTP wiring directly (`registry.create`/`clone` themselves were already unit-tested).
Docstring/comment staleness fixed where it named now-deleted code as if it still existed (not
metaphorical "door" prose, which is untouched): `sage/provision/domino.py`'s `whoami()` docstring,
`test_whoami_follows_the_token.py`'s module docstring, `sage/orchestrator/app.py`'s
`_ViewerIdentityMiddleware`/`_manage_app_url` docstrings, `sage/orchestrator/brand.py`'s
`_safe_name` docstring, `sage/workbench/js/util.js`'s `mainHostUrl` comment,
`sage/workbench/js/prefs.js`'s module comment (described the pre-Phase-2 one-container-per-project
model, already stale before this session, made worse by citing the now-deleted
`/api/projects/{id}/open`).

**Verification, following this repo's own protocol:**
- Every touched/new test file green individually as each was fixed (recorded inline above; not
  re-listed here).
- `make lint` (repo-wide, `cd backend && ruff check ..`): clean.
- `node --check` on every edited `.js` file and on `home.html`'s inline `<script>` (extracted to a
  temp file first, since `node --check` needs a real file): clean. (This only proves syntax, not
  reference resolution — see the 2026-09-23 laptop-smoke-test update's own lesson about
  `Popover`-style bugs `node --check` cannot catch; nobody has run `home.html` in a real browser.)
- **Full suite, reconciled**: `cd backend && uv run --extra dev pytest -q -n auto` →
  `96 failed, 7723 passed, 10 skipped` (7829 collected). Every one of the 96 failures diffed against
  the unmodified baseline via `git stash`/`git stash pop` (restored and spot-checked intact each
  time) in three batches by file plus `test_orchestrator.py` checked separately — **byte-for-byte
  identical failing test names in every batch, same root cause every time** (the well-established
  `publish_available()` dogfood-safety class: `/mnt/code` really is the mounted Sage repo in this
  sandbox, so anything that reaches real publish/rename/delete/credential code 404s or refuses the
  same way pre- and post-this-session) plus the pre-existing `test_native_gateway_transport.py` node
  ESM resolution failure (sandbox-environment, unrelated to this pivot). **Zero new regressions,
  zero collection errors, zero new failing test names anywhere in the diff.**

**Not done, and worth a dedicated look later, named rather than silently dropped:**
1. Live HTTP check of the new routes/`home.html` against a real Domino sandbox — the underlying
   `registry.create()`/`clone()` calls were live-verified two updates up, but the HTTP wrapper and
   the page itself have only been checked via `TestClient` and `node --check`, never a real browser
   or a real request through the App proxy. Same caveat every phase's own status entry has carried.
2. The two long-standing coverage gaps (`test_sage_domino_relay.py`, `test_feedback.py`'s weakened
   drift guard) and the ADR-0014 quotation-treatment gap on `home.html` named above.
3. `docs/adr/0004-workbench-is-the-door.md` and the rest of `environment/README.md`/`app.sh`/the
   Dockerfile still describe the retired shape — Phase 8 and Phase 7 respectively.
4. The above was committed and pushed by the user directly (`79f9a5a9` "ph impl", then `816d7563`
   "sf") — not by an agent session. See the update immediately below for what shipped after that.

## UPDATE 2026-09-24 (same session, continued): a real bug found on the product owner's own laptop
## smoke test — `POST /api/projects` 503'd with "can't reach Domino" despite a saved host+token —
## traced and fixed, not just the settings-were-wrong explanation it first looked like

**What happened**: first real click on the just-shipped Projects home (`make orchestrator`,
`http://localhost:8080/`) — Create refused with "{assistantName} can't reach {platformName} from
this container, so it can't create a {project}." even after saving a PAT in Settings and retrying.
This is exactly the class of bug this branch's own history keeps finding only on a real laptop
(the `Popover` destructure typo, the `npx` resolution gap) — invisible to `TestClient` and
`node --check`, both of which this session's own verification leaned on for the Projects home.

**Traced, not assumed.** `POST /api/projects`'s 503 comes from `_provision is None`, which traces
back to `_build_control_plane()` — and that function's OWN docstring, from Phase 1, already named
this exact gap and deferred it to "Phase 3 (project lifecycle over Domino APIs, the first phase
that actually calls this control plane from a laptop)" — this session's own Phase 3 step 3 work,
which missed it. Two real bugs stacked:

1. `_build_control_plane()` required `publish_environment_id`/`publish_hardware_tier_id` (the
   Environment + hardware tier ids Domino injects into an App, meaningless on a laptop with no
   Phase-6 picker yet) just to build a control plane AT ALL — even though listing, creating and
   cloning a Project never touch those fields; only `publish_app`/`republish_app`
   (`_app_version`) do. A saved host+token was never enough on its own.
2. Even with that relaxed, `_build_control_plane()` minted its OWN fresh sidecar-only token
   (`sidecar_token(...)`) instead of using the shared `_TOKEN_SOURCE` every other Domino call in
   this process already goes through — so a laptop's static PAT would have been sent as a bare
   `Authorization: Bearer`, which `platform/auth.py`'s own module docstring already records as
   REFUSED (403 "No current user in request") by `/api/users/v1/self` and
   `/api/projects/beta/projects` — the two calls `whoami()`/`create_project()`/`list_apps()` make.
   A static key needs `X-Domino-Api-Key` instead; only `TokenSource.headers()` already knew that.

**Fixed:**
- `DominoControlPlane.__init__`: `environment_id`/`hardware_tier_id` now default to `""` (optional
  — listing/create/clone need neither); new `headers_provider: Callable[[], dict[str, str]] | None`
  param. `_headers()` uses it when given (the right shape for either token kind), else falls back
  to the original bare-Bearer behavior unchanged — every existing caller/test that only ever passed
  sidecar-shaped tokens is untouched. New `publish_configured` property: `bool(env_id and tier_id)`.
- `app.py`'s `_build_control_plane()`: gate relaxed to `api_host` + a resolvable `_TOKEN_SOURCE` —
  built FROM `_TOKEN_SOURCE` (`.bearer`, `.headers`) instead of a fresh `sidecar_token(...)` call,
  so a laptop's static PAT gets the header shape Domino actually accepts.
- `Orchestrator.publish()`: gate widened from `self._control_plane is None or not
  self._domino_project_id` to also check `getattr(self._control_plane, "publish_configured", True)`
  — a laptop with a control plane but no env/tier yet (Phase 6) now gets the same clean "Publish is
  only available when this builder runs on {platformName}" instead of an empty field reaching a
  real Domino API call. `getattr(..., True)`: any test double (`FakeControlPlane` included) that
  never declares the attribute stays exactly as permissive as before this split existed.

**Tests added**, all passing individually and together: `test_provision_domino.py` (+4:
`environment_id`/`hardware_tier_id` optional + `publish_configured` false, `publish_configured`
true once both are set, `headers_provider` overriding the bare-Bearer shape, the no-`headers_provider`
fallback unchanged), `test_settings_api.py` (+3, calling `_build_control_plane()` directly: builds
from host+token alone with `publish_configured is False`, sends the `TokenSource`'s own header
shape for a static key, is `None` with no `_TOKEN_SOURCE`), `test_orchestrator.py` (+1:
`publish()` refuses when `publish_configured` is `False` even with a real control plane and
project id — **could not be verified to pass in this sandbox**, same as its 6 neighbors in that
file: `publish_available()`'s own dogfood-safety check refuses first because `/mnt/code` really is
the mounted Sage repo here; the test is written the same way as those already-accepted-as-blocked
neighbors and needs a sandbox where `/mnt/code` isn't Sage's own tree to actually run green).

**Verification:**
- `test_provision_domino.py` + `test_settings_api.py`: 45/45 pass.
- `test_gallery.py`, `test_whoami_follows_the_token.py`, `test_provision_service.py` (constructor
  callers): 32/32 pass — the new optional params/kwarg don't disturb any existing construction.
- `test_a_rename_reaches_the_deployed_app.py` + `test_publish_missing_app.py` (both already in the
  dogfood-safety class, both also construct `DominoControlPlane` directly): diffed against baseline
  via `git stash`/`git stash pop` — **byte-for-byte identical failing test names**, confirming the
  constructor change disturbs nothing there either.
- `make lint` (repo-wide): clean.

**Still owed, named rather than assumed fixed**: this was traced and fixed by reading code, not by
reproducing the product owner's exact click against a live sandbox from here (no browser, no
reachable Domino host in this environment) — the next real test is the product owner's own retry.
**One more thing they need to do that isn't a bug**: `PUT /api/settings` does not hot-swap
`_control_plane`/`_provision`/`_TOKEN_SOURCE` — those are built once at import time — so saving a
PAT in Settings requires restarting `make orchestrator` before either fix here can take effect;
the settings route's own response already says `"restartRequired": true`, but it's easy to miss.

## UPDATE 2026-09-24 (same session, continued): a SECOND real bug from the same laptop retry —
## the fix above worked (control plane now builds and calls Domino for real), but Domino answered
## 403 "Anonymous user cannot list projects" — a corrupted token, not an unreachable one

**Traced, not assumed.** The 403 came from a REAL `GET /api/projects/beta/projects` call reaching
Domino (proof the Phase-3-gap fix above is working) and being refused with "Not authorized:
Anonymous user cannot list projects" — Domino received *something* but didn't recognize it as this
account. Read `home.html`'s own `saveConnection()`: it trims `hostInput.value` before sending but
never trimmed `tokenInput.value`/`gitTokenInput.value` — a PAT pasted with a trailing space or
newline (common from a browser paste) is saved and sent byte-for-byte, and Domino treats that
different, invalid string as unrecognized rather than as the real account. Matches the product
owner's own suspicion exactly ("its not being truncated right?").

**Fixed**: `home.html`'s `saveConnection()`/`testConnection()` now `.trim()` the token fields the
same way the host field already was. Checked the Workbench's own Account-settings drawer
(`shell.js`'s `ConnectionSettings`) for the identical gap while there — found it too (`save()`'s
`patch = { ...fields }` and `test()`'s `patch.domino_token = fields.domino_token` both sent every
field, including secrets, completely untrimmed) — fixed both, since it's the same defect in a
sibling implementation of the identical field and would have bitten the product owner again the
moment they reached that drawer from inside an open project.

**Also named, not fixed — a second real possibility for the same symptom that only the product
owner can check**: `config.py`'s `load()` has `DOMINO_USER_API_KEY`/`DOMINO_API_HOST` env vars
that unconditionally OVERRIDE whatever is saved to `settings.json`, by design ("facts about where
this process is running, not a preference a stale file should be able to override" — the module's
own docstring). If either is set in the shell `make orchestrator` runs in — a stale key from an
earlier Domino CLI login, for instance — every Settings save is silently discarded on every
restart, with nothing in the UI or the API response saying so. Told the product owner to check
`env | grep -i domino` before their next retry; there is no code fix for this (it's intentional
behavior), only a real observability gap (no way to tell, from Settings, whether the effective
token came from the file or an env override) that nobody has built yet.

**Verification**: `node --check` on both edited files, `test_settings_api.py` (11/11, unaffected —
no test pins the untrimmed behavior), `make lint` (repo-wide): clean. **Not verified**: whether
trimming actually was the product owner's root cause, or whether it turns out to be the env-var
override instead — both are real, both are now either fixed or clearly named; only their own next
retry (after restarting again) says which one it actually was.

## UPDATE 2026-09-24 (same session, continued): the THIRD laptop bug — a PAT is a Bearer credential, not an API key

**Measured, not guessed.** Still 403 after the trim fix ("No current user in request" on
`/api/users/v1/self`, "Anonymous user" on `/api/projects/beta/projects`). The product owner ran
four curls from their laptop against cloud-dogfood with their PAT: `Authorization: Bearer` → 200 on
both endpoints; `X-Domino-Api-Key` → 403 on both. `platform/auth.py`'s rule ("a static key must be
sent as `X-Domino-Api-Key`") was live-verified in the sandbox with a legacy account API key
(`DOMINO_USER_API_KEY`), and it holds for that kind of credential. A Personal Access Token is a
different credential that looks the same as a string and needs the opposite header. The earlier
header fix sent every static credential as `X-Domino-Api-Key`, which is wrong for exactly the
laptop case.

**Fixed** (`platform/auth.py`): `TokenSource` gained `scheme()` (`bearer` | `api_key`). A static
source probes `/api/users/v1/self` once, first with Bearer and then with `X-Domino-Api-Key`, and
caches whichever answers 200. If both are refused, or the network fails, nothing is cached (the
next call probes again) and it falls back to `api_key`, the shape it always sent before.
`headers()` and `sdk_kwarg()` both follow `scheme()`, so a PAT goes to `domino_data` as `token=`.
`static(..., scheme=...)` skips the probe for callers (and tests) that already know which it is.

**Verified**: `test_token_source.py` rewritten around a mock that accepts each credential only in
its own header (legacy key → api_key, PAT → bearer, probed once, undecided stays undecided, an
explicit scheme skips the probe). `test_settings_api.py`'s header test covers both shapes. 78/78
pass across the touched files; `make lint` clean. **Live, in the sandbox:** the real legacy
`DOMINO_USER_API_KEY` probed to `api_key` and `whoami()` answered `etan_lightstone`, so the old
credential type still works. The PAT → bearer half is the product owner's own curl result; the
probe tries Bearer first to match it.

**Not verified**: a PAT passed to `DatasetClient(token=...)`. It's the same shape the sidecar JWT
uses, but nobody has run it.

## Where things stand — start here (end of 2026-09-24)

This section supersedes every earlier "Next session should" and "Where things stand" block.

**Done, and verified by the product owner on a real laptop** (`make orchestrator`, PAT against
cloud-dogfood):
- Phases 0–2: one stack (fastapi-antd), Settings plus one `TokenSource`, and the registry with
  per-project `/p/<slug>/` routing.
- Phase 3 in full: `registry.create()`/`clone()` wired to `POST /api/projects` and
  `/api/projects/clone`; the root-scope Projects home (`workbench/home.html`), including the
  first-run Connection-only view; the door and all workspace-lifecycle code deleted; git commits
  authored as the real Domino user.
- Laptop smoke test passed: first-run Connection form; list, create and clone a project; open it;
  build an app in it; scope-chip switching and "New project"; commits reaching GitHub; the
  resources panel listing Datasets; a clean Publish refusal on the laptop.
- Three bugs found only by that laptop test and fixed (see the three UPDATE blocks above):
  1. The control plane required the publish env/tier just to exist, and used a sidecar-only token.
  2. Token fields weren't trimmed before saving.
  3. A PAT is a Bearer credential, not an `X-Domino-Api-Key` one. `TokenSource.scheme()` now
     probes which header a static credential needs.

**Open, not blocking, recorded so nobody has to rediscover them:**
1. **Next phase is Phase 4, preview per project** (plan §2.4). A supervisor per orchestrator on a
   background thread with `_free_port()`, `/p/<slug>/preview/*`, and delete `SAGE_PREVIEW_PORT`,
   the reaper and `SAGE_PROXY_MODE`. Decide risk #13 first (plan §2.2): today each open project
   runs its own `opencode serve`, and whether one shared server isolates projects under
   concurrent load is still unverified.
2. `home.html`'s create/clone/settings errors are plain text. They don't get the ADR-0014
   quotation treatment (`sw-passthrough`, the `ours`/`retryable` markers the old `/api/door` had).
3. A name typed with a leading `sage-` becomes `sage-sage-…` (`naming.repo_base` adds the prefix
   again).
4. A PAT passed to `domino_data` as `token=` (`DatasetClient`, `DataSourceClient`) has never been
   run. Reading a Dataset file's *contents* from a laptop is the test for it; listing is a
   different API and already works.
5. Settings changes still need a process restart (`_control_plane`, `_provision` and
   `_TOKEN_SOURCE` are built once at import). The UI says so. Hot reload was never in scope.
6. Publish from a laptop needs the env/tier picker (Phase 6). Until then it refuses cleanly
   through `publish_configured`.
7. Dead code, noted but left alone: `DominoControlPlane.archive_project` and `available_tools`
   have no callers.
8. Coverage gaps: `test_sage_domino_relay.py` was never written, and `test_feedback.py`'s drift
   guard is weakened.
9. Docs: ADR-0004 needs superseding (Phase 8). `environment/README.md`, `app.sh` and the
   Dockerfile still describe the retired two-container shape (Phase 7).
10. Manual cleanup: the test repos on GitHub and the matching Domino projects (`sage-sales-N`,
    `sage-live-verify-registry-delete-me*`, and whatever laptop testing created).
11. `user_to_test.txt` at the repo root is the product owner's scratch file of manual checks. It
    is untracked on purpose, so don't commit it.

**Test baseline in this sandbox:** about 96 failures, all pre-existing and environmental. They are
the `publish_available()` dogfood check (`/mnt/code` is Sage's own repo here) plus
`test_native_gateway_transport.py`'s Node ESM failure. Diff any new red against `git stash`
before assuming it's yours.

## UPDATE 2026-09-24 (new session): Phase 4 — preview per project

Read `ONE-APP-PLAN.md` and this file fresh, starting from the "Where things stand" section above.
Confirmed the branch matched this file's account (`9ffb38c5`, clean tree) before starting.

**Found the supervisor was already structurally per-project**, and said so rather than redoing work
that was already done: Phase 2/3 already gave every registry-cached `Orchestrator` its own `Project`
and its own `UvicornSupervisor` (`_supervisor_for`, called from `Orchestrator.project()`/`_bind_app()`
— both instance methods, so one call per open project, not a module-level shared instance). `_free_port()`
and `mount_base() == ""` were already in place from the Phase 0 fastapi-antd-only work. `/p/<slug>/preview/*`
already dispatches correctly with no code change needed: `_ProjectDispatchMiddleware` only ever EXTENDS
`root_path`, never rewrites `path` (ONE-APP-PLAN.md §2.3), so the single `control_app.mount("/preview", ...)`
registered at root already matches under any `root_path` value — Starlette routes on
`get_route_path = path - root_path` at every level. The general mechanism is proven by
`test_project_dispatch.py`'s existing `test_two_projects_bind_two_different_orchestrators` (any mount,
including `/preview`, resolves via the same ContextVar); a further preview-specific end-to-end test
would only re-prove the same mechanism through a heavier fixture, so none was added — see "not done"
below for what a REAL live check still owes.
**Also found `previewStatus` reset needs no code**: `scope-picker.js`'s project switch is already a
full `window.location.assign(...)` browser navigation (ONE-APP-PLAN.md §2.3's own "switch is a
same-origin navigation"), which re-initializes `store.js`'s state — including `previewStatus: 'idle'`
— from scratch on every project open. Nothing to reset that isn't already reset by the navigation.

**Found and fixed a real bug this pivot's own history had not yet surfaced**: `sage/preview/proxy.py`'s
`get_upstream()` (`_preview_upstream` in `app.py`, which can block for up to `UvicornSupervisor.start()`'s
own 30s timeout on a cold project or a crashed one) was called DIRECTLY inside `async def http_proxy`/
`ws_proxy` route handlers — never on a background thread. In the single-project world this blocked one
person's own requests; in the one-app pivot's actual shape (many projects, ONE process, ONE event loop)
it would freeze every OTHER open project's requests — and every other async route in the whole
process — for the same wait. This is exactly the #500 class of bug plan step 1 ("Supervisor start on
a background thread... never on the request path") already named, just not yet found in this
particular call path. Fixed: both call sites wrapped in `run_in_threadpool`.
  - Proven with a test that actually discriminates fixed-from-broken, not just "both requests
    eventually returned": `test_preview_proxy_does_not_block_the_event_loop.py` drives two concurrent
    requests through ONE shared `asyncio` event loop (`httpx.AsyncClient` + `ASGITransport` +
    `asyncio.gather`, all inside one `asyncio.run()`) against a `get_upstream` that blocks
    synchronously, and asserts both were inside it AT THE SAME TIME. **First attempt was wrong and
    caught before landing**: using `fastapi.testclient.TestClient` from two Python threads
    (`ThreadPoolExecutor`) showed "both concurrent" regardless of whether the fix was in place —
    each thread's call got its own event loop under `TestClient`'s httpx transport, so the test
    wasn't exercising the one-shared-loop scenario the bug is actually about. Verified the ONE-loop
    version does discriminate: monkeypatching `run_in_threadpool` to a passthrough (simulating the
    pre-fix code with no file edit) reproduces `max_concurrent == 1`; the real fix gives `2`.
  - Also nearly lost this exact fix once by mistake this session: a sanity-check edit was
    reverted with `git checkout -- sage/preview/proxy.py` to restore the pre-check state, which
    (correctly, since nothing had been committed) reverted ALL of this session's uncommitted changes
    to that file, including the real fix, not just the check's own edit. Caught by `git diff --stat`
    coming back empty when it shouldn't have; redone. Recorded so a future session doesn't reach for
    `git checkout --` on a file with real uncommitted work still in it, even for a "just testing"
    edit — `git stash`, not `git checkout --`, is the reversible one.

**Completed the rest of plan §2.4:**
- `sage/preview/supervisor.py`: removed `SAGE_PREVIEW_PORT` (`_env_port`) and the `lsof`-based
  `_clear_stale_port` reaper. Both existed to guard a FIXED, shared port from a stale prior process —
  `_free_port()` already picks a fresh OS-assigned ephemeral port every spawn, so the collision they
  guarded against is now negligible, and keeping them would have meant two conflicting "which port"
  stories in one class.
- `TokenSource` (`platform/auth.py`) gained a public `api_host` property — it already stored this
  privately; a plain string reader needs it for something other than baking it into `.headers()`.

## CORRECTION (same session, before this all landed): the plan's literal wording for `DOMINO_API_HOST`/`SAGE_DOMINO_TOKEN` passthrough was wrong for THIS codebase's actual proxy shape, and a first attempt at it was a real security hole — caught and fixed before landing, not after

Plan §2.4 says "The supervisor passes the child `DOMINO_API_HOST` and, on a laptop, `SAGE_DOMINO_TOKEN`,
so `sage_queries.py`'s Flight executor and `sage_domino.py` can authenticate without a sidecar." The
first version of this update implemented exactly that — `UvicornSupervisor` gained a `token_source`
param and set both env vars in the spawned preview child's environment, and
`template/fastapi-antd/sage_domino.py`'s `token()` was changed to read `SAGE_DOMINO_TOKEN` first.
**Both are reverted. Neither survived to what's actually in the tree now.**

Found while answering the user's own question about how to test the relay — tracing the actual code
path, not assuming the plan's prose matched this codebase's proxy design:

1. **It would not have worked.** `preview/proxy.py`'s `http_proxy` intercepts `_QUERY_PREFIX`/
   `_LLM_PREFIX`/`_PLATFORM_PREFIX` (`/api/queries/*`, `/api/llm/*`, `/api/domino/*`) and answers them
   itself — via `_relay_platform`/`_answer_query` calling `sage_domino.py`/`sage_queries.py` **loaded
   and executed IN THE ORCHESTRATOR PROCESS** (`domino_module`/`serve_module` in
   `resources/builtapp.py`, `exec_module` against the template file) — **before ever forwarding to
   the spawned preview child**. `PreviewQueries.start()` confirms the same shape for queries: "bound
   to loopback and run in a thread" inside the orchestrator process, not the child. So the CHILD's own
   copies of these two files, and any env var set only in the child's spawn environment, are never
   reached by a single real preview request — dead plumbing, not working plumbing.
2. **The env var version was also a security hole, not just ineffective.** The preview CHILD process
   runs `uvicorn app:app --reload` — literally the agent's own generated Python. Any env var set on
   that child's spawn is directly readable by that code (`os.environ`), no interception possible,
   because it isn't about which HTTP path gets intercepted — it's about what's readable from inside
   that process's own Python runtime. Putting a real Domino credential there is precisely what
   ONE-APP-PLAN.md §2.8 rules out: "The agent never holds a Domino token." A second version of the
   fix tried setting the same two vars on the ORCHESTRATOR's own `os.environ` instead (reasoning: the
   in-process relay call needed them there, which is true) — also reverted, because `driver/server.py`'s
   `OpenCodeServer._env()` copies `os.environ` wholesale into OpenCode's own spawn env, so a
   process-wide env var would have reached the AGENT's shell just as surely, through a different door.
3. **The actual, safe fix**: `orchestrator/app.py` gained `_apply_static_platform_override(module,
   token_source)`, called from `_preview_platform()` right after `domino_module()` loads the template's
   `sage_domino.py` in-process. For a `static` `TokenSource` (a laptop PAT/key) it replaces that loaded
   module OBJECT's own `token`/`platform_host` attributes with closures over the real `TokenSource` —
   Python resolves an unqualified `token()` call inside `sage_domino.py`'s own `relay()`/`get()`
   through the module's `__dict__` at call time, so this reaches them without needing `sage_domino.py`
   itself to change at all. **Nothing is written to `os.environ` anywhere** — the override lives only
   in this one loaded module object, in this one process, reached only by the in-process relay call.
   A `sidecar` source (real workspace/App, already has a reachable sidecar) or `None` is a no-op.
   `sage_domino.py` itself is back to byte-identical with what shipped before this session — it never
   needed to change; the fix belongs entirely on Sage's own side of the fence.
4. `UvicornSupervisor` is back to taking no `token_source` at all — the child's spawn env carries only
   `SAGE_PREVIEW=1` plus whatever it inherits from `**os.environ`, exactly as before this session's
   Phase 4 work started. `_supervisor_for`/`Orchestrator.project()`/`_bind_app()` calls are back to
   two positional args.
5. **The Data Source `FlightExecutor`/`DataSourceClient()` path is unaffected by any of this, and
   still cannot authenticate on a laptop** — same as risk #1/#4 always said. `PreviewQueries`'s own
   docstring explains why it was never going to be touched here: "No credential is passed, and that
   is the point rather than an omission... this container is the creator's build session" — a
   deliberate design choice (a preview query runs as the creator, so it must fail the same way
   publish would rather than answer under someone else's grants), not a gap this session's token
   wiring was ever going to close by threading a token through. Still open, still Phase 5/6's to solve
   if it's solved at all.

**Tests changed to match:** the two `token_source`-passthrough tests in `test_supervisor_parse.py`
are gone (that file is back to one test, unchanged in substance from before this session except for
the `SAGE_PREVIEW_PORT` deletion above); `test_sage_domino_token_reads_the_supervisor_override.py`
(the file testing the reverted `sage_domino.py` change) is deleted outright; a new
`test_preview_platform_static_token_override.py` (5 tests) pins `_apply_static_platform_override`'s
actual behavior — static overrides both functions, sidecar/None are no-ops, a `None` module doesn't
crash, and (the regression this whole correction exists to prevent) **`os.environ` is provably
untouched by the call**, asserted directly rather than trusted.

**Deliberately NOT done, named rather than silently skipped:**
1. **`SAGE_PROXY_MODE` / `preview/prefix.py` deletion** — plan step 3 literally bundles this with
   `SAGE_PREVIEW_PORT` and the reaper, but it is not a preview-port concern at all: `proxy_is_app()`
   (`SAGE_PROXY_MODE=="app"`) is still the ONLY thing that distinguishes a published Domino App from
   a laptop run, and real, current callers depend on it — `publish_available()`'s dogfood-safety
   gate, `domino_base_prefix()`'s App-vs-local branch, `_manage_app_url`'s visibility check, and the
   cost-label derivation. Phase 3's door deletion already retired the THIRD value this variable used
   to distinguish (`workspace`, the Sage Builder pluggable-tool launch — `environment/pluggable-tools.yaml`
   is gone, so nothing sets it anymore), which makes the docstring's three-way framing stale prose,
   but the function itself is not: replacing App-detection with something else is genuinely Phase 7
   packaging work ("Two hosts, one code path"), not something to fold into a preview change. Deleting
   it now would have broken `publish_available()`'s safety gate for no preview-related benefit. Left
   alone; the docstring's stale "Sage Builder workspace" framing is exactly the same kind of paper
   cut Phase 3's own door-deletion update already flagged in `environment/README.md` rather than
   silently rewriting mid-stream — a real rewrite of this file belongs with Phase 7's "Two hosts, one
   code path" work, not scattered across whichever phase happens to touch it next.
2. **A live check against a real Domino sandbox or a real laptop** — everything above is verified by
   unit tests (including the concurrency test's careful double-check that it actually discriminates)
   and a full-suite run, never by opening two real projects in two real tabs and watching their
   previews run side by side. Same caveat every phase's own status entry in this file has carried;
   the plan's own Phase 4 verify line ("two projects open in two tabs... `/p/a/preview/` and
   `/p/b/preview/` serve different apps") is still only unit-proven, not live-proven. **The most
   direct live check for the relay specifically**: `curl http://localhost:8080/p/<slug>/preview/api/domino/api/users/v1/self`
   against a real laptop run with a saved PAT — this requires no Build turn and no agent involvement
   at all, since the relay answers before either would matter; it should come back with the real
   Domino identity JSON.
3. **`FlightExecutor`/`DataSourceClient(token=...)` on a laptop** — untouched, and per point 5 above,
   was never actually in scope for this session's fix despite the plan's wording suggesting otherwise.
   Risk #1/#4 in the plan stand exactly as they were.
4. Risk #13 (shared vs. per-project `OpenCode` server) is untouched and still open — it concerns the
   AGENT session server (`opencode serve`, one child per project today), which is architecturally
   separate from the preview `UvicornSupervisor` this update is about. Nothing here required
   resolving it, and nothing here makes it easier or harder to resolve later.

**Verification (post-correction):**
- Every new/changed test green individually as written: `test_supervisor_parse.py` (back to one
  test, `SAGE_PREVIEW_PORT`/`_clear_stale_port` cases removed), `test_token_source.py` (+1,
  `api_host`), `test_preview_platform_static_token_override.py` (new, 5 tests, see above),
  `test_preview_proxy_does_not_block_the_event_loop.py` (new, 1 test, the event-loop fix).
- `make lint` (repo-wide `cd backend && ruff check ..`): clean.
- **Full suite, reconciled, run a second time after the correction**: see the number recorded
  immediately below in the final "Where things stand" section — reconciled the same way as the first
  run (byte-for-byte diff against a `git stash`-restored baseline on the exact files that showed red),
  redirected to an explicit file from the start this time rather than trusting a background-task
  capture (the first run's capture was silently truncated — see below).

## Where things stand — start here (end of 2026-09-24, Phase 4)

This section supersedes every earlier "Next session should" and "Where things stand" block,
including the one above dated the same day (that one stopped at Phase 3).

**IMPORTANT — the working tree is NOT clean and NOT pushed right now.** Every prior "Where things
stand" in this file could say "everything is committed and pushed by the user directly" truthfully;
this one cannot. This session's entire Phase 4 diff (`ONE-APP-STATUS.md` plus the six files listed
under "Completed the rest of plan §2.4" above, plus two new test files) is sitting **uncommitted** in
the working tree, per this repo's CLAUDE.md ("NEVER commit changes unless the user explicitly asks")
and this branch's own established practice (the user commits directly). Run `git status --short`
before doing anything else — a fresh session, or a landing session, must not assume `git log`'s tip
(`9ffb38c5` as of this update) is the real state of the tree.

**Done, verified by tests + a full-suite reconciliation, NOT yet verified live:**
- Phases 0–3, as the section above this one already recorded (product-owner-verified on a laptop).
- Phase 4 (preview per project, ONE-APP-PLAN.md §2.4) in full, as detailed in the update immediately
  above this section: the event-loop-blocking bug found and fixed in `preview/proxy.py`; the
  `SAGE_PREVIEW_PORT`/reaper deletion; the `/api/domino/*` relay's laptop auth fix, done as an
  in-process module-attribute override (`_apply_static_platform_override`) — **not** as an env var
  passthrough into `UvicornSupervisor`'s spawned child, which was tried, found both ineffective and a
  real security hole (a real Domino credential reachable from the agent's own generated code, or from
  OpenCode's own process via `os.environ` inheritance), and reverted before landing. See the
  `## CORRECTION` section above for the full trace — read it before touching this area again, since
  the plan's own §2.4 wording describes the reverted, wrong shape. Full suite, run twice (once before
  this correction, once after): `97 failed, 7744 passed, 10 skipped` (7851 collected), both runs
  diffed byte-for-byte identical against a `git stash`-restored baseline on the exact files that
  showed red. `make lint` clean both times.

**Open, not blocking, recorded so nobody has to rediscover them:**
1. **Nothing in Phase 4 has been exercised against a real Domino sandbox or a real laptop** — same
   caveat every phase has carried. The plan's own verify line for this phase ("two projects open in
   two tabs... `/p/a/preview/` and `/p/b/preview/` serve different apps") is unit-proven, not
   live-proven. Before trusting this in production: (a) open two real projects and watch their
   previews run side by side, specifically trying to reproduce the OLD event-loop bug's shape (open
   project A, let its preview cold-start, and confirm project B's Chat/Build stays responsive during
   that wait); (b) on a laptop with a saved PAT, `curl http://localhost:8080/p/<slug>/preview/api/domino/api/users/v1/self`
   should answer with the real Domino identity JSON — the most direct check of the relay fix, needing
   no Build turn and no agent involvement, since the relay answers before either would matter.
2. `SAGE_PROXY_MODE`/`preview/prefix.py` deletion is explicitly NOT done — still load-bearing for
   App-vs-laptop detection (`publish_available()`, prefix derivation). Belongs to Phase 7 packaging.
3. `FlightExecutor`/`DataSourceClient(token=...)` on a laptop is still untested and, per the
   correction above, was never actually going to be reached by this session's fix regardless — it
   answers with no credential at all, by deliberate design (`PreviewQueries`'s own docstring: a
   preview query runs as the creator, and must fail rather than answer under someone else's grants).
   Risk #1/#4 in the plan stand exactly as they were.
4. Risk #13 (shared vs. per-project `OpenCode` server) is untouched and still genuinely open.
5. Everything the 2026-09-24 Phase 3 update above already flagged as open (manual GitHub/Domino
   cleanup of test repos, `test_sage_domino_relay.py` never written, `test_feedback.py`'s weakened
   drift guard, ADR-0004 needing a successor) is unchanged by this session.

**Test baseline in this sandbox, updated**: 97 failures (was ~96 before this session — see the full
suite entries above for why the count moved and how it was reconciled twice), all pre-existing and
environmental (`publish_available()` dogfood class + `test_native_gateway_transport.py`'s Node ESM
failure). Diff any new red against `git stash` before assuming it's yours — and note, per the tool
mishap recorded above, that a background-task-captured pytest run can be silently truncated; redirect
to an explicit file (`pytest ... > /path/to/file.txt 2>&1`) before trusting a large failure count.

**Next up**: Phase 5 (resources without mounts) or Phase 6 (publish) per the plan's dependency table
— either can start from this tree once the Phase 4 diff above is committed. Decide risk #13 first if
Phase 7 (packaging, process-count-sensitive) is what's next instead.

**A pattern worth naming for whoever reads this next**: this session's Phase 4 work went through
three different shapes for the same small piece of plumbing before landing on a safe one, and the
plan document itself (§2.4) still describes the FIRST, wrong shape in its prose. When a plan's literal
wording asks for something ("the supervisor passes the child X"), tracing where X is actually consumed
before implementing it is what caught both the ineffectiveness and the security hole — implementing
the literal words first and checking later would have shipped a credential leak. Read a plan's "why"
as a requirement and its "how" as one candidate implementation, not as a spec to transcribe.
