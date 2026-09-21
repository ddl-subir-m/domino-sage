---
status: accepted
---

# Native reasoning stays with the harness

Issue #479 adds native Messages and Responses through the existing LLM Gateway.
ADRs 0031, 0032, 0043, 0049, 0052 and 0057 remain in force. Model default
means no Sage reasoning override. An explicit setting must be honored or refused.

## Evidence recorded on 2026-09-20

The gateway at `https://sage.gcp.cs.domino.tech/apps/llm_gateway` reports version
2.0.12 in its signed-in browser UI. Its deployed Git revision has **not** been
verified. The read-only gateway checkout reviewed was
`4387d1b0df5693d6dec36cf65e12a9fb5a8e67db`; that is not evidence of deployment.
No gateway file or deployment was changed.

| Exact Alias | Provider identity | Backing model | Verified gateway route |
|---|---|---|---|
| `Opus-4.8` | `efedd07b061d422d978f6973cb02171d` / anthropic | `claude-opus-4-8` | `/anthropic/v1/messages`, native |
| `gpt-5.4` | `91c8542a21374549b76e9328b0c1a988` / openai | `gpt-5.4` | `/v1/responses`, passthrough |

Both Aliases had an empty fallback chain. These are exact route identities, not
rules inferred from model names. The gateway audit rows corroborate native
Messages and Responses passthrough. See
[sanitized evidence](../research/479-native-gateway-cycles.json).

Direct synthetic tool cycles passed with Claude adaptive thinking / High and GPT
Low, respectively. Claude returned signed thinking before the tool call; the
continuation replayed it unchanged. GPT returned encrypted reasoning; the
continuation replayed it with `store:false` and
`include:["reasoning.encrypted_content"]`. Removing Claude's signature returned
400. Moving the GPT Low request with tools to Chat Completions also returned 400.
These negative controls establish that state and route matter, beyond a successful
HTTP status. The effort matrix and OpenCode lifecycle evidence below extend them.

The new transport client also passed both native tool cycles and the two 400
plants. Its Chat Completions path completed a Gemini Low tool cycle using the
exact Alias `domino/gemini-3.7-flash`, Vertex provider
`65610f91db0146b0b76f682543b8b058`, backing model `gemini-3.7-flash`.
The tool response carried a thought signature, and the continuation retained it.
This is compatibility-route evidence, not a claim of native Gemini support.

## Harness boundary

The pinned OpenCode is 1.18.4. Its `MessageV2.toModelMessages` compares the
stored provider/model ID with the requested provider/model ID. On a change it
removes provider metadata from tools and converts reasoning to ordinary text.
Three independent provider registrations therefore cannot preserve native state
through Sage's current inference-by-inference routing.

Sage uses one stable OpenCode model ID and a small provider module.
Before serialization, that module asks a loopback route resolver which native
AI SDK codec to use. OpenCode retains the original provider metadata in its own
database. The module does not keep a signature cache. The production endpoint
must revalidate the resolved model, route, effort and current policy before
forwarding. Selecting a codec is not permission to bypass the shared policy.

The spike in `spikes/native-reasoning/` has isolated configuration and storage,
uses the pinned binary's v1 prompt path, and keeps gateway credentials outside
OpenCode. Its recorder stores shapes and counts, not reasoning or signatures.
The transition sequence is an artificial probe; it does not establish that
Sage's Auto or rescue policy is connected.

The installed SDKs include rejected events in validation errors and keep request
bodies in HTTP errors. Both can contain private state. The provider boundary
removes those payloads, suppresses raw diagnostic chunks, and replaces codec
warnings with a fixed explanation. Safe errors from the enforced local endpoint
retain their message, HTTP status and retry behavior, including the policy-change
checkpoint. Normal provider metadata remains intact in OpenCode storage.

## Completed protocol experiment

The four reports `docs/research/479-opencode-{messages,responses,chat,transition}.json`
record real OpenCode 1.18.4 runs. Each completed parallel reads, a second user turn,
a process restart using the same session database, compaction, and a following tool
turn. Both synthetic work markers survived. The transition run switched Messages
and Responses between tool inferences and returned to the earlier codec. Its saved
provider metadata remained available. This artificial sequence proves the codec
boundary; production Auto/rescue wiring is verified in `479-production-auto-rescue.json`: a
write selects GPT Responses, and a synthetic compiler error returns to Claude
Messages without losing the written marker.

`479-native-effort-levels.json` records complete gateway tool cycles for Opus-4.8
and sonnet (claude-sonnet-5): none, low, medium, high, xhigh, max. Non-none uses
adaptive thinking plus output effort. None disables thinking. Haiku remains at
Model default; no numeric budget is invented. GPT-5.4 Responses completed none,
low, medium, high and xhigh. The JSON capability evidence binds these results to
exact gateway, Alias ID/name, provider ID/type, backing model and update timestamp.
The Gemini compatibility levels and cloud-dogfood GLM 5.3 OR low/high/max evidence
from #472 remain compatibility evidence. No native Bedrock-Claude or Vertex-Claude
configuration was available for a lifecycle check, so none is advertised as verified.

## Amendments and policy

- ADR-0031: keep one stable OpenCode provider/model identity, but choose the wire
  codec before serialization. Do not add three independent harness model catalogs.
- ADR-0032: retain Gemini's pin and unsigned-history veto. Other native codecs
  retain their own opaque state in OpenCode storage; a foreign codec omits state it
  cannot accept without changing the stored original. No signature is fabricated.
- ADR-0043 and ADR-0057: resolve under the same approved-model, tool, web, artifact,
  and content policies. The scoped endpoint rechecks the decision and refuses a
  model/protocol mismatch. Every endpoint requires the active turn lock and session, or a task child whose
  parent chain and workspace the harness verifies.
- ADR-0049 and ADR-0052: the chosen assignment supplies both model and effort.
  Model default omits the explicit control. Invalid saved levels fail with an
  explanation; they never become a hidden Low or a silently removed setting.

Native content is viewed through the shared policy representation, then rendered
back with original opaque blocks and tool IDs. Unknown carriers and provider-stored
Responses histories are refused. A per-session policy fingerprint contains no model
state. A restriction change requires the existing Clear recall flow, with a visible-summary seed
before old opaque state can be replayed. New state may be generated under the new
policy. OpenCode remains the sole owner of the state and tool lifecycle.

Metadata refresh invalidates capabilities at discovery and after a maximum five
second validation cache. A nonempty Alias fallback chain is unverified. Responses
must echo a per-request metadata nonce, store:false, and the requested explicit
effort in native creation/completion events; the gateway's translated response does
not satisfy that contract. A failed contract fails the turn. Unknown destinations
retain compatibility availability with no guessed effort choices.

## Verification limits

The deployed gateway Git revision remains unknown; version 2.0.12 and native audit
records are the available deployment evidence. Evidence is deployment-specific.
An administrator can change a route between metadata validation and inference;
there is no gateway route-revision precondition. Responses has the runtime contract
check above. Messages relies on the exact recent metadata and measured native route.
No gateway repository or deployment change is included.

## Sources

- [OpenCode 1.18.4 message conversion](https://github.com/anomalyco/opencode/blob/v1.18.4/packages/opencode/src/session/message-v2.ts)
- [OpenCode plugins](https://opencode.ai/docs/plugins/)
- [Anthropic thinking controls](https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost)
- [OpenAI Responses migration](https://developers.openai.com/api/docs/guides/migrate-to-responses)

The native codec packages are pinned in the root lockfile and installed by the
image recipe. Rebuild the image before deployment; runtime dependency fetching
is not part of the native model path.
