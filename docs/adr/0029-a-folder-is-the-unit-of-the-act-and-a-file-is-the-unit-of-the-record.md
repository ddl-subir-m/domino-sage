---
status: accepted
extends: ADR-0023 (an Upload crosses by becoming an Attachment — this adds the act that makes many
  Attachments at once), ADR-0011 (removal lives with the list that owns the scope — the folder's
  removal door is the app's), ADR-0021 (a door lives on the surface that owns it)
---

# A folder is the unit of the act, and a file is the unit of the record

A Domino Dataset holds folders, and those folders hold folders. A person who attaches a partitioned
Dataset — `raw/2024/…`, `raw/2025/…`, `raw/2026/…` — wants the app to carry all of it, and asked a
question that had no good answer: do I have to walk down to every file and add them one at a time?

They did. `attach_file` still takes one `path` (`backend/sage/orchestrator/service.py:10051`), the
endpoint behind it still takes one `path` (`backend/sage/workbench/js/api.js:403`), and a folder row
in the Dataset tree used to carry nothing but expand and collapse (`FolderNode`,
`backend/sage/workbench/js/components/resource-tree.js`). Two hundred files was two hundred clicks.

## The listing was never the problem

Worth saying first, because it is the thing everyone assumes is broken. `walk_files` already walks
the whole mount with `rglob("*")` and returns POSIX paths relative to the root — `raw/2026/train.csv`
— with real sizes (`backend/sage/assets/provider.py:88`). The Workbench already re-nests those paths
into a tree of arbitrary depth (`nestFiles`, `resource-tree.js`). One fetch brings all of it.

So nobody was ever navigating Domino folder by folder. They were looking at a complete tree and
clicking its leaves one at a time, because the tree offered no other act. The gap was in the act, not
in the listing, and that is why this ADR adds no listing call.

## The act is a folder, at any depth, including the root

Every `FolderNode` gains **Attach folder to `<app>`**, recursive over everything below it. The Dataset
root gets the same act, so "the whole Dataset" is this act at depth 0 rather than a second feature
with its own name and its own edge cases.

The label is not new vocabulary. The app's addition door already says `Attach to {app}`
(`backend/sage/workbench/js/store.js`) and its removal door already says `Remove from {app}`
(`backend/sage/workbench/js/components/resource-panel.js:224`). The folder acts inherit both words.

Rejected: **multi-select checkboxes**, and **attach everything the search box currently matches**.
Checkboxes are a second interaction model to build and keep consistent with the first, for a rarer
need. A filter-driven attach commits a set that changes as you type, so there is no stable thing to
name in a confirmation — and this act confirms, for the reason below.

## The act confirms, because the cap decides whether it can succeed

The folder row carries its file count and its total size, and the act confirms with both and with the
app it acts on: *Attach 43 files (12 MB) to "Revenue dashboard"?*

That is not politeness. The total attach budget is 500 MB (`SAGE_ATTACH_MAX_BYTES`,
`service.py:2946`), and the number that decides whether this act can succeed at all has to be on
screen **before** the click rather than inside the refusal after it. It is also the only act in that
tree that is not one file — the same reason **Reset app** confirms and names its app.

## No partial state, in either direction

The attach measures the subtree first. If it crosses the cap it refuses the whole act and names the
folder size, the current total, and the cap. Nothing is attached.

**Remove folder from `<app>`** mirrors it, and mirrors its refusal: detach already refuses while the
app's own source still references a file (`DataReferenced`, 409). For a folder of 200 where 3 are
still referenced, the whole detach is refused and those 3 are named. The reference scan runs once
over the set, not 200 times.

Rejected: **attach until the cap is reached, then stop and report the count**. A part-attached folder
leaves the Built App with a data directory nobody chose, which is precisely the failure the rehydrate
script's `N left to fetch` line exists to make visible — except here nobody would be told at all. A
refusal that names three numbers is a decision the person can act on; a partial success is a decision
made for them, badly.

## The record stays one entry per file

`.sage/attachments.json` gains one entry per attached file, exactly as a single attach does today.
The folder is an act, never a record.

This is the load-bearing half of the title. `template/react-vite/scripts/rehydrate-data.mjs` loops
manifest entries and symlinks each one; `detach_file` keys on a path; leak detection walks the list;
and the git commit backstop flattens `_detect_leaks` into an exclude list. All of that keeps working
untouched, and none of it learns a second entry shape.

Rejected: **one folder entry, expanded at rehydrate time on the App hardware.** It would make the
published app's data directory depend on what the mount holds at *deploy* time rather than on what
was chosen at *build* time. Those two can differ, and the app would then change without anybody
deciding. The manifest is the record that has to read when the network does not
([ADR-0010](0010-publish-reads-the-declaration-not-the-code.md)); a record that says "whatever is in
there" is not a record.

One thing did change: the write. `_descriptor` used to cache its summary by calling
`write_attachments` per file, so N single attaches rewrote the whole manifest N times. It now fills
the cache into the entry and leaves the persist to its caller (`service.py:4068`). The batched act
computes the set, then writes once — one `write_attachments`, one `_write_agents_data_block`, one
`_ensure_gitignored`, one `_rebaseline_turn`.

## What the agent is told collapses with the act

The managed block in the workspace `AGENTS.md` lists one line per Attachment and **is re-read every
turn** (`_write_agents_data_block`, `service.py:11076`). Per-file lines are right for five files and
ruinous for two hundred: the prompt then grows with the file count, on every turn, forever. That is
not a theoretical cost — attachment-driven context bloat has already wedged OpenCode mid-build once.

So above `FOLDER_COLLAPSE_THRESHOLD` (10, `service.py:719`) the block collapses to one line for the
folder: the count, the shape the files share, and the served-path pattern. Below it, per-file lines
as before. That constant is the one copy of the collapse rule; the `@` menu is handed the grouping
it produces (`menu_folder`) rather than a second copy of the loop
([ADR-0030](0030-a-mention-names-one-file-or-says-what-else-it-matched.md)).

A folder holding one file keeps its file line, above the threshold as well as below it. Naming the
file describes it exactly as well as summarising would, and better — a summary of one is the file
with its name taken off. It is not an exception either: the group is the unit, and a group of one is
the file. The same rule reaches the `@` menu ([ADR-0030](0030-a-mention-names-one-file-or-says-what-else-it-matched.md)).

The block's whole reason for existing survives the collapse, which is why the collapse is safe. It is
prescriptive because agents otherwise guess a flat `/data/<name>`, hit the SPA fallback instead of the
CSV, and "fix" it by copying the file into `src/` — which leaks data into the app's git repo, since
`public/data/` is gitignored on purpose. A pattern teaches that as well as an enumeration does. And a
folder of 200 CSVs sharing one schema is described *better* once than 200 times.

Rejected: **per-file lines with the block capped and a `+188 more` line.** It tells the agent a file
exists and then hides most of them, which is worse than either honest option.

## Bulk is offered only where the size is knowable

A Dataset this container has no mount for is readable — that is how a Dataset shared from another
Project is attachable at all — but every file comes down through `_download_attachment`, serially,
and the API listing **carries no sizes**, so those files report 0 (`provider.py:337`).

That breaks both halves of this ADR at once: the subtree cannot be measured, so the cap cannot be
pre-flighted and the confirmation has no numbers to show. So the folder act is offered on mounted
Datasets only. An unmounted folder row says why it is unavailable, and per-file attach stays there
untouched.

This closes cleanly rather than by exception: `walk_files` stats every file on a mount, so wherever
the act is offered the count and the size are always real.

Rejected: **offer it there too, streamed, with progress.** That is a second feature — a progress
channel that does not exist and an unbounded serial download — wearing the same button.

## A truncated listing refuses the act

`walk_files` used to stop at `_MAX_FILES = 5000` and return no truncation flag; the unmounted path
sliced `[:_MAX_FILES]` just as silently. The walk is sorted, so truncation cuts the tail: early
folders are complete, late ones are cut or absent, and nothing downstream can tell which is which.

A root attach on a 12,000-file Dataset would therefore have attached the 5,000 that happened to be
listed and called it all of them. So `walk_files` and `list_files` now report truncation
(`provider.py:29`, `:88`, `:337`), `list_asset_files` carries it to the Workbench, and while that
flag is set the folder act is unavailable at **every** level, with a reason — no subtree can be
proven complete. A complete listing is unchanged.

The cost is near zero and worth naming: 5,000 files inside a 500 MB cap is a 100 KB average, so the
cap would refuse most truncated Datasets anyway. This only makes the refusal deliberate instead of
accidental.

Rejected: **refuse at the root, allow subfolder acts.** Unsound. Sorted truncation gives the client no
way to know which subtrees survived intact.

## What this makes true

1. No partial state. Attach and detach both refuse whole rather than land half.
2. The prompt stops tracking the file count, by one rule that also governs the `@` menu
   ([ADR-0030](0030-a-mention-names-one-file-or-says-what-else-it-matched.md)).
3. Nothing downstream moves: rehydrate, detach, leak detection, and the commit backstop are untouched.
4. Bulk is offered only where size is knowable, so every confirmation shows real numbers.

## What stays per file on purpose

**Pin** gains no folder act. Pin promotes a few rows to the top of a menu that shows as many rows
as the collapse lets through (`workingSetFirst`, `backend/sage/workbench/js/util.js:510`); pinning
200 files promotes nothing, and would buy a third `_normalize_pin` branch (`service.py:1010`) to
achieve it. Pin stays the precision tool standing beside the bulk one.

**Chat** gains no folder act. Chat's route is `fetch_dataset_file_for_chat`, deliberately not
`public/data/` — no manifest entry, no publish consequence. Chat already answers "many subfolders"
with the whole-Dataset chip, which tells the agent to read the mount itself at any depth with nothing
landing anywhere ([ADR-0020](0020-the-working-set-is-orientation-never-context.md)). Bulk-fetching
200 files into `.sage/scratch/` to answer one question buys nothing that chip does not already give,
and spends the context budget this ADR just protected.

**The whole-Dataset chip** does not become an attach. The two acts answer different questions and
both are honest: the chip is "read this and answer me", with nothing entering the app; the folder act
is "the app ships these bytes". Since the root already carries the folder act, making the chip attach
would give one gesture two meanings — and quietly commit up to 500 MB and a publish-time rehydrate to
a question that was only asked out loud. The chip's context line names both routes
(`_chat_context_line`, `service.py:1891`): reading the mount answers the question, and Attach folder
on that Dataset is the act that puts the bytes in an app.
