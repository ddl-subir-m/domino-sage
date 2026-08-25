# Workbench Chat

Executable spec for Chat mode. Companion: [handoff.md](handoff.md). Locked decisions: [ADR-0003](../adr/0003-workbench-chat-and-untitled.md). Language: [CONTEXT.md](../../CONTEXT.md).

**Done when** every acceptance check at the bottom is true, and `sage-chat` can answer an open-ended question with a chart PNG and a table JSON in the Thread without touching `src/`.

## 1. Who this is for

A person who is comfortable with data and does not want to manage projects, git, or a coding agent. They land in Chat, ask a question, attach a file or a Data Source, and get an answer with Artifacts. They can stay in Chat forever. Building an app is a later, explicit step ([handoff.md](handoff.md)).

## 2. Untitled provision

On first Workbench open, if the caller has no scratch project:

1. Run the existing hub pipeline (`provision/service.py`) with a unique Domino + git name `sage-<user-slug>-<id>` derived from the caller’s username and Domino user id. Do **not** name the Domino project `Untitled` — that collides across users and is a bad URL slug.
2. Write `.sage/settings.json` `{ "untitled": true }` on first builder boot (seed must not wipe this).
3. The scope chip shows **Untitled** (Sage overlay). Open Chat on a new Thread in that project.

If that scratch project already exists (same `sage-<user>-<id>`, including a `-N` collision suffix, or a legacy project still named Untitled), reuse it. Never provision a second scratch project for the same user.

Rename (the first time a handoff names the app) writes `displayName` into `.sage/settings.json` and sets `untitled: false`. Domino’s project name, id, repo, and Threads do not move — there is no Control Plane rename API.

**New conversation** creates a Thread in the current project. It does not call provision.

Chat turns **do not** consult `has_built` or the first-build plan gate. `Mode.CHAT` is not added to `ModelControl` — Chat is a Workbench mode, not a plan/implement phase. The orchestrator maps Chat turns to agent `sage-chat` and skips `_build_stream`'s plan-gate / typecheck loop.

## 3. Thread storage

Today one OpenCode session lives at `.sage/session.json` and one transcript at `.sage/history.jsonl`. That remains the **Build** session for the project's one app.

Chat adds:

```
.sage/threads.json                         # index, committed
.sage/threads/<threadId>/session.json      # OpenCode session id for this Thread
.sage/threads/<threadId>/history.jsonl     # UI replay for this Thread
.sage/threads/<threadId>/context.json      # Session context (chips)
.sage/threads/<threadId>/artifacts.json    # manifest of files under examples/<threadId>/
.sage/threads/<threadId>/handoff.json      # see handoff.md; absent until suggested or done
examples/<threadId>/                       # Artifact files; committed
```

Chat writes those files every turn. Git commit + push is coalesced so a burst of follow-ups does not spam the remote. Push when losing the workspace would hurt: the first message of a Thread, a turn that produced Artifacts, leaving the Thread (New conversation, a different Thread, Chat → Build / Code / Manage), ~30s idle, and container stop. Mid-stream and tool steps do not push. Reuses `_save_to_git`; commit message `sage: chat ({reason})`. Local `/tmp` workspaces are not a repo root — treat that as saved.

`threads.json` shape:

```json
[
  {
    "id": "thr_01HZX…",
    "title": "Gross exposure by desk",
    "createdAt": "2026-08-25T18:01:00Z",
    "updatedAt": "2026-08-25T18:12:00Z",
    "pinned": false
  }
]
```

Title: first user message, truncated to 60 characters, until the user renames. `id` is a ULID (sortable, no coordination).

`Workspace.read_session_id` / `history_path` stay Build-scoped. Chat goes through new helpers on `Workspace` (`thread_session_id`, `append_thread_history`, …) so a Chat turn cannot append to the Build transcript and a Build turn cannot append to a Thread.

OpenCode: `create_session(directory=workspace.root)` per Thread, persist that id under the Thread. One `opencode serve` per container still; sessions are many. Compaction is still backlog — a long Thread will get expensive; do not solve it in this slice.

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
- **Strip** any edit/write whose path is under `src/`, `public/`, `.sage/` except that Thread dir, or any config file (`package.json`, `AGENTS.md`, `vite.config.ts`, `app.sh`, `serve.py`).
- **Do not** run the typecheck feedback loop. A Chat turn that writes a PNG is done.

The React `template/react-vite/AGENTS.md` still sits at the workspace root and still says "every turn must end with edits to `src/`". The shim allowlist is what makes that instruction harmless on a Chat turn. Do not delete or swap that file for Chat — Build needs it, and Untitled is already a seeded app repo.

### Data the agent can see

On each Chat turn the orchestrator injects, as prompt context, the same style of notes used today for mentions:

- Session context chips (see §6): name, kind, and for files the existing `describe.py` summary (shape, not content).
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

Collapsed "Ran Python" is optional. If the SSE stream includes a bash tool step, show it collapsed the way the prototype does (`Ran Python · 1.2s`). If it does not, do not fake a sandbox card.

## 6. Session context and chips

Two bags, never one (the prototype's P0 bug was using `planId || threadId` as a single context id).

| Bag | File | Lifetime | UI |
|-----|------|----------|----|
| Session context | `.sage/threads/<id>/context.json` | This Thread | Chips on the composer; "IN CONTEXT" in the resource panel |
| Bindings | `.sage/bindings.json` | The Built App | "IN THIS APP" in Build after handoff; Resource Browser today |

`context.json`:

```json
{
  "items": [
    {
      "id": "ctx_1",
      "kind": "file",
      "name": "positions_q3.csv",
      "path": "public/data/positions_q3.csv",
      "addedBy": "user",
      "addedAt": "2026-08-25T18:02:00Z"
    },
    {
      "id": "ctx_2",
      "kind": "data_source",
      "bindingKey": ["data_source", "ds-dwh"],
      "name": "Snowflake-Data-Warehouse",
      "addedBy": "user",
      "addedAt": "2026-08-25T18:08:00Z"
    }
  ]
}
```

`kind` is `file` | `data_source` | `model_api` | `llm_alias` | `artifact`. A Resource in Session context is **not** automatically a Binding. `bindingKey` is a pointer at a row that *may* already exist in `bindings.json`; Chat will usually leave it unset until handoff.

Chips:

- Persist across turns in this Thread.
- Clicking × removes the row from `context.json` for subsequent turns. Already-sent messages keep the chips they were sent with (store them on the history `user` event as `contextIds`).
- `@` autocomplete lists Session context first, then project Resources (existing Resource Browser data), then files.
- Adding from the resource panel appends to `context.json` and shows the chip. Provenance `addedBy: user | sage`. When Sage adds one, it reports in the Thread in a sentence ("I'll use card-transactions-q3") — mixed-initiative from the mock, but the panel is the accounting.

The resource panel in Chat: **IN CONTEXT** (this Thread's `context.json`) above **PROJECT RESOURCES** (what the caller can pick: Datasets, Data Sources, Model APIs, LLM Aliases — today's explorer). Files produced as Artifacts appear under IN CONTEXT as `kind: artifact`. Do not show `.sage/` or `AGENTS.md`.

## 7. Workbench UI

Lift `sage_workspace_prototype/static` from `etanlightstone/sage_explorations` into `backend/sage/workbench/`. Replace fixture `api.js` calls with orchestrator routes. Code and Manage tabs: placeholder pane, not 404.

Minimum Chat chrome that must work (the rest of the mock can wait):

- Scope chip (Untitled + named projects)
- Chat / Build tabs (Build is the existing builder, restyled into the shell)
- Conversation rail of Threads in the current project
- Composer with chips, `@`, attach, model picker (existing Auto control)
- Message list with Artifact blocks
- Resource panel (IN CONTEXT / PROJECT RESOURCES)
- The plan-suggestion callout and Open in Build — specified in [handoff.md](handoff.md)

## 8. What this slice does not do

- Jupyter / kernels / notebooks
- Named queries from Chat
- A second LLM stack or Open Web UI
- RAG, MCP connector UI
- Many Built Apps per Domino project
- Token streaming, compaction
- Reverse handoff ("Ask about this app") — nice, not required

## 9. Acceptance

An implementer is done when all of these pass:

1. First Workbench open as a user with no scratch project provisions one Domino project named `sage-<user>-<id>` and lands in Chat with an empty Thread; the chip says Untitled. Second open reuses it. "New conversation" does not provision.
2. A Chat turn with "what's in this CSV?" on an attached file writes a PNG and/or a `.table.json` under `examples/<threadId>/`, appends the manifest, and the Thread shows the Artifact after reload. `src/` is untouched (git diff).
3. A `sage-chat` attempt to edit `src/App.tsx` is stripped by the shim; the user-visible reply does not mention tools or permissions.
4. Removing a chip drops that Resource from the next turn's prompt context and from IN CONTEXT. The previous user message still shows the chip it was sent with.
5. `@` lists IN CONTEXT rows first.
6. Chat turns never produce a plan-approval card and never run `tsc`.
7. `template/chat/AGENTS.md` is the prompt body OpenCode receives for `sage-chat` (inline in `opencode.json`, kept in sync).
8. Tests: shim path-allowlist for `sage-chat`; Thread history does not leak into Build `history.jsonl`; Untitled reuse (no second provision when `untitled: true` already exists).
