# Model-call diagnostic fields

Issue #511 extends the existing `/api/diag/timing?format=json` record. Existing keys
remain. New fields are optional for older callers and records. This is an observation
at Sage's gateway boundary, not a measurement of provider compute or reasoning time.

## Identity

- `turnId` is the existing turn ticket ID for Build, approve and Chat. Other recorder
  callers receive a generated ID. `appId` and `conversationId` describe the bound
  turn context. App setup takes place after the recorder starts.
- Each call has a distinct `callId` and its owning `turnId`.
- `sessionId` is the harness session making the request; `rootSessionId` is the active
  session whose verified parent chain admitted it. Different values identify a child.
  The field does not claim to identify the child's immediate parent.
- `model`, `phase` and `reason` retain the routing result. `protocol` names the native
  wire lane. `requestedEffort` is the explicit override, or null. `effortStatus` is
  `explicit`, `provider_default` (no override; actual provider level unknown), or
  `unknown` (a caller did not supply route metadata).

## Time and size

All call event offsets are milliseconds from call start. `atMs` remains the offset
of call start from turn start.

- `prepMs`: request rewriting and final serialization-size measurement completed.
- `ttfbMs`: first raw gateway chunk, including a partial SSE frame or keepalive.
- `firstTextMs`, `firstToolArgumentMs`: first received chunk in which the parser can
  observe that event. Null means no such event was observed. Reasoning is not text.
- `lastChunkMs`: last raw received chunk. `maxChunkGapMs`: longest observed interval
  between chunks. `chunks` counts raw gateway chunks, not complete SSE frames.
- `ms`: call close, after stream delivery or failure. `ms - lastChunkMs` can include
  queue drainage and downstream delivery. Gaps can include transport and backpressure;
  they do not prove that the provider was computing during that interval.
- `reqBytes`: incoming OpenCode request. `forwardedReqBytes`: final rewritten JSON body,
  including the Responses contract nonce, encoded with the transport's JSON settings.
  Neither body is retained by this measurement.

`inTokens`, `cachedTokens`, `outTokens` and `reasoningTokens` preserve missing values.
Reasoning usage is a reported component, not an amount added to the output total.

## Tool announcements and outcomes

`toolInvocations` keeps up to 40 announcements. Each entry has `name`, `providerId`,
`protocolIndex`, `identityStatus`, and `metadataTruncated`. Protocol identity deduplicates
repeated announcements. Distinct provider IDs with the same name remain distinct.
`identityStatus` is `provider_id`, `protocol_index`, or `unidentifiable`. The last is an
observation that cannot prove a distinct invocation. `toolsTruncated` says the bounded
list omitted further announcements. `tools` remains the compatibility list of names.

These are provider announcements, **not tool executions**. There is no asserted mapping
to an OpenCode tool ID; a matching name or nearby time does not establish one. Live
progress labels continue to use their separate name-based line counts.

`outcome` is `running`, `success`, `cancelled`, `refusal`, `incomplete`, or `error`.
Closing a turn with an unfinished call marks that call incomplete. A closed handle
cannot accept new chunks, usage or outcome updates. The native route captures its
record before awaiting the request body so a late request cannot enter a later turn.

New metadata excludes prompts, reasoning, signatures, arguments, credentials and data
rows. The older recorder's `prompt` and error fields still exist; #512 must use its
sanitized export contract rather than treating the entire legacy record as safe to share.
