# Chat reliability replay — 2026-09-25

Issue: #557, P11 Chat subset. Current result: the catalog disclosure fault is repaired and directly verified with Haiku and the control model. Xiaomi still has unresolved malformed-tool observations from two capped turns; the later paired-boundary probe did not reproduce them. The sections below keep the tested revisions separate.

Scope: Each matrix contains one trial per model and prompt. This checks real model and OpenCode delivery with synthetic local data. It does not validate production Snowflake access.

## Original tested bytes and route

- Revision: `8b0654db055b435771d161df7326255a8e8d5e01`; tree: `29133f87dd72572011bfc9adde75f476b133d3f6`.
- The branch contains P1/P3/P5/P6/P7/P8a and #555/#556. P2 main/P4/P8b and later changes were not yet present during this run.
- The runner reused `tests.opencode_server._opencode_server`, the installed OpenCode binary and its process lock, `control_app`, the native provider, `OpenCodeClient`, `Orchestrator.chat_stream`, and installed Sage tools/skills. Fresh synthetic projects only. No saved app or customer export was used.
- Real gateway credentials were loaded only in the Python process. They were not printed, written into the evidence, or passed to the OpenCode child environment. Catalog models were read from the real alias endpoint.
- The synthetic resource provider executed SQL against DuckDB and exposed three small tables plus metadata. Production disclosure, receipts, artifact publication, history and final events remained in the path. `keep_rows=true` affects persisted rows; it does not bypass model disclosure.
- The original runner intended a five-minute per-turn cap but called the workspace stop method instead of turn stop. It had no remote or control plane, so this only saved its synthetic project. Xiaomi was then stopped through the actual aimed turn endpoint at 377.884 seconds. The corrected replay uses stop_build; production deadlines were not changed.

## Models

| Catalog alias | Catalog and observed builder model | Native builder wire protocol |
|---|---|---|
| `gpt-5.4` | `gpt-5.6-sol` | Responses |
| `haiku` | `claude-haiku-4-5-20251001` | Messages |
| `mimo-v2.6-pro` | `xiaomi/mimo-v2.6-pro` | Responses |

Intent and handoff helper calls use Chat separately. A catalog mapping is not a provider receipt: the table above also matches model IDs actually observed in builder response events. Data-use receipts that say delivery/serving-model `unknown` remain unknown; the runner does not rewrite them.

## Fixed prompts and expected results

1. `What is 17 + 25? Reply with the number only.` Expected: `42`.
2. `Create a table showing total revenue by region from @sales.csv. Also state the grand total. Use all rows.` The synthetic CSV has North 100/50 and South 80/20. Expected: North 150, South 100, total 250.
3. `Fuse data from sfdc cases and gong transcripts and tell me which of our customers have asked for ARM support?` No source is initially attached. Expected: source clarification.
4. `the data warehouse is here @Snowflake-Data-Warehouse`. Attach that conversation source. Expected: one investigation offer for the original question.
5. Accept the offer with its task ID, continuing the original prompt. Expected: discover and use `SFDC__ACCOUNT`, `SFDC__CASE`, `GONG__CALL_TRANSCRIPTS` in `DWH.MARTS`; answer Synthetic Acorn (1 case, 1 call), Synthetic Birch (0 cases, 1 call), Synthetic Cedar (1 case, 0 calls).
6. Submit the same accepted task ID again. Expected: stale-question response and no second model run.

## Result table

| Alias | Case | Seconds | Final decision | Answer/artifact check | Repairs |
|---|---|---:|---|---|---:|
| gpt-5.4 | direct | 7.769 | answered | Pass: 42 | 0 |
| gpt-5.4 | artifact | 12.102 | answered | Pass: 150/100; total 250; 2 cards | 0 |
| gpt-5.4 | arm_source | 5.388 | answered | Asked for missing sources | 0 |
| gpt-5.4 | arm_reply | 0.172 | investigation offer | One offer; no builder run | 0 |
| gpt-5.4 | arm_accept | 66.466 | answered | Pass: correct three customers; 1 answer + 3 working cards | 0 |
| gpt-5.4 | arm_duplicate_accept | 0.015 | stale question | Rejected; no gateway call | 0 |
| haiku | direct | 5.509 | answered | Pass: 42 | 0 |
| haiku | artifact | 10.153 | answered | Pass: 150/100; total 250; 1 card | 0 |
| haiku | arm_source | 9.311 | answered | Asked for missing sources | 0 |
| haiku | arm_reply | 2.426 | investigation offer | One offer; no builder run | 0 |
| haiku | arm_accept | 58.036 | answered | Fail: no customer answer; 6 working cards | 0 |
| haiku | arm_duplicate_accept | 0.014 | stale question | Rejected; no gateway call | 0 |
| mimo-v2.6-pro | direct | 12.583 | answered | Pass: 42 | 0 |
| mimo-v2.6-pro | artifact | 27.689 | answered | Pass: 150/100; total 250; 1 card | 0 |
| mimo-v2.6-pro | arm_source | 232.534 | answered | Asked for missing sources | 0 |
| mimo-v2.6-pro | arm_reply | 3.286 | investigation offer | One offer; no builder run | 0 |
| mimo-v2.6-pro | arm_accept | 377.884 | stopped | Incomplete: stopped; 1 working card | 0 |
| mimo-v2.6-pro | arm_duplicate_accept | 0.024 | stale question | Rejected; no gateway call | 0 |

“Repairs” counts Sage corrective prompts for missing answers or invalid tables. It does not count OpenCode internal tool repair or ordinary model/tool calls.

All result checks read the final table bytes and answer, not only `done.ok`. `answered` means Sage delivered prose or an artifact; it is not proof that the requested analysis succeeded.

## Findings

### Metadata discovery is withheld from the model

Haiku's accepted investigation ran a valid metadata query and found three matching tables. Production `liveread.run._statement` passed its rows through `disclosure.decide`, which refused `TABLE_SCHEMA`/`TABLE_NAME` as stored values. The model received the number of rows and column labels but not the discovered names. It then guessed nonexistent table names and ended with a request for the user to provide the catalog cards.

This is a code and prompt conflict, not evidence that the warehouse lacked tables. `catalogue_read` marks these artifacts as working material but does not change disclosure. `template/skills/investigate-weak-signals/SKILL.md` explicitly tells the model to query catalog names and `ROW_COUNT` without grouping. That exact query shape is also refused. A direct call to the existing disclosure function confirmed:

| Query shape | Catalog recognized | Values disclosed |
|---|---|---|
| Catalog schema/name SELECT | yes | no |
| Skill's schema/name/ROW_COUNT SELECT | yes | no |
| Schema/name grouped with COUNT(*) | yes | yes |

The control model used the aggregate form and reached the correct answer. The accepted investigation prompt permits discovery across relevant conversation sources and says not to ask for another investigation. No one-table stop caused this failure. This is not the cross-turn repeated-measurement condition of #442.

A bounded metadata discovery contract is needed so models do not have to discover the count workaround. It must expose allowed schema facts without opening general raw row disclosure. No such product fix was present in the original replay.

### Xiaomi timing

Xiaomi took 232.534 seconds to ask for missing sources, including builder calls of 89.323 and 124.627 seconds. It loaded the investigation skill and inspected local folders before that clarification. The accepted investigation was stopped at 377.884 seconds after it encountered the same hidden catalog names. The final saved decision was `stopped`, with a working card and no customer answer. This one trial demonstrates latency and an incomplete result; it does not establish a general model latency distribution. The real aimed Stop endpoint returned `stopped=true`.

### Fallback and fixture limits

The initial control-only fixture lacked Snowflake ACCOUNT_USAGE metadata. That run ended with an honest limitation; it is not counted as a successful ARM answer. The next fixture supplied synthetic TABLES/COLUMNS metadata and the control answered correctly.

The fixture's DataSource omitted `connector_type=SnowflakeConfig`. Its direct SQL path worked, but the inherited table-sample path therefore returned unsupported. That message is a fixture defect, not a claim about real Snowflake sampling. Haiku's preceding catalog refusal is independent of this defect.

DuckDB INFORMATION_SCHEMA lacks Snowflake's ROW_COUNT field. Xiaomi first encountered that fixture gap, removed ROW_COUNT, then received the production name-disclosure refusal. The repaired fixture maps parsed metadata references to its synthetic Snowflake catalog fields.

The local OpenCode shell did not have `domino_data`. Python fallback could not access the synthetic in-process warehouse. `live_read.ts` appends a generic Python fallback after read errors, even while the turn prompt points at live_read_query. Some generated commands also changed above the Chat working directory and hit the existing external-directory permission rule. The allowed relative scratch/findings/examples paths existed. Neither this synthetic setup nor its fallback failures prove production SDK access.

### Redundant answer card

The control revenue turn produced two valid answer cards: `revenue-by-region.table.json` from the calculation and `revenue-by-region-final.table.json` from a further model write. Both totals were correct. Haiku and Xiaomi each produced one. Strict validation works, but it does not prevent semantically duplicate output.

## Controlled fault checks

On the same revision, 28 targeted tests passed, zero skipped, in 54.96 seconds. They use actual files/history/events for empty completion, one shared repair allowance, partial-text terminal error, rejected controlled writes with unlinked success prose, later-turn retry, source clarification, and investigation scope. The only warning was the existing Starlette/httpx TestClient deprecation. The run used `-n0 -rs`; no full suite was started. `make lint` passed.

These are controlled faults, not faults injected into a live paid model conversation. All observed live turns are reported separately above.

## Evidence and limits

Machine-local evidence: `/tmp/557-p11-chat-live-v2/report.json`, per-turn event files, synthetic project histories/artifacts, bounded redacted tool trace, `/tmp/557-p11-chat-live-v2.log`, `/tmp/557-p11-controlled.txt`. The first fixture-limited run remains under `/tmp/557-p11-chat-live/`. The temporary runner `/tmp/557-p11-chat-replay.py` adapts the existing host; no second product harness was added. These temporary paths are not durable CI artifacts.

Not run here: production warehouse permissions/data/SDK, deployment, saved-app migration, browser navigation/history return, either stack's Build/preview acceptance, or the integrated full suite. This report covers the Chat subset of P11 only. No push or merge to main was performed.

## Repaired ARM matrix — bc61778a

Revision `bc61778a6f731d42bedbf6e01f0b822870cd553f`, tree
`e475cd9b0c4ee6ac42dff76a30378e2759ae5372`. This includes the first catalog fix and the reviewed
P2/P4/P8b/provider recovery dependencies. The code stayed fixed for the whole run. The source,
source-reply, acceptance and duplicate-acceptance prompts were the same as the original matrix.

The fixture now supplies `connector_type=SnowflakeConfig`, maps parsed INFORMATION_SCHEMA
references to its synthetic Snowflake metadata tables before DuckDB execution, and uses the actual
aimed `stop_build` method at its 300-second cap. SQL execution, disclosure, receipts and artifacts
still run through the production paths. This remains synthetic warehouse evidence.

| Alias | Source clarification | Offer | Accepted ARM | Duplicate acceptance | ARM result |
|---|---:|---:|---:|---:|---|
| gpt-5.4 | 15.169s | 1.396s | 105.241s | 0.015s | Correct three customers and four source matches; 7 cards, 9 reads |
| haiku | 8.959s | 2.536s | 26.257s | 0.017s | Correct three-customer answer and four-match evidence cards; 3 cards, 4 reads |
| mimo-v2.6-pro | 300.055s, stopped | 3.318s | 300.018s, stopped | 0.001s | No answer or artifacts; no warehouse read completed |

All three offers addressed the original question; every duplicate acceptance was stale and made
no gateway call. All turns had zero Sage corrective prompts. Observed builder response IDs were
`gpt-5.6-sol`, `claude-haiku-4-5-20251001` and `xiaomi/mimo-v2.6-pro` respectively.

Haiku directly exercised the fix. Its ordinary table-name SELECT returned all three discovered
names without an aggregate. It then queried the three source tables and produced exactly Synthetic
Acorn, Synthetic Birch and Synthetic Cedar. The ordinary row-disclosure rule still kept customer
values from its UNION query on the answer card; Haiku accurately reported the three-customer count.

The control's task answer passed, but its first `LOWER(TABLE_NAME)` discovery predicate still hit
the old refusal: the initial exception rejected a function anywhere in the statement. It recovered
with grouped counts. That result is not counted as proof of direct metadata disclosure. The final
follow-on below fixes this overly broad predicate check.

Xiaomi made 17 invalid tool calls across the two capped phases. OpenCode recorded incomplete JSON
arguments, including calls ending at `title` and later calls embedding a prior validation error.
These observations remain unresolved. The trace did not capture upstream argument deltas at that
time, so it cannot assign the fault to the model, gateway, Sage relay, SDK or OpenCode assembly.
Both turns closed `stopped`, not success. No model-specific workaround was added.

## Final boundary and control replay — 6a7463a6

Revision `6a7463a6378238911aa1b9bf108a0c93241ef5a3`, tree
`d0e8feb508652f3852d196c210b8a407e069a007`. The final exception applies only to the verified
Snowflake connector. Unknown, custom and other connectors retain their existing disclosure policy.
A predicate or ordering may use LOWER/UPPER; all returned fields must still be direct allowlisted
metadata columns from one recognized source. Returned expressions, functions, joins, CTEs and
nested queries do not gain an exception. No other source code changed during this final live run.

Four final plants failed before the change (one predicate and three connector cases). The final
five-file check passed 214 tests, zero skips, in 5.38s. Lint and diff-check passed; all three changed
file hashes matched before/after. The earlier quoted-identifier and namespace plants remain in
that check. The exact new regression file has 39 cases.

The control replay on these final bytes passed: source clarification 30.223s, offer 1.452s,
accepted ARM 72.049s, duplicate acceptance 0.014s. It made seven warehouse calls and published
seven cards, with zero Sage corrective prompts. The answer again had all three expected customers
and exact source evidence. The duplicate acceptance made no gateway call.

This run directly used the exception: plain TABLES and COLUMNS SELECTs against
`SNOWFLAKE.ACCOUNT_USAGE` returned table/catalog/schema names, row counts, column names, data
types and positions, without grouped counts. The builder response model was `gpt-5.6-sol` for
alias `gpt-5.4`. The allowed LOWER/UPPER predicate path is covered by the behavioral regression;
this live model chose ILIKE predicates instead.

## Xiaomi boundary probe on final bytes

The additional probe repeated only the source-clarification prompt, with a 180-second cap. It
completed normally in 85.584s, asked for source attachment and did not use the cap or a Sage
corrective prompt. Alias `mimo-v2.6-pro` returned builder response model
`xiaomi/mimo-v2.6-pro`. It made four valid tool calls. No attached-warehouse ARM success is claimed
for this probe.

A temporary observer kept only function-call argument delta/added/done fields at the gateway
response and actual `/v1/sage/responses` ASGI output. It did not retain headers, request bodies or
reasoning. Read tokens were masked after joining chunks, so a token split across deltas could not
survive the redaction. The observer's split-token and reasoning-exclusion checks passed.

The same OpenCode call IDs were matched at both boundaries:

| Call ID | Tool | Delta count | Argument SHA-256 at both boundaries |
|---|---|---:|---|
| `chatcmpl-tool-855c19c9b2b996c1` | bash | 1 | `6bb0d8eba9acd29c995ab601858f7d487de0db8b5ad88406703caeef9bd513c6` |
| `chatcmpl-tool-a343e344f2219a15` | live_read_query | 1 | `cfa1a618f4ca786c1db7ae59c1f9e055005fc380d8948cd4aa57f6f07f24300d` |
| `chatcmpl-tool-a7c1eeff9e8b6b45` | skill | 1 | `465fb845a7c30d4b171f9a7655e67babc6849b9d8e8e7f43a5b711180c4b16ab` |
| `chatcmpl-tool-b7076bd68e3183fa` | live_read_query | 1 | `834afd0c6fa45de3a546de28bd1db71329cafa76788546249647c1cbb78ed50c` |

For all four calls, the delta sequences and joined argument bytes were identical at the two
boundaries. Joined arguments were valid JSON. At each boundary they also equaled both the
`function_call_arguments.done` and `output_item.done` full arguments. Sage relay corruption was
not reproduced. This successful probe cannot locate the origin of the earlier malformed calls,
and it does not establish Xiaomi reliability beyond the observed trial.

Both live hosts exited and released the shared OpenCode lock. No more paid Chat reruns were made.
The original failures and two stopped turns remain part of the result, not passes.

Additional machine-local evidence: `/tmp/557-p11-arm-repaired/report.json`, its redacted tool
traces and per-turn histories/artifacts; `/tmp/557-p11-final-probe/report.json`, `wire.json`,
`xiaomi-boundary-comparison.json` and redacted tool traces; `/tmp/557-p11-boundary-final.txt` and
`/tmp/557-p11-boundary-hashes-before.json` / `after.json`. These are temporary local evidence,
not durable CI attachments. This report preserves the decisive counts, identities and limits.
