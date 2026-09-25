# Controlled Build matrix — 2026-09-25

Issue #557, P11. This closes the selected-stack coverage gap in the earlier P9/P10
orchestration tests. Each case creates and selects an app with `create_app(stack=...)`.
It uses the shipped template, the recorded stack, actual files and `FeedbackRunner`.
The model uses the existing `FakeOpenCode` and `ScriptedGateway`; no paid call runs.

The test file is `backend/tests/test_controlled_build_faults_on_each_stack.py`.
Every row below runs for both `react-vite` and `fastapi-antd`. The selected stack remains
unchanged, and streamed and saved terminal outcomes agree. TypeScript uses the installed
React template dependencies; missing Node or template dependencies give explicit skip reasons.
Python files use `py_compile`, and page scripts use `node --check`.

| Exact test name | Parameters after the stack ID | Required outcome |
|---|---|---|
| `test_missing_title_keeps_the_selected_stack_and_can_be_approved` | none | Optional name call fails once; title stays empty; the same selected app's plan can be approved and built. |
| `test_real_code_errors_exhaust_the_existing_repair_bound` | none | Real TypeScript name error / Python syntax error repeats for three turns; the existing no-progress rule stops it, no page validation runs, and written work remains. |
| `test_startup_failure_after_real_clean_code_is_not_success` | none | Actual code check passes; controlled server startup failure ends failed and keeps written work. The Python file has a missing import that compilation alone cannot detect. |
| `test_runtime_fault_after_reload_obeys_the_repair_bound` | `repair-succeeds`, `repair-exhausted` | Each page validation has a new document ID. One repair succeeds despite a late old-page error, or a second current-page error exhausts the one-repair test policy and fails. |
| `test_data_outcomes_keep_failure_empty_and_unverified_distinct` | `tags`, `empty`, `wrong-id`, `unauthorized`, `denied`, `not-found`, `timeout`, `transport`, `no-call` | Known tags and explicit empty tags succeed distinctly; wrong ID, 401, 403, ambiguous 404, timeout and transport failures stay failed; no call stays unverified. Platform failures get exactly one repair; query transport failure does not gain a new retry rule. |

These are 28 cases: 14 per selected stack. The runtime test policy sets its existing
repair limit to one, while code errors exercise the existing three-turn no-progress
limit. Data repair uses the existing one-attempt platform rule. No product limit changes.

The preview status and page acknowledgments/runtime/data reports are controlled inputs,
using the existing `Preview` fixture and reporting methods. These tests do not launch a
browser or prove that generated UI shows loading, error and empty states. They also do not
prove the supervisor's real retry/start/spawn lifecycle. The real FastAPI import/reload
tests, both-stack reporter browser checks, the separate double-generation regression and
the fresh live Build replay provide those distinct checks. In particular,
`test_changed_page_validation.py::test_validation_keeps_the_generation_reserved_before_async_spawn`
uses both real supervisor classes through retry/start/spawn and replaces only OS launch.
No production warehouse,
Dataset service, deployment or saved generated app is used.

Baseline: root `65e40f15b1bd2ec4d7a9adc0245c041732a2a585`, merged without fast-forward
into the isolated worker as `06e978a0369c9da4cd123457749e5a3238444195`.
Only this report and the new test file change in this follow-on.

The first complete matrix passed 28 tests, zero skips, in 83.97 seconds on that baseline.
Before the final gate, root `12d9309e63ba2a35def73d1a69bd6220a87c9787` was merged without
fast-forward. It includes the reviewed supervisor-generation fix `2e034061`; none of
the controlled fixture contracts changed.

The first attempt stopped at a fixture-only duplicate dependency symlink; the workspace
manager had already installed that link. After correction, 16 cases passed before the
query transport case exposed an incorrect test expectation about platform repair.
The expectation now preserves the existing distinction. Neither result established a
product defect. Final targeted check and source hashes are recorded in the worker report.

After integration, the frozen full matrix passed 28 collected / 28 passed, zero skips,
in 84.29 seconds. Independent review found two possible false passes in the tests:
the no-call case also needed proof that the current page was acknowledged and its
startup/page/runtime stages passed; code faults needed the actual planted TS2304 or
SyntaxError cause. Those assertions were added. The four affected stack/case IDs then
passed, zero skips, in 14.00 seconds. Test and product-source hashes stayed unchanged
during each reported run. Lint and diff-check passed. No product source changed in this
follow-on, and these added tests do not claim a new red/green product repair.

Local evidence: `/tmp/557-p11-stack-matrix-final.txt`,
`/tmp/557-p11-stack-matrix-before.txt` and `after.txt`,
`/tmp/557-p11-stack-matrix-review-final.txt`, and
`/tmp/557-p11-stack-matrix-review-before.txt` and `after.txt`.
These temporary logs are not durable CI attachments. The results and exact case names
are retained here. The full integrated suite and live fresh-app acceptance remain
separate gates.
