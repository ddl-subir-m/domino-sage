# PII data-flow experiment — 2026-09-14

The core data flow works with the existing OpenCode process. A combined operation can run
local code, write a table, and return selected values in one tool call. A separate selection
step added model calls and latency. The prototype also exposed correctness, tracing, and
gateway error-handling gaps that must be resolved before production work is selected.

This is a synthetic design experiment, not a production benchmark or completed PII feature.
No production behavior, gateway policy, deployed app, or existing project was changed.
The agreed product decisions are in [ADR-0052](../adr/0052-the-llm-gateway-is-the-trusted-enforcement-point.md).
Coordination and findings are on [issue 346](https://github.com/ddl-subir-m/domino-sage/issues/346).

## Evidence and reproduction

- [Results JSON](2026-09-14-pii-results.json): measurements, model outputs, diagnostic results,
  source hashes, and a dated gateway rule snapshot.
- [Source and evidence archive](2026-09-14-pii-evidence.zip): prototype scripts, exact synthetic
  requests, responses, stored transcripts, artifacts, failure records, and SHA-256 manifest.
  Extract into `/tmp` to recreate `/tmp/sage-pii-prototype/`.
- Working files also remain in `/tmp/sage-pii-prototype/` on the experiment machine.

Sage baseline: `3e39a36` (full hash in results). OpenCode and plugin SDK: 1.18.4.
Gateway source inspected: `4387d1b0df5693d6dec36cf65e12a9fb5a8e67db`. This source snapshot
does not prove the deployed gateway runs identical bytes.

The scripts use the installed OpenCode binary, `sage-chat` configuration, Sage's real
`EnforcementShim`, and pooled `OpenAICompatibleClient`. They run in isolated temporary git
directories. Model aliases are held constant across each task's steps. Title generation is
stubbed locally; web tools are excluded. The existing tools and skill catalog otherwise
remain available. Only experimental data files contain the rows being tested, all synthetic.
Credentials are loaded in memory from the existing backend `.env`, never printed or archived.

From the repo root, using its backend Python environment:

```sh
python /tmp/sage-pii-prototype/run.py
python /tmp/sage-pii-prototype/run-final.py
python /tmp/sage-pii-prototype/run-final.py --task text
python /tmp/sage-pii-prototype/probe-payloads.py
python /tmp/sage-pii-prototype/probe-coverage.py
python /tmp/sage-pii-prototype/hook-probes/probe.py
node /tmp/sage-pii-prototype/gateway-probes/probe-app-helper.mjs
python /tmp/sage-pii-prototype/gateway-probes/probe-redaction.py
```

These are captured experiment recipes, with machine-specific installed-runtime paths.
The last three commands use only a local scripted provider or mocked functions. The first
five make real gateway requests. The scripts are throwaway probes and must not be deployed.

## 1. Tool output can have a separate model view

The real OpenCode `experimental.chat.messages.transform` hook was loaded as a plugin.
A local scripted provider drove actual tools so their selection was deterministic.

| Operation | Stored transcript | Next model-bound request |
| --- | --- | --- |
| Read synthetic CSV | Contains synthetic email | Receipt; email absent |
| Bash prints the CSV | Contains synthetic email | Receipt; email absent |
| Read source code | Original code retained | Original code retained |
| Read missing data file | Original error retained | Error receipt |
| Task subagent result | Synthetic email retained | Receipt; email absent |
| Explicit value selection | Selected email retained | Selected email included |

The child's own bash result was also filtered before its next model request. Six parent
cases completed; the task case included two child requests. This proves stored-transcript
separation, not browser rendering. The child email was supplied by the scripted provider
to test parent handling; it does not prove a real child model had obtained that value.

Direct invocation confirmed two uncovered paths: synthetic user messages containing
background-task results, and values already present in tool-call arguments. An interrupted
bash error also needs its `metadata.output` replaced, not only its error string.

A generic stdout filter cannot reliably infer which files were read, which columns were
used, or which records contributed to a result. The prototype's source descriptions are a
fixed synthetic manifest. They are not a general provenance system. Images, arbitrary MCP
tools, compaction, and every background-result path were not validated end to end.

## 2. Keep calculation and selected results in one operation

The sales fixture has 12 rows, three columns (`region`, `revenue`, `email`), and exact totals:
North 360, South 420, grand total 780. The final task explicitly requests the grand total.

The first candidate returned a receipt from bash, then required a separate `data_values`
call to obtain totals. The final candidate provides `analyze_data`: the model supplies local
Python, the result file, selected columns, and a purpose together. The operation executes
the code and returns the selected result. No separate model call classifies the task.

| Model | Current tools: calls / task time | Separate selection: calls / task time | Final combined operation: calls / task time |
| --- | --- | --- | --- |
| Sonnet | 2 / 56.88 s, refused; no artifact | 3 / 12.65 s, correct | 2 / 101.79 s, correct; includes 90.46 s before first gateway request |
| Gemini Flash | 2 / 6.62 s, correct | 4 / 12.93 s, correct | 2 / 6.08 s, correct |
| Gemma 4 31B | 2 / 14.86 s, correct | 3 / 17.47 s, correct | 2 / 18.90 s, correct |

In the final combined run, time from first gateway request to task completion was 11.33 s
for Sonnet, 5.43 s for Flash, and 18.27 s for Gemma. These are not total user wait times.
The corresponding time spent within gateway calls was 10.43 s, 4.72 s, and 17.49 s.
All three wrote correct tables and gave correct totals. No synthetic email reached the
gateway in these final runs.

Sonnet's baseline read the CSV and sent its rows on the next request, which returned an
actual gateway 400 for Block PII. The experiment relay had already opened an SSE response
and closed on the exception. Thus it establishes the data transfer and gateway refusal,
not the exact production Workbench error display or recovery behavior.

**Verdict:** the combined operation removes the extra selection round. Flash stayed near
its baseline in this small test. Gemma did not improve in elapsed time despite two calls.
There is no evidence here for a universal speed gain or a production p95 claim.

Local startup delays were substantial and variable. Early runs spent about 52 s initializing
before the first model request; the final Sonnet sales run spent 90.46 s there. Calling
`/agent` did not fully warm the location services. The cause remains unproven. This is
separate from model latency and must not be hidden by quoting only gateway time.

## 3. Local processing can still produce a wrong answer

The text fixture has 12 complaints: four late deliveries, four damaged items, and four
unexpected charges. Email is a separate column that need not be selected.

The first combined candidate failed with Gemma. It invented a keyword classifier without
reading the complaint text. Its delivery keywords included `arrive`, so “Item arrived
broken” was counted as delivery. It reported Delivery 8 and Pricing 4, omitting damaged
items. Every source row was processed, but the answer was wrong.

The final experiment made the tool contract explicit: inspect actual text and record IDs
before semantic classification, and use values already returned instead of fetching them
again. It also clarified that the result path is the existing workspace artifact, which
removed a Sonnet retry caused by selecting an out-of-workspace temporary CSV.

| Model | Model calls | Whole task | Before first gateway request | After first gateway request | Result |
| --- | --- | --- | --- | --- | --- |
| Sonnet | 3 | 66.05 s | 52.74 s | 13.31 s | Correct: three themes, four each |
| Gemini Flash | 3 | 10.22 s | 0.62 s | 9.60 s | Correct: three themes, four each |
| Gemma 4 31B | 3 | 17.12 s | 0.66 s | 16.47 s | Correct: three themes, four each |

All three selected `id` and `complaint`, then wrote the correct table. No email was included
in their gateway requests. This is a small prompt-and-tool-contract experiment, not proof
that instructions alone enforce correct reasoning or necessary-value selection.

## 4. Coverage needs record IDs, not a claim of completeness

A separate batched probe classified 60 synthetic complaints in three batches of 20, with
at most two concurrent gateway requests. Every response had to return every input ID
exactly once, plus a category. Local code checked the IDs and categories against the fixture.

| Model | All 60 IDs exactly once | Correct categories | Batched wall time |
| --- | --- | --- | --- |
| Sonnet | Yes | Yes | 7.13 s |
| Gemini Flash | Yes | Yes | 10.58 s |
| Gemma 4 31B | Yes | Yes | 15.57 s |

All produced 20 delivery, 20 damage, and 20 billing results. Deliberately removing a batch
or duplicating an ID was rejected. This proves the coverage check on these outputs, not
semantic accuracy for arbitrary text or performance on 10,000 records. Reading every row
and classifying every row correctly are separate conditions.

The first overall coverage checker mistakenly passed an expected-ID dictionary directly
to `Counter`, treating its category strings as counts. The checker was corrected and the
unchanged captured model outputs rechecked without new calls. The initial results and
correction are preserved in the evidence.

## 5. Gateway and deployed-app gaps

Nine local probes used actual source: six cases against the compiled published-app helper
with mocked fetch, and three against the gateway's extracted redaction function. Their
assertions reproduce current behavior; they are not passing acceptance tests for the design.

- `appLlm.ts` discards the body of a guardrail 400 and reports “Try again in a moment.”
- A partial SSE answer followed by an error is returned as success. Truncated EOF without
  a completion is also returned as success.
- The helper discards fallback/model evidence and returns only answer text.
- Gateway redaction rewrites the first user text but leaves synthetic values in a tool
  result, a later user message, and a later text part unchanged.

Source inspection also shows that input and output denials use the same public
`guardrail_blocked` error type. An output denial can occur after provider completion.
Streamed output is delivered before output guardrail checks, which are then log-only.
Cache hits and fallback further prevent a configured alias from proving fresh model receipt.

Relevant sources: `template/react-vite/src/appLlm.ts`, gateway `routes/gateway.py`,
`routes/usage.py`, `services/fallback.py`, and `services/policy_engine.py`. Exact source
hashes and probe methods are in the JSON and archive.

The live gateway snapshot still scoped Block PII to selected aliases. Raw synthetic emails
were refused on Sonnet and Flash; the Gemma alias accepted them. This is a dated policy
observation, not evidence that data is safe or a reason to switch models after a refusal.
Selected totals and selected complaint text succeeded on all three in simple direct calls.

An initial direct probe fabricated prior tool-call history. Flash rejected it because the
required thought signature was absent. That was an invalid experiment shape for Flash,
not a model capability failure. The corrected comparison supplied data as ordinary user
content; the real OpenCode runs separately tested valid tool traffic.

## What to carry into implementation planning

1. Extend the existing data tools with a combined local-computation/result-selection
   contract. Preserve ordinary code reads and useful diagnostics. Avoid a mandatory extra
   model round for each result.
2. Make record coverage explicit for batched text work. Keep semantic correctness checks
   separate; a `complete` flag or output-row count cannot establish source coverage.
3. Record data-use events at data operations and the gateway boundary. Until evidence is
   stronger, the UI can say “Regional totals submitted through the LLM Gateway” and label
   the requested model. It cannot always say the named model received those values. Keep
   file/model chips, but do not use a green completion state for an unknown outcome.
4. Fix the published-app helper's refusal and stream-completion handling before rollout.
   Gateway redaction and response evidence need gateway work. Evaluate output enforcement
   separately from input enforcement; client-side copy cannot fix missing server evidence.
5. Keep the all-tool and refusal-recovery promises as open engineering work. The hook is a
   useful integration point, not a complete data provenance or sensitive-value detector.

## Limits and work not completed

No production changes, migration, policy mutation, deployment, or full repository test suite.
No completed automatic recovery of an arbitrary blocked conversation: a poisoned Recall
cannot simply be sent back to ask the model for a recovery plan. Local artifacts may remain
usable, but preplanned local recovery and user-approved Recall changes need further proof.

No live deployed-app test across a real policy change, no UI/browser rendering check, no
general CSV/dialect/parser validation, no permission or path-sandbox claim, no complete
background-task/image/MCP/compaction coverage, and no large-data/load/p95 benchmark.
The prototype executes Python in the same local environment as the existing agent; its
lexical path checks are not a security boundary. Trace manifests are fixture descriptions,
not independent evidence of every file read by arbitrary code.

The model measurements are single small runs. Some experiments overlapped in time, provider
load was uncontrolled, and prompt caching may apply. Prompt/tool changes and cold-start
differences prevent a causal speed claim. All failed candidates and setup failures are kept.
Further testing should target the unresolved questions above, not repeat the already-proven
happy-path examples as if that would establish broader guarantees.
