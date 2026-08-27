---
status: accepted
revises: ADR-0005 (the build log is per app, not per project), ADR-0006 (history.md leaves git)
---

# A Project holds many Built Apps, one directory each

A Project had many Threads and one Built App, so the second Thread that asked for a dashboard had
nowhere to put it. Reset was the only way to start over, and Reset takes the previous app's code,
its plan documents and its queries with it.

The old app kept serving — a deployed App runs the commit it was deployed from — so nothing looked
broken. But its source was gone from the working tree, which meant it could never be fixed or
republished. The loss was of the ability to maintain the app, and nothing said so. That is the
failure this decision exists to close.

`handoff.md` §4 held the only answer there was: on confirm, rename the Default Project to the plan
title and set `default: false`. That is a Project-per-app rule wearing a one-shot disguise. It
works exactly once per Project, and the second time it has two apps fighting over one name.

## Decision

The **Built App** is the unit that owns built code, and a Project has many.

- Each lives in `apps/<appId>/`, seeded from the template. That directory is the Build agent's cwd
  and it looks exactly like the old workspace root, so `AGENTS.md`, `plan.md`, `architecture.md`,
  `bindings.json` and `queries.json` keep the paths they have always had, one level down.
- `appId` comes from `new_id("app")` and **never changes**. Domino fixes an App's `entryPoint` when
  the App is created and a republish cannot change it, so a directory named after a mutable title
  would invite a rename that strands the deployment. The display name is the mutable half; it
  starts as the plan title.
- A Built App is born when a handoff is **confirmed**, not when it is published. An unpublished app
  is a real app that has no URL yet.
- Root `.sage/` keeps what belongs to the Project: `threads/`, `plan-docs/`, `settings.json`.
- A Thread points at a Built App **per handoff**. Two Threads may drive one app; one Thread may
  produce several apps over its life.
- There is no `apps.json` and no `threads.json`. Both lists are directory scans. An index file is
  one file with many writers, which is the problem below.

Domino imposes none of this. `list_project_apps` already returns a list, `entryPoint` is a
free-form path, and a Domino project can hold many Apps started from scripts at different paths.
The one-app rule was Sage's alone.

## Why a directory and not a branch

| | directory per app | branch per app | project per app |
|---|---|---|---|
| Publish | one App per Sage App, `entryPoint: apps/<appId>/app.sh`, all republish from `head` | each App pins `gitRefValue=<branch>` | today's path |
| `.sage/` | one root copy, untouched | **diverges per branch** | one per project |
| Preview | Vite `cwd` is the app directory | one app at a time | one per project |
| Apps live at once | many | many | many |

**Branch per app was rejected on ADR-0006.** Conversation logs and Artifacts are committed, so
switching branches to switch apps makes `.sage/` diverge: the Chat rail would empty when you change
app, and `public/data/` attachments would fork. Saving it means reversing ADR-0006, which was
decided on measurements that have not changed.

**Project per app was rejected as the thing that broke.** It is what `handoff.md` §4 already did,
once. It also puts a Project boundary between a Thread and the app it produced, when the whole
point of the rail is that they sit together.

## Multi-writer, which this makes normal rather than odd

Two viewers in one Project each have their own Sage Builder and both push to one remote. Before
this they collided on `src/` and found out immediately. After it they build `apps/x/` and `apps/y/`,
which merge cleanly — and then collide silently on the files at the root. The collision moved from
loud to quiet, which is worse.

Three fixes, each removing a writer rather than adding a lock:

- **`history.jsonl` and `history.md` move to `apps/<appId>/.sage/`.** The agent's memory is
  per app, not per Project. ADR-0005 made it project-wide because a Project *was* an app; it no
  longer is. This also fixes a live bug: the stop button's baseline is a *position* in the log, so
  two builders sharing one log means one viewer's stop can truncate the other's turns.
- **`threads.json` is deleted.** `ThreadStore.create` did read-modify-write behind an in-process
  lock, and two Sage Builders are two processes. Last writer won and the other viewer's Thread
  vanished from the rail while `threads/<id>/` still sat on disk holding its history. One
  `meta.json` per Thread, one writer each, list by scanning.
- **`history.md` stops being committed.** It is generated from the log beside it and rewritten
  whole every turn, so two writers conflict every turn over reproducible data. Gitignore it and
  regenerate on demand. This is the half of ADR-0006 that changes; the log and the Artifacts stay
  in git for the reasons measured there.

What is left at the root has one writer each: `settings.json` (Project-level and near-static),
`plan-docs/<id>/`, `threads/<id>/`.

Two viewers on the **same** app still conflict, and that is correct — they are editing one thing.
Sage surfaces it at turn start and lets the person choose; `_save_to_git` keeps its existing
agent-resolve at save time, because by then the person has moved on and a push that cannot
fast-forward means the build's work never reaches the repo at all.

## Consequences

- **`find_project_app` becomes wrong.** It returns `apps[0]` from `list_project_apps`, which was
  right when a project had one App and is an arbitrary choice when it has several. Publishing app B
  would ship its code as a new version of app A. Each Built App records its own `dominoAppId` on
  first publish and republishes that. `list_project_apps` also reads a single page of 100.
- **`_binding_for_membership` becomes wrong.** Its docstring says "if the Built App still records
  one" and it reads one `bindings.json` to decide whether a Project Resource may be removed. With
  Bindings per app it must ask every app in the Project, or removing a Resource silently breaks an
  app that still reads it.
- **`handoff.json` becomes a list.** One entry per handoff, each with its own `planId`, `appId` and
  state. The classifier's rule changes from *never suggest again for this Thread* to *never suggest
  again while the newest entry is unresolved*, so a Thread that has already produced an app is
  eligible again. `suppressed` stays permanent: that is the person saying stop.
- **The handoff sheet gains a target row**, defaulting to **New app**. An existing app is never
  preselected — preselecting is the silent-overwrite path this ADR exists to close.
- **Reset narrows to one app**, which makes it safer than it was. **Delete app** is new, and when
  the app is published it must offer to delete the deployment: a project holding a published App is
  refused for archive, and a live URL nobody can find is the same stranding in a new costume.
- **The rail's contents follow the mode.** Threads in Chat, Built Apps in Build. No new chrome.
- **Nothing caps app count.** Unpublished apps cost a directory and a symlinked `node_modules`.
  The real cost is a published app, which is a running container on a hardware tier, so the rail
  shows which apps are live. If pressure is ever needed, it belongs on live apps, never on app
  count.
- **One preview at a time.** Switching app in the rail stops the supervisor and restarts it in the
  new directory. A build already running keeps running, and the rail marks that app busy.
- **The Default rename in `handoff.md` §4 is deleted.** It was specified, never built.
- Language: [CONTEXT.md](../../CONTEXT.md).
