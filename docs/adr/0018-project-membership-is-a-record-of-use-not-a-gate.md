---
status: accepted
extends: ADR-0010 (a Binding stays a human pick; only membership follows it), ADR-0011 (removal still lives with the list that owns the scope)
---

# Project membership is a record of use, not a gate

A Project holds a working set of Domino Resources — the rail's list, `.sage/project-resources.json`,
and the thing "Add to project" writes. Nothing reads it as a permission. A Built App reaches a Data
Source because it holds a **Binding**, and publish checks that Binding and nothing else
([ADR-0010](0010-publish-reads-the-declaration-not-the-code.md)). Membership gates no call, no
build, and no publish.

It was nevertheless being read as a gate — by people, and for a while by us. "Add to project" reads
as *make available*, so a creator who added a Resource believed the app could now use it, and a
creator who had not added one believed the bind was not yet allowed. Neither is true, and the gap
between the two beliefs is where the dead ends in [#132](https://github.com/ddl-subir-m/domino-sage/issues/132)
came from.

We decided that **every act that records use also records membership**, server-side, and that the
rail says what the list is: what this project uses.

## Why the join, rather than a gate that means something

The other repair available was to make membership real — refuse a bind for a Resource not in the
working set, so "Add to project" earns the reading it already gets. We rejected it twice over.

It would make the working set a second authorisation list in front of Domino's own. The bind
methods already validate against the project's live listing — `list_llm_aliases` has intersected
accessible model ids with registered aliases, `bind_model_api` refuses without a demonstrated call,
`bind_data_source` resolves the id against the project's sources. A Resource that reaches a bind
has already been proven reachable. A membership check in front of that would refuse binds Domino
permits, on the strength of a list Sage keeps for its own convenience.

And it would put the chore before the reason. Picking a Resource for an app is the act with intent
behind it; being in the rail is bookkeeping. Ordering them the other way is what made "Add" the
first step in every path — and made it look like the important one.

The reverse — dropping membership entirely and deriving the rail from Bindings and chips — was also
rejected. Staging a Resource before you know which app needs it is a real thing to want
(story 16 in #132), and Browse Domino's Add is where it happens. Membership has to be writable
without a Binding; it just must not be required by one.

## Where the join is written

In `_join_project_on_bind`, called from `Orchestrator._record` — the one place every Binding
reaches the manifest, and so the one place the join can be written once. Three doors bind
today — the panel's "Use in {app}", the rail tree's row action, and the Chat handoff's
`_bind_from_handoff` — and all three pass through it. A join per door would be three writes to keep
in agreement, and the fourth door, whenever it is hung, would arrive without one.

The join is idempotent because `add_project_resource` is: it keys on id, and a row already in the
working set is left where it is. It is not duplicated, not renamed to whatever this bind happens to
call the Resource, and not thinned back to the three fields a Binding carries. What the bind fills
in is what the row was missing — an LLM Alias row with no `alias` or `reasoning_efforts` is an
option the model picker draws blank whenever the live listing is unavailable, and the bind knows
both. A Binding does not carry them, so the door that resolved the Resource hands `_record` the
listing row it already read; the join fetches nothing of its own.

The id written is the prefixed one — `data_source:ds-dwh`, not `ds-dwh` — because that is the space
every other surface keys a Resource on. A Binding records the bare Domino id beside its kind, so
the prefix is put back on the way in, or the rail draws a second row for a Resource it holds and
the removal guard fails to join the two.

A membership write the disk refuses is logged and swallowed. The Binding is the record that
matters and it is already written; failing the bind the creator asked for, over the bookkeeping
that follows it, would be the wrong way round.

## Alternatives considered

**Join on send, or on the agent touching a Resource.** Rejected for the same reason ADR-0010 keeps
Bindings declared: what the code turns out to touch is not what a person chose. Membership follows
a human pick or it follows nothing.

**Announce the join.** The mention path returns a `joinedProject` flag, so the panel refreshes the
rail and says so once. A bind returns no such flag and draws no toast: a toast for the bookkeeping
half of an act the creator just watched succeed is noise. The refresh it still needs is decided on
the client, where the answer is already known — a row drawn from the rail was a member before the
bind, and only a Resource bound from outside the rail makes `bindToApp` re-read the scope.

**Rename "Add resources".** Rejected. It is the accurate label for what that button does — it adds
a Resource to the project without binding it anywhere. What was wrong was that nothing said what
the list underneath it was, so the button was carrying the whole explanation. The caption carries
it now and the button is left alone.

## Consequences

**Binding a Resource puts it in the rail.** In every mode, through every door, for all three bound
kinds. A creator who binds without ever opening Browse Domino still gets a rail that shows what the
app uses.

**Browse Domino and its Add act are unchanged.** Staging still works, and a staged Resource still
sits in the rail bound by nothing.

**The removal guard gets stricter in practice, not in rule.** More Resources are in the working set,
so more removals meet [ADR-0011](0011-removal-lives-with-the-list-that-owns-the-scope.md)'s refusal
naming the apps that still bind them. That refusal is the one the creator wants: it now fires for
the Resources an app actually depends on, which is what it was always for.

**Unbinding still leaves membership alone.** Removal lives with the list that owns the scope. A
Binding dropped says this app stopped using the Resource; the Project's own list is removed from
its own row, with its own refusal.

**Publish is untouched.** It reads the per-app declaration and only that. This ADR adds a write to
a list publish does not consult, and loosens nothing in ADR-0010.

**The rail's copy is part of the decision.** "What this Project uses. Using a Resource in Chat or in
a Built App adds it here." The sentence and the behaviour ship together; a rail that says this while
membership is still a separate chore would be a lie in the product's own words.

**CONTEXT.md's Resource Browser entry says the same thing.** The glossary is the entry an agent
reads before touching this code, and a bind that quietly writes membership is a mystery without it.
