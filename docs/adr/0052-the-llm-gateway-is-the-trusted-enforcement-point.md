---
status: accepted
---

# The LLM Gateway is the trusted enforcement point

The LLM Gateway's guardrails can change independently of Sage. On 2026-09-14, the user
chose to trust the LLM Gateway to receive sensitive values for inspection and enforce its
rules before forwarding a request to a model. Sage should avoid sending unnecessary values
and handle refusals clearly, rather than reproduce the gateway's changing rules.

The alternative was to require sensitive values to stay outside the LLM Gateway itself.
That would require a separate control before the gateway; a gateway test endpoint cannot
protect that boundary because the values have already reached it.

This decision establishes the trust boundary. The mechanism for selecting values remains
open in the design interview. It does not approve new recipients for artifacts or remove
existing Dataset declarations, model restrictions, or controls on keeping artifact rows
(ADR-0043 and ADR-0045).

## Proceeding with a task

The user accepted that Sage may choose local computation, model requests through the
LLM Gateway, or both to complete a task. It need not ask for a second confirmation before
each transfer needed for that task. An upload alone does not authorize sending the whole
file. Administrator restrictions and explicit user limits still apply.

A model may write local analysis code using column names without receiving the data rows.
Local code can then process those rows and display results. Tasks that need model access
to actual values send the relevant values through the LLM Gateway for enforcement.

## Replacing the per-table sharing requirement

The user accepted replacing the older requirement for a recorded per-table Sample rows
approval with automatic relevant-data use under the same rules as uploaded files. This
revises the model-sharing prerequisite described in ADR-0041; it does not change artifact
retention consent in ADR-0045 or administrator restrictions in ADR-0043.

The current records store only positive table shares. Removing a share removes its record,
so absence cannot distinguish never shared from stopped sharing. The user chose to start
with fresh projects and do no migration now. There is no legacy migration flow in this
scope. This choice does not authorize deleting existing projects or resetting their data.

## Consistent handling across tools

The user accepted the same data-handling rules across file reads, Python output, errors,
and subagents. Local tools can process full data. Their normal model response should
describe the result's structure, counts, and file reference; actual values enter model
requests through a deliberate selection when needed, subject to gateway enforcement.

This is an intended behavior, not proof that the current general-purpose tools can enforce
it. A technical experiment must verify normal agent work, necessary value access, and
avoidance of accidental row transfers without unnecessary model calls. The mechanism must
also preserve useful code reads and debugging information.

## Local analysis and model access

The user accepted local processing as the normal approach for table calculations and
charts. For a sales-by-region chart with an explanation, local code reads the rows and
produces the chart and regional totals; the model receives those totals to explain the
trend. For a task that requires understanding text, such as summarizing complaints, the
model can receive the relevant text through the LLM Gateway.

Sage avoids sending whole files by default. It does not add a separate model call just to
classify every task. Computed results still pass through gateway enforcement when sent
to a model; being a total does not establish that a value is non-sensitive.

## Visible data use

The user chose a collapsed, expandable Data used detail beside the result, with no extra
approval prompt. It comes from recorded actions, not the model's account of its actions.
For example: "Calculated from sales.csv in Domino. Regional totals sent to Claude for
explanation." The named model must be the actual recipient; a request reaching the gateway
does not by itself establish that the model received it. A gateway refusal is shown clearly.

The record describes the actual path. It must not claim rows stayed local if another tool
read sent them through the gateway. The events needed to support this claim remain to be
designed and verified.

File and model names should appear as chips within well-spaced, readable text. The user
requested this presentation explicitly. Source file chips and generated Artifact chips must
retain their distinct meanings; an input file is not necessarily a Sage Artifact. The
detail remains collapsed by default. The exact layout remains to be designed.

## Coverage of large text tasks

The user chose to process all relevant records by default, using batches when needed,
even when a sample would be faster. If time or size limits prevent completion, Sage must
explain the limit and offer a clearly labelled sample. It must never present a sample as
analysis of the entire file. Batch execution and coverage verification remain to be designed.

## Published apps

The user chose to apply the same data-handling rules both while building an app and after
deployment. A published app should process data locally where practical, send needed model
input through the LLM Gateway, and handle refusals clearly, including after gateway rules
change. This extends the design and validation scope beyond the Workbench. It does not
claim that Sage's existing pre-publish controls enforce these rules in a running app.

## Recovery after a refusal

The user also accepted this recovery principle on 2026-09-14: Sage may complete the same
task through local computation when possible, while respecting the refusal. If recovery
would change the requested answer or discard conversation information, Sage must explain
the choice and ask first. This preserves ADR-0022's requirement that clearing Recall is
chosen, not automatic.

Sage must not repeatedly send the same blocked content, switch models to escape a refusal,
or silently remove information and claim the answer is complete. The mechanism for local
recovery remains to be designed; this does not assert that a blocked model can plan its
own recovery through another request carrying the blocked content.

This is a design decision, not a claim that the current gateway implementation enforces
every configured rule correctly. No PII handling implementation is part of this record.

## Technical questions for the next experiment

The product choices above are agreed. These technical questions still need evidence before
an implementation is selected:

- Can data tools return useful structure and local file references while keeping incidental
  row dumps out of model requests, including Python errors and subagent results?
- Can an agent obtain selected values when it needs them without an extra model call solely
  to classify the task, and without breaking normal source-code reads and debugging?
- Can a task recover locally after a gateway refusal without resending refused Recall or
  silently discarding conversation information?
- Can recorded events distinguish local processing, submission to the gateway, confirmed
  model receipt, refusal, and an unknown outcome accurately enough for Data used?
- Can a batched text task prove which records were processed and report incomplete coverage?
- Do the same rules work in a running published app after gateway rules change?

The experiment should use synthetic data and measure task correctness, model-call count,
time to useful output, and total completion time. It must test both open-weight and closed
models. Its results, including failures and capability limits, inform the implementation;
the intended behavior above is not evidence that the current code already supplies it.

## First experiment findings

The user confirmed the shared understanding before the experiment began. Results are in
[the synthetic experiment report](../performance/2026-09-14-pii-prototype.md). The combined
local operation worked on three models without an extra selection round. The experiment
also found a wrong semantic classification, incomplete coverage of tool/message paths,
and published-app refusal/stream handling defects. A prompt correction fixed the small
classification fixture; that is not a general correctness guarantee.

The gateway contract does not always establish which model received particular values:
input/output denials share an error type, and caching and fallback affect receipt. The
Data used example above therefore requires stronger evidence than a configured model name.
Until available, the record must distinguish gateway submission and requested model from
confirmed receipt, and retain an unknown outcome when the evidence is incomplete.

The product decisions stand as intended behavior. Full provenance, general refusal recovery,
and live published-app enforcement across policy changes remain unproven. Production PII
implementation has not started.

## Sage-only first release

On 2026-09-14, the user narrowed implementation to Sage. The gateway remains the trusted
enforcement point, used through its existing API. This supersedes the earlier release plan
that required gateway fixes and stronger post-publish model enforcement.

Sage will provide local processing, relevant-value selection, coverage, supported tool-path
handling, Recall recovery, Data used, and correct app response handling. Missing evidence
about provider receipt, decision stage, cache, or fallback remains unknown. Sage does not
claim to fix the gateway's internal redaction, streaming-output, cache, or fallback behavior.

Gateway issues #32–#34 and Sage #360 are deferred. Existing Dataset/model restrictions and
artifact retention/access controls remain. The stronger guarantee over every direct app
call and gateway fallback is outside this release. App tests prove handling of changed
responses; they do not establish new gateway enforcement. No existing projects are migrated.
