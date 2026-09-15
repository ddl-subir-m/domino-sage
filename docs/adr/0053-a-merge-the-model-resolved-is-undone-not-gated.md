---
status: accepted
---

# A merge the model resolved is undone, not gated

`git pull` conflicts, and Sage hands the conflicted files to the model, which rewrites them.
`_resolve_conflicts` (`backend/sage/orchestrator/service.py:14956`) then checks three things: the
model call did not fail, no conflict markers remain, and that is all. A resolution that keeps both
sides passes. So does one that deletes a side entirely. `finalize_merge` commits it and `sync()`
pushes it, with the message "merged teammate changes (conflicts resolved)". Nobody read the result
(#233).

The obvious fix is a gate: refuse to finalize until a person has seen what changed. On 2026-09-15
the user rejected it, and the reason is a fact about the product rather than a preference.

**There is nowhere for that person to go.** `backend/sage/workbench/js/modes/code.js` renders
"Code is on a parallel branch"; `api.js` has no file write of any kind. The Workbench cannot edit a
file. And "Pull latest" is not a control somebody chooses: `syncProject` has exactly one caller,
`pullAndBuild` (`store.js:6410`), reached from one button labelled "Pull and build this"
(`message-blocks.js:1417`). The person asked for a **build**. The pull is Sage's own prerequisite.
So a gate would interrupt a request they did make, with a task they never asked for and have no
tool to finish.

A gate offers a person the one thing they cannot do. An undo offers them the one thing they can.

## Decision

**One. The merge lands.** Nothing is blocked, on either path. The promise this ADR makes is that
**nothing is lost** — the state before the merge is always recoverable, and the person is told the
merge happened. It is deliberately NOT the stronger promise that nothing changes unread; that
promise needs a review surface, and there is not one to build on yet.

**Two. A merge the model resolved says so, and says who decided.** It names the files it rewrote,
capped with "and N more" as the incoming-changes card already does. A merge git completed by itself
says **nothing** — `pull()` returns `merged` with an empty `conflicts` list when the model was never
called, and announcing that trains people to ignore the sentence that matters.

**Three. The way back is a revert, never a reset.** `sync()` pushes immediately after merging
(`service.py:15037`), so by the time anybody reads the sentence the merge may already be on the
remote. A reset would rewrite history the remote and a Collaborator have already seen. The undo
reverts and then pushes; a push git rejects is reported with the sentence #347 already ships for it.

**Four. The offer is derived, never recorded.** `pull()` merges with `git merge --no-edit` and
`finalize_merge` commits it, so the result is a real merge commit and `HEAD^1` **is** the pre-merge
state — permanently, in every clone, with nothing written down. A second copy in `.sage/` could only
disagree with git, and git's copy is the one that survives a clone.

Note what this rules out. `HEAD` is not the test: `pullAndBuild` builds immediately after merging
and a build ends in `commit_all` (`service.py:14923`), so `HEAD` stops being the merge commit inside
the same turn. The condition is the newest un-reverted Sage merge reachable from `HEAD`, over a
bounded window, and it lives beside `behind` (`service.py:5691`) — a derived boolean on the app rail
row, kept current by a background check, already drawn three times in `builder.js`.

**Five. A revert that will not apply refuses, and is never handed to the model.** Once a build has
landed on top, `git revert -m 1` can conflict. Resolving that with the model is #233 rebuilt inside
its own fix. It says it could not be applied, names the merge, and stops.

**And it names the change, not the person.** `CONTEXT.md` defines **Collaborator** and lists
`teammate` under `_Avoid_`, but the code says "teammate" to the person's face (`service.py:15001`)
and in the model's own prompt. Sage cannot know that whoever pushed is a Collaborator — that term
means somebody Domino records on the Project, and push access to the repo is a different grant.
"Incoming changes" is true in every case.

## What this is not

**Not a way for a person to resolve a conflict themselves.** That is the real gap and it is a
separate issue, blocked on the Code tab. This ADR does not pretend to close it; it makes the
unreviewed merge visible and reversible in the meantime.

**Not a gate, and not by oversight.** A gate was the first design and was rejected for a stated
reason. Do not reintroduce one because this ADR's promise looks weak — it IS weaker than
nothing-changes-unread, on purpose, because the stronger promise cannot be kept without a surface
that does not exist.

**Not a persisted record.** No sha in `.sage/`, no new file, no migration.

**Not an announcement on every merge.** Rule two is a narrowing, not a reporting feature.

## What retires this

When the Code tab lands and a person can resolve a conflict inside the Workbench, rule one is worth
re-opening: a gate stops being a dead end the moment there is somewhere to go. Nothing else here
changes — rules three, four and five are about git and hold either way.

## Consequences

More merges land with nobody having read them, which is the state today. What changes is that each
one now says so and can be taken back, and that is the whole of the improvement. It is not a claim
that the model resolves conflicts well.

The unattended path is the one that most needs this and the one with no witness. `_save_to_git`
merges after every clean build, after a Chat idle save, before publish, and on the way down from a
SIGTERM (`service.py:20390`, "save before stop"). A person's next sight of that merge is after a
restart, which is why the offer is derived from git rather than held in a transcript card that a
reload would retire.

A revert that conflicts leaves the person where a gate would have: needing a tool they do not have.
Rule five makes that honest rather than absent. It is the residue this ADR accepts, and the sibling
issue is where it goes away.

Refs: #233, and #347 for the rejected-push sentence this reuses.
