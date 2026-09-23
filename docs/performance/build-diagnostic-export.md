# Download one Build turn's diagnostics

Build history has a **Download diagnostics** action for each captured turn. The action
uses the app, conversation, and turn ID on that row. It never substitutes the latest
capture. Old rows without an ID have a disabled action. An expired or absent capture
returns HTTP 404; an unreadable store returns HTTP 503.

The route is `GET ./api/project/build-diagnostics/{turnId}?app_id=…&conversation_id=…`.
It uses the existing workspace route and Domino proxy authentication. The relative
API URL preserves the workspace prefix. Reading a capture does not seed app files or
start a preview. Downloads use `Content-Disposition: attachment` and `Cache-Control:
no-store`. Local tests cover route scope and prefixes; the deployed proxy's rejection
of unauthenticated requests needs a live workspace check.

## Version 1 contract

- `schemaVersion`: export schema version, currently 1.
- `sourceRevision`: the Sage Git revision when available, otherwise null.
- `turn`: exact `turnId`, `appId`, `conversationId`, `kind`, and `startedAt`.
- `buildOutcome.status`: `success`, `failure`, `stopped`, or `unknown`. Capture completion
  does not imply a successful Build. Missing outcome remains unknown.
- `capture`: lifecycle status, completeness, recorder availability, dropped event counts
  by section, and upstream truncation flags. Status is `running`, `finished`, or
  `interrupted`. A start record left by a prior process reads as interrupted.
- `timing`: allowed numeric timing fields, calls and tool announcements, spans, observed
  tool executions, wait intervals, repeat-brake decisions, counters, and observations.
- `retention`: record, byte, and event limits plus the cumulative dropped-record count.

The capture starts after the existing project context bind. There is no extra project
read before the turn timer. Its identity is stamped on existing persisted history rows.
There are two store writes per captured turn: start and finish, not one write per event.
A crash can retain only start metadata; that record is explicitly incomplete. A turn
that fails before context is available has no capture and receives the explicit 404.

## Bounds and persistence

The project volume stores `.sage/build-diagnostics.json`. The file and its atomic-write
staging files are ignored by Git. It holds at most 20 records, each at most 256 KiB, and
has a total byte cap of `20 * 256 KiB + 4096`. The oldest records are evicted. A record
contains at most 4000 events, including nested tool announcements. Section caps are
256 calls, 1000 spans, 500 tools, 2000 intervals, and 500 repeat-brake events. Each call
retains at most 40 announcements. Compatibility tool-name lists also have a 40-item cap.
Byte pressure drops additional array rows. Each omission is counted. Upstream truncation,
including shortened announcement metadata, is reported and makes completeness false.

The store uses an atomic replacement under a process lock. A failed write leaves the
last valid file in place and cannot fail the Build. A missing or disabled timing record
still yields identity and lifecycle metadata, with `complete: false`. No cross-turn
record fallback is allowed.

## Privacy boundary

This export uses field allowlists, bounded identifier strings, finite numbers, booleans,
and explicit enums. It excludes prompts, code, reasoning, raw messages, tool arguments
and results, errors, credentials, and dataset rows. Paths and commands are not exported;
existing keyed fingerprints identify repeated targets and commands without disclosing
them. Unknown fields are omitted. Arbitrary span `why` text is mapped to a fixed retry
category or `unknown`. Known no-edit attempt, stack, write, and exhaustion fields remain
available. Persistence warnings contain only exception type, never exception text.

The in-memory raw timing endpoint is unchanged. This download contract is separate from
that developer endpoint and from application Chat artifacts.
