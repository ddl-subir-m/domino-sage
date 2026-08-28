---
status: accepted
---

# Publish reads what was declared, never what the code touches

Sage holds two answers to "what Resources does this Built App use", and they can disagree. The
**declared** answer is the list of Bindings a person made by picking, recorded per app and read by
`_refuse_unsafe_publish` (`backend/sage/orchestrator/service.py:6144`). The **derived** answer is a
scan of the app's own source for the tokens that identify each Binding — `_resource_usage`
(`service.py:7472`), which finds an LLM Alias by its name in the code and a Data Source by the names
of the queries recorded against it. We decided that **the declaration is authoritative for
everything publish does**, and that the derived scan is advisory: it may label a Binding on screen,
and it may never gate a publish, a bind, or an unbind.

Read the counterpart carefully before touching this. `_data_usage` (`service.py:7438`) is *not* the
derived scanner for Bindings — it scans the app's source for uses of an **attached file**, so
detach and delete can refuse to orphan code. The Binding-side scanner is `_resource_usage`. The
issue that opened this decision named the wrong one, and the next reader will too.

## Why the declaration wins

For a Data Source the declaration is not a record of the pick. It is the app's **permission to reach
that store at run time**. The published app's own server reads `.sage/bindings.json` at startup
(`template/react-vite/serve.py:load_sources`) and refuses any named query whose binding is absent
from it — *"reads the Data Source X, which the app is no longer recorded as using."* A grant that
the agent's own SQL could widen is not a grant. Making the derived answer authoritative would let
the code being guarded decide what the guard covers, which is the one shape ADR-0001's publish-time
gate cannot take.

The derived answer is also wrong by construction at the moment it matters most. It is a token search
over source text, so a Data Source bound two minutes ago — before the agent has written a single
query against it — scans as unused. A gate on that refuses correct work in the middle of a build.

The grant is per kind, and only Data Sources carry one. An LLM Alias Binding is not read by the
published app at all: the browser calls the LLM Gateway directly, same-origin, on the viewer's
cookie. So an Alias in `bindings.json` grants nothing at run time and an Alias missing from it blocks
nothing. The record still publishes, because "what does this app use" has to be answerable without
running the app.

## Considered options

**Derived is authoritative — publish ships what the code demonstrably touches.** This is what the
request behind the decision literally asks for: *"I only want to publish resources the app uses."*
Rejected because it inverts the guard, as above, and because it makes an app's permissions a
side effect of the last thing the agent wrote.

**Derived can veto — declared publishes, unless the scan says nothing uses it.** Rejected because a
veto is a gate, and a gate on a signal that is wrong mid-build is a gate that refuses correct work.
It also fails in the fail-open direction, which is worse than the guard it would be softening.

**One manifest for Bindings and Attachments.** Rejected, and not for taste. `.sage/bindings.json` is
read at run time by the app's server to decide what a query may touch; `.sage/attachments.json` is
read at deploy time by a Node script to rebuild `public/data/` symlinks off the Domino dataset
mounts. Merging them puts one file in front of two consumers that want different things at different
moments. The glossary already keeps them apart: a **Resource** is a Data Source, a Model API or an
LLM Alias, and a file is an **Attachment**, which is why `binding_from_context` returns `None` for
`file:` and `dsfile:` rows rather than that being a gap to close.

## Consequences

**An unused Binding still publishes, and still faces the credential guard.** No exemption. It is
tempting to skip `_refuse_unsafe_publish` for a Data Source nothing queries, and it is wrong twice:
it would make the advisory scan a gate after all, in the fail-open direction; and "unused" means
"reachable as soon as the agent writes one query", because the grant is already held. The way out of
a Binding you do not want is to unbind it, which is a deliberate act with its own cleanup.

**The derived scan runs at the end of a build turn, not on render.** `_scan_app_sources`
(`service.py:7415`) walks the whole app tree and reads every code file. Anything that renders per app
switch must read a written answer instead. This follows `publish_check`'s discipline — local, pure,
JSON off the disk, no network — and its staleness only ever errs in the true direction: a Binding
added after the last build turn genuinely is not used yet. `_resource_usage` keeps its live scan for
unbind, which is a one-off act that can afford one.

**A surface that shows a Binding is a report on the record, not a diff against the code.** The
reverse disagreement — source that calls a Resource the app never declared — is not reported there.
For a Data Source it is already caught before deploy by `publish_check`. For an LLM Alias nothing
catches it and the app works anyway, which leaves the record quietly incomplete; that is a known hole
and has its own issue rather than a line in a manifest nobody can act on.
