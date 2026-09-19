---
status: accepted
---

# A failed save is past tense, and unsent work is not

#456 took the git commit-and-push off the read path in `get_thread`, and required that a flush which
fails must still surface, because "a silent background failure is worse than a slow click." The
server half shipped. `_chat_save_failed` is set on every outcome that did not reach the remote
(`service.py:8492`, `:8504`), `get_thread` returns it as `saveFailed` (`service.py:7721`), and
`_chat_save_landed` (`service.py:3975`) asks the right question, so a refused push is recorded
rather than passing as success.

Nothing draws it. `grep -rn "saveFailed" backend/sage/workbench/` returns nothing. The field rides
out on every `GET /api/threads/<id>` and no component reads it.

That is this session's doing, not the worker's: the brief said "do not touch
`backend/sage/workbench/js` at all", because #451 and #455 were both live in `store.js` and a third
session there would have produced a merge nobody could review. Right for collisions, wrong as a
definition of done, and the two were in tension without anyone noticing.

## What is measured

**A person cannot find out by any means short of dev tools.** No client reads the field. There is
no per-Conversation saved indicator, no toast on save failure, and `get_thread` is the field's only
exit. This is not a missing affordance; it is a silent path.

**A save commits the whole Project.** `_save_to_git` — "The Project root, not the app: one repo
holds every Built App and the Project's own record" — and `chat.md`: "the save, because it commits
the whole tree." A refused push leaves *everything* uncommitted in that Project unsent, not one
Conversation's work.

**In the case that matters, `ok` is `True`.** `git.push` treats a refusal as a result, not an
error: `rejected=True, pushed=False`, which `_save_to_git` hands back as `ok: True`. A client
written as `if (!saveFailed.ok)` renders nothing for a non-fast-forward, a dead credential or an
unreachable remote. `pushed` is not the test either — a Project with no remote and a `/tmp`
workspace both answer `pushed=False` and both are saved. Only `rejected` says the remote was asked
and said no.

**The field is one in-memory slot with no Conversation on it.** `self._chat_save_failed`
(`service.py:5315`) is a single instance attribute; the dict carries no thread id in either shape.
Two Conversations fail, the second overwrites the first. A container restart clears it while the
work is still unsent.

## Decision

**The truth is a standing state derived from git, not the event that produced it.** The condition is
[[Unsent work]]: commits this workspace holds that the remote does not. It is read from refs a push
or fetch already left behind, the way `incoming()` reads the opposite direction — no network call,
because a refused push does not advance `origin/<branch>` while a successful one does.

**It is reported as a [[Problem]]**, through the machinery ADR-0027 already built: the drawer, the
chip, the owner grouping, `message` / `fix` / `body`, and a lifetime that ends when the condition
stops being true rather than when anybody acknowledges it.

**Owner is `you`**, one Problem, with git's own words in `body` — the precedent `slot_problems`
already set: "Owner is the creator even though an administrator owns half the remedy." Splitting on
parsed git text would be classifying by substring.

**`saveFailed` stays, and becomes the trigger rather than the surface.** A failed save kicks a
Preflight immediately, the way `store.js:2424` already does on a failed turn, so the two consecutive
sightings `survivors()` requires cost seconds rather than hours. #456's server work is not wasted;
it stops being something to render and becomes something to react to.

**The id is constant.** `Problem.id` carries two loads — the toast fires once per id per session,
and survival is counted per id. An id encoding the commit count or the branch head would mint a new
Problem on every commit, re-toasting and resetting the survival counter to zero, so the Problem
could never report at all.

## Why the derived state is load-bearing, and not an implementation detail

The glossary defines a Problem as a condition that "will make the person's **next** act fail, or
make it silently do something other than what it says it does", standing from the moment the
Workbench opens, and its `_Avoid_` list names "error (that is one that already happened)".

**As an event, "your last save failed" fails that test by name.** It is past tense, the next Chat
turn works, and nothing downstream misbehaves.

**As a standing state it passes**, because a remote that refused once refuses the next turn too: the
work keeps not arriving, and every turn from here silently does something other than what Chat says
it does. The escape is narrow and it runs through the derived state. A future reader who
"simplifies" this back to rendering the in-memory flag removes the justification for where it lives,
not just a layer of indirection.

## Rejected

**Rendering `saveFailed` in the client, as the issue first asked.** It names no Conversation, cannot
name one truthfully, dies with the process, and reports `ok: True` in the case it exists for.

**Naming which Conversation's work is at risk.** A save commits the whole tree, so naming one
understates the blast radius and sends someone to re-type one thing while the rest is equally
unsent. There is a Conversation that *triggered* the save; that is trivia, not the risk.

**A toast.** ADR-0011 forbids it for anything somebody has to read, and ADR-0027's exception is a
toast carrying only a count and a direction, never content. A transient surface also misrepresents a
condition that is permanent until something clears it.

**A Retry control in the drawer.** It would be the first action that drawer has ever held, would make
every other Problem look inert beside it, and belongs to a decision made for all Problem kinds at
once. `fix` names the retry path in a sentence instead. This withdraws the issue's original
acceptance criterion 3 rather than satisfying it.

**Surfacing an uncommitted tree.** Sage commits on a timer, so that state is ordinary and passes in
seconds. Reporting it would cry wolf and teach people to ignore the chip.

## Sequencing

#459 lands first. A refused push currently clears `_chat_dirty` and arms no retry, so
`POST /api/threads/save` finds nothing dirty and no-ops — any remedy offered before that is a
sentence pointing at a door that does not open.

## What this does not decide

Whether `/api/health` is the right route. It is a boot Preflight over ports, agents, model slots and
the data library, and a git push is not deployment readiness. The `Problem` type is general — "one
condition, one sentence, one remedy, one owner" — and the objection is to the route's name rather
than to the home. Renaming it is its own change.
