# Workbench Chat

Executable spec for Chat mode. Companion: [handoff.md](handoff.md). Locked decisions: [ADR-0003](../adr/0003-workbench-chat-and-untitled.md) (shell, artifacts),
[ADR-0004](../adr/0004-workbench-is-the-door.md) (door, Default, Sage Builder). Language:
[CONTEXT.md](../../CONTEXT.md).

**Done when** every acceptance check at the bottom is true, and `sage-chat` can answer an open-ended question with a chart PNG and a table JSON in the Thread without touching `src/`.

## 1. Who this is for

A person who is comfortable with data and does not want to manage projects, git, or a coding agent. They land in Chat, ask a question, attach a file or a Data Source, and get an answer with Artifacts. They can stay in Chat forever. Building an app is a later, explicit step ([handoff.md](handoff.md)).

## 2. Default

Hub is not a product. The Workbench App is the door ([ADR-0004](../adr/0004-workbench-is-the-door.md)):
with extended identity the sidecar is the viewer. First open finds or creates this viewer's
**Default** Project (`sage-<user-slug>-<id>`), starts or resumes **their** Sage Builder, and lands
in Chat there. Wait only when that builder is down.

The chip says **Default**. That is a Sage overlay, not a Domino rename. Naming it writes
`displayName` and clears the default mark; the Domino/git name stays `sage-<user-slug>-<id>`.
There is no Control Plane rename API. They never get a second Default.

The chip lists only Projects whose Control Plane and git name start with `sage-`. Switching it
starts or resumes this viewer's Sage Builder in that Project and takes them there.

**New conversation** creates a Thread in the current Project. It does not create a Domino project.

**New project** creates a git-backed `sage-*` Project (GitHub repo + git-based Domino project +
this viewer's Sage Builder) and lands in Chat there.

**Gallery** is Built Apps this viewer may see. It lives in Sage Builder chrome. Opening an item
opens that Built App; it does not switch Project.

**Publish** is a Sage Builder verb. Default is a real git Project, so Publish is available once
they are in that builder.

Chat turns **do not** consult `has_built` or the first-build plan gate. `Mode.CHAT` is not added to
`ModelControl` — Chat is a Workbench mode, not a plan/implement phase. The orchestrator maps Chat
turns to agent `sage-chat` and skips `_build_stream`'s plan-gate / typecheck loop.

## 3. Thread storage

Today one OpenCode session lives at `.sage/session.json` and one transcript at `.sage/history.jsonl`. That remains the **Build** session for the project's one app.

Chat adds:

```
.sage/threads/<threadId>/meta.json         # the Thread's own record, committed
.sage/threads/<threadId>/session.json      # OpenCode session id for this Thread
.sage/threads/<threadId>/history.jsonl     # UI replay for this Thread
.sage/threads/<threadId>/context.json      # Session context (chips)
.sage/threads/<threadId>/artifacts.json    # manifest of files under examples/<threadId>/
.sage/threads/<threadId>/handoff.json      # see handoff.md; absent until suggested or done
examples/<threadId>/                       # Artifact files; committed
```

Chat writes those files every turn. Git commit + push is coalesced so a burst of follow-ups does not spam the remote. Push when losing the workspace would hurt: the first message of a Thread, a turn that produced Artifacts, leaving the Thread (New conversation, a different Thread, Chat → Build / Code / Manage), ~30s idle, and container stop. Mid-stream and tool steps do not push. Reuses `_save_to_git`; commit message `sage: chat ({reason})`. Local `/tmp` workspaces are not a repo root — treat that as saved.

A turn ends at `done`. What comes after it — classifying the turn for a Build offer, compacting the session, the commit + push — is aftercare, and it runs with the turn lock already released, so the next question is accepted while the last one is still tidying up. Aftercare that touches what the next turn touches takes the lock back for itself: compaction, because it rewrites the session the next prompt runs in, and the save, because it commits the whole tree. Losing that race defers the work rather than running it unguarded — compaction waits for the turn after next, the commit falls back to the idle timer.

`meta.json` shape:

```json
{
  "id": "thr_01HZX…",
  "title": "Gross exposure by desk",
  "createdAt": "2026-08-25T18:01:00Z",
  "updatedAt": "2026-08-25T18:12:00Z",
  "pinned": false
}
```

Title: first user message, truncated to 60 characters, until the user renames. `id` is a ULID (sortable, no coordination).

There is no index. The list is a scan of `.sage/threads/*/meta.json`, newest `updatedAt` first, because two viewers in one Project are two Sage Builders and two Builders are two processes: one shared file rewritten whole means the second writer drops the first one's Thread while its history is still on disk (ADR-0008). Each record has one writer. Delete shrinks the record to `{id, deleted, updatedAt}` and removes every other file in the directory — the transcript included, and the title with it, since the title is the person's own first message — but never the directory itself, so the scan cannot resurrect a Thread the person threw away ([ADR-0036](../adr/0036-a-deleted-conversation-takes-its-transcript-with-it.md)). `examples/<threadId>/` goes too, unless a live Built App's handoff digest names those files by path — and `handoff.json` then survives with them, as the record of why they are still there and the evidence a later sweep re-reads when that app is itself deleted. The delete commits and pushes on its own rather than waiting for the next turn, through `_flush_chat_save` so it holds the turn lock and cannot commit a build turn's half-written tree; a save that did not land is reported to the person, because the files are gone here and still on the remote. Threads deleted before that change are tombstones with every file beside them; a bounded sweep finishes them as the Project is opened, beside the membership backfill — it releases their fetched files and does not push, so the next save carries it. An index that will not parse is left alone rather than read as empty: it is the tombstone path, and a bad read must not become permanent loss.

`Workspace.read_session_id` / `history_path` stay Build-scoped. Chat goes through new helpers on `Workspace` (`thread_session_id`, `append_thread_history`, …) so a Chat turn cannot append to the Build transcript and a Build turn cannot append to a Thread.

OpenCode: `create_session(directory=.sage/chat-work)` per Thread, persist that id under the Thread. One `opencode serve` per container still; sessions are many.

After a successful Chat turn, Sage asks OpenCode to compact that Thread's session when the model's context is getting full (`POST /api/session/{id}/summarize`, same Chat alias as the thread; `auto=false` so OpenCode does not start a synthetic follow-up). Token usage on the latest assistant message is the trigger when OpenCode reports it (70% of that alias's window in `opencode.json`). If usage is missing, Sage falls back to 12 user turns since the last compact. Compact errors are logged and ignored — the turn the person just got still succeeds.

Sage `history.jsonl` is the UI replay and is never rewritten. The Thread still shows every turn; only what OpenCode sends the model next shrinks.

## 4. Agent: `sage-chat`

Add to [opencode.json](../../opencode.json) next to `sage-ask`. Prompt body **is** [template/chat/AGENTS.md](../../template/chat/AGENTS.md) (inline it the way the other agents inline theirs; that file is the source of truth, the JSON is the cache OpenCode actually loads).

```jsonc
"sage-chat": {
  "mode": "primary",
  "description": "Analysis: answers questions, runs Python, writes chart/table files. Does not build the app.",
  "permission": { "edit": "allow", "bash": "allow" },
  "prompt": "<contents of template/chat/AGENTS.md>"
}
```

Do not reuse `sage-ask`. `sage-ask` is read-only Q&A **about the Built App** and bash/edit are denied. Chat must run Python and write Artifacts.

### Enforcement — permissions are not enough

OpenCode `permission` is not trusted (same lesson as `sage-ask`: the shim strips tools). For `sage-chat`:

- **Allow** bash.
- **Allow** edit/write only when the path is under `examples/<currentThreadId>/` or `.sage/threads/<currentThreadId>/`.
- **Reject** any edit/write whose path is under `src/`, `public/`, `.sage/` except that Thread dir, or any config file (`package.json`, `AGENTS.md`, `vite.config.ts`, `app.sh`, `serve.py`). The shim rewrites that tool result to an error naming `examples/<threadId>/` so the model retries there instead of `src/` in a loop. Revert the file on disk at turn end.
- **Do not** run the typecheck feedback loop. A Chat turn that writes a PNG is done.

OpenCode's Chat session directory is `.sage/chat-work/` (a stub `AGENTS.md`, plus links to `examples/` and `.sage/scratch/`). The stub holds no rules: OpenCode loads AGENTS.md from the session directory, so writing the body there sent it twice per turn alongside the `sage-chat` prompt. It stays as a stub only so an `AGENTS.md` added at the Project root above it cannot reach a Thread. It is not the React app clone. Chat attach does not seed `template/react-vite` or start Vite. Build / the preview proxy seed when they need the app.

### Data the agent can see

On each Chat turn the orchestrator injects, as prompt context, the same style of notes used today for mentions:

- Session context chips (see §6): name, kind, and for files the existing `describe.py` summary (shape, not content).
- Artifacts already on this Thread (`artifacts.json`): path and title only, so a follow-up can edit a chart without pinning it. `describe.py` runs only if the person `@`s that file.
- Bound Data Sources in Session context: display name, connector, scope. The agent queries live via the Python SDK already on the image (`domino_data`), not via named queries. Named queries remain a Built App contract and are out of scope for Chat.
- Uploaded files already in `public/data/` (existing Attachment path) if they are in Session context.

Chat may attach a Data Source without creating a Binding. A Binding is written at handoff, when Session context that names a Resource becomes `App.requires`.

## 5. Artifact directory and manifest

The agent writes files. The UI renders from the manifest + the files. There is no chart object in the SSE payload beyond "an Artifact appeared at this path".

### Files

| Kind | Path | Body |
|------|------|------|
| chart | `examples/<threadId>/<slug>.png` | PNG. Title lives in the manifest, not in a sidecar DSL. |
| table | `examples/<threadId>/<slug>.table.json` | `{ "title": str, "columns": [str], "rows": [[str\|number\|null]] }` — max 500 rows written; the UI shows 10 and "Show all". |
| query | `examples/<threadId>/<slug>.sql` | UTF-8 SQL the agent actually ran, if any. Optional. |
| note  | `examples/<threadId>/<slug>.md` | Rare; a short markdown file the user might want to keep. |

Slug: lowercase, hyphenated, unique within the Thread. If a name collides, suffix `-2`.

Scratch Python files the agent needs to run belong in `/tmp` or a gitignored `.sage/tmp/` — they are not Artifacts and the UI does not list them.

### Manifest

`.sage/threads/<threadId>/artifacts.json`:

```json
{
  "items": [
    {
      "id": "art_01HZX…",
      "kind": "chart",
      "name": "exposure_by_desk.png",
      "title": "Gross notional by desk",
      "path": "examples/thr_01HZX…/exposure_by_desk.png",
      "producedAt": "2026-08-25T18:11:04Z",
      "messageId": "m_01HZX…"
    }
  ]
}
```

The orchestrator appends a row when it observes a write under `examples/<threadId>/` during a `sage-chat` turn (the same event stream it already uses for tool steps). The agent does not edit the manifest. If the agent writes a file the orchestrator does not recognise (`.xlsx`, `.html`), record `kind: "file"` and the UI offers a download, not an inline renderer.

### Rendering in the Thread

Assistant history entries gain an optional `artifacts: [{ id, kind, path, title }]`. The Workbench Chat pane (lifted from the prototype `message-blocks.js`) renders:

- `chart` → `<img>` of the PNG, with Open / Export. Export is the file.
- `table` → the JSON as a table, 10 rows, Show all.
- `query` / `note` / `file` → a compact file card.

Collapsed "Ran Python" is optional. If the SSE stream includes a bash tool step, show it collapsed the way the prototype does (`Ran Python · 1.2s`). Do not render write, read, or edit tool steps in Chat — those are how a missed `src/` path used to fill the Thread. If there is no bash step, do not fake a sandbox card. Intermediate assistant text ("let me save there") is dropped; the Thread keeps the last text part of the turn.

## 6. Session context and chips

Three bags, never one (the prototype's P0 bug was using `planId || threadId` as a single context id).

| Bag | File | Lifetime | UI |
|-----|------|----------|----|
| Session context | `.sage/threads/<id>/context.json` | This Thread | Chips on the composer, and a tick on the row in the resource panel |
| Working set | `.sage/project-resources.json` | This project | The working-set rail. What the list means, and why it never reaches a prompt, is the **Working set** entry in `CONTEXT.md` and [ADR-0020](../adr/0020-the-working-set-is-orientation-never-context.md) — not repeated here. Mechanics: parents plus optional **pins** (Dataset files, Data Source tables), and pins are not prompt context either. Putting a **parent** in Session context joins it here in the same click, and the answer carries `joinedProject: true` so the rail refreshes. Leaves (a `dsfile:` file, a `table:` table) join nothing — they are reached by expanding a parent that is already a member. |
| Bindings | `.sage/bindings.json` | The Built App | The App dependencies modal, off Build's header; and a mark on the Project row the panel draws |

Chat-local files live in gitignored `.sage/scratch/`. They persist on this workspace volume. **Add to a Dataset** copies them onto a writable Dataset so they outlive this workspace.

`context.json`:

```json
{
  "items": [
    {
      "id": "ctx_1",
      "kind": "file",
      "name": "positions_q3.csv",
      "path": ".sage/scratch/positions_q3.csv",
      "addedBy": "user",
      "addedAt": "2026-08-25T18:02:00Z"
    },
    {
      "id": "ctx_2",
      "kind": "data_source",
      "resourceId": "table:ds-dwh:DWH.MARTS.DIM_ACCOUNT",
      "parentId": "data_source:ds-dwh",
      "bindingKey": ["data_source", "ds-dwh"],
      "name": "DIM_ACCOUNT",
      "scope": { "database": "DWH", "schema": "MARTS", "table": "DIM_ACCOUNT" },
      "addedBy": "user",
      "addedAt": "2026-08-25T18:08:00Z"
    }
  ]
}
```

`kind` is `file` | `dataset` | `data_source` | `model_api` | `llm_alias` | `artifact`. A Resource in Session context is **not** automatically a Binding. `bindingKey` is a pointer at a row that *may* already exist in `bindings.json`; Chat will usually leave it unset until handoff. A Data Source **table** chip stores `scope` without writing a Binding.

The prompt receives one line per chip (plus `describe.py` for files, column names/types for a scoped table). Listing a Dataset or opening a Data Source must not inject the tree.

Chips:

- Persist across turns in this Thread.
- Clicking × removes the row from `context.json` for subsequent turns. Already-sent messages keep the chips they were sent with (store them on the history `user` event as `contextIds`).
- `@` autocomplete lists Session context first, then this Thread's Artifacts (from the manifest, not as chips), then project **pins**, then parent Resources, then Files, then parent Resources this project has **not** joined yet. Cap 8 over all six groups. The menu draws one heading, off the first row: "Not in {project} yet" when a catalogue row leads, and every catalogue row is also tagged `not in {project}` on the row, since the heading cannot speak for six groups. Picking one joins the project (see Project membership above). The last group is parents only, off the catalogue listing the panel already fetched — it does not fetch a warehouse catalog, so a table never appears in the menu. Tables stay reachable by pinning one in the rail.
- Picking `@` inserts `@name` into the composer text (and the prompt OpenCode receives) **and** adds the chip. Do not strip the token.
- Adding from the resource panel appends to `context.json` and shows the chip. Provenance `addedBy: user | sage`. When Sage adds one, it reports in the Thread in a sentence ("I'll use card-transactions-q3") — mixed-initiative from the mock, but the panel is the accounting.

The resource panel is one list, headed **Project resources**: Plans, then Data (Datasets and Data Sources under one head), Language models, Predictive models, Agents, Skills, MCPs, then Files. A group with nothing in it is not drawn at all — a group whose *listing failed* still is, carrying its reason, because empty and unknown are different answers. Dataset and Data Source rows expand to browse files or database/schema/table.

Session context is **not** a second section in it. A row in this Thread's `context.json` wears a tick, with a tooltip pointing at the chips; a row that is not wears a `+` on hover, in Chat only. The chips over the composer are where context is shown and taken back — one accounting, not two. The row's own menu and the details drawer behind it both offer "Stop using here".

Files Sage writes as Artifacts stay in the Thread and `artifacts.json`; they are **not** auto-added to context. `@` can still name them. Do not show `.sage/` except `.sage/scratch/`, and do not show `AGENTS.md`.

Remove from project is on every membership parent. It is refused while a Binding still names that Resource. Removing a parent also drops matching chips from the open Thread.

## 7. Workbench UI

Lift `sage_workspace_prototype/static` from `etanlightstone/sage_explorations` into `backend/sage/workbench/`. Replace fixture `api.js` calls with orchestrator routes. Code and Manage tabs: placeholder pane, not 404.

Minimum Chat chrome that must work (the rest of the mock can wait):

- Scope chip (Default + other `sage-*` Projects)
- Chat / Build tabs (Build is the existing builder, restyled into the shell)
- Conversation rail of Threads in the current project
- Composer with chips, `@`, attach, model picker (gateway aliases this caller can use; reasoning effort when the alias supports it)
- Message list with Artifact blocks
- Resource panel (one list, headed Project resources)
- The plan-suggestion callout and Open in Build — specified in [handoff.md](handoff.md)

### The conversation view (#56)

A Conversation has two halves and, since #62, they live in several files: the Thread's own
`history.jsonl`, and one log per Built App the Conversation drove. The viewer's `conversationView`
preference (#52, `js/prefs.js`) decides which of them Chat draws.

- **`split`** — the Workbench as it was. Chat reads the Chat half and nothing else.
- **`unified`** — Chat reads `GET /api/threads/<id>/conversation`, which merges both halves in the
  order they happened, each row labelled with the half it came from. That read is a **scan**: it
  reads every app in the Project and filters on the conversation tag, because `Orchestrator.history`
  answers for the selected app only and one Thread can hand off more than once (#72). Each build run
  folds into one collapsed row — Chat has no preview pane, and twenty implementation turns would
  bury the questions around them.

  The same read serves Build, and Build cuts it (ADR-0019): it has a preview bound to one app, so it
  draws that app's build turns and that app's **Lead-in** — the Chat turns after the previous
  handoff and before this app's first build turn. Chat is not cut, because it has no app to cut
  against. The cut is made in `splitConversationHalves` (`js/store.js`), on the `at` stamps and
  never on the merged index, and each gap it leaves draws a `lead_in_fold` block where the gap is,
  naming the app whose Lead-in it holds and counting its turns. A turn nobody can place — after the
  last handoff, with no `at` on it, or in a gap holding no request at all — is shown, and an app with
  no build turn yet has no boundary and so applies no filter at all.

`SW.prefs.get('conversationView')` has exactly one reader, `store.conversationMessages`. Everything
downstream draws what the store handed it, so #61 can settle the question by deleting one branch.

The **`app_change` block** is not part of that branch. A build turn emits one per app it changed,
server-side and blind to the preference, into that app's own log; `AppChange`
(`js/components/message-blocks.js`) renders it under both views. What is unified-only is the
**folding** — the `build_run` block, its renderer, and the merged read behind them — and a collapsed
row's face is built from the run's `app_change` blocks rather than from a second source of app facts
(ADR-0008, #83). The card carries the app's name as it stood then and reads publish state live off
the rail's list, so a long transcript costs one read and not one per card.

## 8. What this slice does not do

- Jupyter / kernels / notebooks
- Named queries from Chat
- A second LLM stack or Open Web UI
- RAG, MCP connector UI
- Many Built Apps per Domino project
- Token streaming
- Reverse handoff ("Ask about this app") — nice, not required

## 9. Acceptance

An implementer is done when all of these pass:

1. Opening the Workbench finds or creates Default, starts this viewer's Sage Builder if it is down, and lands in Chat there. The chip says Default until they name it. "New conversation" does not create a Domino project. "New project" does.
2. A Chat turn with "what's in this CSV?" on an attached file writes a PNG and/or a `.table.json` under `examples/<threadId>/`, appends the manifest, and the Thread shows the Artifact after reload. `src/` is untouched (git diff).
3. A `sage-chat` attempt to edit `src/App.tsx` is stripped by the shim; the user-visible reply does not mention tools or permissions.
4. Removing a chip drops that Resource from the next turn's prompt context, and the tick comes off its row in the panel. The previous user message still shows the chip it was sent with.
5. `@` lists this Thread's context rows first, then its Artifacts. Picking a row leaves `@name` in the sent message; OpenCode's prompt includes that token and the file path. A generated PNG is not auto-chipped.
6. Chat turns never produce a plan-approval card and never run `tsc`.
7. `template/chat/AGENTS.md` is the prompt body OpenCode receives for `sage-chat` (inline in `opencode.json`, kept in sync).
8. Tests: shim path-allowlist for `sage-chat`; Thread history does not leak into Build `history.jsonl`; Default slug hydrates the Default chip; a new conversation does not provision; New project does.
