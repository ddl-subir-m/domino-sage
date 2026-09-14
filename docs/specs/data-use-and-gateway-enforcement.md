# Sage-only data use in Chat, Build, and Built Apps

Status: Sage-only release approved by the user on 2026-09-14. This revision supersedes the earlier cross-repository release scope. Implement the 11 active Sage tasks; defer gateway #32–#34 and Sage #360. Task setup does not itself start implementation.

Published as [issue #348](https://github.com/ddl-subir-m/domino-sage/issues/348), labelled `ready-for-agent`.

## Problem Statement

A person uploads a CSV and asks Sage for analysis. One tool choice displays rows in Domino. Another copies the same rows into a model request, where a gateway guardrail can refuse them. The refusal can then affect later turns because the rows remain in Recall. The person cannot reliably tell which values were used locally, submitted to the gateway, or received by a model.

Avoiding all model access to values is not a solution: some tasks need the actual text. Asking for permission on every transfer adds work without explaining the gateway's changing rules. Local processing also does not guarantee correct output: the experiment processed every complaint but initially assigned some to the wrong category.

Built Apps have related faults. The current model helper loses refusal details and can treat an interrupted answer as complete. Gateway source probes also found incomplete redaction and output checks that occur after streamed content is released. Sage will fix its own data flow and error handling. Gateway defects remain documented upstream limits and do not block this release.

## Solution

Sage chooses local computation, model work through the LLM Gateway, or both to complete the requested task. The gateway is trusted to receive sensitive values for inspection and enforce its current rules. Sage sends relevant data deliberately and avoids unnecessary rows. It does not try to predict the gateway's changing PII rules.

Calculations, tables, and charts use local data processing where practical. Semantic tasks can send the relevant text and record identifiers through the gateway. Users do not approve each necessary transfer. Explicit user limits and administrator restrictions still apply.

An expandable **Data used** detail explains recorded actions beside the result. It uses file and model chips, clear coverage counts, and honest outcome text. After a refusal, Sage preserves useful local results, avoids repeated blocked requests, and asks before changing the task or discarding Recall.

Deliver this behavior for fresh projects, in both Workbench modes and newly generated Built Apps. Existing projects and apps are not migrated or reset.

## User Stories

1. As a Chat user, I want to calculate from my data locally, so that an unnecessary transfer does not block a simple answer.
2. As a Chat user, I want tables and charts to remain available, so that I can inspect results in the form I need.
3. As a user, I want semantic analysis to use the relevant text, so that Sage does not replace understanding with an invented keyword rule.
4. As a user, I want Sage to choose the necessary computation and model steps, so that I do not approve each routine transfer.
5. As a user, I want uploading a file to make it available for my task, so that availability does not mean the entire file is sent to a model.
6. As a user, I want explicit limits on data use to remain effective, so that Sage respects my instructions.
7. As an administrator, I want Dataset and model restrictions to remain effective, so that automatic data use does not remove existing controls.
8. As a user, I want all relevant records included by default, so that a summary does not silently describe only a sample.
9. As a user, I want missing, duplicate, failed, and unprocessed records reported, so that I know the actual coverage.
10. As a user, I want a sample clearly labelled and offered when a limit prevents full work, so that I can choose whether it meets my need.
11. As a user, I want accurate calculations and useful semantic results, so that a complete record count is not mistaken for a correct answer.
12. As a user, I want file and model names shown clearly in Data used, so that I can understand how the result was made.
13. As a user, I want requested models distinguished from confirmed serving models, so that a fallback or cache hit does not create a false claim.
14. As a user, I want uncertain transfer outcomes labelled as uncertain, so that a timeout is not shown as proof that no data left Domino.
15. As a user, I want a clear gateway refusal message, so that I understand why the task stopped and what remains usable.
16. As a user, I want local completion after a refusal when it can satisfy the same task, so that permitted work is not lost.
17. As a user, I want to choose before Sage changes my task or clears Recall, so that recovery does not silently discard information.
18. As a user, I want subagents and background work to follow the same data-use rules, so that delegation does not change the behavior.
19. As a builder, I want source-code reads and useful errors to remain available, so that data handling does not prevent app development.
20. As a builder, I want new Built Apps to handle the gateway responses they receive at runtime, so that refusals and failures after publication have a clear outcome.
21. As an app user, I want an interrupted or refused response marked incomplete, so that partial text is not presented as a successful answer.
22. As a user, I want artifact retention and audience controls preserved, so that model data use does not grant permission to store or publish rows.
23. As a user, I want fast progress across open and closed models, so that the design does not depend on one provider's speed or policy configuration.
24. As an operator, I want measured call counts and whole-task latency, so that performance claims include startup, recovery, and validation costs.

## Implementation Decisions

### 1. Keep one agent system and one trust boundary

- Extend the existing OpenCode integration, live data tools, enforcement shim, and gateway client. Keep Chat and Build on the existing agent system. Retain task delegation, to-do tools, the skill catalog, tables, and charts.
- Treat the gateway as the enforcement authority. Do not add a local PII classifier, a copied policy list, or a preflight request for every model call. A gateway policy snapshot is diagnostic evidence, not a future permission decision.
- Remove the old positive Sample rows grant requirement for automatic relevant-value use in fresh projects. It currently applies to bound Data Source tables, not arbitrary CSV uploads. Do not invent a current sharing switch: the inspected UI has a read-only Sample rows heading.
- Preserve declared Dataset/model restrictions, explicit withholding, authentication, authorization, and Kept rows controls. Data Source and Dataset remain separate domain concepts.

### 2. Combine local work and selected output

- Extend existing data operations to accept source references, the requested computation or selection, selected result fields, record scope, and purpose. Let one operation compute locally, write an artifact, and return the selected result needed by the model.
- By default, a data read exposes useful structure, counts, and a local reference to the model. Actual values are selected when they contribute to the task. Column names and metadata can themselves contain sensitive information; gateway enforcement still applies.
- For semantic work, provide the relevant text and stable record identifiers. Do not substitute unvalidated keyword heuristics merely to avoid a model transfer. Do not require another model call solely to classify the task or fetch a result already returned.
- Validate source access, result location, requested fields, and output shape. Reuse existing workspace and data-access controls. The prototype's lexical path check and Python execution are not a new sandbox.
- Preserve source-code reads and diagnostic information needed for Build. Use known source and operation types rather than a blanket stdout filter or file-extension-only sensitivity rule.

### 3. Separate local results from model context across all supported paths

- Preserve authorized local display and artifact behavior while constructing a separate model view of data results. Keep valid tool-call/result pairs and provider-specific message metadata intact.
- Carry source and operation references through reads, local execution, errors, subagent results, and background results. Process both normal output and error metadata. Apply the same handling before child model calls and parent continuation.
- Test synthetic background user messages, tool-call arguments, images, MCP output, and compaction explicitly. A tool-result hook alone does not cover them. Previously selected values in history must not lose their attribution when reused.
- Use the final outgoing request boundary to check that known data-bearing results follow their declared selection and user restrictions. This is a consistency check, not PII detection. Ordinary user-provided task text may go through the gateway under the agreed trust boundary.
- Do not claim general file lineage for arbitrary code. Record verified inputs and selected outputs from structured operations; mark unsupported lineage unknown. For a supported path that cannot enforce an explicit restriction, stop that affected model operation with a clear explanation. Do not block unrelated local work.
- Complete the path inventory and behavior tests before claiming consistent handling. Disabling useful agent capabilities is not the proposed fix.

### 4. Record coverage separately from correctness

- Create a record manifest for each full-data task, tied to the actual source snapshot or query result. Give records stable task-local identifiers even when source IDs are absent or duplicated.
- Batch semantic work within model and gateway limits with bounded concurrency. Persist task-local batch progress so cancellation, retries, and partial failures have an accurate outcome. Do not retry policy denials unchanged.
- Validate that returned IDs belong to the input and occur exactly once where one output per record is required. Validate the requested output shape. Report excluded, failed, and unfinished records separately from completed ones.
- A source change during processing invalidates a claim of one consistent full-data result unless the reader provides snapshot semantics. Report the condition and use a consistent read before claiming completion.
- Use deterministic checks for calculations and schema constraints. Use labelled evaluation fixtures for semantic quality. Full coverage is never labelled proof of semantic accuracy.
- If resource or time limits prevent full completion, report the limit and offer a clearly labelled sample. Do not silently narrow the task or discard failed batches.

### 5. Make Data used a record of actions

- Record data-use events at data operations and the outgoing gateway boundary. Associate them with the task, result, source references, selection, coverage, and request attempt. Store metadata, not a second copy of raw rows or prompts.
- Distinguish local computation, request attempt, confirmed gateway response, provider forwarding when confirmed, cache use when confirmed, refusal, interruption, and completed answer. A transport failure can leave receipt unknown. HTTP 200 headers alone do not prove completion.
- Keep requested alias and confirmed serving model separate. Preserve available request correlation, fallback, cache, refusal direction, and policy decision evidence. Missing fields mean unknown, not allowed or not sent. Consume the existing gateway contract and tolerate absent fields. Do not require a new gateway contract or call an administrator-only endpoint to fill the gaps.
- Render collapsed Data used beside the result. Expansion gives short sentences and accessible file/model chips. Distinguish source files from generated Artifacts. Chips inherit existing access controls; metadata must not expose sources to a new audience.
- With current evidence, use wording such as: “Calculated from [sales.csv] in Domino. Regional totals submitted through the LLM Gateway. Requested model: [Claude].” Use “sent to [Claude]” only when gateway evidence confirms the serving model and forwarding. Do not describe a gateway as cloud unless its destination is known.
- Keep partial output and uncertain delivery visibly distinct from a complete result. Persist the record with the result under existing access and retention rules; do not let an assistant's narrative overwrite observed events.

### 6. Recover without evading policy or losing context silently

- Classify policy refusals separately from authentication, rate-limit, transport, and provider failures. Preserve a safe, useful gateway reason without copying blocked values into an error message.
- After a denial, do not resend the same blocked payload or change models to escape the rule. Existing normal routing is not permission to retry a refusal elsewhere.
- Complete the same task locally when the available data and computation permit it. Preserve already generated artifacts. If local completion is not possible, state what remains unfinished and offer the existing user-chosen Recall recovery path.
- Do not ask a model to plan recovery by resending poisoned Recall. Ask before discarding Recall or changing the task's meaning. The visible transcript remains distinct from model history.
- Establish recovery using real multi-turn tests, including a subsequent ordinary question after a refusal. The experiment did not prove general recovery.

### 7. Use the existing gateway contract

- No gateway source changes, new gateway deployment, or policy mutation are required for this release. The three upstream gateway tickets are deferred and are not blockers for Sage.
- Treat existing gateway responses as the available enforcement outcome. Show safe refusal details and preserve model, cache, fallback, or request evidence only when present and trustworthy. Missing evidence stays unknown.
- Do not claim that input/output redaction is complete, that every cache/fallback path uses current rules, or that output denial happens before release. The inspected gateway does not establish those guarantees.
- Sage can stop displaying or consuming an answer after an explicit failure and mark prior text incomplete. It cannot retroactively prevent content already released by the gateway, nor infer a missing output-policy decision.
- Preserve existing administrator restrictions on Sage's own routing and publish/preview paths. Enforcement over the gateway's internal fallback and direct calls in deployed apps is deferred with Sage #360. Do not replace it with an unenforceable browser-only promise.
- Gateway acceptance is not sensitivity classification or permission to change artifact audiences. Do not add a competing Sage PII detector or a policy preflight call.

### 8. Apply the contract to new Built Apps

- Update the existing generated-app model helper and preview/deployed request path. Parse structured HTTP errors and streaming error events. Require a valid terminal completion; truncated EOF, refusal, or error after partial text is not success.
- Retain partial text only as explicitly incomplete output and prevent it from being treated as a validated final result. Expose Data used evidence through the existing app integration.
- Preserve the current declared Dataset/model restrictions and viewer authentication. This release does not extend the existing publish-time restriction guarantee to all direct deployed-app calls or gateway fallback; that stronger guarantee is deferred.
- Verify that an app handles changed allowed/refused responses on later requests without a rebuild, using a controlled gateway in tests. Live checks use the unchanged gateway configuration. Report this as response handling, not proof of the gateway's internal policy coverage.

## Testing Decisions

The proposed primary test boundary is the existing user-task flow through real OpenCode tools, Sage, and a controlled HTTP gateway. Assert the actual outgoing request, visible result, artifact, coverage, and next-turn behavior together. Use a scripted provider for reproducible failures rather than relying on a model to choose the same tool each time.

Use two test boundaries: the existing Sage/OpenCode user-task flow with a controlled HTTP gateway, and the actual generated-app helper in preview/browser tests. Existing live-gateway runs supplement these tests without changing its code or policies. Gateway-internal enforcement tests belong to the deferred upstream work. Prefer existing harnesses; do not add a separate agent system.

Prior art includes the live-read receipt/end-to-end tests, enforcement-shim tests using FakeGatewayClient, withheld-file and Recall recovery tests, mid-stream refusal tests, and the preview proxy's loopback gateway. Reuse these patterns, then add real OpenCode and browser coverage where a mocked internal call cannot prove the outcome.

Acceptance checks:

1. **Local calculation:** the synthetic sales fixture produces North 360, South 420, total 780 and a usable table/chart. Requests contain the needed selected totals, not the unrelated email column. Data used agrees with captured operations.
2. **Semantic analysis:** actual complaint text and task-local IDs reach the model, unrelated email does not, and the labelled fixture yields four records in each of the three expected themes. Include the “Item arrived broken” regression.
3. **Coverage:** missing batches, duplicate/unknown IDs, malformed output, cancellation, retry, source changes, and resource limits cannot produce a false complete result. Exercise at least 10,000 synthetic records with a controlled provider; do not infer this from the 60-record experiment.
4. **Message paths:** run actual read, bash/Python output, source-code read, error metadata, child task, background completion, MCP output, image, argument, and compaction cases. Check model requests and preserved authorized local display. Include explicit withholding and provider message validity.
5. **Recovery:** input refusal, output refusal, unknown refusal stage, and refusal retained in Recall have distinct honest outcomes. No unchanged policy retry or evasion by model switch occurs. Local completion works when possible; lossy context recovery requires user choice.
6. **Existing gateway compatibility:** handle current success, refusal, stream-error, truncated-response, and transport-failure shapes. Assert that missing decision stage, receipt, cache, or serving-model evidence remains unknown. Do not assert new gateway-internal enforcement guarantees.
7. **App behavior:** test structured HTTP refusal, partial stream plus error, truncated EOF, cancellation, normal completion, fallback, cache, and unknown metadata. Vary allowed/refused responses in the controlled gateway between requests, without rebuilding the app. This tests app behavior; it does not prove real gateway policy enforcement.
8. **Presentation and controls:** browser checks cover readable chips, expansion, keyboard access, refresh/history, partial results, both Workbench modes, and a new Built App. Existing source authorization, Dataset restrictions, Kept rows, tables/charts, and task/skill capabilities remain effective.
9. **Performance:** repeat fixed tasks on Sonnet, Gemini Flash, and an open-weight model through the gateway. Keep failed runs. Record calls, input volume, whole-task time, startup time, first useful output, and completion separately for warm/cold runs. Small calculation should need one compute/selection step, not a mandatory extra selection round. Report regressions and required enforcement tradeoffs; do not invent a universal latency SLA or derive p95 from a few runs.

Tests assert externally visible behavior and captured traffic, not private helper layouts. Live model evaluation supplements deterministic regression tests; it does not replace them. Only synthetic data is required for acceptance testing.

## Out of Scope

- Migrating, resetting, deleting, or retrofitting existing projects and already deployed apps.
- A Sage copy of gateway PII detection, a static guardrail catalogue, or per-transfer user approval.
- A new agent system, chart language, generic operating-system sandbox, or automatic proof of lineage through arbitrary code.
- Removing charts, tables, skills, task delegation, or to-do support to simplify data handling.
- Changing artifact audiences, Kept rows consent, administrator data restrictions, or shared production gateway policies.
- Gateway source changes, new gateway response contracts, gateway redaction/output/cache/fallback fixes, or a required gateway rollout.
- Extending the model restriction guarantee to all deployed-app direct calls and internal gateway fallback; Sage #360 is deferred.
- Guaranteeing semantic correctness on arbitrary data or a fixed response time across all providers.
- Production implementation, deployment, commit, push, or merge as part of this specification task.

## Further Notes

Accepted product decisions are in [ADR-0052](../adr/0052-the-llm-gateway-is-the-trusted-enforcement-point.md). Preserve the distinctions in ADR-0022 (Recall recovery), ADR-0041 (local rows/model values), ADR-0043 (Dataset/model restrictions), and ADR-0045 (Kept rows).

The [experiment report](../performance/2026-09-14-pii-prototype.md), [results](../performance/2026-09-14-pii-results.json), and [evidence archive](../performance/2026-09-14-pii-evidence.zip) support this spec. Investigation and limits are recorded in [issue #346](https://github.com/ddl-subir-m/domino-sage/issues/346). Baseline Sage commit: `3e39a3636ff0f698f2b8e210405ecd92600c9155`. Inspected gateway commit: `4387d1b0df5693d6dec36cf65e12a9fb5a8e67db`.

The experiment established a useful combined data operation, not a completed enforcement system. It found no universal speed gain: Flash's final small sales run took 6.08 seconds versus 6.62 baseline; Gemma took 18.90 versus 14.86. Sonnet had a large unresolved pre-request delay. Full path coverage, generic Recall recovery, browser display, and runtime policy-change behavior remain implementation acceptance work.

Active delivery is Sage #350–#359 and #361: local data operations, relevant-value selection, coverage, supported message paths, Recall/recovery, app response handling, and integrated Sage-only acceptance. Gateway #32–#34 and Sage #360 remain deferred upstream/runtime work and do not block this release. A configured model name does not prove receipt. Full gateway-enforcement guarantees remain out of scope. Completion of #361 requires the Sage-only acceptance checks, not the deferred gateway changes.
