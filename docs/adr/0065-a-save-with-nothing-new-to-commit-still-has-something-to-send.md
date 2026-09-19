---
status: accepted
---

# A save with nothing new to commit still has something to send

#456 made a refused push visible rather than silent. #459 was filed as the follow-up: a refused push
answers `ok: True`, so `_flush_chat_save` clears `_chat_dirty` and arms no retry, and the work is
committed locally and never sent.

Two of that ticket's claims did not survive measurement, and correcting them is what found the real
defect.

## What the ticket got wrong

**"An unconditional retry against a diverged remote is the same failure every 30 seconds."** False.
`_save_to_git` **pulls before it pushes** — `_integrate_remote` runs `git.pull`, and a conflict goes
to `_resolve_conflicts`, which hands the files to the model. A retry is a genuinely new attempt, not
a repeat. And a sync that cannot be resolved returns `ok: False`, which already arms a retry today.
The diverged case was never in the `rejected` population at all.

**"Not on `shutdown`, because nothing is dirty any more."** False. `shutdown` (`service.py:22931`)
calls `_save_to_git` unconditionally and never reads `_chat_dirty`.

Both errors made the defect sound worse than it is. The conclusion survives anyway, through a cause
the ticket never named.

## The real defect

`service.py:17201`, inside `_save_to_git`:

```python
if not committed and (synced is None or synced.status == "up-to-date"):
    return {"type": "saved", "ok": True, "pushed": False, "detail": "no changes to commit"}
result = git.push(path)          # never reached
```

A workspace that is **ahead but clean** — commits made, push refused, nothing typed since — returns
before `git.push`. It can never catch up: not on the timer, not on the next open, and **not at
shutdown**, because shutdown's save hits the same early return.

What actually happens after a refused push:

| Next event | Does the work reach the remote? |
|---|---|
| the person types again | yes — a new commit makes `committed` true, and the push carries the earlier commits with it |
| graceful shutdown, tree clean | **no** — `:17201` returns first |
| the container is killed | no |
| nothing | no |

So the exposure is narrower than the ticket said and permanent where it bites: a refused push as the
last act of a session.

## Decision

**Narrow the early return rather than delete it.** It skips the push only when nothing was committed
**and** the branch is not ahead. This needs a reader that answers "ahead", which is added here:
`git.unsent(path)`, the mirror of `incoming()` — `rev-list --count` over refs a push or fetch
already left behind, no network call, because a refused push does not advance `origin/<branch>`
while a successful one does. It must also answer true for a branch with no `origin/<branch>` ref at
all, which `incoming()` treats as empty and which is the worst case rather than a quiet one.

**`unsent()` lives here, and ADR-0064's Problem consumes it.** Otherwise both changes build the same
reader and the dependency points in both directions.

**`_chat_dirty` is not held True on a refusal.** It means "Chat has files the local repo has not
committed", and after a successful commit with a refused push the files *are* committed. Holding it
True would overload the one flag whose wrong question caused this. Instead the gate at
`service.py:8390` also proceeds when `unsent(path)` is true — the flag keeps its meaning and the
question is asked of git.

**No timer is re-armed on a refusal.** The work rides out on the next natural save: the next turn,
leaving, a delete, a handoff, publish, stop or shutdown. With the early return narrowed, shutdown
now genuinely pushes, which today it does not.

**This changes every caller, not just Chat.** `_save_to_git` is called from Chat's flush, a build, a
build plan, publish, stop and shutdown. All six have the same latent bug — a Built App committed and
refused is equally stranded — and all six are fixed. The cost is that an ahead-but-clean workspace
now pays one push on each of those acts, every one of which a person initiated.

## Why no retry loop

The causes that survive `_integrate_remote` are a race, a dead or expired git credential, no write
permission, and an unreachable remote. Credentials are expected to dominate, and a retry never fixes
one — so re-arming every 30 seconds would be a network call every 30 seconds for the life of the
process. That is the spin the ticket feared, arriving through a door it did not name.

Bounding the attempts instead would need a number, and no measurement justifies one. Detecting the
cause would mean classifying git's stderr by substring, which this repo already has open bugs about.
Deferring to the next natural event needs neither.

The precedent is in the same file: `_on_chat_save_idle` gives up entirely when `_turn_wedged`, logs,
and defers to the restart rather than re-arming against a condition it cannot beat.

## What this costs, honestly

If the person walks away after a refused push and the container is killed rather than stopped, the
work stays local. Nothing short of a spin changes that, and a spin does not fix a dead credential
either. [ADR-0064](0064-a-failed-save-is-past-tense-and-unsent-work-is-not.md) is what makes it
survivable: they were told, and told what to do.

## Rejected

**Deleting the early return.** Tempting — it is barely pinned, and `git.push` already no-ops without
a remote. But five deliberate acts would each pay a round trip that reports "Everything
up-to-date", and the coalescing that return exists for is real.

**Fixing only `:8495`, the dirty flag, as the ticket described.** A retry armed there calls
`_save_to_git`, which hits `:17201` and answers "no changes to commit". It would report success and
push nothing — a fix that cannot work, and that looks like it did.

**Fixing it at the Chat call site only.** It would leave the same bug behind five other doors and
make the next reader guess which were deliberate.

**Reading `pushed` instead of `ok`.** A Project with no remote and a `/tmp` workspace both answer
`pushed=False` and both are saved. Only `rejected` means the remote was asked and said no;
`_chat_save_landed` already encodes that three-way distinction.

## Pinned by

The early return is nearly untested — one test mentions its string and *constructs* the result
rather than exercising the branch, so it will keep passing whatever breaks. Three things need
holding down: that a refused push leaves work the next save actually sends; that an **ahead-but-clean
workspace pushes**, driven against a real refusing remote rather than a constructed dict; and that
no timer is armed after a refusal, asserted directly because it is a negative.

`test_a_refused_push_really_does_answer_ok_true` (`backend/tests/test_chat_turn.py:1772` — a
function, not a file) stays green. It anchors the producer rather than this file's belief about it.
