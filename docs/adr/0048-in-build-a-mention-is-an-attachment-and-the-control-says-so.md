---
status: accepted
extends: ADR-0021 (the door rule, carried down from the surface to the control on it),
         ADR-0029 ("Chat gains no folder act", carried down a grain to the file)
revises: ADR-0035 (Attachment provenance comes back, for the reason its own table said was missing),
         ADR-0043 (the declared lock's scope, left unstated there, is the selected app's)
---

# In Build, a mention is an attachment, and the control says so

A person picked a Dataset file in the rail. The control said **Use here**, and its glossary term is
`Use in this conversation` (`resource-tree.js:307`). What happened was that Sage copied the file
into the Built App, wrote a committed manifest entry that rehydrates it at publish, and armed the
sensitivity lock. Nothing on screen said any of it.

Four consequences followed from one click, and the fourth had no way out:

1. `add_thread_context` forked on `inBuild` and called `attach_file` (`service.py:6417`).
2. The Attachment armed the lock through ADR-0043's second door (`_datasets_in_scope`).
3. The Dataset was later removed from the working set. `remove_project_resource` allowed it, because
   it asks which apps *bind* a Resource and which chats hold a *chip* on it, and never which apps
   carry *files* from it.
4. The lock stayed on, in Chat, naming no Dataset, with its only removal control in Build.

## The two controls, one row apart

Both ship bytes into the app and commit a publish-time rehydrate. They are drawn in the same tree,
in the same component, at two grains:

| Grain | Ink | Glossary term | Mode-gated | Confirms |
|---|---|---|---|---|
| Folder (`resource-tree.js:508`) | `Attach` | `Attach this folder to {app}` | yes, `inBuild` | yes, names the app |
| File (`resource-tree.js:290`, `:483`) | `Use here` | `Use in this conversation` | **no** | **no** |

The reason the folder act is gated is written beside it, quoting
[ADR-0029](0029-a-folder-is-the-unit-of-the-act-and-a-file-is-the-unit-of-the-record.md): *"Chat
gains no folder act. The act ships bytes into a Built App and commits it to a publish-time
rehydrate, and Chat draws no app rail."*

That argument holds word for word at file grain. Nobody ever carried it down.

We decided that **in Build, a mention of a Dataset file IS an attachment — the fork stays — and
every control that performs it says so, records who performed it, and can be undone.**

## Why the fork stays

The `inBuild` branch is not an accident, and deleting it was the first thing tried here.

**Build cannot reach scratch.** Chat's session opens on `ensure_chat_workdir`
(`service.py:7528`), which symlinks `examples/`, `.sage/scratch/` and the selected app's
`public/data/` into one cwd (`threads.py:711`). Build's session opens on
`project.app_for_turn().path` (`service.py:5386`, `:11916`, `:12682`). Nothing links scratch into
an app tree, so a scratch path handed to a Build turn resolves inside `apps/<id>/` and finds
nothing.

**The built app fetches over HTTP.** `_promote_chat_file` states it (`service.py:7352`): the app a
Thread becomes "is a static build that fetches `data/<slug>/<name>` over HTTP, and only
`attach_file` writes the manifest entry that rehydrates the file at publish." Vite serves
`public/`, so the location is a runtime dependency and not a convenience.

**The bytes never publish; the record does.** `public/data/` is gitignored on purpose
(`service.py:7512`). `.sage/attachments.json` is committed, and `_rehydrate_attached`
(`service.py:5203`) rebuilds the links from it at the far end. Location and durability are two
separate decisions that `attach_file` makes in one call, which is why the act is heavier than it
looks from the control that fires it.

## What the act owes

**The label, where there is one.** The fork fires only for a row that is `kind: "file"` carrying a
`datasetId` and a `datasetRelPath` (`service.py:6410`), so exactly two controls reach it: the
Dataset **file** leaf in the rail tree (`resource-tree.js:483`) and the composer `@`-menu
(`composer.js:393`). Only the leaf has a label. In Build it reads `Attach`, with `Attach file to
{app}` as both its tooltip and its aria-label; in Chat it keeps `Use here` / `Use in this
conversation`, which is true there. The folder tooltip shortens to `Attach to {app}` so
the pair reads as one act at both grains — two phrasings for one act at two grains is the drift
[ADR-0030](0030-a-mention-names-one-file-or-says-what-else-it-matched.md) exists to stop. The leaf's
hover moves from the native `title` attribute to the `Tooltip` the folder row already uses.

The ink stays short. `LeafRow` already settled that (`resource-tree.js:298`): the glossary term is
the title and the aria-label, never the ink, because a 320px dock spends its width on the file's
name.

**The receipt, where there is no label.** An `@`-mention has nothing to rename. ADR-0021 requires
the act to say what it did afterwards, "naming the scope and the way back", and this path says
nothing. It gains a mark on the composer chip — `In {app}` — and not a toast: a toast is gone in
eight seconds and this fact outlives the Conversation. The chip is already on screen and already
names the file.

**The record.** `attach_file` writes `dataset_id`, `dataset`, `file`, `path`, `size`, `source` and
`dataset_rel_path` (`service.py:843`). No author. The chip one layer up carries `addedBy`
(`api.js:534`) and the fork drops it, passing only `dataset_id` and `rel`. It now carries `added_by`
and the Conversation id across.

This is what un-deletes ADR-0035's provenance line. That table records `You added this` /
`{assistant} added this` as removed on 2026-09-05, and flags the removal as one "made without
stating a reason, found in review" — accepted then rather than restored. The reason to restore it is
new: under ADR-0021's separation the only acts that reached an app were deliberate ones taken on the
app's own surface, so "who" was answerable by standing there. This fork is the exception nobody
reconciled with that, and it is exactly the case where a person cannot reconstruct the answer.

## The way out

`remove_project_resource` (`service.py:13814`) gains a third holder question, through
`_dataset_is_attached` (`service.py:1055`) — a predicate that already exists for two other readers
and was never asked here. It scans every Built App, the way `_apps_that_bind` does, and not
`project.attached`, which is one app's.

It is a fourth argument on the existing `ResourceStillBound` rather than a new exception, because
the rule at `service.py:13835` is that both questions are asked before either refuses: two holders
produce one refusal, and reporting one would send a person off to fix it and straight back into a
second refusal they were never warned about. A third class does not get to break that.

The sentence rolls up by Dataset folder. A folder attach writes one entry per file (ADR-0029), so
the refusal must survive a hundred of them; `FOLDER_COLLAPSE_THRESHOLD` and the truncation at
`service.py:1364` already answer that and are reused rather than reinvented.

## The lock's words

**ADR-0043 never says what scope the declared lock has.** It calls `SensitivityGate` "per Project"
and speaks of "the Project's Datasets" (`0043:430`), which reads as Project-wide and is the reading
this bug was filed under. The code is narrower: `_datasets_in_scope` (`service.py:13360`) reads
`project.workspace.read_bindings()` and `project.attached`, and `Project.attached` is the app on
screen (`service.py:3284`). **It is the selected app's**, and that is stated here because an
unstated scope is what let a reader assume the wider one. The sentence is corrected rather than
the code, because the lock's job is to cover the read paths a turn actually has: a Build turn's cwd
is one app, and a Chat turn reaches exactly that app's `public/data/` through the symlink
`_ensure_dir_link` re-points when the selection changes (`threads.py:739`). Widening it to every app
in `apps/` would refuse turns over rows they cannot read. Changing the selected app does drop a
`declared` lock, and the sticky bit is what keeps that from being a hole.

**The notice names the Dataset under `declared`.** `sensitivity_state` already returns `datasets`
(`service.py:14194`); `LockNotice` reads it only for a plural count (`composer.js:133`) and prints
the brand word instead. `declaredPhrase` (`util.js:192`) already builds the named sentence and
already runs in the model picker, so one surface names the Dataset and the other does not. The
anonymity stays for `reason: "session"`, which is the case ADR-0043 argued it for — there the row is
gone and naming it sends a person to remove something already removed.

**The notice carries a pointer into the App dependencies modal**, with the app. ADR-0021 keeps the
act on the surface that owns the scope, and ADR-0011 already blessed pointers that name their
destination in the words the reader will see on arrival.

## Considered options

**Delete the `inBuild` fork; a file chip is always Session context.** Rejected on the two facts
above: Build cannot read a scratch path, and the built app fetches its data by a served path. This
was the first answer and it was wrong.

**Attach on use — land the bytes, withhold the manifest entry until the app's source reads the
file.** Rejected on `_data_usage` (`service.py:16244`), whose own docstring says `refs` "errs
towards *used* and always has". It is a text scan for the served path, falling back to the bare
basename. Its false positives are harmless; its false negative is not — an app that builds
`` `data/${slug}/${file}` `` at runtime matches nothing, the entry is never written, and the
published app 404s on its own data. [ADR-0010](0010-publish-reads-the-declaration-not-the-code.md)
already draws this line, and the codebase already confines `_data_usage` to warnings on removal.

**Link `.sage/scratch/` into the app tree so Build can read a scratch path.** Rejected. The
mechanism exists (`_ensure_dir_link`) and the outcome is worse: the agent writes code against a path
the published app cannot fetch. That trades a silent attach for a silent publish break.

**A confirmation on the attach.** Rejected for ADR-0021's own reason — a confirm "taxes the repeat
user on every repetition", and separation plus a receipt is learned once. The folder act's
confirmation stays where it is because it moves a whole subtree.

**A rail row for stranded attachments.** Rejected under
[ADR-0035](0035-the-panel-is-the-projects-one-list.md): the panel is the working set and nothing
else. It also does not work — a Dataset removed from the working set has no row left to hang an act
on, which is the same shape as the lock it was meant to release.

## Consequences

- **One gesture now reads differently in two modes, on purpose.** `Use here` in Chat and `Attach` in
  Build are the same call with two contracts, and the contract is the mode. A reader who makes them
  identical again reopens this.
- **The `@`-menu is the one door whose weight is carried by a receipt rather than a label.** There is
  nothing to rename, so the chip's mark is load-bearing rather than decorative.
- **An Attachment written before this records no author.** `added_by` is absent on every existing
  entry, and the row says nothing rather than guessing. `_rehydrate_attached`'s symlink-scan fallback
  for pre-manifest workspaces has never carried `dataset_id` either, and still will not.
- **`_dataset_is_attached` matches on the served root when an entry records no id.** Two Datasets
  that slugify alike collide there, and the third holder question inherits it. Named rather than
  fixed: the entries that can only match by root are the pre-manifest ones above.
- **The removal refusal now has three classes to say in one sentence.** It was already the longest
  sentence in the panel, and the roll-up is what keeps a hundred-file folder from making it
  unreadable.
- **Two controls that look like doors here are not, and are deliberately untouched.** The table leaf
  one branch over (`resource-tree.js:756`) is `kind: "table"` and never reaches the fork. The details
  drawer never holds a Dataset file at all — it opens from the panel row, the catalogue and the
  command palette, which all carry working-set Resources — and its control is a toggle whose other
  half is deliberately scope-free under
  [ADR-0015](0015-the-conversation-is-not-a-removal-scope.md). Renaming either would break a pair to
  fix nothing.
- **ADR-0043's amendment about the sticky lock is unchanged.** The scope correction is about which
  Datasets are in scope, not about what happens once a turn has run under the lock.
- Language: [CONTEXT.md](../../CONTEXT.md).
