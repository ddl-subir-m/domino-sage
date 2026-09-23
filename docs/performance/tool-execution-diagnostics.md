# Tool execution and elapsed-time diagnostics

Issue #394 adds fields to the existing timing record. It does not infer a slow
warehouse query from a gap between model calls. The original issue's warehouse
claim was withdrawn; the measurements to explain were an open-tool gap and Build
completion work.

## Tool identity and clocks

`tools` holds at most 500 lifecycles. A lifecycle joins a harness session ID and
OpenCode call ID. The start's name survives a nameless completion. A missing call
ID can use an available part ID, explicitly marked `identitySource=part`; it does
not claim to be a harness call ID. Events with neither ID increment
`tools.unidentified_events`. Provider IDs are not joined to these records.

`startUnixMs` and `endUnixMs` retain available OpenCode timestamps with individual
source labels. Supported shapes are V1 `state.time.start/end`, transcript
`time.ran/completed`, and `session.next.tool.*` event `properties.timestamp`.
`executionMs` exists only when the harness supplies an ordered start/end pair.

Sage separately records the first/last observation and first completion observation.
`completionLagMs` uses that first completion observation; repeated snapshots cannot
increase it. `observedMs` is the sampled interval, not exact execution time. Clock
values outside the turn cannot receive an elapsed slot. An open call at turn end
still says it was open; it does not receive an invented completion.

`intervals` stores at most 2000 existing poll/read and wait intervals. Read entries
include whether OpenCode answered and its reported running status. They distinguish
an open tool with successful polls from failed harness requests. They cannot prove
that an internally stalled tool is still making progress. No diagnostic performs
another network request or filesystem content read.

## Read targets and edits

Target and search-pattern fingerprints use a random key kept only in memory for
this turn. Known working directories normalize relative paths without filesystem
reads; unknown directories remain session-scoped. The same observer and key cover
all phases of a turn. Only bounded numeric offsets/limits/line ranges are retained.

An observed successful write/edit to that target marks the next read
`targetState=observed_edit`. Otherwise content state remains `unknown`: no observed
edit is not proof of unchanged content. Opaque operations, including shell and
patch tools, mark `opaqueOperationSincePreviousRead` without parsing or recording
their commands or bodies. No current harness field provides a verified file
revision, so this change does not emit an `unchanged` claim.

`repeatBrake` records the real brake decision, session/call ID, remembered tool
name, a keyed input fingerprint, consecutive count and limit. The recorder is
bound to its original turn. It neither changes the stop rule nor stores a command.
`toolsTruncated`, `intervalsTruncated`, and `repeatBrakeTruncated` mark capped traces.

## Completion work and reporting

Existing retry spans keep bounded reasons. Runtime error text is replaced with
`runtime repair` in diagnostic span metadata. Typecheck and Chat finalization spans
remain. Build now also measures runtime wait, leak/gateway scans, git save,
attachment restore, data scan and resource accounting at their existing call sites.

`scripts/turn-timing.py` partitions intervals into measured categories, explicit
active-work overlap, `unconfirmed_tools`, and `other`. Same-category parallel and
nested intervals are unioned. Polling during active work is not added to elapsed
time again. An observed tool with no exact execution clock occupies an explicitly
unconfirmed interval; a 240-second open tool cannot become 240 seconds of claimed
polling cost. Separate work totals can overlap and are labelled as such.

Legacy polling totals have no interval placement and stay unplaced. Unknown time
remains visible. The elapsed buckets reconcile to turn duration, including overlap
and unknown categories. This metadata supports #512's bounded export and #515's
controlled GLM measurement; local fixtures do not constitute a live latency result.
