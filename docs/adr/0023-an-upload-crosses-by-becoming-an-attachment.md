---
status: accepted
extends: ADR-0011 (removal doors, now including one that destroys bytes), ADR-0021 (addition doors, now including a file's)
---

# An Upload crosses by becoming an Attachment

Chat can be handed a file. Ask it a question about that file, accept the nudge, cross into Build, and
the file is not there. Every sentence on the Build page still talks about Chat: the panel row's menu
offers "Use in this chat" with no mode check
(`backend/sage/workbench/js/components/resource-panel.js:184`), and the empty app scope promises
"Chat's resources and files land here after Open Builder" (`backend/sage/workbench/js/util.js:169`),
which for this file is false.

The mechanism chose that, not a person. `_promote_chat_file`
(`backend/sage/orchestrator/service.py:4686`) is the only thing that carries a Chat file across a
confirmed handoff, and it returns early unless the item has both a `datasetId` and a
`datasetRelPath` (`:4702`). A Dataset file that Chat fetched to answer a question has both, so it
crosses. A file a person handed to the composer has neither — `upload_scratch` (`:9367`) writes it to
`.sage/scratch/<name>` with no Dataset behind it at all. So the two look identical on screen and only
one of them is real in Build.

The root cause is a missing noun. `CONTEXT.md` defined **Attachment** — app-scoped, in the committed
manifest, rehydrated at publish — and gave the composer's file no name whatsoever, listing "upload"
only as a word to avoid. Three surfaces then guessed independently.

We decided that **an Upload is a named thing that becomes an Attachment when a handoff is
confirmed**, that **it becomes one by being written into a writable Dataset**, and that **it refuses
to cross rather than crossing in a form a published app cannot read**.

## What crosses, and how

`promote_scratch_to_dataset` (`:9381`) already does the work: it hands the bytes to `upload_file`,
which writes them under the Dataset's `uploads/` folder with `source: "upload"` (`:9342`) and attaches
the result like any Dataset file. That is the shape the whole removal story below depends on, so the
crossing reuses it rather than inventing a second path.

Crossing rides the handoff sheet's existing Resources toggle, which already gates
`_promote_chat_file` (`:4621`). No new control: an Upload is one of Chat's resources, and a person who
turns Resources off is answering this question too. The confirm receipt names the file, so the
crossing is something you can see happened rather than something you trust happened.

The Upload stays in scratch. `promote_scratch_to_dataset` unlinks its source and `_promote_chat_file`
deliberately does not — the two disagreed, and this settles it the second way. Moving the bytes would
leave the Conversation's chip pointing inside one Built App's tree, and a Conversation outlives any
one of a Project's apps ([ADR-0008](0008-a-project-holds-many-built-apps.md)). The cost is the bytes
existing twice, which we accept.

## Rejected: the two ways that avoid needing a Dataset

**Copying the bytes into the app's `public/data/` with a manifest entry carrying no `dataset_id`.**
`public/data/` is gitignored (`_ensure_gitignored`), so publish would have nothing to rehydrate from.
The dashboard works in the preview and the published app has no data. A failure discovered at publish
is worse than one discovered at the crossing.

**Symlinking scratch into the app tree.** Same ending, less work: the build agent can read the file,
the published app cannot.

Both were rejected for the same reason, so when no writable Dataset is mounted the Upload does not
cross and the receipt says so — "`<file>` stayed in Chat — no writable Dataset is mounted here". The
row keeps its own "Add to `<app>`" door, so recovery is one click and permanently on screen, which is
worth more than a durable warning: the handoff itself is not durable
([ADR-0007](0007-the-plan-document-is-durable-the-handoff-is-not.md)) and a receipt scrolled past is
gone.

This is a refusal where `_promote_chat_file` currently logs a warning and carries on (`:4712`). That
was right for its own case — losing one Dataset file is not worth losing a handoff — and it is wrong
here, because the file is the reason the person opened Chat.

## Two copies, two doors, and one that destroys bytes

An Upload and an Attachment are two things in two scopes, so ADR-0011 gives each its own door on the
list that owns it. What is new is that one of those doors deletes data.

| Scope | Bytes | Door | Server |
| --- | --- | --- | --- |
| Conversation | — | "Stop using here" | drops the chip |
| Project | `.sage/scratch/<name>` | "Delete file" | unlinks the scratch bytes |
| Built App | `public/data/…` symlink | "Remove from `<app>`" | `detach_file` (`:9269`), keeps the Dataset bytes |
| Dataset | `uploads/<name>` | "Delete from `<Dataset>`" | `delete_file` (`:9434`), deletes them |

The last row is why this ADR exists. Confirming a handoff writes a person's file into a Domino
Dataset, which is shared, durable, and outside anything Sage owns. A crossing that can put bytes
there and not take them back is not a defensible default.

`delete_file` was already written and already correct: it deletes the Dataset bytes only for files
under a Sage-created `uploads/` or `sensitive/` folder (`_delete_upload_bytes`, `:9463`), never a
pre-existing Dataset file, and it refuses with `DataReferenced` when the app's code still reads what
you are deleting. It had no caller in the Workbench and no test.

Removing a declaration and destroying bytes stay two items rather than one, styled the way
[ADR-0011](0011-removal-lives-with-the-list-that-owns-the-scope.md) styles a destructive act: last,
below a divider, danger. The Project-scope delete needs no such guard and no turn lock — after a
crossing the Attachment symlinks to the Dataset mount, not to scratch, so deleting the Upload cannot
break a Built App, and scratch sits outside the app tree a turn's baseline watches (`:2201`).

## The rows stay duplicated, and get labelled instead

After a crossing the same file appears three times in Build: once under "In `<app>` → Attachments",
and twice under "In this project → Files", once as the Upload and once as the `public/data/` entry
(`backend/sage/workbench/js/store.js:477`, `:484`). This is already true today of a Dataset file that
crossed.

We rejected collapsing them. The `public/data/` row is what `collectTurnRefs` (`store.js:941`) reads
to resolve `@data.csv` in a Build turn, so dropping it would silently stop mentions working — the
exact class of bug that function was written to end. And the Upload row is not a duplicate of the
Attachment: they are the two things this ADR just named.

So each row states its own scope, and the structural cleanup — one row per scope, with the @ menu
drawing the app's files from the app section — is filed separately rather than folded in here.
