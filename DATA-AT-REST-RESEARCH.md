# Where a row from a declared Dataset can come to rest

**What was inventoried:** every place in Sage where a row of data from a declared (possibly
sensitivity-tagged) Domino Dataset can end up **at rest** — on disk, in a git commit, on a push, in
a published container, in a log, or in a model's context. The goal is a *closed* inventory, so this
errs toward listing a place and marking it low-risk rather than omitting it.

**Method:** a read of this repo's own source. No Domino API was called. Every claim cites
`path:line`. Nothing was changed — this was read-only. Three parallel sweeps (all disk writes;
publish and egress; caches, temp dirs and logs) plus a direct read of the commit, transcript and
copy-detection paths.

**Commit:** `550ef9d`, read on 2026-09-09.

**Working tree state:** `git status --short` at the time of the read showed exactly one entry:

```
?? PERMISSIONS-RESEARCH.md
```

No tracked file was modified. (The session's opening snapshot listed a dozen modified files under
`backend/sage/` and `backend/tests/`; those were committed or reverted by another session before this
read began. Everything below is read against committed `550ef9d`.)

---

## The three sentences that matter

1. **The transcript is the biggest surface, and it is committed, pushed, and shipped into the
   published App container.** Everything under `.sage/` and `examples/` is in git *by default*; only
   the eight paths in the template `.gitignore` are out. There is no allowlist.
2. **The guard that exists — `_leaked_copy_paths` — only catches a byte-exact copy of an *attached*
   file under a *scanned* extension.** Derived data, renamed CSVs, and anything from a Data Source
   walk straight through.
3. **The largest single sink is not in this repo at all.** Sage never sets `XDG_DATA_HOME`, so
   OpenCode accumulates every turn's full tool results — file bytes, command stdout, base64
   attachments — in `~/.local/share/opencode/opencode.db`, uncapped, unrotated, and unreachable by
   Sage's own delete paths.

---

## Summary table

Legend for **In a commit?** — `yes` = staged by `git add -A` and committed; `no (ignored)` = a
`.gitignore` rule keeps it out; `no (excluded)` = staged then unstaged by
`commit_all(exclude=...)`; `n/a` = not in a repo at all.

| # | Resting place | Exact path | In a commit? | Pushed? | What keeps it out |
|---|---|---|---|---|---|
| **A. Committed and pushed — transcripts** |
| 1 | Chat transcript | `<root>/.sage/threads/<id>/history.jsonl` | **yes** | **yes** | **nothing.** Model prose + every bash command string |
| 2 | Build transcript | `apps/<appId>/.sage/history.jsonl` | **yes** | **yes** | **nothing.** Same two payloads |
| 3 | Handoff transcript | `apps/<appId>/.sage/handoff-transcript.md` | **yes** | **yes** | **nothing.** Chat reproduced *verbatim* — a second copy |
| 4 | Handoff digest | `apps/<appId>/.sage/handoff.md` | **yes** | **yes** | nothing |
| **B. Committed and pushed — plans** |
| 5 | Live plan | `apps/<appId>/.sage/plan.md` | **yes** | **yes** | **nothing.** Model prose; no filter reads it for values |
| 6 | Plan doc, every version | `<root>/.sage/plan-docs/<id>/v001.md`…`vNNN.md` | **yes** | **yes** | **nothing.** Append-only, never pruned |
| 7 | Archived plans | `apps/<appId>/.sage/plans/NNN.md` | **yes** | **yes** | nothing |
| 8 | Architecture note | `apps/<appId>/.sage/architecture.md` | **yes** | **yes** | nothing |
| **C. Committed and pushed — manifests** |
| 9 | **Attachment manifest** | `apps/<appId>/.sage/attachments.json` | **yes** | **yes** | **nothing — carries up to 3 verbatim rows per CSV/TSV** (§9) |
| 10 | The app's SQL | `apps/<appId>/.sage/queries.json` | **yes** | **yes** | nothing. Literals in a `WHERE` ride along |
| 11 | Bound schema | `apps/<appId>/.sage/schema.json` | **yes** | **yes** | by design — names and types, no rows |
| 12 | Conversation pointers | `.sage/threads/<id>/context.json`, `artifacts.json`, `meta.json` | **yes** | **yes** | shape: pointers and titles only (§6) |
| 13 | Standing instructions | `<root>/.sage/instructions.md` | **yes** | **yes** | user-typed |
| 14 | Generated app config | `apps/<appId>/<llm_config_path>` | **yes** | **yes** | **a pasted Model API token is `json.dumps`'d into app source** (§28) |
| **D. Committed and pushed — Artifacts** |
| 15 | Chat Artifacts | `<root>/examples/<threadId>/*` | **yes**, under 10 MB | **yes** | `ARTIFACT_COMMIT_MAX` — size only, never content (§7) |
| 16 | **Live-read tables** | `<root>/examples/<threadId>/<slug>.table.json` | **yes** | **yes** | **nothing — up to 500 real rows** (§16) |
| 17 | Live-read statement | `<root>/examples/<threadId>/<slug>.sql` | **yes** | **yes** | nothing |
| 18 | Oversized Artifacts | `<root>/examples/<threadId>/<big file>` | no (excluded) | no | `oversized_artifacts` > 10 MB; **stays on disk** |
| **E. Committed and pushed — app source** |
| 19 | Verbatim copy into `src/` | e.g. `src/sales.csv` | no (excluded) — **partly** | no | `_leaked_copy_paths`; big holes (§3) |
| 20 | **Derived / aggregated data in `src/`** | any source file | **yes** | **yes** | **nothing** (§4) |
| 21 | Arbitrary editor write | anywhere under the workspace | **yes** | **yes** | nothing — `PUT /api/project/file` (§29) |
| **F. On the volume, out of git** |
| 22 | Attached Dataset files | `apps/<appId>/public/data/<slug>/…` | no (ignored) | no | `_ensure_gitignored`; **never force-added** (§5). Symlinks — *or real bytes* (§25) |
| 23 | **Shared sample rows** | `apps/<appId>/.sage/samples.json` | no (ignored) | no | `.gitignore` only. Real rows, deliberately (§17) |
| 24 | **Chat scratch uploads** | `<root>/.sage/scratch/<slug>` | no (ignored) | no | `.gitignore`. **Whole uploaded file, raw bytes** (§24) |
| 25 | **Downloaded Dataset files** | `<root>/.sage/scratch/datasets/<slug>/<file>` | no (ignored) | no | `.gitignore`. **Full Dataset file pulled out of Domino** (§25) |
| 26 | **Turn-snapshot object store** | `<root>/.sage/snapshots/.git/**` | no (ignored) | no | `.gitignore`. Full content of the tree, every turn (§18) |
| 27 | Rendered transcript | `apps/<appId>/.sage/history.md` | no (ignored) | no | `.gitignore` + `.ignore`; regenerated per turn |
| 28 | Chat workdir | `<root>/.sage/chat-work/` | no (ignored) | no | `.gitignore`; symlinks expose scratch + `public/data` |
| 29 | Usage scan | `apps/<appId>/.sage/usage.json` | no (ignored) | no | `.gitignore` |
| 30 | Model API credentials | `<root>/.sage/model-api-credentials.json` | no (ignored) | no | `.gitignore` + `chmod 0600` |
| 31 | Built bundle | `apps/<appId>/dist/**` | no (ignored) | no | `.gitignore`. **`vite build` bakes `public/data/` in** (§21) |
| **G. Outside the repo entirely** |
| 32 | **OpenCode session DB** | `~/.local/share/opencode/opencode.db` | n/a | n/a | **nothing. Full tool results verbatim** (§26) |
| 33 | **OpenCode snapshot store** | `~/.local/share/opencode/snapshot/*/*/` | n/a | n/a | **nothing.** Git object copies of the workspace |
| 34 | OpenCode's own log | `~/.local/share/opencode/log/opencode.log` | n/a | n/a | nothing; no rows at default level |
| 35 | OpenCode spilled tool output | `~/.local/share/opencode/tool-output/` | n/a | n/a | nothing — **NEEDS A RUN** |
| 36 | **OpenCode server log** | `$TMPDIR/sage-opencode.log` | n/a | n/a | nothing. Append-only, unrotated (§27) |
| 37 | **`/tmp/<name>` Dataset downloads** | `/tmp/<name>` | n/a | n/a | **nothing — Sage's own prompt tells the agent to put them there** (§30) |
| 38 | **Domino Dataset mount** | `<mount>/uploads/<name>` | n/a | n/a | nothing — an upload *writes bytes there on purpose* (§19) |
| 39 | **Published App container** | the whole repo, `.sage/` and `examples/` included | n/a | arrives by `git push` | nothing (§20) |
| 40 | **Published App `dist/`** | `dist/data/<slug>/…`, static-served | n/a | n/a | nothing (§21) |
| 41 | **Container stdout / log ring** | `logging.basicConfig` → stdout | n/a | n/a | **nothing.** Three echo paths (§31) |
| 42 | Diag responses | `GET /api/diag/log`, `/api/diag/timing` | n/a | n/a | return prompt text; no disk write (§32) |
| 43 | Model context | the provider's side | n/a | n/a | ADR-0043 gate — **off by default** (§8) |
| 44 | Published App → gateway | the viewer's browser | n/a | n/a | **nothing at runtime** (§22) |
| 45 | Preview query cache | process memory, 30 s TTL | n/a | n/a | in-memory only — **not** at rest (§23) |

**The ignore set, in full.** From `template/react-vite/.gitignore`, seeded to both the project root
and each app: `.sage/model-api-credentials.json`, `.sage/samples.json`, `.sage/scratch/`,
`.sage/chat-work/`, `.sage/threads/*/.*.tmp`, `.sage/history.md`, `.sage/snapshots/`,
`.sage/usage.json`. Plus `public/data/`, added dynamically at attach time by `_ensure_gitignored`
(`service.py:15802-15806`). Everything else under `.sage/` and all of `examples/` is committed.

---

## 1. `.sage/threads/<id>/history.jsonl` and `.sage/history.jsonl` — CONFIRMED

The user's item 1 is correct in every part, and the intent *is* recorded.

Both are append-only JSONL, written with a bare `open("a")` and never pruned:

- `backend/sage/workspace/threads.py:247-248` (Chat) — `with p.open("a") as f: f.write(json.dumps({**entry, "at": _now()}) + "\n")`
- `backend/sage/workspace/manager.py:1086-1087` (Build) — same shape, plus `app` and `conversation` tags.

Committed and pushed by `_save_to_git` (`backend/sage/orchestrator/service.py:11670`), which commits
the **Project root**, not the app:

> `service.py:11686` — `committed = git.commit_all(path, message, exclude=leaked)`
> `service.py:11695` — `result = git.push(path)`

`commit_all` is `git add -A` (`backend/sage/workspace/git.py:100`).

**The intent is recorded — `docs/adr/0006-conversation-logs-and-artifacts-stay-in-git.md:5-11`:**

> "Sage keeps two kinds of durable state in the project repo: the conversation log
> (`.sage/history.jsonl`, `.sage/threads/<id>/history.jsonl`, and the rendered `.sage/history.md`)
> and Artifacts (`examples/<threadId>/*.png`, `*.table.json`, `*.pdf`). Both stay in git."

**What lands in an entry that could be a row.** Two payloads, both unfiltered:

1. **The model's prose.** `service.py:8013-8014` — `ev = {"type": "agent", "kind": "text", "text": body}`
   then `store.append_history(thread_id, ev)`. Build is the same: `"agent"` is in
   `_PERSISTED_EVENTS` (`service.py:316`), persisted at `service.py:9937-9938`.
2. **Every bash command string.** `_tool_detail`, `service.py:2855-2856`:
   ```python
   if tool == "bash":
       return (inp.get("command") or "").strip()
   ```
   An agent that runs `python -c "rows = [('Smith','444-…'), …]"` writes those literals into the
   committed transcript. No leak scan ever reads a history file.

**Mark:** CONFIRMED. The user's "on purpose" is right for the *file*; ADR-0006 never considers row
content — it is a decision about size and durability (`0006:22`).

## 2. `.sage/plan.md` — can a plan quote data values? YES — CONFIRMED

`backend/sage/workspace/plan_doc.py` is **pure parse/render**. Its own docstring, `plan_doc.py:7-9`:
*"`parse_sections(render(sections))` is the whole contract; both directions are pure functions with
no gateway, no OpenCode and no workspace behind them."* It never inspects a value.

The only transforms applied to plan markdown anywhere are cosmetic: `_drop_i_will_openers`
(`service.py:2509`), `_drop_empty_questions` (`service.py:2517`), `_tidy_plan` (`service.py:2537`).
None reads for data.

The body is model output, written to `apps/<appId>/.sage/plan.md`
(`backend/sage/workspace/manager.py:684`), which is not ignored — committed and pushed.

**Mark:** CONFIRMED — a plan can quote values and nothing stops it. **And it is worse than the item
assumes:** see §12.

## 3. `_leaked_copy_paths` / `_kept_out_of_the_commit` — what it matches, and what it misses

The pair is real and it does fire, but its reach is far narrower than "copies under `src/`".

**The mechanism.** `_kept_out_of_the_commit` (`service.py:15253-15265`) returns
`self._leaked_copy_paths(project) + heavy`. `_leaked_copy_paths` (`service.py:15267-15280`) walks
every Built App and collects `_copies_in_app(...)["copies"]`. That list goes to
`commit_all(exclude=...)` (`service.py:11686`), which stages everything then unstages those paths
(`backend/sage/workspace/git.py:100-102`):

```python
_git(path, "add", "-A")
if exclude:
    _git(path, "reset", "-q", "--", *exclude, check=False)
```

**Exactly two things make a file a `copy`** — `_data_usage`, `service.py:14985-14996`:

1. **Basename match.** `if PurePosix(rel).name == name:` (`service.py:14986`) — any file under the app
   tree whose *filename* equals the attachment's.
2. **Inlined copy**, only for extensions in `_SCAN_EXTS` (`service.py:2712-2713` — `.ts .tsx .js .jsx
   .mjs .cjs .json .css .html .vue .svelte`), judged by `_is_inlined_copy` (`service.py:2748-2754`):
   the whole decoded body appears verbatim when `64 <= read <= _COPY_SCAN_MAX` (512 KB,
   `service.py:2716`), **or** the first `_SAMPLE_MATCH_ROWS = 6` lines (`service.py:2721`) appear as a
   verbatim contiguous block of ≥ 64 bytes.

**What slips past — CONFIRMED, each from the code above:**

- **A renamed copy in a non-code format.** `sales.csv` → `src/rows.csv`: the basename differs, and
  `.csv` is not in `_SCAN_EXTS`, so `text` is `None` and the loop takes `if text is None: continue`
  (`service.py:14989-14990`). **Never examined at all** — the cheapest way to leak the raw file.
- **Anything under `public/`.** `_SCAN_SKIP_DIRS = frozenset({"node_modules", "dist", ".git",
  ".sage", "public"})` (`service.py:2708`); the walk prunes it (`service.py:14908`). The `.gitignore`
  is the only cover there (§5).
- **Anything outside a Built App tree.** `_scan_app_sources` walks `workspace.path`
  (`service.py:14905-14907`). A file dropped at the **Project root** is staged by `git add -A` and is
  in no scan.
- **A detached attachment.** The list is built from `project.attached` / `read_attachments()`
  (`service.py:15244-15250`). The code says it out loud — `service.py:14936-14937`: *"once [the]
  entry leaves `project.attached` the commit backstop (`_leaked_copy_paths`) stops covering it, [and
  the] bytes can reach git."*
- **Rows from a Data Source, not a Dataset.** No attachment entry exists to compare against, so
  `_is_inlined_copy` has nothing to match. Query results pasted into source are outside this guard
  entirely.

**Mark:** CONFIRMED. The guard catches two careless shapes (same filename; straight paste of a small
text file) and nothing else. **In its favour:** it correctly scans *every* Built App, not just the one
being built (`service.py:15271-15280`, `_attached_per_app` at `:15238`), because the commit is
Project-wide.

## 4. Derived data — UNCAUGHT, CONFIRMED

The user believed this is uncaught. It is, and the reason is one line — `service.py:2748`:

```python
if 64 <= read <= _COPY_SCAN_MAX and body in text:
```

`body` is the attachment's decoded bytes; `body in text` is byte-exact containment. The sample branch
(`service.py:2753-2754`) is the same test on `"\n".join(lines[:6])`.

Every transformation defeats it: an aggregate (`const revenueByRegion = [{region:"NE", total:
41233.02}, …]`), a filtered or sorted subset, CSV rows re-serialized as JSON objects, a different
delimiter or column order, and **any run of rows starting after line 6** (the sample test is anchored
at `lines[:6]`).

None is a `copy`, so none reaches `commit_all(exclude=...)`. All are committed and pushed. And
`publish_guard.py` never reads app source at all (§8), so publish does not catch them either.

**Mark:** CONFIRMED uncaught. The largest hole in the commit path.

## 5. `public/data/` — is it also never staged? CONFIRMED YES (with one dependency)

The user is right to distinguish "gitignored" from "never staged", and the answer holds: **there is
no `git add -f` and no explicit pathspec anywhere in the codebase.**

Every `git add` in the repo, exhaustively:

| Site | Call |
|---|---|
| `backend/sage/workspace/git.py:100` | `_git(path, "add", "-A")` (`commit_all`) |
| `backend/sage/workspace/git.py:195` | `_git(path, "add", "-A")` (`finalize_merge`) |
| `backend/sage/workspace/snapshot.py:45` | `self._run("add", "-A")` (`commit_before_turn`) |
| `backend/sage/workspace/snapshot.py:80` | `self._run("add", "-A")` (`working_tree_hash`) |
| `backend/sage/provision/seed.py:109` | `_git(repo, "add", "-A")` (initial seed, in a tempdir) |

All five are `-A`, no pathspec, no `--force`. `git add -A` honours `.gitignore`, so the ignore rule is
sufficient — **as long as it is there.**

**The dependency, and it is not the template's.** `public/data/` is **not** in
`template/react-vite/.gitignore`. It is written at attach time by
`_ensure_gitignored(project.workspace.path, "public/data/")` — four call sites, `service.py:6992`,
`:14293`, `:14395`, `:14736` — appending to the *app's own* `.gitignore` (`service.py:15802-15806`).
An app tree that has never had an attach has no rule (and nothing under `public/data/`, so this is
benign). If the agent deletes or rewrites the app's `.gitignore`, the rule is gone until the next
attach. Nothing re-asserts it per turn that I found — **LIKELY** exposure; the gap is that I did not
find a per-turn re-assertion, not that I proved one absent.

`TurnSnapshot` is clean here: it runs `--work-tree` at the workspace root and so honours the
workspace `.gitignore`. `service.py:8225-8226` states it: *"which honours the workspace .gitignore,
and attach_file puts `public/data/` there (`_ensure_gitignored`) so those symlinks were never in the
snapshot at all."*

**Mark:** CONFIRMED never staged. **But the conclusion people draw from it is wrong** — see §21 and
§25: the data reaches the published App anyway, and `public/data/` is not always symlinks.

## 6. `context.json` and `artifacts.json` — pointers, CONFIRMED

`artifacts.json` rows come from `record_artifact` (`backend/sage/workspace/threads.py:496-511`) and
hold exactly `{id, kind, name, title, path, producedAt}` plus optional `messageId`. No content.

`context.json` is `{"items": [...]}` from `add_context` (`threads.py:275-280`) — Resource references:
`kind`, `bindingKey`, `resourceId`, `parentId` (`backend/sage/orchestrator/handoff.py:536-547`). No
content.

**Mark:** CONFIRMED — pointers only. One caveat: `record_artifact` derives `name` and `title` from the
filename (`threads.py:497`, `:502`), and `meta.json` carries a thread title derived from the user's
prompt. A filename that *is* the datum (`patients_john_smith.csv`) puts that string in a committed
manifest. A naming problem, not a rows problem.

## 7. Chat Artifacts and `ARTIFACT_COMMIT_MAX` — CONFIRMED

`ARTIFACT_COMMIT_MAX = 10 * 1024 * 1024` (`backend/sage/workspace/threads.py:801`).

- **Below:** an ordinary file under `examples/<threadId>/`, committed and pushed by `git add -A`.
  `examples/` is deliberately committed at the Project root — it is gitignored **only** in each app's
  copy, where it is a symlink up (`service.py:15845`, and the closing comment in
  `template/react-vite/.gitignore`).
- **Above:** `oversized_artifacts(root)` (`threads.py:829-845`) lists it, `_kept_out_of_the_commit`
  appends it (`service.py:15261-15265`), `commit_all` unstages it. The file **stays on disk** —
  `threads.py:797-800`: *"the file STAYS ON DISK [and is] kept out [of the] commit instead… Too big
  [to] keep forever [is] not [a] reason [to] destroy someone's file."*

The threshold is a **size** rule, not a content rule. A 2 MB CSV of sensitive rows written as a Chat
Artifact is under the ceiling, so it is committed, pushed, **and** shipped into the published
container (§20).

**Mark:** CONFIRMED.

## 8. Model context and the ADR-0043 sensitivity gate — CONFIRMED, but off by default

The gate is wired into four places: Chat turns (`service.py:7672`), Build turns
(`service.py:10183-10188`), the preview LLM proxy (`backend/sage/orchestrator/app.py:3697`), and
publish (`service.py:12135`, `:12173`, `_refuse_unsafe_publish`).

**What triggers it.** `declared()` matches Datasets carrying a configured tag
(`backend/sage/resources/sensitivity.py:47-58`); `approved()` returns the administrator's model group.

**What slips past — three, all confirmed:**

1. **It is OFF BY DEFAULT** — `backend/sage/resources/sensitivity.py:11-14`:
   > "OFF BY DEFAULT. With `SAGE_SENSITIVE_MODEL_GROUP` unset, `approved()` answers None [and]
   > `declared()` answers empty without [a] single call"

   With the env var unset, `sensitive_model_problems` returns `[]`
   (`backend/sage/resources/publish_guard.py:283`) and the turn gate resolves to no restriction. A
   deployment that has not set this has **zero** model-side protection.
2. **Sensitivity is a freeform Domino tag.** An untagged Dataset holding real PII is not "declared"
   and the gate never engages (`sensitivity.py:47-58`).
3. **It gates the model, never the bytes.** The gate decides *which alias may run the turn*. It does
   not read the prompt and does not read what the turn writes. Everything in §1–§7 happens
   identically with the gate fully armed.

**Two direct bypasses of the gate's premise:** the attachment descriptor (§9) and a shared live-read
table (§33) both put real rows into the model's context with no consent check on that path.

**Mark:** CONFIRMED. The user's item 8 is right about the *mechanism* and wrong about the *coverage*:
the gate is real, narrow, and inert unless configured.

## 9. NOT ON THE LIST — `.sage/attachments.json` commits real data rows

The most consequential finding of the sweep, and it contradicts the file's own docstring.

`.sage/attachments.json` is a **committed** manifest — `backend/sage/workspace/manager.py:1282-1285`:

> "Committed manifest of attached/uploaded data files. `public/data/` itself is gitignored
> (**data never enters git**), so this manifest is the source of truth that lets the PUBLISHED app
> rebuild public/data/ from the project's dataset mounts at startup"

Each entry carries a cached `descriptor` — `service.py:5209`, `entry["descriptor"] = d`, persisted by
`write_attachments` (`manager.py:1297-1299`, a plain `json.dumps` to disk).

And `describe()` puts **up to three verbatim data rows** into that descriptor's `detail`, for any CSV
or TSV — `backend/sage/orchestrator/describe.py:173-175`:

```python
if sample:
    lines.append("Sample rows:")
    lines += [f"  {delim.join(r)}" for r in sample[:3]]
```

So **three real rows per attached delimited file are committed to git and pushed to the remote**, in a
file whose stated purpose is that data never enters git.

**It also reaches the model, past every consent gate.** `_descriptor`'s own docstring claims the
opposite — `service.py:5185-5186`: *"The agent needs each file's SHAPE, never its content."* But for
an `@mention` the full descriptor including `detail` is inlined into the prompt —
`backend/sage/driver/opencode.py:36-37`:

```python
if a.get("detail"):
    s += f"\n{a['detail']}"
```

and the surrounding prompt tells the model something untrue about what it is reading —
`opencode.py:45`: *"The lines below are shape, not the rows"*; `opencode.py:49`: *"it is NOT the
data."* Both sentences sit directly above three real rows. Those rows then land in
`~/.local/share/opencode/opencode.db` (§26).

**Mark:** CONFIRMED (read end to end: `describe.py:173` → `service.py:5209` → `manager.py:1297` →
committed; and `describe.py:173` → `opencode.py:36` → the prompt).

**Caveat, honest:** the ignore rule that *does* exist for this shape covers the Data Source half only.
`template/react-vite/.gitignore` ignores `.sage/samples.json` with the comment *"Real rows from a Data
Source, shown to the agent because the creator asked (#16). Never committed."* The Dataset half has no
equivalent. The asymmetry reads as an oversight, but I found no document either way — **that reading
is LIKELY, not confirmed.**

## 10. NOT ON THE LIST — `.sage/handoff-transcript.md` is a second verbatim transcript

`backend/sage/orchestrator/handoff.py:513-527` writes the Chat conversation into the Built App's repo.
Its docstring (`handoff.py:519`) says the two speakers' text is *"reproduced verbatim."* Written to
`project.workspace.path / ".sage" / "handoff-transcript.md"` (`service.py:6746`), not ignored — so
committed and pushed. `.sage/handoff.md` beside it (`service.py:6745`) is the digest.

**Why it matters separately from §1:** it *moves* content across a boundary. A Chat transcript in
`.sage/threads/<id>/` belongs to the Project; handing off copies it into a specific Built App's
`.sage/`, and that app is the thing that gets published (§20). Deleting the conversation does not
delete this copy — ADR-0036 is about the Thread record, and this is not one.

**Mark:** CONFIRMED.

## 11. NOT ON THE LIST — `.sage/queries.json` is agent-written SQL, committed

`service.py:15016` — *"the SQL lives in `.sage/queries.json`"*; read at `service.py:15184`; listed in
`_RESET_CLEAR` (`manager.py:124`) as an app-owned file; not ignored, so committed and pushed.

SQL is not rows, but an agent-written statement can carry literals: `WHERE email = '…'`, an `IN (…)`
list, a hardcoded id. **LIKELY** a low-volume leak — I confirmed the file is committed and
agent-written; I did not find a live example of a literal in one.

## 12. NOT ON THE LIST — `.sage/plan-docs/` keeps every plan version forever

The user's item 2 named only `.sage/plan.md`, the transient copy. `manager.py:285` writes `v001.md`
and `manager.py:347` writes `vNNN.md`; `manager.py:279-281` never reuses an id. Editing a value out of
a plan does not remove it from the repo — every earlier version is still committed.

**Mark:** CONFIRMED.

## 13–15. `_save_to_git` failures are swallowed

`service.py:11700-11702` wraps the whole body in `except Exception` and returns a soft
`saved: ok=false`. Publish wraps *that* call in its own bare try/except (`service.py:11929-11931`).
Not a leak by itself, but a failure to compute the exclude list degrades to "commit everything"
rather than "refuse". **LIKELY** — I read both handlers; I did not trace an input that makes
`_kept_out_of_the_commit` raise.

## 16. CORRECTION — live-read rows DO come to rest, in git

The user's framing — *"rows read on demand go to the card, not into Recall (ADR-0041)"* — is right
about the **model** and needs correcting about **disk**.

`backend/sage/liveread/result.py:88-90` writes the rows to a file:

```python
(examples_dir / name).write_text(
    json.dumps({"title": title, "columns": list(columns), "rows": kept}, indent=2, default=str) + "\n"
)
```

with `name = f"{slug}.table.json"` (`result.py:84`), `kept = list(rows[:cap])` (`result.py:81`),
`CAP_ROWS = 500` (`result.py:21`). The statement is written beside it as `<slug>.sql`
(`result.py:94`). `examples_dir` is `examples/<threadId>/` — the committed Artifact directory, and
ADR-0006 names `*.table.json` explicitly in the set that "stay[s] in git" (`0006:8-9`). ADR-0041:166
sizes it: *"The card keeps all 500 (412KB on disk, which is a file not a prompt)."*

So **up to 500 real rows per live read are committed, pushed, and shipped into the published app's
container.** ADR-0041's guarantee is precisely and only about the model's context — `result.py:3-5`:
*"the rows go [to the] Artifact [the] person sees. [The] assistant [is] handed [a] receipt — columns,
count, path."* The Artifact channel that keeps rows away from the model is the same channel that puts
them in git.

Two ADRs each correct on their own compose into a leak neither records. ADR-0006 decided Artifacts
live in git for *size and durability* reasons, before live read existed; ADR-0041 chose the Artifact
as the safe channel, on the model axis. Nobody re-read ADR-0006 against ADR-0041.

**Mark:** CONFIRMED.

## 17. `.sage/samples.json` — real rows on disk, by design

`SAMPLES_PATH = ".sage/samples.json"` (`backend/sage/resources/bound_schema.py:36`), written by
`render_samples` (`bound_schema.py:222-225`) with the literal `"rows": s.rows.rows`, via
`_write_generated` at `service.py:13578` / `:13595`.

It is the one file explicitly ignored for this reason —
`backend/sage/resources/bound_schema.py:31-35`:

> "Gitignored, unlike every other manifest here (#16). `.sage/` [is] committed [and] rides into [the]
> published app's container, [which is] right [for] names [and] types [and] wrong [for] rows: sample
> data in [this] file would put production data in [the] creator's git history [and] inside [the]
> deployed app."

Enforced by `_ensure_gitignored(project.workspace.path, SAMPLES_PATH)` (`service.py:13577`) plus the
template rule. **CONFIRMED, and working.** This file is the model for what §9 should have done — and
note it is still swept into `.sage/snapshots/` (§18) and, when shared, into the model (§33).

## 18. NOT ON THE LIST — `.sage/snapshots/.git` holds the full content of every file, every turn

`TurnSnapshot` (`backend/sage/workspace/snapshot.py`) uses git as a private content store:
`--git-dir=.sage/snapshots/.git`, `--work-tree=<workspace root>` (`snapshot.py:33-34`).
`commit_before_turn` runs `add -A` + `commit` (`snapshot.py:45-47`) at the start of every turn; a
phased build takes one per phase.

Its exclude list is `_EXCLUDE = ["node_modules", "dist", ".sage", ".git", ".DS_Store"]`
(`snapshot.py:14`), written into `info/exclude` (`snapshot.py:39-40`). **`examples/` is not in it** —
so every live-read table (§16) and every Chat Artifact is snapshotted too. `public/data/` is covered
only by the workspace `.gitignore` (§5), and when it holds *real bytes* rather than symlinks (§25) the
snapshot still skips it for the same reason.

So this store accumulates, on the volume, a full-content git object for every version of every
non-excluded workspace file, including any derived data the agent wrote into `src/` (§4). It is
gitignored so it never pushes — ADR-0006 records that at `0006:81-85`. But it is content at rest,
`discard_changes` only resets the work tree (`snapshot.py:49-53`), and nothing I found ever prunes the
objects.

**Mark:** CONFIRMED for what it stores and that it is ignored. **NEEDS A RUN** for growth and pruning.

## 19. NOT ON THE LIST — an upload writes bytes into a Domino Dataset

`upload_file` (`service.py:14692-14738`) writes the uploaded bytes to a dataset mount, outside git and
outside the workspace — `service.py:14724`, `dest_bytes.write_bytes(data)`, where
`dest_bytes = _safe_join(Path(target.mount_path), rel_in_dataset)` (`service.py:14713`).

Explicit at `service.py:14717-14719`: *"The bytes land on the dataset mount, which is OUTSIDE git and
outside the workspace, while everything that RECORDS them (symlink, manifest, AGENTS.md) is inside
it."*

This is ADR-0023 working as designed, and it is the right design. It belongs in a closed inventory
anyway: it is a resting place, it is **shared** (the default project dataset, `service.py:14696`), and
`service.py:14719-14720` names a failure mode where bytes are *stranded* there with nothing pointing
at them, "invisible to detach/delete."

**Mark:** CONFIRMED.

## 20. The published App's container carries the whole transcript

`docs/adr/0006-conversation-logs-and-artifacts-stay-in-git.md:74-77`:

> "Both paths ride into the published app's container, as they do today. That is deliberate for the
> log's column names and is why `.sage/samples.json` and `.sage/model-api-credentials.json` are
> ignored separately."

"Both paths" = the conversation logs **and** the Artifacts. Publish ships a git ref:
`backend/sage/provision/domino.py:541-548` sends `{name, projectId, visibility, entryPoint,
configurationType, version}` where `version` is a ref — **no file content in the API payload**; the
bytes travel by `git push`. Auditing the Domino API call therefore tells you nothing about what data
shipped.

The set of files deliberately kept out of that container is exactly two: `.sage/samples.json` and
`.sage/model-api-credentials.json`. Everything in §9 (attachment sample rows) and §16 (live-read
tables) rides in.

**Mark:** CONFIRMED.

## 21. NOT ON THE LIST — `public/data/` is rebuilt and baked into the published `dist/`

Keeping `public/data/` out of git does not keep the data out of the published App. The template's
startup script rebuilds it on the App hardware and bakes it into the static bundle:

- `template/react-vite/app.sh:76` — `node scripts/rehydrate-data.mjs`
- `template/react-vite/app.sh:77` — `"$SAGE_PYTHON" scripts/rehydrate_data.py`
- `template/react-vite/app.sh:81` — `npm run build`
- `template/react-vite/scripts/rehydrate-data.mjs:7-8` — *"Runs before `vite build` (see app.sh),
  which then bakes the linked files into `dist/`."*
- `template/react-vite/app.sh:96` — `serve.py --dir dist`

`rehydrate-data.mjs:47-57` symlinks from the dataset mounts (`/domino/datasets/local`, `/mnt/data`,
`/mnt/imported/data`, `:19`); `rehydrate_data.py:87-88` **downloads** anything the mounts missed via
`DatasetClient().get_dataset` — i.e. Datasets from other projects, fetched with the App's
credentials.

Result: **raw Dataset file bytes are static-served over HTTP by the published App**, with no per-file
authorization beyond the App's visibility setting — and `AUTHENTICATED` ("anyone signed in to
Domino") is on the publish guard's allow list (`backend/sage/resources/publish_guard.py:52`, rationale
at `:43-47`).

This is the intended design (`manager.py:1283-1285` describes the manifest's purpose as exactly this
rebuild). It is on the inventory because the user's item 5 treats `public/data/` as contained by its
gitignore, and the gitignore contains it only with respect to *git*.

**Mark:** CONFIRMED for the mechanism. **NEEDS A RUN** for whether `rehydrate_data.py`'s
`DatasetClient` resolves as the publisher or the viewer on the App container.

## 22. A published App's runtime calls the gateway from the browser — outside every gate

The user's item 9 is correct. `template/react-vite/src/appLlm.ts:124-129`: `endpoint()` routes through
Sage's preview proxy only when `import.meta.env.DEV`; the published bundle takes the direct branch to
the gateway base written into `appLlm.config.ts` by `render_config` (`service.py:15490`). `askModel`
POSTs `{model, messages, …}` with `credentials: "include"` (`appLlm.ts:213-225`, `:133`) —
authenticated as the *viewer's* cookie.

`messages` is whatever the app author put there, including rows read from `dist/data/…` (§21). No
server hop, no content inspection: `pick()` (`appLlm.ts:81-84`) validates the alias *name* against
`config.models` and never looks at the payload.

**What the guards do and do not do here:**

| Guard | Triggers | Slips past |
|---|---|---|
| `sensitive_model_problems`, `publish_guard.py:283-295` | tagged Dataset **and** a bound LLM alias **and** `SAGE_SENSITIVE_MODEL_GROUP` set | env unset (default); no alias bound; untagged data; raw `fetch` to an undeclared alias |
| Individual credential, `publish_guard.py:213` | Data Source with `credential_type != "Shared"` | **all Datasets** — `data_source_bindings` filters to `KIND_DATA_SOURCE` only (`:122`) |
| Open visibility, `publish_guard.py:170`, `:52` | visibility not in the allow list | `AUTHENTICATED` is **allowed**; `discoverable` unchecked; never reached when no Data Source is bound (`:163`) |
| `publish_egress.py` | Data Source **and** alias bound (`:41`) | **Datasets are not in the join** — a sensitive Dataset + vendor alias produces no notice; and it is advisory: `:10-11` says *"it produces no `PublishProblem`, and nothing here can stop a publish"* |
| `gateway_bypass.py` | gateway URL substring in a `_SCAN_EXTS` file (`:71-80`) | advisory only (`:18-19`); runs on **build turns**, never at publish; only scanned extensions; substring match defeated by a URL built from fragments |

**Mark:** CONFIRMED.

## 23. Preview query cache — in memory only, NOT at rest

Included so the inventory is closed. `CachingExecutor._entries` is `dict[str, tuple[float, dict]]`
(`backend/sage/preview/queries.py:70`), `CACHE_TTL_S = 30.0` (`:52`), guarded by a `threading.Lock`.
Nothing written to disk; only successes kept (`:63-64`).

**Mark:** CONFIRMED — not a resting place.

## 24. NOT ON THE LIST — `.sage/scratch/` holds whole uploaded files

`upload_scratch` (`service.py:14758-14769`) writes the raw bytes of anything the user drops into Chat:
`service.py:14767`, `dest.write_bytes(data)`, to `<root>/.sage/scratch/<slug>`. Gitignored
(`service.py:14768` plus the template rule), so never committed. But it is the whole file — a CSV or
XLSX of real rows — persisting on the volume with no lifecycle I found.

**Mark:** CONFIRMED.

## 25. NOT ON THE LIST — Sage downloads whole Dataset files onto the volume

`public/data/` is not always symlinks. When a Dataset has no mount, `_download_attachment`
(`service.py:14237`, `:14242`, called from `:14453`) fetches the file out of Domino to a `.part` path
then `os.replace`s it into place — landing in `<root>/.sage/scratch/datasets/<slug>/<file>`
(`_CHAT_DATA_PREFIX`, `service.py:1093`) and `apps/<appId>/public/data/<slug>/<file>`. The provider
call is `backend/sage/assets/provider.py:502`, `dataset.download_file(rel_path, str(dest))`.
`_restore_attachments` (`service.py:8258`) re-downloads on restore.

Both destinations are gitignored, so this does not reach git. It does mean **whole Dataset files sit
as real bytes on the Builder volume**, not merely as pointers into a mount — which changes the
blast radius of anything that reads the tree, including §18 and §26.

**Mark:** CONFIRMED.

## 26. NOT ON THE LIST — `~/.local/share/opencode/opencode.db` holds full tool results verbatim

**The largest single sink in the system, and it is outside Sage's control.**

Sage spawns `npx opencode serve` and configures only OpenCode's *input* side. It sets
`OPENCODE_CONFIG`, `--print-logs` and `cwd` (`backend/sage/driver/server.py`, `_env()` / `start()`)
and **never sets `XDG_DATA_HOME` or any data-dir override**. The string `~/.local/share/opencode`
appears nowhere in this repo. So OpenCode uses its default data dir, and Sage has neither visibility
into it nor a delete path to it.

Confirmed live on this machine (read-only SQLite): `~/.local/share/opencode/opencode.db` is
**26.5 MB**, with `event` 5,674 rows / 12.6 MB and `session_message` 752 rows / 6.4 MB. Inspection
shows:

- `event` type `session.next.tool.success.1` stores `content[].text` = the **entire** tool output. One
  sampled row is the full source text of `backend/sage/resources/provider.py`.
- `part.data` stores `state.output` for `read`, `bash`, `write`, `glob` — full file contents and full
  command stdout.
- `event` type `session.next.prompted.1` stores the full prompt; the largest single event is
  **4,068,816 bytes**, and five contain `data:image` — base64 attachment bytes at rest, matching the
  inlining path at `backend/sage/driver/opencode.py:270-306`.
- `session.next.tool.called.1` stores tool *arguments* — SQL statements, table names.
- Sessions are keyed by `session.directory`, with rows for `backend/workspaces/demo`, `demo1`, and the
  repo root.

`~/.local/share/opencode/snapshot/<project-hash>/<session-hash>/` are git object stores whose `config`
sets `worktree = <the session's working dir>`; `ls-files` lists the whole workspace including
`.sage/attachments.json`, `.sage/history.jsonl`, `.sage/plans/001.md`, and `cat-file -p` returns real
contents. So workspace bytes are duplicated into a second store outside the project repo.

**Consequences that bear on a storage design:**

- Uncapped, unrotated, and with no Sage-side deletion path. ADR-0036 ("a deleted conversation takes
  its transcript with it") **cannot reach it** — deleting `.sage/threads/<id>` leaves the OpenCode
  copy intact.
- Everything the agent ever read is in it, so `.sage/samples.json` (§17), a downloaded Dataset file
  (§25), and a shared live-read receipt (§33) all end up here even though none of them is in git.

**Mark:** CONFIRMED for the storage shape and for Sage's absence of an override.
**NEEDS A RUN:** `~/.local/share/opencode/tool-output/` is empty here — OpenCode spills oversized tool
results there rather than inline, and whether a 500-row receipt or a large Dataset read crosses that
threshold has not been observed.

## 27. NOT ON THE LIST — `$TMPDIR/sage-opencode.log`

`service.py:4966` — `os.environ.get("SAGE_OPENCODE_LOG") or str(Path(tempfile.gettempdir()) /
"sage-opencode.log")`. Opened append-only and flushed per line in `backend/sage/driver/server.py`
`_read()` (`:136`, `:139`). Live on this machine at 451 KB.

At the default level it is config-load / watcher lines only — grepped for `<content>`, `GONG__CALLS`
and `SELECT` with **0 hits**. Never rotated, never deleted by Sage.

**NEEDS A RUN:** whether `SAGE_OPENCODE_LOG_LEVEL=DEBUG` (`driver/server.py:88-91`) echoes
request/response payloads — i.e. tool results and model output — into this file.

## 28. Secrets, noted in passing

Not rows, but they belong in an at-rest inventory:

- `backend/sage/resources/pinned_model_api.py:67` — a pasted Model API token is `json.dumps`'d into
  **generated app source** (`service.py:15514`), which is committed and pushed.
- `backend/sage/resources/model_api_credentials.py:197-199` — credentials written to
  `<root>/.sage/model-api-credentials.json` with `chmod 0600`, gitignored (`:216` adds the rule).

**Mark:** CONFIRMED for the file locations. The first is a real committed-secret path and deserves its
own ticket.

## 29. `PUT /api/project/file` writes arbitrary content anywhere in the workspace

`backend/sage/orchestrator/app.py:1970`, via `_resolve_workspace_file`. The Code panel can write
arbitrary editor content to any path under `project.workspace.path`, which `git add -A` then commits.
Not a leak Sage causes, but it is an unguarded write path into the committed tree — no copy scan runs
on it.

**Mark:** CONFIRMED.

## 30. NOT ON THE LIST — Sage's own prompt tells the agent to download Datasets to `/tmp/`

When a Dataset is not mounted, Sage instructs the model to fetch it —
`backend/sage/orchestrator/service.py:2338`:

> `` '`.download_file(<name>, "/tmp/<name>")` fetches one to read with pandas.' ``

and again for a single file, `service.py:2363`:

> `` '`DatasetClient().get_dataset("{unique}").download_file("{rel}", "/tmp/{name}")`, ' ``
> `` "then read /tmp/{name} with pandas." ``

So **whole Dataset files land in `/tmp/` on Sage's own instruction.** Nothing tracks them, nothing
ignores them (they are outside the repo), and nothing deletes them. The `bash` command that does it is
also persisted verbatim into the committed transcript (§1) and into `opencode.db` (§26), and the
resulting `read` tool output is stored in full in `opencode.db`.

**Mark:** CONFIRMED. This is the shortest path from a declared Dataset to an untracked file on disk,
and Sage authored it.

## 31. NOT ON THE LIST — three log lines echo content to container stdout

Sink: `backend/sage/orchestrator/app.py:111`, `logging.basicConfig(level=logging.INFO)` → stdout (in
Domino, the container log), plus two in-memory rings at `app.py:117` (`_LOG_RING`, maxlen 400) and
`app.py:123` (`_WARN_RING`, maxlen 200). No `FileHandler` anywhere — nothing writes a Sage log file.

1. **Raw gateway SSE chunks** — `backend/sage/shim/keepalive.py:73`:
   ```python
   log.info("stream chunk %d: %r", seen, chunk[:DEBUG_STREAM_MAX_BYTES])
   ```
   `keepalive.py:36` says it plainly: *"Off by default — verbose, and the chunks carry prompt and
   completion text."* Capped at 40 chunks × 400 bytes (`:40-41`). **Toggleable at runtime with no
   restart** via `POST /api/diag/debug-stream` (`app.py:1548`). The widest aperture here: it is the
   last hop before OpenCode, so it sees whatever was sent and returned — including a shared live-read
   receipt carrying `values` (§33).
2. **Raw tool-call arguments** — `service.py:2820-2838`, `_unparsed_tool_evidence` returns
   `f"len={len(raw)} head={raw[:200]!r} tail={raw[-100:]!r}"`. Its docstring: *"The whole string is
   the file the model was writing, and the log ring is read by people."* Up to 300 characters of the
   file being written. Called at `service.py:10599`.
3. **User prompt text** — `backend/sage/orchestrator/handoff.py:259-261`,
   `log.info("handoff: verdict=%s ... prompt=%r", ..., (user or "").strip()[:_LOG_CHARS])` with
   `_LOG_CHARS = 120` (`handoff.py:40`).

Clean by contrast: `backend/sage/driver/opencode.py:306` logs only counts and byte lengths.

**Mark:** CONFIRMED.

## 32. Diag endpoints — none persist; two return content

All in `backend/sage/orchestrator/app.py`:

| Route | Line | Returns content? |
|---|---|---|
| `GET /api/diag` | 1365 | config + last 30 lines of OpenCode's log + `log_tail` — inherits §31 |
| `GET /api/diag/log` | 1442 | **yes, transitively** — serves the ring where §31's echoes land |
| `GET /api/diag/mcp` | 1468 | no — shells `opencode mcp list` |
| `GET /api/diag/opencode` | 1512 | reads `$TMPDIR/sage-opencode.log` (`service.py:5016-5026`) |
| `GET /api/diag/timing` | 1532 | **yes** — includes `prompt`, 200 chars (`backend/sage/timing.py:131`) |
| `POST /api/diag/debug-stream` | 1548 | turns on §31's chunk logging |

None writes to disk. `backend/sage/timing.py` has no disk write at all — a `collections.deque(maxlen=20)`
(`timing.py:45`, `:95`) in memory, retaining `prompt=(prompt or "")[:200]` (`timing.py:130-131`).

**Mark:** CONFIRMED — RAM and HTTP response only, not at rest.

## 33. CORRECTION to §16 — a *shared* table sends real values to the model after all

ADR-0041's "reaches the person without reaching the model" holds on the default path and is
deliberately breached on the shared path. `backend/sage/liveread/run.py:72-81`:

```python
if receipt.values is None:
    lines.append(
        "You have NOT been shown the values — the person can see them on the card. …"
    )
else:
    shown = len(receipt.values)
    of = "" if shown == receipt.rows else f" — {shown} of the {receipt.rows} on the card"
    lines.append(f"Rows (the creator shared this table{of}): {receipt.values}")
```

`values` is populated at `backend/sage/liveread/result.py:104` when
`grant.values_allowed(binding, table, shared=shared)` (`backend/sage/liveread/grant.py:64`, keyed off
`.sage/samples.json`), budgeted to `VALUES_BUDGET_CHARS = 8000` (`result.py:31`). That string becomes
the MCP `content[].text` at `backend/sage/liveread/mcp.py:147`.

The consent record is real and the ADR reasons about it carefully. What the ADR does not account for
is where the tool result then goes: **§26 confirms OpenCode persists MCP tool results verbatim**, so
up to 8 KB of real row values land in `opencode.db` — a file outside `.sage/`, outside the repo, with
no retention policy and unreachable by ADR-0036's deletion.

**Mark:** CONFIRMED (both halves read).

## 34. Telemetry / feedback — clean

`backend/sage/feedback/` is a build-repair loop, not analytics. `runner.py` runs `npx tsc --noEmit`,
captures stdout+stderr into `FeedbackReport.raw`, parses into `FeedbackError(file, line, col, code,
message)`, and `as_agent_message()` renders `- src/App.tsx:12:5 TS2304: …` lines for the next turn.
`circuit_breaker.py` uses a sorted `file:line:code` signature.

**No HTTP client, no file writes, no transcript, no rows, no user identifiers.** One caveat:
TypeScript error messages can quote a source expression, and `as_agent_message` puts them into the
next prompt — which then lands in `opencode.db` (§26). That is the only way feedback content reaches
disk.

**Mark:** CONFIRMED.

## 35. Non-writes checked and excluded, for closure

`backend/sage/diag_mcp_probe.py:104` (stdio framing), `orchestrator/boot_page.py:86` (HTTP socket),
`shim/keepalive.py:324-325` and `app.py:2811-2865`, `:3124` (SSE yield, network only),
`describe.py:57,154,216,421`, `scope.py:204`, `service.py:2164,5023,5038,15296`, `preview/queries.py`
(all read-mode `open`), `workbench/js/**` (browser only; `prefs.js:163` is `localStorage`).
`tools/dataset_probe.py:123-165` and `tools/app_visibility.py:130` `print(json.dumps(...))` dataset
records to stdout — no in-repo redirect exists.

**No `tarfile`, `zipfile`, `shutil.make_archive`, `to_csv`, `to_json`, `pickle`, `Path.touch`,
`os.system`, `shell=True`, or subprocess file-redirect anywhere in `backend/sage/`.**

`tempfile.mkdtemp(prefix="sage-fake-datasets-")` (`backend/sage/assets/provider.py:212-214`, `:222`)
writes synthetic rows only (the off-Domino `FakeAssetProvider`) and is never cleaned up.
`tempfile.TemporaryDirectory(prefix="sage-seed-")` (`backend/sage/provision/seed.py:91`) holds a
template copy and is context-managed, so it is removed.

---

## Not on the user's list

Ordered by how much they change the storage design.

1. **`~/.local/share/opencode/opencode.db`** — §26. Full tool results verbatim: file bytes, command
   stdout, base64 attachments, full prompts. 26.5 MB live. Sage never sets `XDG_DATA_HOME`, so there
   is no cap, no rotation, and no delete path — ADR-0036 cannot reach it. **The single biggest gap.**
2. **`.sage/attachments.json` commits up to 3 verbatim rows per CSV/TSV** — §9. Committed, pushed,
   shipped into the published container, *and* fed to the model on an `@mention` under prompt text
   that says it is not data. The file's own docstring claims "data never enters git."
3. **Sage's own prompt tells the agent to download Datasets to `/tmp/`** — §30. Untracked,
   un-ignorable, never cleaned.
4. **Live-read Artifacts hold up to 500 real rows and are committed** — §16; and a *shared* table
   sends up to 8 KB of real values into the model and thence into `opencode.db` — §33.
5. **`public/data/` is rebuilt and baked into the published `dist/`** — §21, and it is not always
   symlinks: Sage downloads whole Dataset files onto the volume when there is no mount — §25.
6. **`.sage/handoff-transcript.md`** — §10. A verbatim second copy of the Chat transcript, moved into
   a Built App's repo, unreachable by conversation deletion.
7. **`.sage/plan-docs/<id>/vNNN.md`** — §12. Every plan version, forever.
8. **`.sage/snapshots/.git`** — §18. Full content of the tree, every turn, on the volume; `examples/`
   is *not* excluded from it.
9. **`.sage/scratch/`** — §24. Whole uploaded files, raw bytes.
10. **Three log lines echo content to container stdout** — §31, one of them runtime-toggleable via
    `POST /api/diag/debug-stream`.
11. **An upload writes bytes into a shared Domino Dataset mount** — §19. By design, with a documented
    stranding failure mode.
12. **`.sage/queries.json`** — §11. Agent-written SQL, committed; literals ride along.
13. **The whole `.sage/` tree ships inside the published App container** — §20. The exclusion set is
    exactly two files.
14. **Bash command strings are persisted verbatim into the transcript** — §1.
15. **The commit backstop stops covering a detached attachment** — §3, stated in the code itself at
    `service.py:14936-14937`.
16. **A pasted Model API token is written into committed app source** — §28.

## Contrary to the user's starting list

- **Item 3 is much narrower than "verbatim copies under `src/`."** It misses any renamed copy in a
  non-code extension (`sales.csv` → `src/rows.csv` is never even read: `service.py:14989-14990`),
  anything under `public/`, anything outside a Built App tree, and anything whose attachment has been
  detached.
- **Item 6 is right (pointers), but the manifest that *does* carry content — `.sage/attachments.json`
  — is not on the list at all** (§9).
- **Item 8's "believed covered by the sensitivity gate" overstates it.** The gate is off by default
  (`sensitivity.py:11-14`), keys on a freeform tag, and gates *which model runs*, never what is
  written to disk. Two paths put rows into the model past it: the attachment descriptor (§9) and a
  shared live-read table (§33).
- **Item 9's live-read note is right about Recall and wrong about disk.** The rows land in a committed
  Artifact (§16) — and on the shared path they reach the model too (§33).
- **Item 5 is confirmed exactly as asked** — no `git add -f`, no pathspec anywhere (§5) — **but the
  conclusion is wrong**: the data reaches the published App through `dist/` (§21), and `public/data/`
  can hold real bytes rather than symlinks (§25).
- **Item 2 is confirmed and worse than stated:** plans are versioned forever (§12).
- **Item 1 is confirmed in full**, including the recorded intent (ADR-0006).
- **Item 4 is confirmed as believed:** derived data is uncaught (§4).
- **Item 7 is confirmed:** `ARTIFACT_COMMIT_MAX` is a size rule, never a content rule (§7).

## What only a run can settle

1. **Does `.sage/attachments.json` in a live Project actually contain sample rows?** Every link is read
   (`describe.py:173` → `service.py:5209` → `manager.py:1297`), but `detail` is only written for a
   delimited file the describer could open. Read a real Builder's committed `attachments.json` and
   grep it for row values. **Highest-value single check here.**
2. **Does `~/.local/share/opencode/tool-output/` receive spilled results, and at what threshold?** It
   is empty on this machine (§26). A 500-row receipt or a large Dataset read may cross it.
3. **Does `SAGE_OPENCODE_LOG_LEVEL=DEBUG` echo payloads into `$TMPDIR/sage-opencode.log`?** (§27)
4. **How large does `.sage/snapshots/.git` grow, and does anything prune it?** (§18)
5. **Does the app's `.gitignore` `public/data/` line survive a turn in which the agent rewrites
   `.gitignore`?** Nothing re-asserts it per turn that I found (§5).
6. **Does `rehydrate_data.py`'s `DatasetClient` resolve as the publisher or the viewer on the App
   container?** (§21) This decides whether a published App can pull Datasets its viewer cannot.
7. **What visibility values a given deployment actually returns**, and whether `AUTHENTICATED` is the
   widest one available there (`publish_guard.py:52`; `:133-136` says on cloud-dogfood no refusable
   value exists, so the check "stops nothing" there).
8. **Whether `/tmp/` Dataset downloads (§30) survive a container restart** and how many accumulate in
   a long-lived Builder.
