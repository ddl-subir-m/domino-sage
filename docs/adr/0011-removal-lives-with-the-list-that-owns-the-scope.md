---
status: accepted
---

# Removal lives with the list that owns the scope

Two routes for removing what a Built App holds have been live and uncalled since they were written:
`DELETE /api/bindings/{kind}/{resource_id}` (`backend/sage/orchestrator/app.py:1287`) and
`POST /api/project/files/detach` (`app.py:1389`). Nothing in `js/` calls either, so a Binding or an
Attachment, once made, could only be removed through the API. ADR-0010 already promised the way out
— *"the way out of a Binding you do not want is to unbind it, which is a deliberate act with its own
cleanup"* — and there was no door.

The reason the door was never hung is not that nobody built it. It is that the Workbench has **three**
removal scopes and only ever named two of them, so there was no honest place to put a third control.
A Resource can leave a Conversation, leave a Built App, or leave the Project. We decided that **an
object is removed from the list that owns its scope, and every removal label names that scope in
words**. One surface per scope, one guard per operation, and no bare verb that could mean any of them.

> **Partially superseded by [ADR-0015](0015-the-conversation-is-not-a-removal-scope.md).** The rule
> holds for the Built App and the Project. The Conversation is not a removal scope — stopping there
> discards nothing — so its control says *"Stop using here"* and names no scope. Everything else in
> this ADR stands.

> **Extended to the listing, not only the removal (#148).** A row cannot name its scope from a list
> that is not that scope's. Files were the last place two scopes' records were merged into one list —
> the Project's `file` group was built from the Project's Uploads AND the selected app's Attachments —
> and the symptom is what this ADR predicts: the app's record, drawn under "In this project", carried
> none of the app's doors and none of the Project's either, because the Project has no act to offer
> for a file it does not own. One list, one scope. An object is now **listed** by the scope that owns
> it as well as removed there, and a file that really exists in two scopes is two rows saying so.

## Why a scope-naming rule, and not a new verb

The obvious fix was a distinct verb for app-scoped removal, because `detach` had already been spent
twice: `store.detach` (`js/store.js:1410`) removes a chip from Session context, while the backend's
`detach_file` (`service.py:7463`) removes an app Attachment. Same word, the two scopes that #84 and
the **Session context** glossary entry exist to keep apart.

A new verb does not fix this, because the collision is not between two words. It is between three
scopes and a vocabulary that can hold two. `js/components/resource-panel.js:188` already offers
*"Remove from {project}"*, so any new app-scoped word would be the third claimant on one gesture, and
the next reader would have to memorise which of three verbs meant which of three lists.

Naming the scope in the label scales to as many lists as the product grows, and it is already
half-built: the Conversation's control says *"Remove from this conversation"* (since retired by
[ADR-0015](0015-the-conversation-is-not-a-removal-scope.md)). The rule makes the
other two match rather than inventing anything. The code keeps `unbind` and `detach` as the
app-scoped pair — `attach_file`/`detach_file` already pair correctly — and `store.detach` is renamed
to `store.removeFromConversation`, the name of the API call it already makes, so the word stops
spanning scopes at the only place it did.

## Why one surface, and why it is the panel

`remove_binding`'s docstring names *"the rail and the composer pill"* as the surfaces that would
offer cleanup. Both are Chat-side and Conversation-scoped, so the route's stated design intent
predates the scope split. The intent was wrong, not the split.

#92 then put the app's Bindings and Attachments in the Build header and deliberately left the row
read-only, on the argument that *"unbind and detach each have to report that the app's source still
uses what just went, and a second copy of either here would be a second guard to keep in step with
the first."* That argument holds. The header is a glance — one line that says what the selected app
ships — and a summary that can act is a summary that has to carry a confirm, a scan result and a
cleanup offer in a header row.

So removal lives in the **"In this app"** section of the resource panel (`resource-panel.js:426-455`),
which is where the header's Bindings pointer already sends people, and the header keeps its pointers
instead of growing controls. This is not the status quo: the section as built has
`allowAppActs: false` (`:450`) and holds Bindings only, so today the pointer lands on a list whose one
action is Conversation-scoped and whose Attachments half does not exist. **A pointer is a promise that
the destination can act.** Honouring it means the section gains the removals, gains two labelled
groups — Bindings and Attachments, the two names #92 chose over one umbrella — and takes a head that
names the app, because ADR-0008 makes "this app" a question every surface has to answer.

## Why the report comes after the act, and only offers

ADR-0010 makes the derived scan advisory: it may inform and it may never gate. That permits a
post-hoc report and a non-blocking pre-warning both, so the choice is ours. It is post-hoc, as both
routes already build it: `unbind` reads `_resource_usage` before the record goes and returns it as
`refs`, and `detach_file` returns the same from `_data_usage`.

A pre-warning would have to run the scan before the act — per row, on menu open — and
`_scan_app_sources` walks the whole app tree. ADR-0010 already ruled that anything rendering per app
switch must read a written answer instead, and reserved the live scan for unbind *"which is a one-off
act that can afford one"*. A warning shown before the act also reads as a gate however it is worded,
which is the one thing the scan must never become.

The report is a dismissible notice inside the section, not a toast, because it is only worth having if
it can be acted on and five seconds is not long enough to read a file list and decide. Its cleanup
action **pre-fills the composer and does not send**. A button that fires a build turn can be refused
by the per-project turn lock if a turn is already running, and it would put work past the plan gate
that the person never read.

## Considered options

**A distinct verb for app-scoped removal.** Rejected above: three scopes, and the third claimant
(`Remove from {project}`) already ships.

**Removal in the Build header, or in both places.** Rejected. In both is two implementations of one
guard, which is precisely what #92 declined and would have to be kept in step through every change to
what `refs` reports. In the header alone moves a confirm, a scan result and a cleanup offer into a
one-line summary, and leaves the panel section — the list that actually holds the objects — still
unable to act.

**An undo on removal.** Rejected. It is a second write path into the manifests for a flow that is two
clicks to repeat, and restoring a Data Source's Scope silently is exactly the kind of write nobody
asked for. Re-picking from the Project pool is the recovery, and the confirm says what re-picking will
cost.

**Wire the existing Requirement pair to the real routes.** `resource-panel.js:168-175` already shows
*"{app} needs this to run"* and *"{app} no longer needs this"*, backed by `api.js:416-418`, which are
stubs returning `empty()` and `{}`. It looked like the affordance was half-built. Rejected:
**Requirement** is a third name for what the glossary calls a **Binding**, and adopting it would put
two words on one concept in the same panel. The pair is deleted, and the word goes to the glossary's
_Avoid_ list. What is not deleted is in the consequences below.

## Consequences

**The confirm is asymmetric, and that tracks real cost.** Removing a Binding confirms; removing an
Attachment does not. Re-attaching a file is one click on the same Dataset file, while re-binding a
Data Source means choosing its Scope again — the Scope goes with the Binding record, and nothing else
holds it. The Model API credential does *not* go: it lives in its own store keyed by model id
(`service.py:7142`), so re-binding will not ask for the sample request again, and the confirm says so
rather than letting the person expect the worse outcome.

**Removing a Binding does not touch the Conversation's chips.** This is the one moment the two scopes
visibly disagree — the app stops being allowed to read `sales-db` while `sales-db` is still sitting on
the composer — and it is correct, not a bug to fix later. It is also the mirror of the message at
`store.js:1414`, which tells someone dropping a chip that the app still needs the Resource. Both
sentences describe the same split from opposite sides, and the glossary carries it so that neither
reads as a leak.

**Requirement dies, but its two ideas move to `bindings`.** `state.requires` is always `[]`, so two
consumers have never once rendered: the *"{app} still needs it"* branch at `store.js:1414`, and the
*"Required by {app}"* subtitle driven by `requiredIds` (`resource-panel.js:317`, used at `:376`).
Both ask "does this app need this Resource", which is what a Binding answers. They are re-pointed at
`state.bindings` rather than deleted with the stub. The literal `required: true` at `:448` is
unaffected — it is the In-this-app rows describing themselves.

**"Add to this conversation" stays on app-scoped rows.** It was never the problem; being *alone* was.
(The gesture stays; the label is now *"Use in this chat"* on every surface —
[ADR-0015](0015-the-conversation-is-not-a-removal-scope.md).)
Bringing the app's Data Source into Chat to ask about it is a real gesture, and its label already
names its own scope. Those rows do carry a synthetic id, `` `${b.kind}:${b.id}` ``, which will not
match a Project Resource id — whether it makes a sound chip is a build-time check, not a decision.

**An Attachment's removal takes the declaration and the app's copy, never the source.** This is what
`detach_file` already does: the `public/data/` symlink goes, any raw copy the agent leaked into the
app tree goes, the manifest entry and the AGENTS.md data block are rewritten, and the Dataset bytes
stay. The copy on screen may only promise that when there is a Dataset to name. `detach_file`'s own
docstring records *"rehydrated entries with no dataset_id"*, and for those the source cannot be
named, so the row says so instead of inventing one.

**The panel names an empty kind; the header does not.** #92's rule — *"a kind with nothing in it is
not the same state, so it is not named"* — is right for a glance and wrong for a destination. The
panel shows both group labels always, because it is where the two words are learned and where someone
arrived intending to act; a group reading *"Attachments — none"* answers the question they came with.
The header keeps omitting. When both are empty the panel takes the header's exact wording
(`js/modes/builder.js:473`) so the two surfaces cannot drift apart again.

**Both header pointers change, and one of them was false.** They say *"listed in"*, a read-only word,
now that the destination can act; and the Attachments pointer sends people to *"Project resources,
with the app's files"* (`builder.js:481`), a group that does not exist. Both take one shape —
`in Project resources, under {app} — remove it there` — which names the destination in the words the
reader will actually see on the way to it, and names the action.

Those words are the **dock tab's**. When this was written the panel's section head carried them too,
so the distinction never arose; [#140](https://github.com/ddl-subir-m/domino-sage/issues/140)
renamed that head to "In this project", because the working set holds Assets as well as Resources
and a head reading "Project resources" was a claim about the contents that half of them do not meet.
The rule and the pointers are both unchanged: the tab is what the reader clicks on the way in, so
the tab is what a pointer names.

**Removal is scoped to the *selected* app, and no route says so.** `unbind` takes a kind and a
resource id but no app id; it acts on `self.project().workspace`, which resolves through
`app_workspace` to the selected app (`backend/sage/workspace/manager.py:1090-1095`). The scope is
carried entirely by selection. That is why the section head names the app rather than saying "this
app", and why any future caller outside the panel has to establish the same thing before it calls.
