---
status: accepted
---

# A provider reports failure, and the caller decides what it costs

`ResourceProvider.list_collaborators` returns an empty list when it cannot read the project record,
and the Protocol says so on purpose (`backend/sage/resources/provider.py:582-584`):

> Empty is a real answer: a solo project has no collaborators, and a caller that cannot read the
> record gets the same empty list rather than an error, because a plan page is still usable without
> names on it.

That was right for the caller it was written for. The plan page puts names on review comments, and
a plan with ids where names should be is worse than names and much better than a page that will not
load. Swallowing the failure there is a real kindness.

It stops being one the moment a second caller arrives. The People modal adds a person to the
Project, and it has to tell three states apart: Sage is not connected to the platform, the Project
genuinely has nobody else on it, and the read failed. Under the old contract the last two are the
same value, and no amount of care in the modal can separate them — the information was destroyed
one layer down, by a decision the modal cannot see and cannot opt out of.

So the contract moves: **`list_collaborators` raises when it cannot read, and the plan-review path
catches and degrades to empty.** The forgiveness is unchanged in behaviour and unchanged in reason.
It just lives with the caller that wants it.

## Why not the alternatives

**A second method that raises**, beside the forgiving one, was the smaller diff. It also means two
ways to ask one question, and every future caller has to know that one of them lies. The next
person to add a caller picks whichever name reads better.

**A flag on the response** — `{people: [], readable: false}` — keeps one method, and pushes the
same decision into a field every caller must remember to check. A caller that forgets gets the old
bug back silently. An exception cannot be forgotten.

**Leaving it alone and having the modal probe separately** — read the project record first to find
out whether reading works — means two calls to answer one question, and a race between them where
the answer changes in the gap.

## The cost, stated plainly

Every existing caller of `list_collaborators` now has to say what a failure means to it, and there
is no default. That is the point, but it is a real cost: it is a wider change than the feature that
prompted it, and a caller added later that forgets to catch will surface a platform outage as a
stack trace rather than a thin page. The trade is that the failure is visible to whoever is best
placed to judge it, instead of being decided once, invisibly, for everybody.

This generalises past this one method. A provider's job is to report what the platform said,
including that it said nothing. What a failure is worth — a thin page, a retry, a refusal — is the
caller's judgement, because only the caller knows what it is in the middle of doing.
