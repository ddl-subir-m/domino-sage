# Fresh Build acceptance — 2026-09-25

Issue #557, P11. The control alias and Haiku completed the React new-build and edit
checks. Both also produced valid FastAPI apps, but their original edit validations
exposed a Uvicorn restart defect. Those original results remain **unverified**.
After the lifecycle fix, each saved FastAPI edit passed real page validation without
another model call or app-code change. Xiaomi did not complete the full matrix within
the bounds below. The control FastAPI Dataset-tag case passed Build validation and
the separate loading, error, Retry, and valid-empty UI checks.

## Method and exact source

Each cell used a fresh temporary project and the selected shipped template. The test
used real OpenCode 1.18.4, the configured gateway, Sage's Orchestrator and control app,
the real preview supervisor/proxy, and Chromium through Playwright CLI. The existing
OpenCode test host held `/tmp/sage-opencode-test.lock` for its lifetime. Configuration
and runtime state were isolated; the runner did not install global OpenCode config.

For each metrics cell, the prompt requested `Sample Metrics`, a Region/Revenue table
with North = 150 and South = 100, and a labelled total of 250. These were hard-coded
synthetic rows, with no data source, external API, or new dependency. The next turn
requested an actual footer with exactly `Synthetic data checked`. The same prompts
were used across aliases and stacks. Table checks matched column positions and row
values; the footer check read a footer/contentinfo element. Body substring checks
alone were not accepted.

The runner first requested a plan, then called the normal approval route, then made
the edit if approval completed. Each turn had a 300-second test cap. The timer used
`stop_build` with the exact kind, conversation, app, and turn ID. A `preview-validation`
event caused real browser navigation to its document ID. Only the template reporter
sent page acknowledgment; the runner did not synthesize it. Missing validation or
acknowledgment was not counted as success. Metrics data validation was not applicable.

| Revision label | Worker commit | Exact source tree | Included root |
|---|---|---|---|
| A | `b7b9775f8ef1b70f446f11f09a9956c73199e5eb` | `5cb663ae2ac16351c70596dfd5318476efd94826` | `12d9309e63ba2a35def73d1a69bd6220a87c9787` |
| B | `748e8dce92f1b61a9776867c2de117cd53b13973` | `07421d1f1b8bab4c8bdc2ac5246e269983646218` | `ef33810fdc1a1b036646e856572c58fcd5e1cb71` |

A includes the reserved-generation repair. B also includes the Uvicorn port-release
repair. These worker merge trees equal the corresponding root trees tested for those
fixes. All seven final live cells recorded identical before/after hashes of tracked
`backend/sage` and `template` files. No source changed during a cell.

| Requested alias | Model reported in builder responses | Observed wire protocol |
|---|---|---|
| `gpt-5.4` | `gpt-5.6-sol` | Responses |
| `haiku` | `claude-haiku-4-5-20251001` | Messages |
| `mimo-v2.6-pro` | `xiaomi/mimo-v2.6-pro` | Responses |

These are observed gateway response identities, not an independent provider audit.
The separate scope helper is not included in this model identity claim.

## Paid metrics results

Times are wall-clock seconds per turn, including orchestration. Repairs are emitted
`iterate` events; planning recovery is distinguished from code repair below.

| Alias / stack | Revision | Plan | Approval / new build | Edit | Repair or recovery count |
|---|---|---|---|---|---|
| Control / React-Vite | A | Passed, 15.518 | Passed, 37.568 | Passed, 32.168 | 0 |
| Control / FastAPI-AntD | A | Passed, 12.381 | Passed, 45.341 | **Unverified**, 27.793 | 1 syntax repair in approval; 1 in edit |
| Haiku / React-Vite | A | Passed, 8.986 | Passed, 27.154 | Passed, 26.022 | 0 |
| Haiku / FastAPI-AntD | A | Passed, 8.715 | Passed, 35.147 | **Unverified**, 18.061 | 0 |
| Xiaomi / React-Vite | A | **Failed after bounded recovery**, 247.016 | Not run: no valid plan | Not run | 1 clean planning retry |
| Xiaomi / FastAPI-AntD | B | Passed after recovery, 254.825 | **Confirmed Stop**, 300.126 | Not run: approval incomplete | 1 clean planning retry; 0 code repairs |

Every “Passed” approval/edit above had code, startup, page, and runtime stages passed,
plus the strict DOM checks. The two original FastAPI edits had code passed but zero
preview-validation events; startup, page, and runtime remained unverified. Their old
browser documents did not show the new footer. Their `done.ok: true` meant the code
check was clean, while the explicit verification result remained unverified; this
report does not promote that result to a page success. Neither turn hit the timer.

The control FastAPI repair causes were `static/app.js:54:1`, missing `)`, on approval,
and `static/app.js:56:1`, unexpected `)`, on edit. Each was repaired once. The Haiku
FastAPI turns needed no syntax repair. This separates generated-code errors from the
shared preview lifecycle defect.

### Xiaomi evidence and limits

React planning made two actual Xiaomi Responses calls, 120.203 and 120.727 seconds.
Both were cancelled by Sage's no-action policy before upstream completion. Production
diagnostics counted 943 and 525 reasoning frames, with no first text, tool, or action.
The OpenCode transcript had reasoning and step-start parts but no assistant text or
tools in either session. Its `UnknownError` entries followed cancellation; they are
not evidence of an upstream provider error. Direct frame counters were added only for
the later FastAPI cell, so the React trace cannot alone separate model from gateway
or relay responsibility. No extra paid retry followed the built-in clean retry.

FastAPI planning also needed one clean retry, but that retry returned a valid plan:

| Call | Seconds | Upstream completion | Reasoning delta characters | Text delta characters | Tool starts |
|---|---|---|---|---|---|
| Initial plan | 120.096 | No, cancelled by policy | 7,920 | 0 | 0 |
| Plan recovery | 128.231 | `response.completed` | 8,333 | 1,653 | 0 |

The first call had 521 `response.reasoning_text.delta` frames. Recovery had 582 such
frames and 76 `response.output_text.delta` frames. Canonical and unknown frame-type
counts were retained; no unclassified or structured delta characters appeared in
these two calls. No upstream error frame was observed. Only counts were retained,
not reasoning text. OpenCode received the recovery's 1,653 text characters and a
normal stop finish. Thus the React timeout does not establish all-stack incompatibility.

FastAPI approval made four completed Xiaomi calls, taking 21.947, 128.367, 41.678,
and 14.134 seconds. Their seven tool starts became five completed reads, one completed
write, and one completed edit in OpenCode. There was no malformed or failed tool call
in this trial. A fifth call emitted only reasoning deltas before the 300-second test
cap. The exact Stop was accepted, and the stream emitted a terminal `stopped` event.
There was no Build validation. The final OpenCode message was `MessageAbortedError`
after that Stop, not an observed upstream error. All observed builder calls stayed
on Xiaomi; no control-model fallback explains the work.

Written work was retained. Separate post-Stop checks passed `node --check static/app.js`
and an AST parse of `app.py`. They do not establish a finished Build, a valid running
page, or completion of the requested edit. The missing Xiaomi approval/edit acceptance
remains open evidence, not a pass hidden by an additional paid retry.

## Lifecycle failures found by the live checks

An earlier control/React trial on worker `4f29706d1ba65d625c23151e3629332cceaf9a3e`
(tree `343e0c443426c21a07573ab0a81f412b6c836c2c`, root `e9cc9d9e`) produced clean code
but unverified approval/edit in 27.513/19.716 seconds. Async retry reserved a generation,
then spawn advanced it again. Validation waited for the first generation while the
ready process owned the next. Both-stack regressions reproduced zero validation events.
Fix `2e0340615fae96b84af170c8c970e1117336e227` made the child consume its reserved
generation; 87 affected tests passed, zero skips. A no-model validation of the saved
React app on A then passed startup/page/runtime with a real reporter acknowledgment.
Its app code hash stayed `cc520e6b86ae8c362f8273b0643762add104b3dd`.

The two FastAPI edits on A exposed a second defect. Their new processes failed with
`Address already in use`, and auto-restart advanced the generation beyond the one
being validated. Control and preview ports were distinct. The live runs did not retain
a pre-restart PID snapshot, so they do not directly prove which process held each port.
A separate no-model reproduction held an active 0.8-second request during restart
and confirmed that its own old Uvicorn parent/child still held the preview port after
termination was requested. Fix `bfefb48e9b39556dda78a64e8532e4adf328ef5d` waits in the
background for bind/listen release, for at most three seconds, with cancellation before process start.
It does not weaken validation or add another kill. Three real restarts then passed on
the reserved generation; 77 affected tests passed, zero skips.

On B, both saved FastAPI edits passed `_validate_page` with the real browser reporter
and strict DOM checks, without inference or app-code edits:

| Saved app | Unchanged app code hash | Separate fixed-code result |
|---|---|---|
| Control FastAPI edit | `aacf1c131432d9df4c1e9295ede437aed05df07e` | Startup/page/runtime passed; exact footer visible |
| Haiku FastAPI edit | `8098e0dfa40394db5f6fd54951c456bc6874ace1` | Startup/page/runtime passed; exact footer visible |

These are later cold validations of saved code, not paid Build/edit reruns. The prior
syntax-check results were reused. The separate active-request restart reproduction
tests the release race. Original paid reports remain unchanged and unverified.

## Bound Dataset tag UI

One fresh control/FastAPI app on B used the existing binder for synthetic Dataset
`synthetic-tags-557`. A local platform relay supplied known-ID metadata and unique tag
`p11-tag-exact-557`; no customer Dataset or remote platform read was made. Planning
passed in 23.681 seconds; approval passed in 34.913 seconds, with no repairs or Stop.
Code, startup, page, runtime, and data all passed. The actual preview proxy recorded
successful Dataset listing and known-ID taxonomy reads. The exact unique tag was
visible, so the Dataset name alone could not satisfy the check.

After Build, a browser probe held the taxonomy response pending, returned 403, clicked
the generated Retry button, then returned the known Dataset row with explicit
`taxonomyTags: []`. All nine checks passed: correct ID and query flag, visible loading,
no premature empty state, visible error, no error-as-empty state, visible Retry, a new
request, visible valid-empty state, and no stale error. The app showed “Loading Dataset
taxonomy”, then the 403 error and “Retry metadata read”, then “Synthetic Tags has no
taxonomy tags.” App code hash stayed `a9974f4b72e92e758c021fc44791d9c29587a08c`.

The first probe hit a Playwright CLI fixture error: `URL` was not defined in its outer
script scope. Only the temporary request parser was corrected. The same saved app was
reopened without inference; the original Build result was preserved. The injected UI
sequence tests generated UI behavior after Build. It does not pass through the proxy
or retroactively alter the closed Build validation. The initial Build's real proxy
read and the separate deterministic P10 tests cover those different paths.

## Other browser and controlled evidence

Root's navigation check on `6bc38ea2` passed Alpha-last → Beta → Alpha-last, refresh,
an explicit older link plus refresh, history 503 retaining the answer with Retry, and
New showing empty history. Its preview check on `7303fd19` used real control app,
Uvicorn reload, proxy, Workbench, and Chromium. A planted missing import produced a
persistent “uvicorn is unavailable” message with the exact cause and expandable
output. Retry attempt 2 stayed failed; restoring valid `app.py` returned ready with
`error: null` and a healthy iframe. The model/control inputs were synthetic.

Both shipped reporters were tested in real copied Vite/Uvicorn templates. P9 planted
a React module throw before App import and a FastAPI script throw. Each reported the
startup error with the original document validation ID, retained it after a URL
change, and acknowledged only after DOMContentLoaded. The first React check exposed
an interactive-readyState timing defect; the corrected reporter waits unless the
document is complete. P10 passed 20 browser assertions, 10 per stack: immutable ID,
issue-time read tagging, caller and Request headers retained, explicit empty response
unchanged, caught rejection reported, no query/token values retained, and foreign or
control requests untouched. These reporter probes intercepted API requests; they did
not replace the actual Orchestrator acceptance above.

Final B file hashes equal the frozen P10 browser-test hashes:

| File | SHA-256 |
|---|---|
| `template/react-vite/src/main.tsx` | `9d1ad7caa1499fb87a49a138ee9a50008f38e79f21caa652060c5fddaa6fc4bc` |
| `template/react-vite/src/reportRuntimeError.ts` | `74e84764c8719bb80df46d307a5762e6704624a51891201334ea91afca5feabf` |
| `template/fastapi-antd/static/sage/reportRuntimeError.js` | `a62b59c55050bfd32a3a5f6b61dda52d61304138373c60526d8a829ab913305e` |

The separate [controlled Build matrix](2026-09-25-controlled-build-matrix.md) records
28 selected-stack cases for optional naming failure, code failure, startup failure,
runtime recovery/exhaustion, and distinct read outcomes. Its fake model and controlled
page reports complement these real browser/model checks; they are not live model trials.

## Evidence, cleanup, and remaining limits

Local evidence is under `/tmp/557-p11-build-final/<alias>-<stack>-metrics/`, plus
`gpt-5.4-fastapi-antd-tag-ui/`. Each contains `report.json`, streamed event JSONL,
browser output, and the isolated synthetic project/runtime. Separate files retain
`no-inference-validation.json`, `validated-dom-inspection.txt`, Xiaomi activity counts,
`post-stop-code-checks.json`, and `tag-ui-no-inference-recheck.json`. The preliminary
React trial is `/tmp/557-p11-build-gpt-react`. Runner scripts are
`/tmp/557-p11-build-replay.py`, `/tmp/557-p11-inspect-existing.py`, and
`/tmp/557-p11-tag-ui.js`. They use the normal production routes; no second application
workflow was added to the repository.

Other local evidence: `/tmp/557-preview-browser` and its
`output/playwright/fastapi-import-failure.png`; `/tmp/557-uvicorn-port-red.log` and
`/tmp/557-uvicorn-port-green.log`; `/tmp/p9-browser-red-output.txt`,
`/tmp/p9-browser-green-output.txt`; `/tmp/p10-browser-manifest.json`,
`/tmp/p10-browser-probe-output.txt`, and `/tmp/p10-browser-checks.json`.
Temporary logs are not durable CI attachments. This report retains the conclusions,
exact revisions, counts, limits, and failures. Raw logs contain synthetic tool/event
payloads and must be reviewed before publication.

All live cells and browser reopen checks ended. Every live report recorded successful
browser close and unchanged source hashes. The paused matrix driver was terminated
after its active cell finished; no paused driver remains. The OpenCode lock was checked
released, and no trial runner process remained. Temporary synthetic apps were retained
for review. No saved customer app was changed.

This is one final matrix trial per alias/stack, not a reliability rate. Xiaomi's missing
approval/edit results remain unmet acceptance; no extra paid retry was used to hide them. No production
warehouse, deployment, or customer Dataset was tested. No full suite ran in this worker;
the landing session owns the integrated full gate. No push, main merge, or deployment
was performed by this worker.
