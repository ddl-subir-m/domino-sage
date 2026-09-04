---
status: accepted
extends: ADR-0020 (the working set's job is orientation, which is what makes a false row a correctness bug)
---

# Absence proves deletion only where the listing is complete

Domino Resources are shared, and another user can delete one at any time. The working set is a
durable record rather than a cache ([ADR-0020](0020-the-working-set-is-orientation-never-context.md)),
so a membership row can outlive the Resource it names by weeks. Nothing reconciled the two:
`add_project_resource` (`backend/sage/orchestrator/service.py:8933`) trusts the row the client sends
and does no existence check, and `list_project_resources` (`:8849`) enriches each row with `usedBy`
without ever asking whether the Resource still exists. The rail could show a Data Source that is
gone ([#161](https://github.com/ddl-subir-m/domino-sage/issues/161)).

We decided that **a membership row is compared against the platform listing the browser already
holds, that the comparison marks a row rather than removing it, and that absence counts as deletion
only for a kind whose listing is complete by construction.**

Mark, never delete. A silent disappearance is worse than a dead row that says why it is dead: the
creator picked that Resource deliberately, an app may still bind it, and a row that vanishes
overnight leaves nobody anything to act on.

## Three states, because two would lie

`liveness` is computed per row and takes three values.

**live** — the Resource is present in a listing that was read successfully.

**missing** — the Resource is absent from a kind that listed successfully *and* completely.

**unchecked** — everything else, and it is not a residue. `state.resourceListing` is `null` until
the deferred read lands (`store.js:566`); a kind that refused carries its previous rows forward
through `keepUnreadKinds` (`api.js:185`), so the listing in hand can be present, stale and wrong at
once; and one kind can never be checked at all (below). Collapsing `unchecked` into either neighbour
states a fact Sage does not have.

## Why client-side, in the one function that already subtracts these two sets

`applyListing` (`store.js:510`) computes `catalogueParents` as *listing minus members*. The dead set
is *members minus listing*: the same two collections, in the same function, the other subtraction.
It is free there, and it adds no wait to a rail that
[#159](https://github.com/ddl-subir-m/domino-sage/issues/159) and
[#160](https://github.com/ddl-subir-m/domino-sage/issues/160) exist to get out from behind Domino.

The `usedBy` precedent is honoured in the part that matters: liveness is computed on read and
**never written to the membership file**. A stored copy goes stale the moment anyone deletes
anything, which is the property that made `usedBy` a computed field and makes this one too.

It is written in `applyListing` and nowhere else. That function's own header says why — "two writers
for these fields is how the rail and the catalogue end up disagreeing about what Domino holds" — and
the rail, the @ menu and the bind picker are three readers of one answer.

## Model APIs cannot be checked, and the reason is structural

`list_model_apis` fans out over the creator's member projects and **skips any non-home project that
fails** (`backend/sage/resources/provider.py`), returning 200 with no error. It is also capped:
`_MAX_FANOUT_PROJECTS = 25`, sorted by name. A creator on thirty projects has five whose Model APIs
are permanently absent from a listing that reports complete success.

So for `model_predictive`, absence is not evidence. The kind is `unchecked` always. Every other kind
— `llm_alias`, `datasource`, `dataset` — is a single call that either answers or raises, and absence
there means what it looks like.

**The cost, stated plainly:** a Model API deleted on Domino goes on looking live in the rail. That is
the status quo of #161, unfixed for one kind of four. We took it knowingly, because the alternative
marks a working Model API dead forever for anyone on more than twenty-five projects, and a false
death is a new harm where a false life is an old one.

The repair is not in this decision's shape but in the provider's: if `list_model_apis` reported which
projects it skipped, the kind would be checkable whenever it skipped none. That is exactly what
[ADR-0028](0028-a-provider-reports-failure-and-the-caller-decides-what-it-costs.md) says a provider
owes its caller — the `continue` destroys, one layer down, the information the caller needs to judge
what a failure costs. It is filed separately and would upgrade this kind without changing anything
else here.

## A child inherits its parent

The listing fetches parents only; a warehouse Table under a Data Source and a pinned path under a
Dataset are a level below anything it reads. A child row takes its parent's liveness. A Table under a
`missing` Data Source is certainly unreachable, so that direction is sound. The converse is not
covered: a Table dropped from a Data Source that still exists stays `live`, and this check will not
find it. Checking it would cost a cascade call per row on every scope load, for a case nobody has
reported.

## What a marked row does, and what it does not do

**It informs; it does not block.** A `missing` row stays selectable in the @ menu and the bind
picker, carrying its mark and its reason at the point of picking.
[ADR-0027](0027-a-problem-informs-and-the-chip-holds-what-the-toast-points-at.md) settled that a
Problem informs and never blocks, and that the two acts Sage refuses — `_turn_slot_refusal` and
`publish_problems` — are the only two. Making the picker refuse would be a third, added to prevent a
refusal the creator gets anyway one step later, from code that knows more about the failure than the
picker does. #161 asked that a dead row not be offered "as if it were usable"; a mark is what that
sentence asks for.

**It is not a Problem.** It passes the three-part test in `CONTEXT.md`, and we still declined it. The
justification the glossary gives for that test is that it "keeps the count low enough to be worth
reading", and a stale working set produces several dead rows at once — each one lighting the chip to
send the reader back to the rail, where the mark already is. A Problem earns its sentence by naming
something the reader would not otherwise see.

**A kind that errored says so; a kind that is merely uncheckable stays quiet.** Preflight's rule is
that "we could not check" stays a state rather than becoming a sentence, with one exception: the
dependency itself not answering, where the failure to check *is* the fault. An LLM Aliases outage is
that exception and gets a line at its group header. The Model APIs cap is not, and gets none. The
line renders at the group header rather than on each row, because it is a fact about the kind, and
stamping it on twelve rows says it twelve times.

Today `resource-panel.js:758` computes `listingError` but renders it only when the group is empty —
so the one case this decision needs, a kind that errored *and* has rows, has nowhere to appear. That
render site moves to the group header unconditionally.

## The stuck row

A Resource deleted on Domino and still bound by a Built App cannot be removed:
`remove_project_resource` raises `ResourceStillBound` (`:8979`). We leave that refusal alone. The
Binding belongs to the app, and
[ADR-0011](0011-removal-lives-with-the-list-that-owns-the-scope.md) puts removal with the list that
owns the scope, so unbinding is the app's act — the row's action points at the app's own door
(`removeBindingFromApp`, `store.js:3281`), which the 409 already names. Relaxing the guard for dead
Resources would reach across a boundary ADR-0011 drew on purpose, and would silently break an app
that still ships the Binding.

## Consequences

**Liveness is written in one place.** Any consumer that recomputes members-minus-listing for itself
is the disagreement `applyListing` exists to prevent.

**A kind's checkability is a property of how its listing is fetched, not of the row.** Anyone adding
a kind, or widening the Model APIs fan-out, must say which of the two it is. A kind whose listing can
silently drop rows is `unchecked` no matter how complete it looks.

**Tests are client-side.** The cases in #161, plus the Model APIs regression, are a node harness in
`backend/tests/js/`, driven from pytest as #159's was
(`backend/tests/test_browse_domino_reads_the_platform_once.py:26`). The 409-when-bound behaviour is
unchanged and already covered; what is new there is only which door the row's action points at.
