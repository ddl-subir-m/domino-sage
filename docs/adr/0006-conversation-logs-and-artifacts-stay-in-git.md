---
status: accepted
---

# Conversation logs and Artifacts stay in the project git repo

Sage keeps two kinds of durable state in the project repo: the conversation log
(`.sage/history.jsonl`, `.sage/threads/<id>/history.jsonl`, and the rendered
`.sage/history.md`) and Artifacts (`examples/<threadId>/*.png`, `*.table.json`, `*.pdf`).
Both stay in git. Neither moves to a Domino Dataset, to Domino Artifacts, or onto the
workspace filesystem behind a `.gitignore`.

Research: [DATASETS-VS-ARTIFACTS-RESEARCH.md](../../DATASETS-VS-ARTIFACTS-RESEARCH.md).

## Why this is worth writing down

Domino Datasets are right there, and Sage already has the mount plumbing
(`assets/provider.py`). A reader who finds a multi-megabyte JSONL committed next to a pile of
PNGs will reasonably ask why it is not on a Dataset. Three findings answered that, and none of
them is visible from the code.

## The measurement that decided it

The stated reason to move anything was keeping binaries and per-turn churn out of git history.
Measured against real Sage workspaces, at ~68KB of log per user turn:

| | Packed at 100 turns | Loose before `gc` | Push payload per turn |
|---|---|---|---|
| `.sage/history.jsonl` | 2.49 MB | 86 MB | **500 bytes** |
| 100 Artifacts in `examples/` | 6.28 MB | 6.3 MB | **64 KB** |

Git deltas an append-only log against its previous version, so the push cost of the growing
file grows **logarithmically**: 287 bytes at turn 10, 500 bytes at turn 100 against an 11MB
file. **One chart PNG costs 130x more to push than a whole turn of log growth.** The churn the
migration was meant to remove does not exist, and the binaries — the larger cost — are the half
that cannot move (see below).

The one real number, 86MB of loose objects, is a `gc` problem on the Builder's own disk, not a
storage-location problem. Git's auto-gc never fires because its threshold is 6700 loose objects
and a turn makes about three. `environment/app.sh` now sets `gc.auto=50` on the workspace
checkout at Builder start; that collapses 86MB to 2.5MB and needs no new storage.

## Considered options

**Artifacts onto a Dataset** — rejected on mechanism. Every Dataset mount path Domino documents
is a *sibling* of the git working directory (`/mnt/data/<name>` next to `/mnt/code`), and the
mount API's `AddMountInput` has no path field, so the location cannot be chosen. `TurnSnapshot`
reverts a stopped turn with `git --work-tree=<workspace root> reset --hard` plus `clean -fd`.
Anything on a Dataset is outside that work-tree, so a stopped turn would leave its charts
behind. That is a correctness regression, not a trade-off. Symlinking back into the work-tree is
a half-fix that deletes the link and leaks the bytes.

**Conversation log onto a Dataset** — rejected as unnecessary, having first been shaped as
possible. `.sage/` is already outside stop-revert by design, so the work-tree problem does not
apply. But Domino publishes *no* statement about concurrent writes, ordering, or locking on a
Dataset, and says the opposite of a guarantee: *"Domino does not serialize or isolate access to
shared resources."* An appended file would have had to become one immutable file per turn first.
That work buys nothing once the churn is measured at 500 bytes a turn.

**Domino Artifacts** — rejected on mechanism, for either half. `/mnt/artifacts` is hydrated at
container start and pushed back only on a manual Sync, and *"Domino endpoints and Apps cannot
persist local file system changes back to the Blob Store."* It cannot hold live state.

**Gitignore both, leave them on the workspace filesystem** — rejected. It removes the backup and
does not do what it appears to do. `clean -fd` has no `-x`, so ignored files are *left alone*: a
gitignored `examples/` would make a stopped turn's charts **survive** as unreferenced orphans
rather than be reverted — the inverse of the intended effect. For the log it is worse: a
gitignored file has no copy anywhere, and Sage's own sources disagree about whether the volume
survives a restart at all (see Consequences).

**Rotate the log into an archive** — rejected. Rotation exists to cut O(N^2) churn. The churn is
500 bytes per turn, and an archive still grows; it moves bytes rather than removing them.

## Consequences

- Both paths ride into the published app's container, as they do today. That is deliberate for
  the log's column names and is why `.sage/samples.json` and `.sage/model-api-credentials.json`
  are ignored separately.
- Handoff keeps working because it names Artifacts **by path** (`orchestrator/handoff.py`), and
  those paths stay inside the work-tree. A move to a Dataset would have broken this.
- `.sage/snapshots/` is now gitignored. It is a git repo inside the project repo, so `git add -A`
  recorded it as a gitlink pointing at a SHA no remote has, rewritten every turn; a fresh clone
  got an empty directory regardless. Ignoring it also closes the window where the store exists
  with no commit and `git add -A` fails with `fatal: adding files failed`.
- **Left open, deliberately.** `read_history()` reads and parses the *whole* log on every page
  reload and orchestrator restart — about 11MB and 5,400 `json.loads` at 100 turns. This is the
  real compounding cost, it is a runtime cost rather than a storage one, and **no storage option
  here would have fixed it.** Tracked separately.
- **Unresolved contradiction, recorded not settled.** `workspace/git.py` says *"git-based Domino
  compute is ephemeral — only committed files survive a restart"*, while the template
  `.gitignore` says ignored files *"persist on this volume and do not travel with git."* Nothing
  in this decision depends on which is right, because everything that matters is committed. It
  still needs settling before anything else is trusted to that volume.
- Language: [CONTEXT.md](../../CONTEXT.md).
