---
status: accepted
extends: ADR-0038 (a found table is bound by the click, not by the agent — the same rule, one
  Asset over), ADR-0029 (a folder is the unit of the act and a file is the unit of the record),
  ADR-0020 (the working set is orientation, never context)
---

# A Dataset is found the same way, and declared by attaching

[ADR-0038](0038-a-found-table-is-bound-by-the-click-not-by-the-agent.md) settled the Data Source
half: a person who names a store and no table gets candidates and a click, rather than a refusal or
an invention. The Dataset half was left out of #179 on purpose, and this records what it turned out
to be — because it is NOT the same feature with the nouns changed.

## The dead end is real, and it is narrower than the Data Source one

A Dataset is already a bindable kind. `bind_dataset` writes `Binding(KIND_DATASET, …)` and has since
#141 — so the "which store?" question, which cost #185 a whole ticket for Data Sources, does not
exist here. What can still happen is this: the Binding is written, no files are attached, and
[ADR-0020](0020-the-working-set-is-orientation-never-context.md) keeps the working set out of the
prompt. The agent is told the app uses a Dataset and is handed no path it can read.

So it builds on data it made up, and the result looks finished. That is the same failure ADR-0038
exists to stop, reached through the other door.

## No scope, and that asymmetry is the decision

The symmetric design is a "which files" field on the Dataset Binding, mirroring the Table field on a
Data Source Binding. It is rejected.

A Data Source Binding needs a scope because the address is invisible: nothing short of querying the
catalog reveals which tables exist, so the record has to carry the answer. A Dataset has no such
gap. Its declaration already exists and already lives somewhere else — the Attachment — and
`bind_dataset` says so in as many words: *"No scope: the Scope fields belong to a Data Source, and a
Dataset is reached whole,"* and *"This is not an Attachment and does not write one… The two are the
app's two named things, and they stay two."*

Adding a scope would make a second record of one fact and put `bind_dataset` and `attach_file` in
competition to be believed. So the click on this card **attaches**. It writes the record the product
already has, on the surface that already owns it (ADR-0021).

The pleasant consequence: no new word. [ADR-0037](0037-prose-calls-it-a-table-because-scope-already-names-the-project.md)
had to invent *Table* because *scope* was taken three ways over. Here **File**, **Folder** and
**Attachment** already name every part, and `CONTEXT.md` needs nothing.

## Nothing is hidden, so there is nothing to search

`list_asset_files` already returns every file with its size, its attached state, whether the listing
was truncated, and whether the folder act is available. The Dataset tree renders it and the `@` menu
searches it. Everything ADR-0038 spent five tickets buying — the database-wide statement, the
per-session catalog cache, the two-stage ranker, the eight-connector matrix — Datasets have for
free.

That is why this spec has no ranker. Candidates are matched on file names, plus a cached
`describe.py` descriptor where one already exists, and never on a fresh read: reading files to draw
a card would rebuild exactly the cost [ADR-0029](0029-a-folder-is-the-unit-of-the-act-and-a-file-is-the-unit-of-the-record.md)
removed. A model over the ordering was weighed and rejected — it is disproportionate when the person
can already see the whole list, and the one distinction a Data Source ranker exists to make
(`MARTS` versus `STG_`) has no Dataset equivalent.

The `@` menu keeps its own rule through all of this: it offers parent rows only, and *must never
fetch a warehouse catalog*. The card is a turn's answer, not a menu, and the same line holds —
listing a Dataset must not inject the tree into the prompt.

## A truncated listing draws a card; an unwalked database did not

ADR-0038 refused rather than truncated, because a card built from the databases that happened to
answer would say "no name matched" about a warehouse nobody finished reading. That reasoning does
not carry over, and the difference is worth stating so this does not read as a reversal.

There, the gap was invisible: nothing had been read from the missing database, so the person could
not see what was absent. Here a truncated listing is a **sorted prefix** — early folders whole, late
ones cut — and ADR-0029 already treats it that way and says so out loud. The files on the card are
real files the person can see. So the card is drawn, and it names the listing as partial.

An unmounted Dataset draws a card too. Listing works without a mount (the API names files without
measuring them, so sizes come back 0) and a single-file attach still works, as a download rather
than a symlink. Only the folder act is withheld, and it is withheld for a reason that is about
progress reporting rather than about reach. Refusing here would put the person back at #181's dead
end on a Dataset whose files Sage can list perfectly well.

## The card asks even when nothing was named

The gate fires on a **bound Dataset with no Attachments**, whether or not the request's prose
mentions it. #185 settled the equivalent: the person's silence is not an answer, and asking is right
even when there is exactly one. "Build me a daily summary of calls" names no Dataset, and is exactly
the request that invents data.

It runs LAST, after the Data Source gates, and one card is drawn per turn. A Data Source with no
table is a store Sage cannot read at all; a Dataset with no attachments still lets a build start.
Running last also puts the cheaper gates first, since the Dataset listing is one platform call and a
turn that was never going to run should not pay for it.

Like every card before it, it carries `answered` — the gates this turn was already past. Without
that, two cards trade the turn back and forth: answering the Dataset card loses the flag that
answered the reset offer, which re-offers, which loses the Dataset flag. #185 found that loop; it is
cheaper to inherit the fix than to rediscover it.

And it offers a way past itself — *build without attaching anything* — for the same reason #185's
card does. An app that holds its own data is a real case, and a listing outage must not take
somebody's build hostage.

## The block says it too, because failing open must not fail silently

Every path above can end with no card: the person clicks past it, or the listing fails and the gate
falls open. The agent is then holding the exact state that makes it invent data — a Dataset in
`App.requires` with no readable path.

So the managed block says one line: this app records the Dataset and has no files attached from it,
so it cannot be read. Cheap, and it is what keeps failing open from meaning failing silently.

## Chat gets the same question and writes a different record

The mode somebody is standing in must not decide whether Sage goes and looks — ADR-0038's rule,
unchanged. What differs is only where the answer is written down. Chat has no Built App and so no
manifest to attach to, and the mechanism it already has is the `dsfile:` pin: a Dataset file joins
Session context as a chip, its line and its `describe.py` output reach the prompt, and handoff turns
Session context that names a Resource into `App.requires`.

So the Chat click writes a chip, and nothing else. Both modes ship together rather than one behind
the other — #188 shipped Chat a round after #183 and the seam between them cost #193, where the two
paths ordered their candidates differently for no reason a person could learn. There is no catalog
cost here to make Chat expensive, so the reason for splitting them is gone.

Rejected: **a scope field on the Dataset Binding.** Two records of one fact.
Rejected: **a model ranker over the files.** Disproportionate; the person can see the list.
Rejected: **file rows only, no folder act.** Rebuilds the 200-click problem ADR-0029 removed.
Rejected: **Build first, Chat later.** That is the shape that produced #193.
