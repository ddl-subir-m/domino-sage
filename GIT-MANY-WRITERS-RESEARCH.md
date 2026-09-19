# What many writers cost on Apps that already live in git

Research for issue #226, a child of map #223. Read-only. Nothing in this file designs the co-build
mechanism — #223 puts that out of scope. It establishes the **constraint** the store decision must
not foreclose.

Findings are marked **CONFIRMED** (I read the deciding line), **LIKELY** (inference, gap named), or
**NEEDS A RUN**.

---

## Verdict first: the three shapes

**Recommendation: a shared branch, with a per-writer path partition inside it.** Not because a
shared branch is comfortable, but because it is the only one of the three that keeps
`_save_to_git`'s single code path, and because the thing that actually hurts is not the branch —
it is that two writers' files can be the *same* files. Fix the overlap and the branch stops
mattering.

| Shape | What it costs today | What it forecloses |
|---|---|---|
| **Shared branch** (what exists) | Last successful push wins. A lost race is drawn to the user as **`Saved — <git stderr>`, `ok: true`** (store.js:1917-1920). A merge conflict is handed to an **LLM** that edits a stranger's files unattended (service.py:11720-11765). The turn lock does not span writers. | Nothing. It is the status quo, and every other shape is reachable from it. |
| **Per-user folder** | Nearly free to *add* — `apps/` is a directory scan (manager.py:1436-1438) and ADR-0008 already chose directory-per-app over branch-per-app. But it buys **no commit isolation**: `commit_all` is `git add -A` at the **project root** (git.py:100), so a folder prefix changes what collides, not who commits. | Nothing, and it is the cheap half of the recommendation. Not sufficient alone: shared root files (`.sage/settings.json`, the root `.gitignore`, the Project record) still have many writers. |
| **Per-user branch** | Expensive and load-bearing. Sage's git layer contains **no branch code at all** — no checkout, no create, `current_branch()` only reads (git.py:140-141) and `pull` merges `origin/<same name>` (git.py:153). Every sync path would need writing. | **The store itself.** ADR-0008's own comparison table says of branch-per-app: "`.sage/` **diverges per branch**". Under a per-user branch every user's Conversations diverge and never converge — which is the exact question #223 is deciding. This shape answers the store question by accident, and answers it "never shared". |

**The single hardest constraint:** the commit unit is the **whole Project working tree**, not the
app and not the Conversation. Any store that leaves two writers addressing the same *file* inherits
an LLM-resolved merge on every collision. ADR-0008 already wrote the rule — "An index file is one
file with many writers, which is the problem below" — and the turn lock does **not** enforce it
across writers. It enforces it inside one container.

---

## 1. The turn lock: what it refuses, and where

### It is a `threading.Lock` in one process

**CONFIRMED.** `backend/sage/orchestrator/service.py:3640`:

```python
self._turn_lock = threading.Lock()
```

One `Orchestrator` is constructed per Builder process (`backend/sage/orchestrator/app.py:431`). The
lock therefore covers **one workspace container**. It does not span workspaces, users, or hosts.
Every claim below about what it refuses is a claim about one person's Builder.

The reason it exists, at `service.py:3630-3633`:

> Serializes build/approve turns: only one turn may stream at a time. A turn arms shared,
> per-project state (read_only_turn, mode) and mutates one working tree; a second overlapping
> turn would clear the first turn's read-only gate mid-flight […] and interleave edits on one tree.

### Two different refusals

**CONFIRMED.** Streaming turns **queue**; everything else is **refused on the spot**
(`service.py:3634-3639`):

> Turns still run ONE AT A TIME under the queue below (#79): what changed is what happens to
> the second one, not how many run. A streaming turn waits its turn; everything else is
> refused on the spot […] Stop stays lock-free.

- Queue path: `_acquire_turn` (`service.py:9519-9572`), used by `build_stream` / `chat_stream` /
  `approve_stream` via `_TurnQueue`. A queued turn yields a `pending` row and can be cancelled. If
  the context moved while it waited, it is dropped with `contextChanged` (`service.py:9552-9563`).
- Refuse path: `TurnBusy` (`service.py:378-388`), whose sentence is
  `service.py:375` — `"A build is already running. Wait for it to finish or stop it first, then {action}."`

### Every enforcement point

**CONFIRMED** — each is a non-blocking `self._turn_lock.acquire(blocking=False)`:

| `service.py` line | Entry point |
|---|---|
| 4127 | `archive_plan_doc` (raises `PlanArchiveRefused("busy")`) |
| 4538 | `create_app` |
| 4703 | `delete_app` |
| 4990 | `revoice` |
| 5145 | `build` (non-streaming) |
| 6303 | `_flush_chat_save` |
| 6425 | `draft_handoff_plan` |
| 6440 | `confirm_handoff` |
| 6468 | `recross_handoff` |
| 8082 | `_maybe_compact_chat` |
| 8145 / 8160 | `_acquire_for_reset` → `reset_app` (raises `ResetBusy`; this one *waits*, `timeout=wait`) |
| 9545 | `_acquire_turn` — the queue, for the three streaming turns |
| 11788 | `sync` ("Pull latest") |
| 11857 | `publish` |
| 12431 | `set_catalog` |

### The lock is Project-wide, deliberately

**CONFIRMED.** `service.py:11777-11784`, in `sync`:

> The turn lock, for the reasons publish takes it: `commit_all` commits the PROJECT ROOT —
> one repo holding every Built App (ADR-0008) — so under a streaming build it commits half a
> turn's writes, and `_integrate_remote` then runs an AGENT over that tree to resolve
> conflicts. **Two agents in one working tree is the collision #39 exists to prevent.**
>
> Project-wide, so `buildRunning` in another app is still a reason to refuse: a build
> streaming into app A is stopped by nothing when app B is selected, and this commit takes
> A's half-written tree with it.

So *within* a Builder the lock already treats the Project as the unit — which is the right unit and
the wrong scope.

### The wedge

**CONFIRMED.** `service.py:3644-3648`: when a turn is given up on and its OpenCode session will not
confirm it stopped (#39), `_turn_wedged` is set and **the lock is deliberately never released**.
Nothing clears it; restarting the workspace is the remedy, and `turn_busy_message(wedged=True)`
says so (`service.py:373-374`). A many-writer mechanism that waits on another writer's lock would
be waiting on a release that may never come.

---

## 2. Two workspaces pushing the same app folder

### Two writers means two Builders, one repo

**CONFIRMED.** `backend/sage/provision/service.py:92-95`:

> **A Project can hold several people's Sage Builders.** Reusing or resuming a collaborator's would
> put two people in one container and hand this viewer someone else's session, so attaching is
> always scoped to the viewer (#47).

Builders are filtered by `is_builder_workspace` (name `"sage"`, `provision/service.py:78-86`) **and**
by `is_owned_by` (`provision/service.py:337,385`). So the multi-writer shape is: N users × one
Builder each × one `/mnt/code` checkout each × **one Project git remote**. N locks, no shared lock.

### The repo is the Project, not the app

**CONFIRMED.** `service.py:11675-11676`, in `_save_to_git`'s docstring:

> The Project root, not the app: one repo holds every Built App and the Project's own record,
> so this is the only directory git has ever been runnable in.

Layout (ADR-0008, verified in code): Built Apps at `apps/<appId>/` (`manager.py:114`,
`manager.py:1433-1434`); the app's own transcript at `apps/<appId>/.sage/history.jsonl`
(`manager.py:1062`); Conversations at the **volume root** in `.sage/threads/<threadId>/`
(`threads.py:120,126,128`).

### The commit stages everything

**CONFIRMED.** `backend/sage/workspace/git.py:95-106` — `commit_all` is:

```python
_git(path, "add", "-A")
```

with `exclude` unstaging a named list afterwards (`_kept_out_of_the_commit`, `service.py:15253-15265`
— leaked data copies and oversized Artifacts only). There is no app-scoped commit anywhere in the
codebase. `TurnSnapshot` **is** app-scoped (`service.py:3109`, `TurnSnapshot(self.app_for_turn().path)`)
— but that is the revert path, not the commit path. The two disagree, and the commit path is the one
that reaches the remote.

### The save sequence, and who wins

**CONFIRMED.** `service.py:11685-11699`:

```python
committed = git.commit_all(path, message, exclude=leaked)
# Integrate any teammate changes before pushing, or the push is rejected as non-ff and
# the build's work silently never reaches the repo.
synced = self._integrate_remote(project)
...
result = git.push(path)
```

commit → pull+merge → push. `_integrate_remote` (`service.py:11704-11718`) calls `git.pull`
(`git.py:144-167`: `fetch origin`, then `merge --no-edit origin/<current branch>`).

**Who wins: the last successful push.** There is no arbitration beyond git's own. The two failure
shapes:

1. **Clean merge.** Common case. Two writers in different `apps/<appId>/` folders merge without
   conflict, and both commits survive. This already works.
2. **Conflict.** `git.pull` returns `status="conflict"` with the conflicted files, and
   `_resolve_conflicts` (`service.py:11720-11765`) **hands them to an LLM**. The session is created
   at the **Project root**, not the app (`service.py:11743`), and the comment at 11729-11738 says
   why:

   > Git names conflicts from the repo root, which is the volume: a conflict in a Built App
   > arrives as `apps/<appId>/src/App.tsx`. […] A merge is the Project's: it can land in a second
   > Built App, or in the Project's own files at the root, and neither is nameable from inside
   > `apps/<appId>/`.

   The prompt (11745-11752) tells the model to "reconcile the code […] so both sides' intent is
   kept where possible". If markers remain afterwards, `git.abort_merge` rolls the pull back and
   the save returns `conflict-unresolved` (11759-11763).

   **This is the sharpest cost of many writers today: an unattended LLM merges a colleague's code,
   including Built Apps this Builder never selected and the Project's own record.**

### What the user sees when they lose the race

**CONFIRMED, and it is wrong.** `git.push` returns `SaveResult(pushed=False, …)` carrying git's own
stderr for a non-fast-forward rejection (`git.py:120-128`, docstring: "Returns pushed=False (not an
error) [when] there's no remote or push rejected (e.g. non-fast-forward — caller should pull
first)"). `_save_to_git` then returns `service.py:11699`:

```python
return {"type": "saved", "ok": True, "pushed": result.pushed, "detail": detail}
```

`ok` is **True**. And the Workbench renders it at `backend/sage/workbench/js/store.js:1917-1920`:

```js
const value = ev.ok
  ? (ev.pushed ? 'Saved and pushed' : `Saved${ev.detail ? ` — ${ev.detail}` : ''}`)
  : `Couldn't save — ${ev.detail || 'git error'}`;
ensureAssistant().blocks.push({ type: 'status', ok: !!ev.ok, value });
```

A lost push race draws as a **green** `Saved — ! [rejected] main -> main (fetch first)` line. The
work is committed locally and self-heals on the next save (which pulls first), but at that instant
the user is told the save succeeded. **LIKELY** that this is invisible in practice most of the time
because the very next turn's pull fixes it; the gap is that nothing tells the user, and the last
save before a stop (`shutdown` → `_save_to_git(project, "save before stop")`, `service.py:15982`)
has no next turn.

### The incoming gate does not see teammate Conversations

**CONFIRMED, and this is the finding most directly load-bearing for #223.**
`_incoming_files` (`service.py:4404-4413`):

> Of an incoming reading, the files that land inside one Built App, named as that app names them.
>
> **The repo is the Project, so a teammate's commit can touch another Built App, a Thread, or
> the Project's own record. None of those is this app's code**, and this app's code is the only
> thing a turn here would be building on top of.

The filter is literally `f.startswith(prefix)` where `prefix` is `apps/<appId>/`
(`service.py:4412-4413`). So a teammate's writes under `.sage/threads/` — the Conversation store —
never raise the incoming-changes offer (`service.py:5663-5667`). They are still **merged** by
`_integrate_remote` on the next save, and still eligible for `_resolve_conflicts`. The gate warns
about code; the store is silent.

The gate is also dismissible per remote head (`_incoming_dismissed`, `service.py:5661,5665`), so a
writer who chose to build past one teammate commit is not asked again until the remote moves on.

### Neither Builder knows what the other has selected

**CONFIRMED.** `manager.py:1415-1419`:

> Process state rather than a file. One Sage Builder shows one app at a time and the
> selection is that view, not a fact about the Project — **writing it down would put a second
> writer on a shared file** to record something only this browser tab cares about.

Two Builders can be pointed at the same `apps/<appId>/` and neither can tell.

### Commit identity

**CONFIRMED, worth flagging.** `git.py:77-92` — `_identity_args` uses the repo's configured
identity when present, and otherwise falls back to `user.email=agent@localhost` / `user.name=agent`.
**LIKELY** that Domino sets the identity in a real workspace (the docstring says "the platform sets
it"), but I did not verify it live — **NEEDS A RUN** if the store decision wants per-writer
attribution from git history.

---

## 3. Domino's own workspace git endpoints

### Sage does not use them

**CONFIRMED negative.** `grep -rn "pullRepos\|commitAndPush\|MergeResolution\|listBranches\|checkoutBranch" backend docs`
returns **nothing**. Every git operation in Sage is `subprocess` against the local checkout
(`git.py:44-48`, `_git`).

### What they offer

**LIKELY** — read from Domino's private v4 spec
(`automl-service/app/api/domino_private_spec.json` in `dominodatalab/AutoML_Extension`, the same
spec `backend/sage/provision/domino.py` already cites) on 2026-07-25, and **not re-read for this
ticket**. All POST, `{workspaceId}` path param:

| Endpoint | Offers |
|---|---|
| `/workspace/{workspaceId}/pullReposEnhanced` | pull latest |
| `/workspace/{workspaceId}/commitAndPushReposEnhanced` | commit + push |
| `/workspace/{workspaceId}/continueMergeResolution`, `/cancelMergeResolution`, `/forcePushResolution`, `/hardResetResolution` | conflict resolution flow |
| `/workspace/{workspaceId}/stageFileBasedOnCurrentLocalState` / `…LocalCommit` / `…RemoteCommit` | per-file "take mine / take theirs" staging during a merge |
| `/workspace/{workspaceId}/listBranches`, `/checkoutBranch`, `/commitRepos` | branch listing and checkout |

**The decision not to use them was already taken (2026-07-24)** on the ground that the Builder is
the only place with both a working tree and an LLM to resolve with; the control plane has no working
tree to reason about.

**What matters for #226:** every one of these is **`{workspaceId}`-scoped**. They drive *one*
workspace's checkout. **None of them arbitrates between two workspaces.** The conflict-resolution
suite is a human-facing UI over the same last-writer-wins primitive, plus `forcePushResolution` and
`hardResetResolution` — which are "one writer discards the other" with a button. Domino offers no
locking, no reservation, and no merge queue.

`listBranches` + `checkoutBranch` do mean a per-user branch is **drivable from the control plane** —
that is the one capability here that Sage's own layer lacks. **NEEDS A RUN** to confirm the spec is
current.

---

## 4. The shape that fits

### What `_save_to_git` and the folder layout actually constrain

1. **The commit unit is the Project tree.** `git add -A` at `project.record.path` (git.py:100,
   service.py:11679-11686). Nothing narrows it. A per-user folder does not narrow it either — it
   only changes *which* files two writers are likely to have both touched. **CONFIRMED.**
2. **The sync path assumes one branch that tracks its own name.** `git.py:153`:
   `ref = f"origin/{current_branch(path)}"`. There is no checkout, no branch creation, no
   cross-branch merge anywhere in `backend/sage/workspace/`. **CONFIRMED.**
3. **The publish path pins a path, not a ref.** ADR-0008's comparison table records
   directory-per-app as `entryPoint: apps/<appId>/app.sh`, all republishing from `head`; the
   branch-per-app alternative was the one that would have pinned `gitRefValue=<branch>`. That
   alternative was **rejected**, and one of its recorded costs was that "`.sage/` **diverges per
   branch**". **CONFIRMED** (docs/adr/0008-a-project-holds-many-built-apps.md).
4. **A `<username>/` path level is cheap.** `apps/` is a directory scan, not an index
   (`manager.py:1436-1438`), and ADR-0008 says why there is no index: "An index file is one file
   with many writers, which is the problem below." `safe_id()` already guards path segments
   (`threads.py:120`). **CONFIRMED.**
5. **Root files still have many writers.** `.sage/settings.json`, the root `.gitignore` that
   `_PROJECT_IGNORE` writes (`manager.py:120,1625`), and the Project record sit above any per-user
   folder. A path partition below them does not protect them. **CONFIRMED.**

### Reading

**Per-user branch** is the only shape that gives real write isolation, and it is also the only one
that **forecloses the store decision** — it makes every writer's `.sage/` a private fork that never
converges, which is a different answer to #223 than #223 intends to give. It also costs the most
code, in the one path (`_save_to_git` / `pull` / `push`) that currently has no branch concept at all.

**Per-user folder** costs almost nothing and buys almost nothing on its own: it is organisation, not
access control (#223 already records this), and here it is not even *commit* isolation. What it does
buy is real and is the whole point: it makes two writers' file sets **disjoint**, so git's merge is
clean and `_resolve_conflicts` — the unattended LLM — never runs.

**Shared branch** is the status quo and forecloses nothing.

So: **shared branch, per-writer path partition, and the root-level shared files treated as the real
problem.** The store decision's obligation is not to pick a branch shape. It is to make sure that
whatever holds a Conversation is **addressed per writer at file granularity**, the way
`.sage/threads/<threadId>/` already is and the way an `apps.json` deliberately is not. Get that
wrong and the co-build mechanism inherits an LLM merging strangers' files, on a lock that only ever
covered one container.

---

## Open items

- **NEEDS A RUN:** two Builders in one Project, both saving within the same second — confirm the
  push rejection renders as `Saved — <stderr>` with `ok: true` on screen, and confirm the next
  turn's pull heals it.
- **NEEDS A RUN:** re-read Domino's private v4 spec to confirm the workspace git endpoint list above
  is current (last read 2026-07-25).
- **NEEDS A RUN:** confirm Domino sets `user.email` / `user.name` in a Builder checkout, so commits
  carry the writer's identity rather than `agent@localhost` (git.py:77-92).
- **LIKELY, not chased:** whether `_resolve_conflicts` has ever fired in a real two-writer Project.
  Nothing logs it distinctly; the `saved` event carries only the merged/`conflict-unresolved` status.
