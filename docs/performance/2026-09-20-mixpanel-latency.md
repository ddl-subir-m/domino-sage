# Mixpanel count and regression delays, 2026-09-20

Issue: #472. Diagnostic caveat: #471.

## Evidence and limits

Read the running cloud-dogfood workspace and its saved project history. The workspace restarted
at 15:57 UTC, after the requests below. Its boot log reports Sage `5f818a6`; that is the revision
used for the local reproduction, not proof of the revision that served the earlier requests.
The browser could not open the diagnostic timing page (`ERR_BLOCKED_BY_CLIENT`).

The evidence below comes from `.sage/threads/<id>/history.jsonl` and `context.json` in
`ddl-subir-m/sage-subir-mansukhani-66a821b1`, snapshot
`773755fba1f8ca6858431fa3cbd236fcf7b3b0f9`. Times have one-second resolution.
Each duration starts at the saved user message and ends at its first `done` event. Data operations
are deduplicated by `operation_id`; repeated receipt updates are not extra queries.

| Request | Thread | UTC start → end | Duration | Outcome | Recorded data operations |
|---|---|---|---:|---|---:|
| How many distinct users are in @MIXPANEL__EVENT | `thr_1a0bcfb6ad6c96310c323` | 04:03:13 → 04:03:50 | 37s | answered | 1 |
| Fit a regression predicting event count per user from their signup month, using @MIXPANEL__EVENT | `thr_1a0bd703910ebfb2d1592` | 06:11:05 → 06:19:12 | 487s | answered | 5 |
| Same regression, earlier attempt | `thr_1a0bd027e87c84a90c7f3` | 04:11:01 → 04:19:27 | 506s | timeout | 11 |

These saved records alone do not provide a full inference count or SQL execution durations.
The gateway-log recovery below supplies the missing main model calls. Do not use
`/api/diag.model_calls` to fill that gap: #471 describes its cumulative Chat count. Do not use
the current process's revision or timing history as evidence for a turn before its restart.

## Reproduced cause: the prompt drops the date columns

The saved table chip holds 106 column names and types. `_chat_context_line` renders `cols[:40]`.
`DISTINCT_ID` is column 3, but `DATE_PART`, `MIXPANEL_API_RECEIVED_AT`,
`MIXPANEL_PROCESSED_AT`, and `EVENT_OCCURRED_AT` are columns 103–106. None of those four date
columns reaches the model's first prompt.

Replaying the saved chip through the renderer at `5f818a6f` failed:

```text
Saved columns: 106
DISTINCT_ID position 3 in prompt: True
DATE_PART position 103 in prompt: False
MIXPANEL_API_RECEIVED_AT position 104 in prompt: False
MIXPANEL_PROCESSED_AT position 105 in prompt: False
EVENT_OCCURRED_AT position 106 in prompt: False
AssertionError: The event timestamp is stored on the chip but missing from the model prompt
```

The earlier timed-out regression produced six schema-discovery results between 04:12:56
and 04:16:45: `full-column-list`, `timestamp-column-check`, `column-names`,
`column-pattern-counts`, `timestamp-column-names`, and `timestamp-typed-columns`.
The surrounding two operations
(`top-event-names` and `signup-like-events`) concern event values, not schema.

The fix renders all names and types already on the selected table chip. It adds no database
request and does not return data rows. It costs a larger prompt for wide tables. This is preferable
to silently withholding known columns and paying for repeated discovery.

## Other costs visible in the records

- **Result disclosure and retries.** In an earlier attempt (`thr_1a0bcf74277246a2048f9`),
  the assistant explicitly said that column names reached the card only. The numeric-aggregate
  disclosure rule in ADR-0058 does not admit plain metadata rows. In the completed regression,
  the first regression result includes date-valued cohort bounds and has no selected fields.
  A later numeric-only regression result does have selected fields. `decide` refuses a mixed
  result when an aggregate returns a nonnumeric value; this is a deliberate rule, not a reason
  to expose all query results. Keep display-only date bounds in a separate result when the model
  needs the numeric regression statistics.
- **Model-request delays.** Four distinct request IDs in the completed regression have both
  attempted and response-completed timestamps: 13s, 19s, 71s and 99s, totaling 202s.
  They name alias `GLM 5.3 OR` and report serving model `z-ai/glm-5.3-flash`.
  This is a subset of request intervals, not a complete model-time measurement or proof that
  model computation accounts for every second. The older timed-out attempt records eight
  complete intervals totaling 308s, plus an unfinished interval.
- **The simple count has a different profile.** Its one recorded result appears 26s after the
  user message, with the final answer 11s later. It also calculates total events, distinct emails,
  and distinct usernames. There is no evidence here of repeated schema discovery on this turn.
  The gateway-log recovery below separates the main model calls from the query-result interval.

## Validation and remaining work

Two regression tests exercise the full Chat prompt path: a table already selected on the chip,
and a table selected by the Chat gate on this turn. Both fail before the fix because columns after
40 are missing. Both pass after it. All 17 tests in the column-context and Chat/Build-context
files pass, with no skips (15 existing cases plus 2 new cases). Ruff passes on both changed Python
files. The original saved-chip replay now checks all 106 names and types and passes.

No full suite, commit, push, merge to main, or live deployment was performed. The owner authorized
focused tests despite older unreleased suite markers. The live speed gain is still unmeasured.
After deployment, run both original prompts in fresh conversations, capture `/api/diag/timing`
before any restart, and compare the same model and table. Check that the regression avoids schema
rediscovery; then measure numeric-only regression output and model choice separately.

## Follow-up: remaining costs

Reproduced the mixed-result refusal with the real `disclosure.decide` function and synthetic
values. `COUNT(*)`, `MIN(SIGNUP_MONTH)`, and `REGR_SLOPE(...)` together return
`discloses=False` when the minimum is a datetime. Removing that date projection returns
`discloses=True` for the count and slope. No warehouse or model request is needed for this check.

Updated both tool descriptions (`liveread/tools/live_read.ts` and `liveread/mcp.py`) to say
that one unsupported column withholds the entire result. They now tell the model to return
regression statistics and numeric counts together, with date or text bounds in a separate
display query only if needed. The disclosure policy is unchanged. This is preventive guidance,
not proof that a live model will obey it or that a fixed number of seconds will be saved.

The 13 existing live-read protocol tests pass, with no skips. Ruff and diff checks pass.
Both tool-source SHA-256 values were unchanged across the run. Scoped review found no additional
issue in those two changed paths; no new tests were added merely to assert the instruction text.

The initial remaining 285 seconds (487 minus four complete request intervals totaling 202)
could not be called SQL time. The subsequent gateway-log recovery below explains most of it.
Request latencies do not separate provider queuing, model computation and response transport.

Local gateway access works, but its configured host is `sage.gcp.cs.domino.tech`, not this
workspace's `apps.cloud-dogfood.domino.tech`. No inference benchmark was run on that different
gateway: it would not establish the speed of the user's route. The original cloud-dogfood
gateway logs were then recovered through its signed-in UI, as described below.

## Recovered original gateway timings

Read the cloud-dogfood [gateway Logs & Audit](https://apps.cloud-dogfood.domino.tech/apps/llm_gateway/#logs)
on September 20, after the workspace restart. The UI reports gateway v2.0.12. All rows below
show alias `GLM 5.3 OR`, user `subir_mansukhani`, status 200 and streaming. Cost detail identifies
`z-ai/glm-5.3-flash`. These are original calls, not a new benchmark or the cumulative counter
in #471.

The six large regression requests align with the original turn and its tool results. The last
four also align with the four saved request intervals, to the precision of their whole-second
timestamps. The gateway UI does not expose a thread tag here: attribution uses the matching
user, alias, time window and those four anchors, not a direct thread-ID join.

| Regression log time (UTC) | Latency | Gateway row ID |
|---|---:|---|
| 06:12:18 | 62.052s | `19717037175f44c7a650adb4403ad69f` |
| 06:15:13 | 163.683s | `98912adf897043bd86c3305f91331655` |
| 06:15:36 | 13.093s | `958a65787cfa4db7823c39b9a29a86c9` |
| 06:15:57 | 19.036s | `3c440546695640b587e45996e55791a4` |
| 06:17:19 | 71.459s | `8b08b682db8f4ceea9696fe384e48550` |
| 06:19:11 | 98.935s | `95ebbcbe3c8d405389ec60570ca32ddf` |

These six sequential main calls total **428.258s**, about **88% of the 487s turn**. The two
newly recovered calls explain **225.735s** of the previously unexplained time. That leaves
58.742s outside the six main calls, not 285s of presumed database work.

Two smaller calls end at 06:11:08 (3.410s, row `fb5f21eadfd24ca992423c6b225121b6`)
and 06:11:17 (6.658s, row `023ba97f57754f25938c1ea721849ad3`). The latter overlaps
the first large call when its start is reconstructed from end minus latency. Do not add all
eight latencies and subtract that sum from elapsed time. Their exact roles are not shown in
this UI, so the conservative split below leaves them in the remainder.

| Turn | Total | Main model requests | Query-result windows | Other time |
|---|---:|---:|---:|---:|
| Regression | 487s | 428.258s (6 calls) | about 41s | about 18s |
| Distinct count | 37s | 20.343s (2 calls) | about 11s | about 6s |

**Query-result windows are not pure SQL execution measurements.** They run from the preceding
model completion to the saved data result. They include dispatch, warehouse work, retrieval,
disclosure checks and result handling. Whole-second log times and delayed receipt writes limit
precision. Other time includes small model calls, setup and gaps; it is not all server overhead.

Regression result windows:

- 06:12:18 → 06:12:29: about 11s, event-frequency result.
- 06:15:13 → 06:15:20: about 7s, signup-event result.
- 06:15:57 → 06:16:08: about 11s, regression result at 06:16:04 and cohort result at 06:16:08.
- 06:17:19 → 06:17:31: about 12s, numeric-only regression result.

Count model calls end at 04:03:28 (11.087s, row `e66484cd185440e68c7031369f0bad0d`)
and 04:03:48 (9.256s, row `6b770468ebbc49d39f3c3a3dd40203a8`). Its result arrives
at 04:03:39, about 11s after the first call. Smaller calls end at 04:03:15 (2.094s,
row `3ce76d36703f417a8a57b9a6101e0762`) and 04:03:22 (5.366s,
row `c07cd845b7de4540934a360c02e55cd4`). The 5.366s call overlaps the 11.087s call;
it must not be added to the elapsed-time split.

The local gateway source corroborates completion-time interpretation: `routes/gateway.py`
measures the stream through its drain, then schedules `log_request_bg`. This source was read
from the existing `sage-pii-worktrees/landing/gateway` checkout; it is not a verified checkout
of the deployed binary. The four matching original request intervals are the live cross-check.

## What this changes about the next fix

Model latency is the main target for the regression. The 163.683s call reports 6,264 output
tokens. The first 62.052s call reports 2,818. The final 98.935s call reports 3,669 output
tokens, even though the numeric regression result was already available at 06:17:31. That
final call has 19,968 cached input tokens and only 643 uncached input tokens: input caching
alone did not make this call fast. The log does not split reasoning from visible output or
provider queue time from generation, so it does not prove which of those caused the delay.

The existing local fixes address wasted calls: include the known timestamp columns, and make
the numeric-result contract clear so the model need not rerun a mixed regression result.
Next, compare shorter-output/reasoning configurations on the same two prompts. The owner later
confirmed GLM is the only working model. Verify correctness and the signup-month definition too.
Do not switch the user's model based on unrelated Sonnet rows or assume a supported reasoning
setting from the model name. No model setting was changed in this investigation.

For the count, the model spent about 20s before and after a query-result interval of about
11s. A narrower count query could avoid extra email and username distinct counts, but its
benefit is unmeasured. A fresh trace with warehouse query IDs is still needed for pure SQL
duration and query-plan analysis. It is no longer needed to establish that the original
regression was dominated by model requests.

This follow-up changed only this report. No new inference, warehouse query, deployment, or
test run was performed. The existing focused-test results above still apply to the code fixes.

## GLM controls and direct Snowflake measurements

The owner authorized the next fixes and access through Loom's Snowflake client. Only GLM was
used. The local client at `domino-loom/src/loom/ingest/snowflake_conn.py` connected with Okta;
the owner completed sign-in. No Loom source was changed.

### SQL is seconds, not minutes

The DDL_STAFF role cannot read `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`, and its visible
information-schema history contained none of the original requests. These are **fresh**
measurements on `HUMANS`, X-Small, against `DWH.MARTS.MIXPANEL__EVENT` (132,293,377 rows).
They are not the original Domino connector's timings or proof of its warehouse configuration.

| Query | Snowflake total | Compilation | Execution | Client wall |
|---|---:|---:|---:|---:|
| Distinct user identifier only | 2.131s | 0.170s | 1.961s | 2.283s |
| User, email, username distinct counts plus total events | 4.021s | 0.260s | 3.761s | 4.108s |
| Numeric regression, first-event month proxy | 7.641s | 5.265s | 2.376s | 8.061s |
| Regression SQL generated by GLM at low | 3.557s | 0.609s | 2.826s | 3.832s |
| Regression SQL generated by GLM at high, revised Chat prompt | 4.598s | 1.350s | 3.079s | 4.965s |

Each query scanned 22,743,152,128 bytes, so none of these was a reused final query result.
Warehouse data-cache and compilation state were not held constant. The first three had zero
provisioning and overload queue time; the last two had 0.122s and 0.169s provisioning time.
Query IDs and all metrics are in [the measurement record](2026-09-20-glm-measurements.json).

The narrow count returns 1,377,272. All three regression statements return that user count and
the original slope 0.03259377661525074, intercept 74.5285970296367 and R²
2.7458723518590664e-10. Thus a compact numeric query can reproduce the fit. The high-effort
generated query also returns numeric predictor/response means; it has no date-valued final
projection that would withhold the numeric result.

### The missing GLM effort control

The [Z.ai model card](https://huggingface.co/zai-org/GLM-5.3-Flash#note) documents `low`, `high`
and `max`, with `max` as the default. Sage's measured alias table had no `GLM 5.3 OR` row,
so the UI could not offer those choices. The first local prototype automatically sent high;
a real-shim replay verified that override. During commit review, #477 exposed the conflict:
“Model default” must leave the effort unset. The automatic override was removed before commit.

An existing credential in the Sage backend's local configuration had a cloud-dogfood JWT issuer.
It was selected in process for that host; no credential was printed, copied into this repo, or
sent to the other gateway. `/v1/models` confirmed `GLM 5.3 OR` maps to `z-ai/glm-5.3-flash`.
Direct streaming probes verified that `reasoning_effort` reaches this route: low, high and max
each produced a completed function call. Low and high generated regression SQL that was then
executed successfully in Snowflake. Low/high/max were also exercised beside the real
`live_read_query` schema during final-answer calls.

The final change publishes all three measured levels. Select **High explicitly** to reproduce
the high-effort probes. Model default leaves the field unset with #477; no hidden GLM default
is added. Explicit Chat picks and Ask assignments retain their chosen level. Build without an
assigned effort also uses the provider default. The alias match uses the existing case-sensitive,
provider-prefix handling.

### Live model observations, including contrary evidence

All probes used the same dogfood GLM route, temperature 0.7 and output limit 8192. The full-prompt
fixture used the real Chat instructions and query-tool schema, with the already computed numeric
fit supplied as the final-step input. It did not replay the original full OpenCode conversation.

| Fixture | Effort | Wall | Completion tokens | Reported reasoning tokens |
|---|---|---:|---:|---:|
| Original full prompt, fit statistics supplied | low | 41.336s | 370 | 15 |
| Original full prompt, same fit statistics | high | 50.390s | 408 | 5 |
| Original full prompt, same fit statistics | max | 94.375s | 3,951 | 3,543 |
| Revised full prompt, final result | high | 9.148s | 259 | 0 |
| Revised full prompt, same final result | max | 9.546s | 253 | 0 |
| Reproducible probe, revised prompt | high | 8.902s | 301 | 0 |
| Reproducible probe, same revised prompt | max | 9.180s | 214 | 5 |

The large max call demonstrates that reasoning can consume almost the whole original final-step
budget. **The later matched high/max pair does not demonstrate an effort-only latency gain:**
both finished in about 9s. Provider routing, generation speed and input-prefix caching varied.
The 94s sample also overlaps two exploratory shorter-prompt requests in its last 27s; it is
not an isolated throughput baseline. Do not report these samples as a proven 10× end-to-end gain.

The first direct control was a 0.374s gateway response-cache hit and is excluded from latency
claims. A nonsense-effort streaming request returned HTTP 200 but no terminal completion and is
also excluded. Successful rows require a terminal finish reason and `[DONE]`, not just HTTP 200.
The saved replay script adds a unique request `user` value to avoid gateway response-cache hits;
it does not disable the provider's input-prefix cache.

With the revised full prompt and high effort, the count query took 2.610s to generate and
selected only the requested distinct-user count. The regression query took 8.402s to generate;
it was the statement validated in Snowflake above. These are isolated steps with an explicit
first-event proxy for the regression, not an end-to-end rerun of the user's ambiguous signup
question. No alternative model, shared gateway setting or running workspace was changed.

### Prompt changes and limits

Chat is now told to query the requested quantities and necessary validation checks, to stop
after the result answers the question, and to keep the final numeric explanation short. The
count guidance avoids extra distinct email/username counts. The regression guidance keeps the
numeric fit together and retains checks needed to define the predictor. `opencode.json` mirrors
the Chat template exactly.

**The prose checks did not all pass.** Some low replies misread the intercept or converted dates
incorrectly. High preserved the core coefficients, but some replies at high and max still added
unsupported claims about the direction of exposure bias or an invented observation range. The
120-word instruction was not consistently obeyed. This is a soft instruction, not a hard output
limit or a guarantee of statistical interpretation. The visible replies are retained in the
measurement record rather than hidden behind a passing SQL check. The speed work does not fix
all of those interpretation errors.

### Validation and handoff

Merged remote main `ef0585489bcf766dc2d70e637fe4e39e779a3d2f` locally with `--no-ff` before
focused testing; local merge HEAD is `ce681ea805ecd4c94daaa50657bfde7f4dd1d4c4`. Thus this
branch now includes the landed #471 counter fix. None of the original timing attribution used
that old cumulative counter.

The earlier prototype’s seven focused files reported **123 passed, no skips**, in 6.13s.
Its nine new effort cases covered
bare/prefixed GLM defaults, all three explicit choices from the Chat pick and Ask assignment,
and Build without an assigned effort. Together with the earlier two schema cases, that is
112 pre-existing cases plus 11 new cases in this selection. That result predates the #477
conflict resolution and is not the final commit gate. The earlier
#471 suite claim was honored and our marker was released after the focused run.

All nine changed source/test/config SHA-256 values were unchanged across the run. A subsequent
comment-only clarification removes an accuracy overclaim from the default's rationale. Ruff
passes on the changed Python paths and the standalone probe; `git diff --check` passes. The
probe's `--help` and its live high/max run both succeeded. Scoped review found no further code
defect; the model-prose limitations above remain open.

Reproduce the final-step measurement with the backend Python environment after setting
`GATEWAY_BASE_URL` and `GATEWAY_API_KEY` for the same deployment:

```sh
python scripts/glm-chat-latency-probe.py --efforts high max
```

The live workspace is unchanged. Still required after the local commit checks: landing, deployment,
and fresh original-prompt conversations to measure total latency, SQL/model call counts and answer
quality with the actual workspace context. These probes justify the focused changes and expose
their limits; they do not replace that end-to-end check.


### Final commit checks and conflict resolution

Coordinated with #477 in the `ada0` worktree. Its commit `a33a49e9` removes the hidden
Chat Low fallback. Merged it locally with `--no-ff` at `9c4d5744`, including remote main
`51a115d5`, before the final run. The shared files merged cleanly. The proposed hidden
GLM High fallback is removed; GLM offers Low, High and Max as explicit choices, and
Model default leaves the effort unset. This prevents adding GLM's capability row from
activating the old hidden Low fallback.

The full combined run collected **7,413 cases: 7,406 passed, 3 failed, 4 skipped**, exit 1,
in 705.34s. The prior #477 full run collected 7,397. The net increase of 16 is reconciled
by file: two new schema cases, eleven new GLM cases, one existing measured-alias case
instantiated for GLM, and two extra existing tool-description checks generated from the
longer TypeScript description. Changed parameter IDs are not counted as extra cases.
All **132 cases in the seven focused files passed**, with no skips.

The full gate is **not green**. The failures are the real OpenCode sales Chat flow (timeout
with zero model calls), sales Build flow (server startup timeout), and direct-read flow
(server startup timeout). These same three failures were observed in #477 before this
latency patch; #477's final repeated runs retained the first two. They are tracked in #478.
The two other real OpenCode flows passed. All five ran; none was silently skipped. The
four skips are two opt-in Chromium checks and two CI-only checks. No unrelated runtime
repair or repeat-until-green run was added to this task.

The command ran from the main repository root, with explicit candidate test/config/import
paths and `python -P -m pytest`. Import assertions checked this worktree's source paths.
A temporary dependency symlink made the pinned OpenCode binary available and was removed
after the run. The root checkout was not modified. All 952 tracked/candidate file hashes
matched before and after the run; manifest SHA-256:
`d41ba82c416d600605a9ff2b6631db740a0917cd6edeb664805c2c58b9860341`.
Only this Markdown result section was appended after the test run; runtime sources,
configuration, tests and measurement data remain byte-identical to the tested files.

Regression checks: the earlier schema truncation failed both new schema cases. Removing
GLM's measured capability row failed all six explicit-choice cases. Restoring the old
hidden Low fallback failed all four GLM Model-default cases. Both temporary GLM faults
were restored before the full run. Repository `make lint` and `git diff --check` pass.
Scoped review of the eleven feature paths found no further implementation defect. The
model-prose errors and unmeasured live-workspace speed gain described above remain open.

Evidence logs and manifests are `/tmp/sage-472-full.log`, `/tmp/sage-472-full.xml`,
`/tmp/sage-472-full-before.json` and `/tmp/sage-472-full-after.json`. Commit and merge-tree
identity are recorded in the #472 mailbox. This is a local commit, not a deployment or a
passing full landing gate. Landing, #478 resolution and fresh original-prompt workspace
measurements remain outstanding.
