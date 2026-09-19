# What OpenCode offers for controlling its own store

Research for issue #227, a child of map #223.

**Version read: `opencode-ai@1.18.4`.** Everything below holds for that version only. OpenCode
moves fast and the store layer was rewritten inside the 1.x line (there are two live snapshot
implementations in this very tree). Re-read before trusting any of this against another version.

## How the source was obtained

The npm package is not readable. `node_modules/opencode-ai/` contains only `package.json`,
`postinstall.mjs`, and `bin/opencode.exe` — a 131.9 MB `Mach-O 64-bit executable arm64` compiled
by Bun. There is no JavaScript to read.

So the source was taken from the published repository at the matching tag:
`https://codeload.github.com/sst/opencode/tar.gz/refs/tags/v1.18.4` (`sst/opencode` now redirects
to `anomalyco/opencode`, repo id 975734319).

That the tag matches the shipped build was checked, not assumed. Five distinctive strings from the
source were found verbatim in the shipped binary:

| String | Source it comes from |
|---|---|
| `OPENCODE_DISABLE_CHANNEL_DB` | `packages/core/src/database/database.ts:50` |
| `ToolOutputStore.cleanup` | `packages/core/src/tool-output-store.ts:176` |
| `output truncated; full content saved to` | `packages/core/src/tool-output-store.ts:159` |
| `@opencode/v2/ToolOutputStore` | `packages/core/src/tool-output-store.ts:48` |
| `Snapshots are disabled` | `packages/core/src/snapshot.ts:190` |

All five: FOUND. Paths below are relative to that source tree.

---

## The control levers

| Lever | What it controls | How it is set | Which sinks it reaches | Confirmed? |
|---|---|---|---|---|
| `XDG_DATA_HOME` | The whole OpenCode data root | Process env, before start | `opencode.db`, `snapshot/`, `tool-output/`, `log/`, `repos/` — **all of them** | CONFIRMED |
| `OPENCODE_DB` | The SQLite file alone (path or `:memory:`) | Process env, before start | `opencode.db` only | CONFIRMED |
| `OPENCODE_CONFIG_DIR` | The config directory | Process env | Nothing in the data root | CONFIRMED |
| `snapshots: false` | Whether snapshots are captured at all | Config key, **per project** | `snapshot/` only | CONFIRMED |
| `tool_output.max_bytes` / `max_lines` | The size at which a tool result is spilled to a file instead of inlined | Config key, per project | Trades DB row size against `tool-output/` file count | CONFIRMED |
| `compaction.prune` / `OPENCODE_DISABLE_PRUNE` | **Nothing in the store.** Context only | Config key / env | None — see below | CONFIRMED |
| `DELETE /session/:sessionID` | One conversation's rows | HTTP API | `opencode.db` only | CONFIRMED |
| (nothing) | DB size cap, DB retention, DB rotation, log rotation, `VACUUM` | — | — | CONFIRMED absent |

Neither of the two location levers is per-session or per-project. Both are read once, at module
load, and are fixed for the life of the process.

---

## 1. Can the store location be redirected?

### `XDG_DATA_HOME` — yes, and it moves everything

`packages/core/src/global.ts:1-31`:

```ts
import { xdgData, xdgCache, xdgConfig, xdgState } from "xdg-basedir"
const app = "opencode"
const data = path.join(xdgData!, app)
...
const paths = {
  data,
  bin: path.join(cache, "bin"),
  log: path.join(data, "log"),
  repos: path.join(data, "repos"),
  ...
}
export const Path = paths
```

`xdg-basedir` is pinned at `5.1.0` (`packages/core/package.json:126`, `bun.lock:5578`). Its whole
implementation of that export is:

```js
export const xdgData = env.XDG_DATA_HOME ||
	(homeDirectory ? path.join(homeDirectory, '.local', 'share') : undefined);
```

No platform special-casing — this is the same on macOS as on Linux. **CONFIRMED: setting
`XDG_DATA_HOME` in the child process environment relocates `Global.Path.data`, and every sink is
derived from it.**

Every sink, specifically:

| Sink | Derived at |
|---|---|
| `opencode.db` | `packages/core/src/database/database.ts:53` — `join(Global.Path.data, "opencode.db")` |
| `snapshot/<projectID>/<hash>/` | `packages/core/src/snapshot.ts:98` and `packages/opencode/src/snapshot/index.ts:71` — both `path.join(Global.Path.data, "snapshot", <project.id>, Hash.fast(worktree))` |
| `tool-output/` | `packages/opencode/src/tool/truncation-dir.ts:4` — `path.join(Global.Path.data, "tool-output")`; also `packages/core/src/tool-output-store.ts:118` |
| `log/opencode.log` | `packages/core/src/observability/logging.ts:49` — `path.join(Global.Path.log, "opencode.log")` |
| `repos/` | `packages/core/src/global.ts:24` |

**The catch — scope.** `data` is computed at line 11, i.e. at module evaluation, and `Path` is a
plain module constant. There is no per-project or per-session override on any live path. The
`Global.Interface` service *can* be overridden (`Global.layerWith`, `global.ts:81-85`) but every
call site of that in 1.18.4 is a test (`packages/core/test/tool-output-store.test.ts:24`,
`snapshot.test.ts:172`, `config/config.test.ts:41`, …). **CONFIRMED: redirection is per OpenCode
*process*, set before it starts. Not per project, not per session.**

Note `Global.Path.home` reads `OPENCODE_TEST_HOME` (`global.ts:19`) but `data` does not — pointing
`OPENCODE_TEST_HOME` at a scratch dir does *not* move the store.

### `OPENCODE_DB` — yes, for the SQLite file only

`packages/core/src/database/database.ts:42-55`:

```ts
export function path() {
  if (Flag.OPENCODE_DB) {
    if (Flag.OPENCODE_DB === ":memory:" || isAbsolute(Flag.OPENCODE_DB)) return Flag.OPENCODE_DB
    return join(Global.Path.data, Flag.OPENCODE_DB)
  }
  if (
    ["latest", "beta", "prod"].includes(InstallationChannel) ||
    process.env.OPENCODE_DISABLE_CHANNEL_DB === "1" || ...
  )
    return join(Global.Path.data, "opencode.db")
  return join(Global.Path.data, `opencode-${InstallationChannel...}.db`)
}
```

Read from env at `packages/core/src/flag/flag.ts:47`. An absolute path is honoured as-is; a
relative one is joined under the data root.

**Scope: also process-wide, and also frozen at module load** — `database.ts:57` is
`export const node = makeGlobalNode({ ..., layer: layerFromPath(path()), ... })`, so `path()` runs
once when the module is imported. CONFIRMED.

`InstallationChannel` is a build-time define (`packages/core/src/installation/version.ts:7`); the
published npm build lands on the plain `opencode.db` branch, which matches the live filename.

---

## 2. Can persistence be reduced or turned off?

### Off, for the DB: `OPENCODE_DB=":memory:"` — CONFIRMED as a code path, NEEDS A RUN as a decision

`database.ts:44` passes `:memory:` straight through to the SQLite layer. That removes the file
entirely. It also removes every conversation the moment the process exits — the DB is the source of
truth for sessions, messages and the event log, not a cache. Whether Sage survives that (restart,
crash recovery, the Workbench conversation view) is **NEEDS A RUN**.

### Off, for snapshots: `snapshots: false` — CONFIRMED, and it is per project

`packages/core/src/snapshot.ts:124-130`:

```ts
const enabled = Effect.fnUntraced(function* () {
  if (location.vcs?.type !== "git") return false
  return Config.latest(yield* config.entries(), "snapshots") !== false
})
const capture = Effect.fn("Snapshot.capture")(function* () {
  if (!(yield* enabled())) return undefined
  ...
```

Schema: `packages/core/src/config.ts:66-68`, `"Enable snapshots used for undo and revert
behavior"`. Config is Location-scoped and merges the project's own `opencode.json`, so **this one
lever genuinely is per project.** Cost: it disables OpenCode's undo/revert.

### Reduced, for tool results: `tool_output.max_bytes` / `max_lines`

`packages/core/src/tool-output-store.ts:13-14`:

```ts
export const MAX_LINES = 2_000
export const MAX_BYTES = 50 * 1024
```

overridable per project via `tool_output` (`packages/core/src/config/tool-output.ts:6-9`,
`packages/core/src/config.ts:81`), read at `tool-output-store.ts:119-127`.

Read `bound()` (`:138-174`) carefully, because the lever cuts both ways. Under the limit, the full
output is kept inline — and therefore in the DB. Over it, the full text is written to
`tool-output/tool_<id>` and only a head+tail preview goes in the message. **Lowering `max_bytes`
moves bytes out of `opencode.db` and into `tool-output/`; it does not delete them.** It is a lever
on *which* sink grows, not on total volume.

### `compaction.prune` is not a store lever — CONFIRMED, and this is the trap

`OPENCODE_DISABLE_PRUNE` maps to `compaction.prune = false`
(`packages/opencode/src/config/config.ts:582-584`). The name suggests store pruning. It is not.
`packages/opencode/src/session/compaction.ts:278-286`:

```ts
if (pruned > PRUNE_MINIMUM) {
  for (const part of toPrune) {
    if (part.state.status === "completed") {
      part.state.time.compacted = Date.now()
      yield* session.updatePart(part)
    }
  }
```

It stamps `time.compacted` so the context builder skips the part. The output stays in the row, and
`updatePart` publishes another event — so pruning makes the store slightly *larger*. Do not reach
for this flag to control disk.

---

## 3. What retention or pruning exists

| Sink | Retention | Evidence |
|---|---|---|
| `opencode.db` | **None.** No cap, no rotation, no TTL, no `VACUUM`, no `auto_vacuum` | `grep -rn "auto_vacuum\|VACUUM" packages` returns nothing. `database.ts:27-33` sets `journal_mode`, `synchronous`, `busy_timeout`, `cache_size`, `foreign_keys`, `wal_checkpoint` — no vacuum pragma |
| `tool-output/` | 7 days, swept hourly | `tool-output-store.ts:15,176-205`; duplicated at `packages/opencode/src/tool/truncate.ts:13,56` |
| `snapshot/` | `git gc --prune=7.days`, hourly, 1-minute initial delay | `packages/opencode/src/snapshot/index.ts:23,305,760-766` |
| `log/opencode.log` | **None found.** Appended via `Logger.toFile` | `packages/core/src/observability/logging.ts:49` |

Two details on those that do exist.

**`tool-output/` sweep is conditional.** `tool-output-store.ts:199-211` mounts the sweep as its own
node (`cleanupNode`, named `tool-output-cleanup`), separate from the store node. It only removes
entries whose name starts with `tool_` (`:180`) and only if the process stays alive an hour at a
time (`Schedule.spaced(Duration.hours(1))`, `:203`). Whether Sage's OpenCode process lives long
enough for a sweep to fire is **NEEDS A RUN**.

**`snapshot/` retention is real but indirect.** The live implementation is
`packages/opencode/src/snapshot/index.ts` (imported as `@/snapshot` in
`packages/opencode/src/effect/app-runtime.ts:13,68`; the `packages/core/src/snapshot.ts` v2 module
writes to the same directory and has *no* cleanup). Capture is `git write-tree`
(`snapshot/index.ts:341`) — a bare tree, no commit, no ref. Only the current index holds objects
reachable. So older trees and blobs become unreachable and `git gc --prune=7.days` drops them.
**LIKELY: snapshot content self-expires roughly 7 days after the last write to that project's
snapshot repo.** The gap: git's own reachability rules (packed objects, index entries, `gc.*`
defaults) were not tested here, only read.

---

## 4. What a session delete actually removes

The entry point is `DELETE /session/:sessionID`
(`packages/opencode/src/server/routes/instance/httpapi/groups/session.ts:88,215-224`, described as
"Delete a session and permanently remove all associated data, including messages and history"). It
calls `Session.remove` (`packages/opencode/src/session/session.ts:607-628`):

```ts
if (hasInstance) yield* cancelBackgroundJobs(background, sessionID)
const kids = yield* children(sessionID)
for (const child of kids) {
  yield* remove(child.id)
}
yield* events.publish(SessionV1.Event.Deleted, { sessionID, info: session })
yield* events.remove(sessionID)
```

Two mechanisms fire.

**(a) The `Deleted` event deletes the session row**
(`packages/core/src/session/projector.ts:260`), and `PRAGMA foreign_keys = ON`
(`database.ts:31`) makes the cascades bite. Verified against the live schema
(`sqlite3 … "select sql from sqlite_schema"`), every one of these is
`FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE`:
`message`, `session_message`, `session_input`, `session_context_epoch`, `todo`, `session_share`.
`part` cascades from `message`.

**(b) `events.remove(sessionID)` deletes the event log** — `packages/core/src/event.ts:513-522`:

```ts
function remove(aggregateID: string) {
  return db.transaction(() => Effect.gen(function* () {
    yield* db.delete(EventSequenceTable).where(eq(EventSequenceTable.aggregate_id, aggregateID)).run()
    yield* db.delete(EventTable).where(eq(EventTable.aggregate_id, aggregateID)).run()
  })).pipe(Effect.orDie)
}
```

This second step matters more than it looks. `event` has **no** foreign key to `session` — it
references `event_sequence(aggregate_id)` (`packages/core/src/event/sql.ts:16`, and the live DDL
confirms it). Deleting the session row alone would leave the entire event log orphaned. The
explicit `events.remove` call is what saves it. Any Sage-side "delete a conversation" that reaches
for the DB directly, or calls only part of this, will leave the biggest table behind.

That the coverage is total was checked live, read-only, against a copy of `opencode.db`:

```
select count(*) from event_sequence where aggregate_id not in (select id from session);  -> 0
select count(*) from event_sequence, count(*) from session;                              -> 122, 122
select substr(aggregate_id,1,4), count(*) from event_sequence group by 1;                -> ses_ | 122
```

**CONFIRMED: every event aggregate in the live store is a session, so `Session.remove` reaches all
of it.**

### What it leaves behind

| Left behind | Why |
|---|---|
| `snapshot/<projectID>/…` git objects | `Session.remove` never touches the snapshot store; it is per *project*, content-addressed, shared across sessions. Only the hourly `git gc --prune=7.days` reclaims anything |
| `tool-output/tool_*` spill files | Not referenced by the delete path at all. The message keeps only a text marker naming the path (`tool-output-store.ts:159`). Only the 7-day sweep removes them |
| `log/opencode.log` lines | Never rotated, never deleted |
| The freed SQLite pages | No `VACUUM`, no `auto_vacuum` anywhere. `opencode.db` never shrinks; deleted rows become free pages that get reused. The bytes stay on disk until they are overwritten |

That last row is the one that breaks a promise. A "delete" that leaves the plaintext in free pages
of a 26.5 MB file is not a delete for a sensitive-data purpose.

---

## 5. Do the snapshot store and the spilled tool output follow the same setting?

**Location: yes.** All three sinks hang off `Global.Path.data`, so a single `XDG_DATA_HOME` moves
`opencode.db`, `snapshot/` and `tool-output/` together. `OPENCODE_DB` moves only the DB — set that
alone and the two file sinks stay in the old place. CONFIRMED.

**Everything else: no, they need separate handling.**

| | `opencode.db` | `snapshot/` | `tool-output/` |
|---|---|---|---|
| Moved by `XDG_DATA_HOME` | yes | yes | yes |
| Moved by `OPENCODE_DB` | yes | no | no |
| Turned off by config | `:memory:` (env only) | `snapshots: false` | not possible — only the threshold moves |
| Retention | none | ~7 days via `git gc` | 7 days, hourly sweep |
| Reached by session delete | yes (rows only) | no | no |

---

## What this means for ADR-0036

ADR-0036 promises that a deleted conversation takes its transcript with it. Against this store,
today, that promise is not kept: Sage never sets `XDG_DATA_HOME` or `OPENCODE_DB`, never sets
`snapshots: false`, and its driver never calls `DELETE /session/:sessionID` (checked read-only
across `backend/` and `environment/` — the only match for any of those names is Sage's own
`$TMPDIR/sage-opencode.log` at `backend/sage/orchestrator/service.py:4966`).

**It can be made true, but only for a store Sage owns per unit of deletion, not for the shared
default one.** The reasons, in order of how much they constrain the design:

1. **The levers are process-scoped, not conversation-scoped.** `XDG_DATA_HOME` and `OPENCODE_DB`
   are read at module load. Granularity finer than "one OpenCode process" has to come from Sage
   spawning a process per unit, not from asking OpenCode to partition.
2. **Row deletion is genuinely complete — if you use OpenCode's own path.** `Session.remove`
   covers the cascades *and* the event log. Sage should call the HTTP endpoint, not write SQL.
3. **Row deletion is not byte deletion.** With no `VACUUM`, the plaintext survives in free pages.
   The only clean answer is deleting the *file*, which means one store per deletable unit.
4. **Two sinks are outside the delete path entirely.** `snapshot/` and `tool-output/` need their
   own handling: `snapshots: false` per project closes the first, and either a lowered
   `tool_output` threshold plus explicit file removal, or accepting a 7-day window, closes the
   second.

The shape that follows: **a data root per unit of deletion, deleted as a directory.** That is
supported — `XDG_DATA_HOME` reaches all three sinks — and it is the only shape that makes
point 3 go away.

## Still open — NEEDS A RUN

- Does Sage's OpenCode process live long enough for the hourly `tool-output` and snapshot sweeps to
  fire even once? If it is restarted per turn or per build, neither retention path ever runs and
  both directories grow without bound.
- Does `OPENCODE_DB=":memory:"` leave Sage functional across a restart, or does it lose the
  conversation Sage's own Workbench reads back?
- Does one `XDG_DATA_HOME` per project (or per conversation) break OpenCode's project/session
  bookkeeping, which today assumes one shared global store?
- Whether `git gc --prune=7.days` really reclaims `write-tree` objects in practice — read, not
  measured.

## Method notes

- Source: `sst/opencode` (→ `anomalyco/opencode`) at tag `v1.18.4`, checked against the shipped
  binary by string match (table at the top).
- The live `~/.local/share/opencode/opencode.db` was inspected **read-only**, on a copy. No row
  content is reproduced here; only schema, counts and byte sizes. Live sizes at time of reading:
  `event` 13.8 MB, `session_message` 6.9 MB, `session_input` 4.1 MB; `snapshot/` 1.2 MB, `log/`
  5.1 MB, `tool-output/` 0 B. The 4,068,816-byte prompt appears twice in `event`
  (`session.next.prompted.1` and `session.next.prompt.admitted.1`) and once more in
  `session_input.prompt` — one large prompt lands in three places.
- No Sage source was modified.
