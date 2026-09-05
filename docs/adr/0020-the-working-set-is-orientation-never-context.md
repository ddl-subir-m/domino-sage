---
status: accepted
extends: ADR-0018 (what the working set is *for*, now that it is settled it is not a gate)
---

# The working set is orientation, never context

[ADR-0018](0018-project-membership-is-a-record-of-use-not-a-gate.md) settled what the working set is
not. It is not a permission, it gates no bind, no build and no publish. It never said what the list
is **for**, and the code answered honestly: `read_project_resources`
(`backend/sage/workspace/manager.py:354`) has exactly two production callers — the UI list endpoint
at `backend/sage/orchestrator/service.py:7820` and its own writer at `manager.py:369`. It reaches no
prompt, no `AGENTS.md` and no tool listing.

That left a section on screen with no job, and a fair question: give it one, or delete it. The
obvious way to give it one was to inject it — an ambient block naming the Domino Resources this
Project has picked up, so the agent knows the Snowflake Data Source exists without being told again
every turn.

We decided that **the working set's job is to tell the person what this Project has picked up and
where each thing is used, and that it is never injected into a prompt.** Reuse — picking from it
again rather than going back to Browse Domino — is a real second job, and it is already served
without injection. Orientation is the primary one, and that is what makes completeness a
correctness requirement rather than a nicety.

## Why not injected

An injected working set tells the agent about Resources it cannot reach. Membership gates nothing;
a Built App reaches a Data Source because it holds a **Binding**, and publish reads that Binding and
nothing else ([ADR-0010](0010-publish-reads-the-declaration-not-the-code.md)). So a list of members
handed to the model is a list of things it will believe are usable and then fail on — the same
false-availability belief ADR-0018 names as the source of the dead ends in
[#132](https://github.com/ddl-subir-m/domino-sage/issues/132), reissued to the model instead of to
the person.

The agent already gets the truthful version, and has all along. `_record`
(`service.py:8509`) calls `_write_app_resources` (`:9642`), which writes `_write_app_model`,
`_write_app_model_api` and `_write_app_data` and then rebaselines the turn. Dependency reaches the
agent as files. Membership reaches it as nothing. That asymmetry was undocumented and read as an
oversight; it is the design.

`docs/workbench/chat.md` had already reached the same answer for the leaf case — "Pins are not
prompt context" — a year before anyone asked the question about parents.

## Why not deleted

ADR-0018 rejected deriving the rail from Bindings and chips, because staging a Resource before you
know which app needs it is a real thing to want (story 16 in #132). That reason still holds, and
deleting the section takes the staging surface with it.

More than that: nothing else in Sage answers "where is this Resource used." `usedBy` (#133,
`service.py:7811`) is computed on read from the apps' own manifests, and it is the same scan
`remove_project_resource` (`:7876`) refuses on — so the subtitle and the refusal read one answer.
That question is worth a surface, and this is the surface it has.

## A pool with two consumers, and it fills itself

The working set is not a peer of Session context and Bindings. It is the pool they are drawn from:
a Resource enters it, and is then spent on a Conversation or on a Built App. Both consumers also
write back into it — `_join_project_on_mention` (`service.py:3846`) and `_join_project_on_bind`
(`:8512`) — so the pool accumulates on its own and can *also* be primed ahead of time through Browse
Domino.

Saying the self-filling out loud is the point. "Add to project" read as a first step you had to take
before anything else would work, and that reading is what made the chore look like the important
act. It is not a step. It is a record that keeps itself, which you may also write to early.

## Why a one-shot backfill, and not the free union on read

`_join_project_on_bind` landed on 2026-09-01 in `29d4930` with no backfill, so every Binding made
before that date has no membership row. Under orientation-first that is not a cosmetic gap: the
section's only job is to be true, and it is false for every Project that predates the commit.

The cheap repair was a union on read. `list_project_resources` (`:7808`) already scans
`_app_bindings()` on every call to compute `usedBy`, so returning membership rows unioned with every
bound Resource would cost nothing extra and would never go stale.

We rejected it, knowing it was nearly free. It turns the working set from a file into a view, and
that breaks two decisions that depend on it being a record. The removal guard refuses to drop a
Resource any app still binds ([ADR-0011](0011-removal-lives-with-the-list-that-owns-the-scope.md));
under a union, a Resource legitimately removed from the Project would reappear on the next read for
as long as any app binds it, so "Remove from \<project\>" becomes a no-op that looks like a bug. And
the noun stops being definable: a list that is sometimes what you added and sometimes a projection of
Bindings is exactly the ambiguity this decision closes.

So the repair is a one-shot migration: write the missing membership row for every Binding that lacks
one, once, and leave the file as the single source of truth afterwards.

## Consequences

**The section is complete or it is wrong.** Any future door that records use and skips the join is
a correctness bug, not a missing nicety. `_record` and `add_thread_context` are the two places that
join today, and a third door must pass through one of them.

**The panel is this list and nothing else, since
[ADR-0032](0032-the-panel-is-the-projects-one-list.md).** Orientation being the job is what settled
it: two other scopes were stacked in the same column, and a surface whose job is to orient cannot
also be the place three scopes are told apart by their headings.

**`usedBy` stays a subtitle.** Orientation being the primary job settles that the list must be true;
it does not follow that it needs a bigger surface. A count plus the drawer answers the per-Resource
question people ask. A Resource × Built App matrix answers a question nobody has asked yet, and is
not built.

**The working set never appears in a prompt, an `AGENTS.md`, or a tool listing.** If an agent needs
to know a Resource exists, the answer is a Binding, and the door for that is
[ADR-0021](0021-each-scopes-door-lives-on-the-surface-that-owns-it.md).
