# Build context and code navigation

Read this file for delivery slices C and E. These changes support normal Guided builds. They do
not require phased execution.

## C. Preserve evidence without replacing intent

### Existing boundary

`BuildIntent` already carries source requests, the approved plan, answers, and handoff material.
It is installed through the protocol-specific code in `backend/sage/build_intent.py`. Normal
approval reuses the planning session. Context rollover already has a state machine and constructs
a recovery packet from current files.

Extend that recovery orientation. Do not create another task engine, another approval token, or
another copy of the authoritative plan. Keep current intent precedence. A new user request or
approved plan edit is the only way to change the requested work.

Main now has turn-tagged pre-turn snapshots, `TurnSnapshot.turn_changed_app`, and
`Workspace.sage_owned_paths` (#680). Reuse these for attribution instead of adding a second
snapshot history or ownership list. `_previous_turn_did_it` checks that this turn changed no
app files and the previous Build turn in this app/conversation ended with `ok: true` and changed
app-owned files. This supports `ALREADY_DONE`; it does not compare requirement meaning or prove
that the earlier app is correct. Preserve the existing outcome and UI text. Do not create new
passed checks, browser evidence, or phase completion from this outcome or marker.

The no-edit restart now teaches `ALREADY_DONE` in `_pre_edit_recovery_packet`, so an app whose
saved `AGENTS.md` predates the marker can use it. Preserve that instruction when extending recovery
context. Do not rely on a template-only change or overwrite old app instructions to supply it.
An older app may take the existing single recovery before it returns `already done`; count that
recovery in latency and repair measurements. This does not add a new model-call stage.

### Proposed data model

Add a small module, `backend/sage/build_evidence.py`, with a per-app store. Keep an atomic,
versioned record at the generated app's `.sage/build-evidence.json`, outside app source commits
and outside model write access. Register that path with the existing ignore/ownership mechanisms.
It must survive process restart on the project's durable filesystem. It is neither a grant nor
permission to resume an old task automatically.

The store holds one record: the latest attempt for this app. A new build replaces it; long-term
history remains in existing build history/diagnostics. v1 carries only what recovery after a
restart needs and the acceptance tests below exercise: identity, digests, state, check results,
changed paths and the active repair. Phase results and decision references are NOT in v1: phase
results belong to the phased experiment (slice F adds them), and decision text already lives on
the approved plan, which recovery reads directly.

```json
{
  "schemaVersion": 1,
  "appId": "app-id",
  "conversationId": "conversation-id",
  "attemptId": "current-turn-id",
  "intentId": "canonical-intent-id",
  "plan": {"recordId": "plan-id", "version": 2, "digest": "sha256"},
  "codeDigest": "digest-of-current-app-source-and-query-catalog",
  "runtimeDigest": "digest-of-check-relevant-sage-owned-runtime-and-config",
  "state": "running",
  "changedFiles": [{"path": "static/components/MainScreen.js", "digest": "sha256"}],
  "checks": [{"kind": "syntax", "status": "passed", "codeDigest": "same-digest", "runtimeDigest": "same"}],
  "activeRepair": "implementation",
  "updatedAt": "UTC timestamp"
}
```

Optional/missing fields are explicit. A normal build without a stored plan has `plan: null`.
Statuses use fixed enums: build `running|failed|stopped|complete`; checks reuse the current
`passed|failed|unverified|not_applicable` vocabulary. Reuse existing repair-objective values.

Recover decision text from the approved plan and accepted answers, which are already
authoritative; the record does not copy them. Do not use a new model call to generate a summary.
Missing information is marked missing, not inferred as complete.

Use the existing working-tree and query digest functions. Define `codeDigest` from both so a query
change invalidates check evidence. Exclude the evidence file itself and other Sage bookkeeping
from this digest, or each evidence write will invalidate its own result. File content hashes must
use current disk bytes, including uncommitted files.

Keep progress attribution separate from check validity. Exclude `Workspace.sage_owned_paths`
when crediting app edits to a model, as #680 does. Do not exclude all those paths from check
identity: a changed query helper, runtime reporter, or preview config can invalidate a check.
Bind those check-relevant runtime/config bytes as `runtimeDigest`, alongside `codeDigest`. Evidence and other bookkeeping remain excluded. Capture current identity
after #690's `_prepare_turn_app` refresh; revalidate saved checks against that refreshed state.

### Update and recovery lifecycle

1. At implementation entry, bind the record to app, conversation, intent, and approved plan version
   under the existing turn lock, after the turn's app preparation. Use `project.app_for_turn()`;
   the selected app can be different. Initialize changed-file evidence from the current snapshot.
2. After a completed edit batch or before a model continuation, refresh changed paths/digests.
   Coalesce writes; do not flush on every streamed argument fragment.
3. When an actual check finishes, store its result against the checked code digest. Model prose
   never sets a check to passed. For preview checks, retain the validation's app and generation
   identity. Reuse #692's lookup of that app's view; the selected app and request headers are not
   a substitute. Preview acknowledgements and crash reports can arrive with no app request view.
4. At an attempt boundary, save state and active repair. Atomically replace the file in the same
   directory. A write failure produces an internal diagnostic and disables evidence reuse; it
   does not discard app code or report success for missing evidence.
5. On recovery, validate identity and plan digest under the turn lock. Re-read current code. If
   source differs, retain useful path references but invalidate checks tied to the old digest.
6. Render a compact supplement into the existing continuation packet. Include relevant changed
   files, valid check status, and the active repair objective. Keep the
   canonical intent unchanged. Do not append a new user command after each tool result.
7. On a new request, Reset, plan edit, app deletion, or switch to an unrelated build, reject old
   evidence as a continuation basis. Stop records `stopped`; follow existing code-retention/revert
   behavior. This record must not change Stop semantics.

An ordinary continuing session does not need the same evidence pasted into every inference. Use
the supplement at actual recovery/new-session boundaries. Store facts throughout the build so
they are available then. Do not alter the existing process-local continuation registry to make
an old token valid after restart. An explicit retry after restart must reconstruct current inputs
and pass all existing checks.

### Budgets and data handling

Initial store limit: 64 KiB. Initial rendered supplement: 6 KiB. Bound by UTF-8 bytes and drop
whole optional entries, with counts of omitted entries. Keep identity, plan reference, active
repair, and failure status. Never truncate JSON or slice a decision sentence into a new meaning.
Full source remains available through ordinary permitted reads.

Store app-relative code paths and bounded check codes. Do not store raw rows, secrets, signed
reasoning, tool-output bodies, or a full transcript. Model-supplied text uses the existing
disclosure checks and is never exported as diagnostics. Generated paths must remain within the
app pinned to the turn. Diagnostic exports contain counts, digests, statuses, and durations only.

### Phased records, only when that experiment runs (slice F, not v1)

Slice F adds a `phaseResults` field to the record and bumps `schemaVersion`. It replaces the current in-memory-only collection of short notes with persisted `phaseResults` keyed
by plan digest and step position. Each record contains changed files, actual check results, and
an optional bounded closing note from the existing phase response. Mark that note as unverified
model commentary. It cannot prove completion, grant edits, or override a plan.

On retry, reload completed phases' matching records. If code changed since their checks, say so.
Keep the existing phase brief and file allowlist. Do not feed every prior transcript into the new
session. Fix persistence before experimenting with richer structured model summaries. No extra
summary call is required in this design.

### Acceptance

- Normal approval retains its planning session and canonical intent.
- Recovery gets current file references and exact, still-valid check outcomes.
- A query-only change invalidates old evidence.
- Restart plus explicit retry restores evidence but does not resurrect an old authority token.
- Wrong app, conversation, plan version, or digest cannot supply recovery evidence.
- Reset/Stop/plan edit preserve existing lifecycle behavior.
- Model text that says “tests passed” cannot create a passed check.
- A Sage-owned helper refresh alone cannot count as model progress or support `ALREADY_DONE`.
  A changed runtime helper/config still invalidates checks that depend on it.
- An accepted `already done` outcome creates no new verification evidence. Keep the existing
  rejection of claims after failed, unchanged, or other-conversation turns.
- A legacy app without the marker in `AGENTS.md` learns it from the no-edit recovery packet;
  test a model fake that emits it only after the request teaches it.
- A check for app A remains tied to A when B is selected, including reports with no app request
  view. An old validation ID or preview generation cannot supply passed evidence.
- Oversized/corrupt/missing records fall back to existing intent-plus-disk recovery with a diagnostic.
- No new model call is used to write or read this record.

## E. A compact code map, fetched when useful

### Scope

Implement a local map for the generated app, not for the Sage repository. First use it in an
evaluation of follow-up edits. It is a navigation aid, not an authority about runtime behavior.

There is already a map: `_build_source_note` calls `_source_paths` and `_top_level_names` in
`orchestrator/service.py`. It lists up to 60 app source paths and extracts bounded names from line
starts. It is rebuilt for first sends and recovery. Start by moving only those responsibilities
behind the new narrow module without changing their output. Keep `_source_paths` callers that
classify named files on the same implementation. Then add parsed relationships for the experiment.
The existing `ContextContinuation.source_map_digest` hashes this source note; preserve that
contract or explicitly version it. Do not reuse the field for an unrelated cache digest.

Proposed module: `backend/sage/source_map.py`.
Proposed tool: `sage_source_map({paths?: string[], symbol?: string})`.

Register it through Sage's existing built-in tool/MCP path. Bind it to the active Build app and
turn. Accept relative paths, not a caller-selected root. Advertise a short description on Build
turns; do not inject the full map into every prompt. A request with no arguments returns the
entry points and a bounded overview. A specific path returns its definitions and known adjacent
references. Exact symbol lookup returns definitions first, then observed references.

Keep the existing compact source note in the control condition. In the treatment, use its same
small overview plus the on-demand relationship tool. Do not send two file/name inventories.

### Output contract

Return `schemaVersion`, `sourceDigest`, `files`, `relationships`, `coverage`, and `omittedCount`.
Each file has its path, content hash, definition names/kinds/line numbers, and a bounded signature
when available. Each relationship has `from`, `to`, `kind`, and its source location.

Supported relationship kinds in v1:

- FastAPI template script tags and their load order.
- Literal `window.app.Name` definitions and references.
- Python function/class definitions and literal FastAPI route declarations.
- Named-query names and parameter declarations; literal calls to a named query.

Do not include SQL bodies, data files, `.env`, attachments, dependency bundles, build output,
arbitrary `.sage` records, or full function bodies. Read only the query catalog fields listed
above. A dynamic import, computed member, or runtime-built query name yields unknown coverage;
it must not be reported as proof of no dependency.

### Implementation approach

v1 has two steps, in this order, and stops there:

1. Move `_source_paths`, `_top_level_names` and `_build_source_note` into `source_map.py` with
   byte-identical output (prove it with a golden test on both stacks), and keep
   `source_map_digest` unchanged.
2. Add relationships that need no new parser dependency: Python `ast` for definitions and literal
   FastAPI routes; Python's `html.parser` for script elements and their order; the literal
   `window.app.Name` patterns `feedback/runner.py` already matches for `SAGE002`; and the query
   catalog through its existing parser. FastAPI has no imports, so this covers that stack fully.

React imports and exports are reported with `coverage: "unknown"` in v1. A TypeScript compiler
adapter (pinned in Sage's runtime, never installed per generated app) is a separate decision,
taken only if the evaluation shows React follow-ups still thrash with the v1 map. Do not make
regex matches the authority for a complete call graph.

Cache per-file extraction by content hash; rebuild relationships from changed extracts. Hash
untracked app code as well as tracked files. Compute a stable aggregate digest from sorted paths
and hashes. Invalidate on edit, reset, deletion, or changed source. A corrupt cache is disposable.
Use the active app root and its ownership rules to prevent cross-app or symlink escape reads.

Initial response budget: 8 KiB. Rank exact requested paths/symbols first, one relationship hop
next, then entry points. Stable path order breaks ties. Omit whole entries and report coverage.
Do not use embeddings, graph ranking, inferred prose, or an external database in v1. If parsing
is incomplete, return available facts and let the agent use ordinary file reads/search.

Tool output is code-derived data, not instructions. Names/signatures can contain untrusted source
text and still pass through the normal tool-output controls. Ordinary source inspection remains
available. The map does not narrow an edit allowlist or widen a resource grant.

### Acceptance and promotion

- The extracted source note is byte-identical to the old one on both stacks.
- Fixtures for both stacks return correct definitions and known relationships with source
  locations; React imports report unknown coverage rather than an empty relationship list.
- A FastAPI screen registered through `window.app` is discoverable without ES imports.
- Editing/deleting/adding a file changes the result without a stale cache hit.
- Unsupported dynamic relationships are marked unknown.
- Path escape, sensitive-file, and cross-app requests return no content.
- Large fixtures respect the response budget and disclose omissions.
- Same-model follow-up tasks show fewer discovery reads or lower latency without worse correctness.

Promote only after the delivery evaluation. A correct parser alone does not establish useful
context. If the map adds cost without improving tasks, keep ordinary search and remove the tool
from the default offer. A full graph remains a separate future decision.
