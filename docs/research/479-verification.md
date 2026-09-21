# Issue 479 verification

This report separates the real gateway and harness checks from local policy and UI
checks. No LLM Gateway source, Alias setting, or deployment was changed.

## Research and live behavior

- Gateway version shown in its authenticated UI: **2.0.12**. Exact deployed Git
  revision is unavailable. Read-only source inspected: `4387d1b0df5693d6dec36cf65e12a9fb5a8e67db`.
- `479-native-gateway-cycles.json`: native Claude and GPT tool cycles; signed
  thinking / encrypted reasoning replay; negative signature and route controls.
  Provider-reported thinking/reasoning usage is recorded where present. Missing
  reasoning usage is unknown, not zero.
- `479-native-effort-levels.json`: complete tool cycles for each offered native
  level. Claude adaptive effort and disabled thinking are distinct from default.
- `479-opencode-*.json`: pinned OpenCode 1.18.4, isolated configuration/database,
  v1 prompt path, parallel tool calls, second turn, process restart, compaction,
  and another tool turn. Two synthetic work markers survive. The transition probe
  changes Messages/Responses inside tool cycles and retains provider metadata.
- `479-production-routes.json`: actual Sage control save, scoped HTTP endpoint,
  shared policy, local SDK module and deployed gateway. Chat High on Opus-4.8,
  Low on GPT-5.4, and Low on domino/gemini-3.7-flash finish tool continuations.
- `479-production-auto-rescue.json`: real OpenCode and actual Auto classifier:
  native Claude planning → native GPT after a file write → native Claude after
  a synthetic compiler error. The written marker remains in the final answer.
  The first fixture offered no edit permission and used bash instead of a write
  tool; that earlier run is explicitly excluded as transition evidence.
- `479-production-final.json`: a fresh run with live Alias discovery passed all
  three Chat routes, the production compaction entry point and next tool turn,
  Auto/rescue, and a harness-verified child session. All 21 gateway streams
  completed. Requests replay signed Claude blocks and encrypted GPT items.
- The final SDK-error-fix rerun is recorded in `479-production-limit.json` as
  **failed**, not successful evidence. Anthropic returned an HTTP-200
  `invalid_request_error` stream because the workspace usage limit was reached
  (reset October 1, 00:00 UTC). A tiny direct no-history request confirmed it.
  The owner then stated that only GLM was working; no further native/Gemini
  live calls were made. The endpoint now returns nonretryable HTTP 400 for
  an initial invalid-request stream, verified locally on all three protocols.
- GLM 5.3 OR remains on Chat Completions, with #472's measured low/high/max.
  Its current cloud-dogfood Alias/provider/backing model identity was read again.
  These are compatibility results, not native Responses claims.

## Local behavioral checks

The HTTP tests save each choice through Chat, Build and assignments, then inspect
an actual outbound request at a recording gateway. They cover default/off/explicit
settings, Ask/approval/web policy, stale route/session rejection, Auto and rescue,
refusal before and during streaming, translated Responses rejection, incomplete
output, split events, and cancellation of a bounded producer.

Policy tests retain exact opaque blocks and call IDs, remove withheld carriers,
require a Recall checkpoint on restriction changes, and reject provider-stored or
unknown native content. A title call cannot relabel an old policy fingerprint.
Stateless text-analysis consumers keep their existing text-stream contract.

The installed native SDKs are exercised directly, including malformed events,
safe Gateway errors, HTTP policy/retry errors, a broken stream and cancellation on all three
protocols. The provider boundary removes private payloads from diagnostics while
preserving safe error messages and HTTP retry semantics. Route selection does not
increment model calls; each inference through any existing control increments once.
CI installs the same pinned production codecs for these checks.

The complete pre-correction failure audit is in `479-suite-audit.json`. The two
contract failures repeated independently of distribution. Three real-harness
failures in the exclusion run did not reproduce in the unchanged full repeat.
A briefly overlapping, interrupted attempt is excluded from verification.
Final gate totals and source hashes are recorded in the WORKER comment on #479.
The six local mutation witnesses are in `479-fault-witnesses.json`; each
failed as required and was restored. The two live negative controls are in the
native gateway cycle report. Focused checks passed (406, followed by 82 covering
the final recovery change); these overlap and are not added together. The corrected
focused run passed 251 cases with two CI-only skips, including all 18 installed-SDK
error and cancellation cases. The new checks found and fixed missing sequence
numbers on locally generated Responses errors and codec-specific error wrappers.

## Browser checks

Checked the real local Workbench in the in-app browser with verified Alias metadata
fixtures and a synthetic preview. No production Conversation or user data was used.

- Chat: Claude shows default, None, Low, Medium, High, Extra high, Max; High saves.
- Build: Plan mode's existing submenu shows GPT default, None, Low, Medium, High,
  Extra high; Low saves and the chip reads `gpt-5.4 · Low`.
- Assignments: Plan can select Opus-4.8 and save High through the existing drawer.
- Failed save: injected HTTP 400 while changing Chat High to Medium. The error is
  visible and the chip retains High. The rejected Medium is not shown as active.
- Model change: Haiku hides preset effort choices. Its title explains the manual
  budget limit and that Model default leaves settings unchanged. This check found
  and fixed the API projection dropping the new route explanation.

## Scoped review

Reviewed only the changed paths, in groups of at most twelve files:

1. Gateway client, protocol/events, capability evidence/discovery and their tests (8 paths).
2. Native payload policy, scoped endpoints, data-use receipts and policy/HTTP tests (6 paths).
3. Provider module, driver, orchestrator wiring/Recall and driver tests (6 paths).
4. Existing controls, recovery card/store, API projection and changed control test (6 paths).
5. Dependency locks, image setup, baked-source check and isolated spike (10 paths).
6. ADR and research reports, reviewed in two groups of at most 12 paths.
7. Final corrections: provider error handling, installed-codec harness/tests,
   counter derivation and HTTP assertions, alias projection, CI and dependency pins
   (10 paths; documentation reviewed separately).

Findings fixed: partial error frames escaping validation; errors leaking arbitrary
provider text; title calls resetting policy provenance; validation occurring after
successful data-use attribution; missing codec dependencies in the image; a route
explanation dropped by the browser API projection; cancellation not closing its
ledger entry; native cached-input accounting; an unavailable recovery control after a policy change; and parent-session scoping that
would have rejected legitimate OpenCode task children. Children now require a
harness-verified parent chain and workspace directory. Review also confirmed OpenCode's
32,000 default output cap, so no new output or thinking-token budget was invented.
The full-suite audit found an empty alias field changing a legacy response shape
and a counter test treating the new inference endpoint as an ancillary caller.
The fixes omit the empty field and derive the HTTP boundary from decorators.
Final review also found SDK errors retaining private native payloads; the provider
now strips those error payloads without changing normal state replay. A policy
checkpoint summary applies all later withholding rules to earlier text as well.
The three additional plants remove the inference counter, restore the old SDK
error handling, and enable raw chunks; all fail their focused checks.

## Remaining limits

- The final native live rerun is blocked by the provider usage limit above. Earlier
  successful native lifecycle evidence predates the final error/cancellation fixes;
  the final installed-SDK and policy changes are verified locally.
- Exact gateway deployment revision remains unverified. Version and observed
  native audit behavior are the available evidence.
- Native Bedrock-Claude, Vertex-Claude, and untested Responses providers have no
  verified route evidence here. They get no guessed native effort choices.
- The capability cache can be five seconds old. The gateway provides no atomic
  route-revision precondition. Alias fallback chains invalidate the verified
  choices; native Responses also has a runtime settings/metadata contract check.
- Opaque history after a restriction change needs the existing Clear recall checkpoint, with a visible-summary seed. No process-local signature cache or fabricated signature is used.
- The image recipe now installs the codecs, but no image build or deployment was
  performed in this task. Existing deployed Sage has not been changed.
