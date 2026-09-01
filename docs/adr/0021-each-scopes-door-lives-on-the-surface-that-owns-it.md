---
status: accepted
extends: ADR-0011 (the scope-naming rule, generalised from removal to addition)
revises: #92 (the Build header stops being read-only), #129 (a Scope is no longer a cascade position)
---

# Each scope's door lives on the surface that owns it

[ADR-0011](0011-removal-lives-with-the-list-that-owns-the-scope.md) decided that an object is removed
from the list that owns its scope, and that every removal label names that scope in words. It
deliberately rejected distinct verbs, because three verbs for three lists is three things to
memorise. That rule works and is live: `backend/sage/workbench/js/components/resource-panel.js` reads
"Stop using here", "Remove from {app}", "Remove from {scope}".

Addition never got the same rule. So one row menu offers "Use in this chat" (`:182`) and, two items
below it, "Use in {app}" (`:186`). The first writes a chip on a Conversation and nothing else. The
second writes a committed manifest that publish reads
([ADR-0010](0010-publish-reads-the-declaration-not-the-code.md)) and that a deployed app depends on
weeks after the Conversation is dead. Two acts of wildly different weight, adjacent, in one style,
under near-identical words.

The labels are not the problem — ADR-0011 already made them name their scopes. The problem is that
naming the scope does not convey the weight, and ADR-0011 never claimed it did.

We decided that **every act that adds is offered on the surface that owns the scope it writes**, and
that the weight is carried by that separation rather than by a confirmation.

## The three doors

| Scope | Door | Writes |
|---|---|---|
| Session context | the composer (@-mention, and the panel's "Use in this chat") | the Thread's context, plus a working-set row on the way through |
| Working set | Browse Domino's Add | `.sage/project-resources.json` |
| Binding and Attachment | the Built App's own surface — the Build header | `apps/<id>/.sage/bindings.json`, `apps/<id>/.sage/attachments.json` |

The rail keeps `use-in-app` for no scope it owns. It becomes the working set's orientation surface
and nothing else ([ADR-0020](0020-the-working-set-is-orientation-never-context.md)), which is the
same shape ADR-0011 arrived at from the other side: the list that owns the scope owns the act.

## Why separation, and not a confirmation

A confirm on every bind taxes the repeat user on every repetition, which is the cost ADR-0011
refused when it rejected memorised verbs — paid later rather than avoided. Separation costs one
click of distance and is learned once.

The act still says what it did afterwards, naming the scope and the way back. That is the receipt,
and it is what "Stop using here" already does for the cheap side of the pair.

## What this overturns

**#92 left the Build header read-only,** on the argument ADR-0011 quotes: unbind and detach each
report the app source that still uses what just went, so "a second copy either would be a second
guard to keep in step with the first," and a summary should point rather than act. That argument
held while the header was a summary. It stops holding the moment the header is the only door — a
door is not a second copy of anything. The removal guard stays where ADR-0011 put it; what moves is
addition, which had no home at all.

**#129 made a Scope the cascade position the creator is standing on** (`js/store.js:2260`: "the bind
is still the door beside the crumb"). That put a multi-level tree walk — database, schema, table — in
the rail, and made the Binding's Scope a by-product of where you stood in it. Under this ADR the
bind moves to the header and the tree does not follow it. Binding and scoping become two acts: bind
the Data Source from the app's surface, then set its Scope there as a second, cheaper act on a
Binding that already exists.

This is cheap because a scopeless Binding is already a legal, named state. `CONTEXT.md` on **Scope**:
"A Binding may have none, which means the Resource is recorded but the part of it the app reads is
not." The cascade stays in the rail as a way to *look* at a Data Source, which is orientation, which
is the rail's job.

## Alternatives considered

**Split the rail into two panels — "what the agent can read" and "what the app depends on".**
Rejected. It does not repeal ADR-0011's reasoning, it collides with it: a two-panel rail with "Use
here" in each panel is exactly the bare verb that ADR-0011 forbade, now with the scope moved into a
panel heading the user has to remember they are under. It also cannot hold three nouns in two panels.

**Keep the act in the rail, under a visually separate group.** Rejected as the worst of both: the
act still lives on a surface that does not own its scope, and the separation has to be re-taught on
every surface that ever draws a resource row.

**Carve Data Sources out as the one exception, keeping cascade-and-bind together.** Rejected. Data
Sources are the most common Resource in the product, and an exception that covers the majority case
is not an exception — it is a second rule that has to be explained forever.

## Consequences

**`use-in-app` leaves `resource-panel.js`.** The rail's row menu keeps "Use in this chat", "Stop
using here" and the two Removes, which are the acts whose scopes it owns or points at.

**The Build header grows an add control.** It stops being the read-only glance #92 shipped, and its
picker draws the working set first and Browse Domino behind it — the same ordering the composer's @
menu already uses (`js/components/composer.js:100`).

**Two composer repair paths need re-pointing.** The Data Source repair and the credential repair
both routed into the rail, because that is where the act they repair used to live. Their destination
becomes the app's surface. Done in
[#143](https://github.com/ddl-subir-m/domino-sage/issues/143), and the two did not land the same way:
the Data Source repair stopped being a signpost altogether, because once the bind carries no Scope
there is nothing left to walk to and the card records the Binding itself in one click. The credential
repair could not follow — Sage refuses to record a Model API it holds no access token for, so the
bind is an act the server is designed to turn down and a card must not spend its one click on it. It
stays a signpost, and what stands open is the header's own door.

**The Chat handoff stays a door.** It records Bindings without passing through the header
(`_bind_from_handoff`), and that is consistent: the handoff is the moment a person confirms the
Conversation is becoming an app, so the act is on the surface that owns it.
