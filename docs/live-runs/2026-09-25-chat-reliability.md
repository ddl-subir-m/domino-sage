# Chat reliability replay — 2026-09-25

Issue: #557, P11 Chat subset. One trial per model and prompt. This checks real model and OpenCode delivery with synthetic local data. It does not validate production Snowflake access.

## Tested bytes and route

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
| haiku | artifact | 10.153 | answered | Pass: 150/100; total 250; 1 cards | 0 |
| haiku | arm_source | 9.311 | answered | Asked for missing sources | 0 |
| haiku | arm_reply | 2.426 | investigation offer | One offer; no builder run | 0 |
| haiku | arm_accept | 58.036 | answered | Fail: no customer answer; 6 working cards | 0 |
| haiku | arm_duplicate_accept | 0.014 | stale question | Rejected; no gateway call | 0 |
| mimo-v2.6-pro | direct | 12.583 | answered | Pass: 42 | 0 |
| mimo-v2.6-pro | artifact | 27.689 | answered | Pass: 150/100; total 250; 1 cards | 0 |
| mimo-v2.6-pro | arm_source | 232.534 | answered | Asked for missing sources | 0 |
| mimo-v2.6-pro | arm_reply | 3.286 | investigation offer | One offer; no builder run | 0 |
| mimo-v2.6-pro | arm_accept | 377.884 | stopped | Incomplete: stopped; 1 working card | 0 |
| mimo-v2.6-pro | arm_duplicate_accept | 0.024 | stale question | Rejected; no gateway call | 0 |

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

A bounded metadata discovery contract is needed so models do not have to discover the count workaround. It must expose allowed schema facts without opening general raw row disclosure. No such product fix was made in this replay.

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
