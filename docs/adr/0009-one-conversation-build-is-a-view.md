---
status: accepted
revises: ADR-0005 (per-Conversation slicing was not the end of the job), one consequence of ADR-0008 (the rail no longer follows the mode), docs/workbench/handoff.md §3 and §4
---

# One Conversation, and Build is a view over it

A Conversation is one row in the rail and two things in the pane.

Ask a few questions in Chat, accept the offer to turn them into an app, and Build greets you
with "Build the app from a plan" — none of the analysis you just did on screen. Type straight
into Build, then open that same row in Chat, and Chat greets you as though the Conversation
were new. Whichever way you cross, the pane you land on is empty and the work you just did is
somewhere you cannot see.

This is already written down as a defect. `handoff.md` §8 requires that switching back to Chat
shows the same Conversation and **not** a blank greeting. The product cannot satisfy that line.

The cause is that each pane renders its own store and each is right about its own half. Chat's
turns are in the Conversation's own log under `.sage/threads/<id>/`; Build's turns are in the
Built App's log, tagged with the Conversation that drove them ([ADR-0005](0005-build-is-per-conversation.md)),
and since [ADR-0008](0008-a-project-holds-many-built-apps.md) they are spread across one log per
app the Conversation drove. Nothing was ever wrong with either read. Nothing ever read both.

## Decision

**A Conversation has one transcript, and Build is a view over it rather than a second place with
a second memory.**

- **The transcript is a read-time merge, not a new store.** Both writers are unchanged: Chat
  appends to the Conversation's log, Build appends to the Built App's log, and every Build row
  already carries both the app and the Conversation. A control-API read returns the two merged,
  each row labelled with the half it came from. Nothing migrates, and the arm can be deleted
  without touching data.
- **The merged read spans the Built Apps of one Project, and no wider.** A Conversation can hand
  off more than once, so a merge built on the selected app's log alone would show one app's turns
  and silently hide the rest. The read scans every app in the Project and filters on the
  Conversation tag.
- **Stored turns gain a timestamp**, applied where each writer already writes. Rows written before
  this change have none and fall back to a single splice — Chat turns, then Build turns — which is
  correct for every Conversation that exists today, because until now the only way to own both
  halves was Chat, then handoff, then Build.
- **Both routes survive and both render everything.** The route decides only whether the preview
  and resource panes sit beside the transcript. No route is added or removed, so existing links
  keep working. A Build link with no `?app=` resolves from the Conversation's newest bound handoff
  entry rather than falling through to whatever is selected.
- **A build run folds into one collapsed row in Chat**, expandable, with a way through to Build.
  That row owns the transcript's app slot and the app card is its face, not a second block beside
  it (#83).
- **The two harness sessions stay.** Each Build turn is given a bounded summary of that
  Conversation's Chat turns, rebuilt per turn so it cannot go stale and bounded so cost stays flat.
  Merging the sessions is out of scope; it would mean reworking how the enforcement shim scopes
  tools per agent, which is too large to also be the variable under comparison.
- **One rail, listing Conversations, in both modes.** Selecting a Built App moves into the Build
  header (#82). See *What this record covers* below.
- **The handoff keeps its offer and loses its form.** The classifier and its one-time callout are
  unchanged. What crosses becomes a per-viewer preference set once. The sheet keeps its one
  remaining question — which Built App — because that answer is different every time. The plan
  card carries the receipt for what crossed, so a silent handoff is still an inspectable one.
- **The word is Conversation**, in the glossary and in the UI. Identifiers and stored paths are
  not renamed: the wire already says `conversation`, and the reader is what was out of step. See
  [CONTEXT.md](../../CONTEXT.md).

## Alternatives considered

**Fix the greeting, keep the two panes.** The cheapest reading of the defect is that Build's
opening card is wrong. It is not: the card is honest about what its pane holds. Suppressing the
greeting would leave a pane that is empty for a reason the person cannot see, which is the same
defect with the sign-post removed.

**Merge the two logs into one store, and migrate.** This is the obvious shape and it was rejected
on cost of retreat, not on cost of building. A merged store means a migration, and a migration
means the arm cannot be deleted if the comparison goes the other way — the thing #61 exists to do
becomes the thing nobody dares do. A read model keeps both writers untouched and leaves no residue.
The price is that ordering has to be reconstructed at read time, which is why turns gain a stamp.

**Merge the two harness sessions.** This is what "one Conversation" implies if taken all the way,
and it is the honest long-term shape. It is out of scope here because it means reworking how the
enforcement shim scopes tools per agent — the shim gates by tool name, and one session would have
to hold both agents' permissions. Too large to build, and much too large to also be the variable
under comparison. The rolling summary is the deliberate half-measure: the agent gains the context,
the sessions stay apart. A per-turn Ask/Build toggle in the composer is the feature this defers,
and it is only coherent once the sessions are one.

**Collapse Chat and Build to a single route.** If Build is a view, one route with a panel toggle is
tidier. Rejected to keep existing links working — bookmarks and shared URLs into both modes are
already in the wild — and to keep the comparison clean, since a route change is a second variable
that would confound the thing being judged.

**Make the merged read span Projects.** Rejected as scope with no asker. It spans the Built Apps of
one Project because a Conversation lives in one Project; nothing wider was ever needed.

## What this record covers, and what stays on the tickets

The shell changed underneath this decision while it was being built, and the split is deliberate.

**In this record: the rail stops following the mode.** A rail that lists Conversations in Chat and
Built Apps in Build contradicts the sentence above it — you cannot call Build a view over the
Conversation while the rail replaces the Conversations with something else the moment you get
there. That one change is hard to reverse, it is surprising to a reader who finds ADR-0008 first,
and ADR-0008 wrote down the opposite in as many words. Left unrecorded, the next reader repairs
it back.

**Not in this record: where things sit inside the Build header.** That the app control is a
searchable list rather than a one-line selector, that Rename and Delete live behind a `…` beside
the app's name on the precedent #38 set, that the no-apps-yet guidance is promoted into the header,
that the header reserves a row for the selected app's own resources, that "Open in new tab" becomes
"Open preview in new tab" — all of it is placement. It costs an afternoon to reverse, nobody will
wonder why on earth it was done that way, and each is already argued on #82 and #86. An ADR would
add a second place to keep it correct and no reader would be better off.

The line is: **what changes the shape of the object model is recorded here; what changes where a
control sits stays on the ticket that decided it.**

Two hazards the shell change creates are worth carrying, because both are the silent-overwrite
class ADR-0008 exists to close and both are now one line of code away:

- The header selector puts the selected app within easy reach of the handoff sheet, which starts
  its target empty on purpose. Defaulting that field to the selected app would reintroduce exactly
  the overwrite #73 and ADR-0008 forbid.
- No publish control may appear in the header until publish targets the selected app (#70). Today's
  lookup publishes the Project's first app; pairing it with an app selector means picking app B and
  shipping over app A.

## What it revises in ADR-0005, and what it leaves alone

[ADR-0005](0005-build-is-per-conversation.md) made a Build turn belong to a Conversation: its own
Build session, a `conversation` field on every persisted row, and `?conversation=<id>` returning
that slice. **Every one of those mechanisms is kept and is what makes the merge possible at all.**

What is revised is where ADR-0005 stopped. It took Chat's shape as the goal — Chat is per
Conversation, so Build should be too — and having made each pane right about its own half, treated
the job as done. The goal was one Conversation, not two matching halves. So the read grows from
*slice one log* to *merge every log this Conversation touched*, and the pane stops being the
boundary of what a person can see. The tag it filters on is the one ADR-0005 introduced, unchanged.

ADR-0005's own consequences survive: a new Conversation in Build still opens an empty transcript
with no memory of earlier talk, and follow-ups still resolve inside a Conversation rather than
across them.

## It does not revise ADR-0008 — the two fit together

Read quickly, this record and [ADR-0008](0008-a-project-holds-many-built-apps.md) can look like
opposites: one says a Project holds many Built Apps and the Build surface is organised around them,
the other says the Conversation is the thing on screen. They are not. **ADR-0009 depends on
ADR-0008 rather than reversing it.**

- The merged transcript reads **across** Built Apps. It only has several logs to merge because
  ADR-0008 gave each app its own. A Conversation still does not own a Built App, and a folded build
  run names the app it built precisely because there may be more than one.
- **The handoff sheet keeps its target row, and still never preselects.** That row is ADR-0008's
  answer to silent overwrite, not part of this comparison. It survives whichever arm wins (#61).
- The Built App remains the unit that owns built code. `apps/<appId>/`, the per-app build log, the
  per-app Bindings and the immutable `appId` are all untouched.

**One consequence of ADR-0008 is revised, and only one.** ADR-0008 said "the rail's contents follow
the mode — Threads in Chat, Built Apps in Build." That is now wrong: there is one rail, it lists
Conversations, and app selection moved into the Build header (#82). ADR-0008's decision is intact;
this is the piece of chrome it chose in passing, and the reasoning above replaces it. Do not read
the rail change as a reversal of ADR-0008, and do not read ADR-0008 as still owning the rail.

## It shipped as a comparison, and the comparison is a judgement call

People have built habits around the current split UI, so the merged transcript ships **beside** it
rather than replacing it. A per-viewer preference selects the arm; the current split behaviour
stays the default. Both arms read and write the same files, so switching is safe and reversible.

**This is a judgement call, not a measured experiment.** No metric is defined, no cohort is
assigned and no data is collected. The intent is to use both arms for roughly two weeks and then
pick one — on how it feels to work in, which is the honest basis and the only one available. A
future reader should not go looking for the numbers behind the choice, because there are none.

Two permanent code paths is the cost of shipping it this way, and the deletion is what pays it
back. **#61 deletes the losing arm and the preference that selects it**, and is filed now, before
the comparison starts, precisely so it is not forgotten. What #61 must not take with it: the
preferences governing what crosses at handoff, and the sheet's target row. Neither belongs to the
comparison — the first outlives it, the second belongs to ADR-0008.

## Consequences

- **The defect closes, and its acceptance criterion becomes testable.** `handoff.md` §8's "switching
  to Chat shows the same Conversation, not a blank greeting" is delivered by the merged transcript
  in Chat (#56) and in Build (#57).
- **The merged read is the one place that knows about every app.** Every other read stays scoped to
  the selected app. If a read starts scanning the Project for anything else, that is a new decision.
- **The rolling summary is per Conversation, never per Built App.** Two Conversations can drive one
  app; keyed on the app, the second would be handed the first one's Chat.
- **Ordering degrades gracefully, once.** The Chat-then-Build splice is right for existing
  Conversations and wrong for any Conversation that interleaves the two halves — which is exactly
  what this record makes possible. Rows written from here on carry a stamp, so the fallback applies
  only to history that predates it.
- **A plan records the Conversation that produced it**, whether it was drafted at handoff or by the
  gate inside Build. The same two-entry-path asymmetry this record exists to remove had shown up
  there too.
- **Nothing migrates.** The merge is a read model. Turning the arm off leaves no residue, which is
  what makes #61 cheap in the direction it may have to go.
