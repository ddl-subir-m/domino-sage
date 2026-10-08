# Delivery, verification, and experiments

## Implementation sequence

The first release is A + B + C with baseline evaluation. D is a separate usability change. E is a
measured navigation experiment. F and G remain disabled experiments. H is a capability probe.

| Slice | Work | Dependencies | Completion |
|---|---|---|---|
| 0 | Capture fixed tasks and current results | None | Baseline artifacts and source/model identity recorded. |
| A | Screen-first starters, rules, and adapted checks | 0 | Both stacks and legacy fixtures pass app-design acceptance. |
| B | Request timing, stale-result control, query examples | A | Query correctness and request-order tests pass. |
| C | Durable evidence added to existing recovery | 0; independent of A/B | Identity, restart, invalidation, and no-extra-call tests pass. |
| D | Optional hash view state | B | Published-prefix, reload, navigation, and privacy tests pass. |
| E | Extract the source note; add no-new-dependency relationships and the app-scoped tool | A, C | Byte-identical extraction, relationship fixtures, boundary and cache tests pass. Promotion waits for the evaluation. |
| F | Phased execution comparison and persisted notes | C | Comparison report; default remains unchanged. |
| G | Final interaction check experiment | A/B and browser capability proof | Correctly attributed evidence and time-budget report. |
| H | Mid-turn correction probe | Existing driver/lock analysis | Verified harness contract or explicit unsupported finding. |

Do not combine all slices into one change. Each must have a reviewable result and its own evidence.
No application code, issue, PR, commit, push, or deployment has been created by this handoff.

## File ownership and likely changes

| Slice | Existing files to inspect/edit | Proposed new code |
|---|---|---|
| A | Both template `AGENTS.md` files and entries; FastAPI `index.html`; `feedback/runner.py`; `workspace/manager.py`; `implementation_request.py`; plan instructions in `orchestrator/service.py` | Initial screen in each starter; tested architecture examples. |
| B | Both query helpers; `resources/bound_schema.py`; existing query tests and Node test fixtures | Small debounce helper, preferably colocated with query helpers. |
| C | `build_intent.py`, `context_rollover.py`, continuation packet and retry paths in `orchestrator/service.py`; `workspace/snapshot.py`; `Workspace.sage_owned_paths`; diagnostics | `build_evidence.py` and focused tests. Reuse #680 attribution; separate check identity from progress. |
| D | Stack owned-source lists, `_prepare_turn_app`, `WorkspaceManager.for_app`, helper refresh/install paths, starter loading, instruction profiles | `viewState.js` / `appViewState.ts`, with behavior tests. Reuse #690 preparation. |
| E | `_source_paths`, `_top_level_names`, `_build_source_note`, built-in tool registration, per-turn tool filtering, tool-result limits | `source_map.py` extracted from the existing map, parsed relationships, app-scoped tool, fixtures. |
| F | `orchestrator/plan_steps.py`, `_run_step`, `_phased_approve`, existing settings endpoint | Persisted phase results through C, evaluation records. |
| G | Existing preview validation IDs/events, verification result and diagnostics | Experimental browser adapter and fixed interaction runner. |
| H | `driver/opencode.py`, `driver/server.py`, turn queue/lock, Workbench composer/store | Only add a steering adapter/UI if the probe proves safe support. |

Paths in this table under backend are relative to `backend/sage/`. Keep new logic in small modules;
add narrow calls to `service.py`. Avoid unrelated service refactoring.

## Test plan

Test behavior, not only prompt text. A test asserting that an instruction exists does not prove
that a generated app follows it. Use deterministic fixtures for runtime logic and fixed model
tasks for generation quality.

| Population | Required examples |
|---|---|
| Structure | One screen, two screens, screen-only follow-up, missing registration, wrong script order, untouched starter, legacy entry-only app. |
| Queries | Fast typing, immediate Apply, IME input, slow old response, A -> B -> A, unmount, cache refresh, empty result, error, full-population aggregate, bounded detail pages. |
| URLs | Reload, copied link, defaults, invalid/repeated values, Unicode encoding, Back/Forward, private draft input, prefixed published path, preview iframe isolation. |
| Context | Ordinary continuing session, rollover, failed-build retry, process restart, edited plan, changed query, wrong app, stopped/reset app, missing/corrupt/oversized evidence. |
| Code map | React imports, FastAPI globals/script order, Python routes, query names, untracked changes, deletion, dynamic unknowns, output cap, path escape. |
| Negative controls | A false “passed” model note; a map that omits a real dependency; a stale result; a truncated detail set used as a total. Each must fail the relevant check. |

Existing tests to inspect before adding coverage:

- `backend/tests/test_a_placeholder_is_not_a_finished_build.py`
- `backend/tests/test_template_fixes_reach_an_existing_app.py`
- `backend/tests/test_a_cross_file_script_global_is_not_undefined.py`
- `backend/tests/test_a_build_on_an_unselected_app_refreshes_its_sage_files.py`
- `backend/tests/test_no_test_npm_installs_the_real_template.py`
- `backend/tests/test_turn_path.py` (`ALREADY_DONE` evidence, no-edit outcomes, and legacy-app recovery instruction)
- `backend/tests/test_snapshot.py` (turn-tagged snapshots)
- `backend/tests/test_build_instruction_profiles.py`
- `backend/tests/test_build_intent.py`
- `backend/tests/test_context_rollover.py`
- `backend/tests/test_phased_build.py`
- `backend/tests/test_retry_an_approved_plan.py`
- `backend/tests/test_changed_page_validation.py` (including #692's unselected-app acknowledgement/crash cases)
- `backend/tests/test_preview_data_validation.py`

Use the existing Python-driven Node fixture pattern for helper behavior. Test query ordering with
controlled promises, including a response that resolves despite cancellation. Test placeholder
and script loading against actual seeded workspaces. Use the existing real-OpenCode test path for
at least one tool-discovery/request-composition check when the offered tool set changes.

Keep #689's autouse fixture: tests must not install dependencies into the repository's real
templates. Prepare required packages before the run through the approved environment setup.
The #691 tests require real oxlint from `template/react-vite/node_modules` and Node; a skip is
not evidence that cross-file lint works. Use isolated test templates for installation behavior.
A future source-map parser must likewise not trigger package installation during a test.

### Tests leave nothing open

`backend/tests/conftest.py`'s autouse `__nothing_is_left_open` (#608) fails any test that leaves a
thread, a `Popen` process or a file descriptor open. Never disable, bypass or loosen it, and never
mark a test to skip it. Every new test must pass it on its own merits:

- Open files, sockets, sqlite connections and HTTP clients with `with`, or close them in a
  fixture's teardown. A `TestClient` is a context manager; use it as one.
- Node fixture harnesses run through `subprocess.run` (which waits) or a `Popen` that the test
  waits on or kills in `finally`. Never leave a Node or preview process running after a test.
- Join every thread a test starts. Cancel every `threading.Timer`.
- Use fake time for debounce/timing tests. No `time.sleep` to wait out real timers.

Report the wall time of your new test files (`--durations=10`) and of the full suite beside the
baseline, so a slower suite is visible at landing rather than discovered later.

### Repository execution rules

Read the current `AGENTS.md` and any applicable `RTK.md` before implementation. This handoff is not
a replacement for them. No `RTK.md` was located in the checked workspace/parent locations during
preparation; resolve it in the teammate's environment if provided there.

For an assigned issue, read its comments before work and before the final report. Follow the
anchored suite-slot protocol in `AGENTS.md`; machine inspection alone is not authorization to
start a suite. This handoff does not authorize posting messages or creating tickets by itself.

Run targeted tests with `-rs`; the backend defaults to xdist. Coordinate any xdist run through the
machine's queue. Run the full required gate once before committing, after merging current main
under repository rules. For real-OpenCode coverage, use the documented root environment with
dependencies and `--opencode`; do not count skipped tests as coverage. Run `make lint` at repository
scope. Do not push or merge main; the designated landing session owns those actions.

## Fixed evaluation

Use a versioned local fixture dataset with known results, including enough rows to exceed the
normal response cap. Keep fixture values synthetic. Record prompt, fixture revision, expected
outcome, starting app tree, stack, model alias, effective effort, harness version, and Sage revision.

Minimum task set:

1. Build a single-screen table app, then change one column's presentation.
2. Build a two-screen app, then change only the second screen.
3. Build a region-filtered dashboard; prove the chart and table share one selection.
4. Add remote text search; simulate rapid typing and out-of-order responses.
5. Show a KPI and paginated details over more rows than the result cap; compare with fixture truth.
6. Add shareable month/region state; reload and navigate Back/Forward.
7. Retry an interrupted/failed build after restart; preserve the approved requirement and completed code.
8. Make a cross-file follow-up to a larger app; compare discovery with and without the map.

In task 1 or 2, repeat the completed follow-up and record the existing `already done` outcome
separately from a build that writes code. Include an older app whose instructions lack the marker;
count its no-edit recovery rather than assuming the repeated request ends on its first attempt.
In task 7, test app A while B is selected, including a
Sage-owned helper refresh. Refresh-only changes must not count as model progress. The control is
`main` at the revision recorded in the README, which already contains those fixes. Run it there
and nowhere older.

Run each generation task three times per condition as an initial screen for variance. This is not
a statistical guarantee. Use the same model/effort and reset to the same input tree. Alternate
condition order. Record cache state. Compare one change at a time. Label simulated fault tests
separately from model-generated live builds.

Measure end-to-end completion, acceptance pass/fail, repair turns, repeated reads, input/output
tokens, and latency. Include failures, timeouts, retries, and unverified outcomes in the report.
The existing scorecard rejects failed/incomparable turns; preserve that behavior. Add an outer
evaluation report that accounts for all attempts rather than changing failures into missing rows.
Do not claim token counts are monetary cost without verified pricing.

Proposed promotion gate: all deterministic acceptance tests pass; no new data/verification boundary
failure; no loss of completed-task correctness in the paired fixture set. For speed/context
experiments, require at least 10% improvement in median latency or median token use, with no more
than 10% regression in the other, and inspect the worst cases. Treat these as initial product
thresholds. Structure and URL usability can be accepted for their explicit behavior benefit, but
report their latency/cost effect. If results are mixed, expand the sample before default rollout.

Extend diagnostics with bounded version fields and counts for template layout, evidence reuse,
map extraction/retrieval, and optional verification. Do not export query values, source snippets,
decision text, or raw browser responses. Keep missing measurements distinct from zero.

## F. Phased execution experiment

Today approval uses phased execution only when the per-project `phased_build` setting is true and
`is_phasable(plan)` succeeds. The parser requires at least three valid steps. Workbench has a phase
count label but no current enable control. The planner already aims for 3–7 steps, so step count
alone is not a useful future classifier.

Compare normal Guided approval with phased approval on the same plans, models, and initial trees.
Use the existing settings API in isolated evaluation projects; no product toggle is needed for
the experiment. First add persisted phase evidence from C. Include tightly coupled work and
clearly separable work, and compare both a smaller model and the main coding model in separate
pairs. Record session startup overhead and integration repairs.

Keep the default off. If evidence supports adoption, the later product design is a per-plan
execution choice on the approval card: `Automatic`, `One session`, `Separate steps`. Store the
chosen method with the approved plan version, not as a hidden project-wide preference. An edited
plan must be revalidated. Automatic selection should use a recommendation in the existing planner
response plus deterministic validation. Unknown or invalid recommendations fall back to one
session. Do not add a separate classifier call. This UI/automatic-selection work is deferred until
the experiment passes; it is not part of the initial implementation release.

## G. Browser interaction experiment

Existing checks observe startup, runtime errors, and the page's requests. They do not click the
app or prove business correctness. Keep those checks in all cases.

First prove an available browser adapter can reach the app inside the deployment and attribute
actions to the correct preview generation. Keep the experiment disabled if that capability is
absent. Do not install a second browser harness merely for this specification.

For the first experiment, use fixed interaction scripts from the task fixtures, not a new model
call that invents tests. Run after final code checks, once per app build. Proposed total extra
budget: 30 seconds across the original check and any recheck. Test one main interaction, such as
changing a region and checking the known chart/table result. Skip text/style-only fixture changes.

Bind evidence to app ID, turn ID, code digest, and preview validation generation. A repair uses
the existing bounded repair loop and a fresh generation. If the budget is exhausted, report
interaction verification as unverified. Never accept a stale page's result. Stop cancels actions.
Check only the app under test. Use fixture data and no destructive/external write action.

Reuse #692's app-specific validation path: resolve the preview through the validation's app ID,
not the currently selected app or a request-local view. Page reports can arrive without an
`X-Sage-App` header. Keep its acknowledgement, generation, and runtime-fault checks intact.
Test app A while B is selected: A's current acknowledgement is accepted, A's crash fails A's
check, an old validation is rejected, and B's preview is not restarted. Exercise both a bound
request view and a turn whose app was pinned before the selection changed. Apply the same
identity rules to the proposed browser adapter; a UI selection change alone must not move it.

Keep interaction status separate from startup/data-access status. A successful click is not proof
that its displayed total is correct; the fixed fixture assertion supplies that proof. Compare
added build latency with the user repair cycles avoided before proposing default behavior.

## H. Correct a running build

The current Build path serializes turns. Codex's `turn/steer` is a useful design reference, not
evidence that pinned OpenCode 1.18.4 has the same contract.

Probe the pinned server for safe delivery of input during an active tool loop. Verify when the
model sees it, how it is acknowledged, how it persists, and how Stop interacts with it. Inspect
the actual protocol; do not implement by guessing an endpoint or running two prompts concurrently.

If supported, propose a `steer` operation in the existing driver with expected app/turn identity
and an idempotency key. Record accepted user text in order. Keep the project turn lock. Apply the
correction to subsequent work, not to a tool call already in flight. Re-run checks for the final
code. Changes that require new resources or broader approval use existing Guided gates. The UI
must distinguish “queued for next turn” from “accepted for this build.”

If native steering is absent, keep queue behavior and report the unsupported capability. A future
controlled stop-and-resume feature needs its own design because stopping an edit can change
recovery and revert behavior. Do not disguise a queued correction as immediate steering.

## Rollout and completion report

Roll out A/B first to new apps, with C independently testable. Preserve legacy app fixtures at
every slice. Add D only to apps needing shared views. Keep E/F/G behind developer evaluation
controls until measured. A rollback stops new template use or tool offering; it does not rewrite
apps already generated. Preserve record readers or ignore unknown versions safely.

For each slice, report changed paths, acceptance evidence, source/tree identity, model conditions
for live runs, observed time/cost changes, and any unperformed checks. Follow the repository's
full landing evidence requirements when committing implementation. A code-only or fake-harness
pass is not evidence of generated-app quality.

## Suggested skills for the implementer

- `writing-for-agents` when changing template instructions or context pointers.
- `codebase-design` when defining evidence-store or map interfaces.
- `domino-ui` when adding a Workbench control or a Domino UI example.
- `diagnosing-bugs` when a behavior test reveals an unexpected existing failure.

Read the selected skill before use. No skill, plan, or peer message overrides the user's authority
or the repository's landing protocol.
