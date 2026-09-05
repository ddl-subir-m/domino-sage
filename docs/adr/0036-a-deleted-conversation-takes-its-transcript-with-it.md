---
status: accepted
revises: ADR-0006 (the log and the Artifacts still live in git; a delete is the one act that
         takes them back out of the working tree)
---

# A deleted Conversation takes its transcript with it

`ThreadStore.delete` wrote `"deleted": true` into `.sage/threads/<id>/meta.json` and stopped.
Every sibling file stayed exactly where it was — `history.jsonl`, the whole transcript, plus
`context.json`, `artifacts.json` and the Build session ids — and so did the Artifacts under
`examples/<threadId>/`. All of it committed, all of it pushed. The rail stopped drawing the row
and nothing else changed.

We decided **a delete removes the talk**. The record shrinks to a tombstone, every other file in
the Thread directory goes, and the act commits and pushes on its own rather than waiting for the
next turn to carry it.

## Why this is worth writing down

[ADR-0006](0006-conversation-logs-and-artifacts-stay-in-git.md) is titled *Conversation logs and
Artifacts stay in the project git repo*. A reader who finds `delete()` unlinking `history.jsonl`
and `examples/<threadId>/` will read that as a contradiction and reasonably try to undo it.

It is not one. Logs and Artifacts still live in git, for the reasons ADR-0006 measured, and they
still sit inside the `TurnSnapshot` work-tree, which is the mechanism argument that ruled out a
Dataset. This is the one act that takes them back out, on a person's say-so, and it is the only
one.

The second reason is that the old behaviour was defensible and looked deliberate. The tombstone
was doing two jobs at once: keeping the scan from resurrecting a row, which is what its docstring
says, and — silently — keeping the transcript. The first job is real and stays. The second was
never decided, and the word on the button did not admit to it.

## What a delete does

| | |
|---|---|
| `.sage/threads/<id>/meta.json` | Reduced to `{id, deleted, updatedAt}`. **The title goes too** — it is the first 60 characters of the person's own first message, which is transcript. |
| Every other file in `.sage/threads/<id>/` | Removed. |
| `examples/<threadId>/` | Removed **only** when no live Built App's handoff names them. |
| `.sage/threads/<id>/handoff.json` | Survives exactly as long as the Artifacts do, and goes with them. It is the record of why they are still there, it holds ids and a status rather than talk, and it is the only evidence a later sweep can re-read. |
| `apps/<appId>/.sage/history.jsonl` | Untouched. |
| `.sage/plans/<planId>/` | Untouched. It already outlives the Conversation by design (`workspace/threads.py`, `is_deleted`). |
| git | Committed and pushed as one act, not deferred to the next turn — but **under the turn lock**, through `_flush_chat_save`. The save walks the tree and commits it, and Delete is a button anyone can press while a build turn is writing files. Losing that race defers the commit to the idle timer rather than committing a half-written tree. |

The tombstone keeps its job. `get` still collapses "deleted" and "never existed" into `None`,
and `is_deleted` still tells them apart, because a plan document written in a Conversation
outlives it and its page has to say which of the two happened (#167).

## Considered options

**Leave it as a pure tombstone and rename the button.** Rejected, but it was the honest
alternative and it is why the label was on the table at all. `CONTEXT.md` lists *delete* under
`_Avoid_` for the [[Remove]] family, so there was a case that this act was a mislabelled Remove.
It is not: Remove is scoped and the scope is the label ("Remove from <app>"), and a Conversation
is not held by a scope you could name — it is a top-level thing in a Project, like a Built App,
which already says Delete. So the word stays and the behaviour moves to meet it.

**Purge the Conversation's build turns from each app it drove.** Rejected on mechanism. Those
rows live in `apps/<appId>/.sage/history.jsonl` (ADR-0008), and the stop button's baseline is a
*position* in that file (`workspace/manager.py`). Rewriting an append-only log that another
viewer's stop baseline indexes into trades a privacy improvement for a correctness bug, and it
breaks the promise the dialog already makes: the apps it changed stay exactly as they are.

**Push the tombstone first, remove the files only after the push lands.** Rejected. It reads
safer and behaves worse: a briefly unreachable remote makes the delete fail outright, and this is
the act people reach for when they are anxious about what is in the transcript. The removal
happens locally, then the save runs, and a failed save is reported rather than prevented.

**Leave Conversations deleted before this change alone.** Rejected — those are the ones that
matter. Every one of them still has its full transcript on disk and on the remote. A bounded
sweep calls the same purge function `delete()` calls, and runs as the Project is opened, beside
the membership backfill that was already there under the rule *a migration belongs to opening the
thing it repairs*. It does not push: two Builders starting together would both sweep and both
commit, which is a merge at boot over work either one can redo, and unlike the delete itself
there is nobody watching to be told it failed. The removal is local and the next save carries it.

**Sweep from a startup thread instead.** Rejected on mechanism, having first been written that
way. `project()` is a get-or-attach with no lock over it, so a boot thread calling it races the
first request: both build a whole `Project`, and the loser's `ViteSupervisor` is left running
with nothing holding it and nothing to stop it. The attach is already the once-per-Project seam.

**Purge lazily, from the rail scan.** Rejected. Two viewers in one Project are two Sage Builders
and two Builders are two processes with no lock between them (ADR-0008). The scan is a read path
both of them run; a write inside it is the shape of bug that ADR-0008 removed once already.

## Consequences

- **Git history keeps every earlier copy.** Nothing short of a history rewrite changes that, and
  a rewrite is not on the table for a repo two Builders and a published app all clone. The
  confirm dialog says so in its own sentence rather than leaving the person to assume otherwise.
- **A failed push leaves the two sides disagreeing.** The files are gone locally and the remote
  still has them, so a second Builder still lists the Conversation. `_save_to_git` never raises;
  it returns `ok: false` with a reason, and the UI reports it: *deleted here, the project could
  not be saved, this may come back when the workspace restarts.*
- **Whether an unpushed commit survives a restart is still unsettled.** ADR-0006 recorded the
  contradiction — `workspace/git.py` says only committed files survive, the template `.gitignore`
  says ignored files persist on the volume — and this decision does not settle it either. It is
  why the failure message says *may*, and why the push is part of the act rather than deferred.
- **A delete is not recoverable from the working tree.** That is the point, and it is what the
  dialog's *for good* is promising. The tombstone is not a grace period.
- **The Artifacts question stays open rather than being answered once.** Keeping `handoff.json`
  costs one file and buys a verdict that can still change: delete the Built App that was holding
  those Artifacts, and the next sweep reads the same file, finds the app gone, and finishes the
  purge. A verdict frozen onto the tombstone at delete time would have stranded them for good.
- Language: [CONTEXT.md](../../CONTEXT.md).
