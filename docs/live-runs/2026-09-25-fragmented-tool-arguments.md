# Fragmented tool arguments through real OpenCode — 2026-09-25

Issue #560 (slice B of #558). Synthetic, local, no paid call. The pinned OpenCode 1.18.4 binary
(`node_modules/.bin/opencode`) reads Sage's real `/v1/sage/responses` relay through the installed
`backend/sage/driver/provider.mjs` codec (`@ai-sdk/openai` 3.0.84). A scripted local gateway writes
each Responses stream byte by byte. A custom `probe` tool records what it was handed.

Test files: `backend/tests/test_fragmented_tool_arguments_reach_real_opencode.py` (real OpenCode,
8 tests, all RAN, none skipped) and `backend/tests/test_tool_argument_boundaries_are_bounded_evidence.py`
(codec harness `tests/js/native_codec_argument_boundary_harness.mjs`, `StreamEvents`, diagnostics).

## What the pinned codec does (read from `@ai-sdk/openai` `dist/index.mjs`, confirmed by the harness)

- `response.output_item.added` (function_call) opens a lane keyed by `output_index` and emits `tool-input-start`.
- Each `response.function_call_arguments.delta` is forwarded as `tool-input-delta` (UI only).
- The executed `tool-call` is built from `response.output_item.done`'s `item.arguments`.
- `response.function_call_arguments.done` is ignored. A stream with no `output_item.done` executes nothing.

So the protocol's answer to a delta/done disagreement is: the completion runs. Sage records the
disagreement and repairs nothing.

## Observed with real OpenCode

| Case | Executed | OpenCode transcript | Next inference | Sage lane facts |
|---|---|---|---|---|
| Two interleaved `probe` calls, empty deltas, SSE frames and a 4-byte UTF-8 sequence cut by 7-, 3- and 11-byte splits; repeated on a second user turn and after an OpenCode restart | each call exactly once, arguments byte-identical to the fixture | two `completed` parts per turn with the fixture input | paired `function_call_output` per call id; earlier pairs ride history, not re-run | `boundary: match`, `terminal: closed`, `done.source: both`, `agreement: agree` |
| Deltas spell payload A, both completions spell payload B | B, once | one `completed` part with B | output for B | `boundary: mismatch`, `done.jsonValidity: valid_object` |
| Incomplete arguments (`{"label": "a", "text": "unfin`) | nothing | one `completed` part of tool `invalid`, input `{tool: "probe_probe", error: "...JSON parsing failed..."}` | the error text as the call's output | `boundary: match`, `jsonValidity: invalid` |
| Malformed JSON (`{"label": "a", "text": }`) | nothing | same shape as above | same | same |
| No terminal event after two deltas | nothing | no tool part | one inference | `terminal: open`, `done.source: none`; Sage error `Gateway stream ended before its terminal event` |
| Clean EOF inside a frame | nothing | no tool part | one | `terminal: open`; `Gateway stream ended inside an event` |
| Network break after two deltas | nothing | no tool part | one | `terminal: open`; `The model gateway stream stopped (ConnectionError). Retry the turn.` |
| OpenCode abort mid-arguments | nothing | no tool part | one | `terminal: open`, call `outcome: cancelled`; Sage's cancel reached the gateway generator |

In every failure case the turn lock was still held when the relay returned, and no argument text
appears in the diagnostics record.

## What this does and does not establish

- No Sage relay or codec fault was found on any fixture. Nothing in `native_routes.py` or
  `provider.mjs` was changed.
- OpenCode 1.18.4 records an unparseable call as a COMPLETED `invalid` tool part that echoes the
  arguments and the parse error. This is the shape #565 must classify; it is not a Sage fault.
- The Xiaomi failure itself was not reproduced. These fixtures are shapes the relay can be shown
  to carry faithfully; a live Xiaomi stream that damages arguments would now leave `mismatch`,
  `invalid`, `open` or `orphanDeltas` facts in `toolArgumentBoundaries`, which says WHERE the
  first disagreement was observed and never which side caused it.
