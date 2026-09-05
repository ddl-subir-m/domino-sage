---
status: accepted
extends: ADR-0021 (the door rule, followed through to the list the door acts on)
revises: ADR-0011 (where the app's list lives, not the rule about where its removal lives),
         ADR-0025 (the two labels keep their words and change surface)
---

# The panel is the Project's one list

The resource panel held three lists at three scopes, stacked, under three heads: `In context` (this
Conversation's chips), `In <App name>` (one Built App's Bindings and Attachments), and
`In this project` (the [[Working set]]). Above them sat a plan pin, and above that a two-tab bar
offering Resources or Activity.

Every one of those was reasonable when it landed and the stack was not. A reader had to work out
which of three scopes a row belonged to before its menu made sense, the same Resource stood in two
of them at once, and the head that told them apart was an all-caps group label — the same treatment
the kind subheadings inside the list wore. The panel answered "what does this Project have",
"what does this app need" and "what can the assistant see" in one column, and looked like one list
doing it.

We decided that **the panel is the Project's working set and nothing else**: one heading, one scope,
one list. The other two scopes keep their surfaces — Session context is the chips over the composer,
the Built App's records are the App dependencies modal — and the panel says what it knows about them
as a **mark on the row**, never as a list of its own.

## What moved, and where

| Scope | Was | Is |
|---|---|---|
| Session context | `In context`, a list above the working set | The chips over the composer, plus a tick on the Project row and a `+` to add one in Chat |
| Binding, Attachment | `In <App name>`, a section above the working set | The App dependencies modal, off Build's header — which already held this app's Add and its Scope door |
| The plan | A pinned card above everything, Build only | A `Plans` group in the list, the live one accented |
| Resources / Activity | Two dock tabs | One panel. Activity has no surface; the bell still reports |
| Panel search | A `Filter this project…` box over the list, with a `Nothing matches here.` zero-state | Removed, not replaced |
| Session context empty state | `Nothing here yet. Add one below, or type @.` under `In context` | Removed with the section. A tick on a row is the only sign context exists, so nothing is said before the first chip does |
| Attachment provenance | `You added this` / `{assistant} added this`, a subtitle under an attachment row | Removed. The App dependencies row carries a name, a kind icon and its acts, and no subtitle |

The last three rows are removals this rewrite made without stating a reason, found in review and
**accepted on 2026-09-05** rather than restored. They are recorded here because the table above is
what a later reader checks against: an omission that is not written down reads as a defect, and the
next person to notice the missing search box would otherwise put it back. Accepting them is a
judgement about cost, not a claim that the panel is better without them — the filter in particular
went while the list it filtered was getting longer, and if the working set grows past what one
column can be scanned in, that is the decision to revisit first.

## Why the app's list moved rather than staying

[ADR-0011](0011-removal-lives-with-the-list-that-owns-the-scope.md) says an object is removed from
the list that owns its scope. That rule is unchanged and is now better served: the list that owns
the app's scope moved onto the app's own surface, and its removal went with it. What ADR-0011
called "a second copy of the guard" was a hazard only while there were two lists describing one
app — the modal pointed at the panel, the panel acted, and the two had to keep saying the same
sentence. There is one.

[ADR-0021](0021-each-scopes-door-lives-on-the-surface-that-owns-it.md) had already moved this app's
*additions* there, and left the panel holding a list it could not add to. That asymmetry is the
thing this closes: the surface that owns a scope owns the list, the add, the scope and the remove.

The labels do not change. [ADR-0025](0025-the-app-section-groups-by-relationship-never-by-type.md)
picked **"Needs to run"** and **"Files it carries"** because they name the app's relationship to a
thing rather than the record's own word, and made `— none` load-bearing on the surface somebody
arrives at intending to act. Both come across intact, including the empty-state halves — the modal
is that surface now.

Two things the rows gain there. Each carries the **kind as an icon**, which the panel's rows always
had and this list never did: it groups by relationship and cannot use type as a heading, but a row
saying only a name leaves "is that a Data Source or an Alias" unanswered on the one surface where it
decides what the Scope door beside it means. And a Scope is **readable where it stands** — the rail
ellipsised it and needed a tooltip to rescue the tail, which is the half of `DWH.MARTS` versus
`DWH.MARTS_ARCHIVE` that identifies it; a 480px modal does not.

## Why context is a mark and not a list

A chip and a panel row were two accountings of one `context.json`, and the chips are the one people
act on — they sit over the composer, where the prompt is being written, and ADR-0015 already settled
that closing one discards nothing. So the panel stops keeping the second copy and says the one thing
the chips cannot: *whether the Resource you are looking at right now is one of them.*

A tick, with a tooltip naming the chips. Adding is a `+` on hover, in Chat only — Session context is
a door the panel is allowed to own (ADR-0021's table) and Build has no Conversation on screen for
the verb to name (#147). Removing is not on the row's face: it is on the row's menu and in the
details drawer behind it, whose button is a toggle now rather than a dead `In this chat`.

## Why the plan is a row

A plan is app-specific — a plan document carries an `appId` — and the panel is Project-scoped and
stable across apps, so a `Plans` group looks like a category error. It is not: the documents are the
Project's, they live in `.sage/plans/`, `GET /api/plans` has always listed them all, and each one
names the app it belongs to. A project-scoped list can hold an app-scoped artifact honestly as long
as the row says whose it is, which is the same thing `Used by 2 apps` does one group down.

What the pinned card had and a plain row does not is the answer to *which plan is this build reading
right now*. That is kept as an accent on one row, under one rule with two answers, because the two
modes stand in different things: Build stands in an app, so it is the document `plan.md` was copied
from; Chat stands in a Conversation, so it is that Conversation's plan. The row also carries a
visible **open** affordance, which no other row does — a plan opens into an editor rather than a
details drawer, and that is worth saying before the click rather than after it.

`/api/plans` had no caller before this. The pin read `/api/project/plan`, a different route
answering a different question, and the two are now refreshed together in one function: the pin says
which document is live and the listing says what documents there are, and refreshed apart a plan
would be marked live before its row existed.

## Consequences

**The panel draws a group only when it holds something — or when its listing failed.** Six kind
headings over nothing was the panel's loudest noise. The error half is not a nicety:
`GET /api/resources` carries its reason per kind on purpose, and a group hidden for being empty when
it is really unknown turns "the gateway is not answering" into "you have no models". This is also
what finally puts #161's group-level sentence on screen in the case it was written for — a kind that
errored with no rows left to hang it over — since that group is now drawn for the sentence alone.

**`EMPTY_HINT` and `.sw-group-empty` go with the branch that held them.** There is no empty group to
say "No language models here yet." over. The per-group *way in* does not go with them: #164 had
already moved that door out of the empty branch and onto the group head, where it is drawn whether
or not the group holds anything — so the door survives the branch's deletion, which is the whole
point of having moved it. Groups with no catalog behind them still get none, and `Files` joins that
list for a new reason: a file arrives by Upload, so `openCatalog('file')` has nothing to open.

**The search box is gone.** Filtering a list you can see all of is clutter; the search that matters
is inside Browse Domino, over the catalogue you cannot see.

**There is one control that hides the panel, and it is on the panel.** There were two, sixty pixels
apart, in different containers, drawn with the same chevron. Re-opening is the collapsed dock's own
button plus ⌘/, which the help drawer already advertises.

**`dockTab` keeps `'activity'` as a legal stored value.** `prefs.get` refuses a value it does not
recognise, so dropping it would read back as the fallback — a *closed* panel — for exactly the
people who left theirs open. It is migrated on read instead.

**The bind receipt points at App dependencies.** A pointer names its destination in the words the
reader will see on the way to it (ADR-0011); it said "Project resources, under {app}" and that place
no longer exists.

**The heading is `In this project`, not `Resources`.** The list holds Assets as well as Resources,
and Plans since this change, so "resources" is a claim about the contents that most of them do not
meet — which is why `CONTEXT.md` has it on the working set's _Avoid_ list. The dock tab that said it
is gone, so the two names for one list collapse onto the one that claims no type.

**`SW.ActivityFeed` is left in `components/collab.js` with no caller.** The tab was its only door.
It is kept rather than deleted because nothing about the feed was decided here — only that a panel
about the Project's things is not where it goes.
