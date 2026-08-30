---
status: accepted
supersedes: 0011 (the scope-naming rule, for the Conversation only)
---

# The Conversation is not a removal scope

ADR-0011 decided that the Workbench has three removal scopes and that **every removal label names
that scope in words**. That rule stands for two of them. It was drawn one item too wide: taking a
Resource out of a Conversation is not a removal, so it leaves the removal vocabulary altogether. The
menu now says *"Stop using here"*, paired with *"Use in this chat"*, and *"Remove from {app}"* and
*"Remove from {project}"* are unchanged.

## Why the rule's domain was too wide

ADR-0011 was solving a real collision and solved it correctly. Three gestures shared one bare verb,
and naming the scope in each label is what tells them apart. The error is upstream of the naming:
the Conversation was counted as the third member of the set.

The other two destroy a record. *"Remove from {app}"* deletes a Binding or an Attachment — the Scope
a Data Source was bound with goes, and re-binding means choosing it again (ADR-0011's own asymmetric
confirm exists because of that cost). *"Remove from {project}"* drops a membership. Both are writes
somebody may not want to repeat, which is why both take a confirm and why ADR-0011 wanted them
distinguishable at a glance.

Stopping a Conversation from using a Resource writes nothing. The Resource stays in the Project, the
app still holds whatever it holds, and the way back is to name it again. ADR-0011 already recorded
this asymmetry as a consequence — *"Removing a Binding does not touch the Conversation's chips"*,
the moment "the two scopes visibly disagree" — but read it as two scopes disagreeing rather than as
evidence that only one of them was a removal.

So the labels were teaching a false equivalence. Three items wearing one verb told a reader that
three comparable things were on offer and the only difference was where they landed. The one that
cost nothing looked exactly as expensive as the two that cost something.

## The two axes

The Conversation's pair moves on its own axis. *"Use in this chat"* and *"Stop using here"* are
using and not-using: reversible, per-turn, and about what the assistant can see right now.
*"Remove from {app}"* and *"Remove from {project}"* are about what a durable list holds. A person
reading the menu now sees two kinds of act rather than three flavours of one, which is the
distinction that actually governs what happens if they click.

This also settles the attach verb, which ADR-0011 left inconsistent by accident. Its consequence
*"'Add to this conversation' stays on app-scoped rows"* is still the decision — the gesture belongs
there — but the label was one of three names the same act carried across three surfaces (*"Add to
chat"*, *"Mention in this chat"*, *"Add to this conversation"*). All three are now *"Use in this
chat"*, and *"Stop using here"* is its opposite rather than a borrowed removal.

## Considered options

**Keep the scope-naming rule and leave *"Remove from this conversation"*.** Rejected. It is the
uniform answer, and uniformity is what makes the two real removals legible — but it buys that by
making the free act look costly. The rule was never about symmetry for its own sake; it was about
telling apart gestures that a reader has to choose between.

**Name the scope without the removal verb** — *"Stop using in this conversation"*. Rejected as
wordier without being clearer. The control sits inside the Conversation it acts on, which is what
"here" refers to; the surrounding UI is the scope. ADR-0011's argument that scope must be in the
words applies where one list summarises another and the reader could be looking at either, which is
the case for the app and Project rows and not for this one.

**A fourth scope word for the Conversation.** Rejected for the reason ADR-0011 rejected a new
app-scoped verb: another claimant on the same gesture, and a reader made to memorise which word
means which list.

## Consequences

**The scope-naming rule keeps its force where it still applies.** Any future list that can hold a
Resource takes *"Remove from {that list}"*, and the glossary's _Avoid_ on a bare "Remove" stands.
What no longer follows from the rule is that a Conversation must have a "Remove from" of its own.

**The chip's `aria-label` speaks the scope that the visible label only points at.** The X button
announces *"Stop using {name} in this chat"* where the menu item beside it says *"Stop using here"*.
Same verb, same act, worded twice on purpose: *"here"* is the panel a sighted reader is already
looking at, and audio has no *here* to look at. This is the only place the pair diverges, and the
reason is the absence of the surrounding UI, not a second vocabulary.

**Nothing about removal's ownership changes.** ADR-0011's other decisions — that the act lives in
the list that owns the scope, that the Build header points instead of acting, that the report comes
after the act and only offers — are untouched. This ADR narrows one rule's membership and renames
one pair of controls.
