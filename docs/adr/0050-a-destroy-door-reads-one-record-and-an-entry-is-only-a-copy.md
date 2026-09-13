---
status: accepted
extends: ADR-0023 (an upload crosses by becoming an attachment — the standing this reads),
         ADR-0048 (in Build a mention is an attachment — the answer for entries that predate a
         field), ADR-0029 (a folder is the unit of the act, a file the unit of the record — the
         per-app record this demotes)
---

# A destroy door reads one record, and an entry is only a copy

One control in Sage destroys data on the platform: **Delete from {dataset}**, which unlinks bytes
out of a Dataset with no undo. ADR-0023 already said when Sage may offer it — never for data Sage
did not write. What was never settled is **what Sage reads to know that**, and two answers in a row
turned out to be the same mistake at different sizes.

The first answer was the folder. `_is_sage_upload` returned True for any attachment whose path
inside the Dataset began `uploads/` or `sensitive/`, because that is where `upload_file` writes. A
Dataset can already hold a folder of either name, full of the person's own files, and every one of
them was drawn a no-undo destroy door (#274).

The second answer was the entry. Replacing the folder test with a `sage_upload` field on the
manifest record is the obvious fix and it is not enough, for a reason the folder bug hides: **an
attachment entry is a per-app copy of a claim about bytes that are not per-app.** `.sage/
attachments.json` lives in `apps/<id>/`, one Dataset file can be attached in several apps, and one
Project volume can carry more than one Workspace. Destroy the file from one holder and every other
copy goes on saying Sage wrote it — over whatever the person has written at that path since. Two
separate review findings turned out to be one defect wearing two hats: a copy outliving its subject.

## Decision

**One record is the authority: `.sage/uploads.json`, at the Project root, written by the act that
creates the fact.** `upload_file` notes `(dataset_id, path-inside-the-Dataset)` there. `delete_file`
forgets it when it destroys those bytes. Nothing else writes it.

It is its own file, at the Project root, for reasons that are each load-bearing:

- **Not a field on the entry**, because the entry does not survive what the fact must survive. Detach
  deletes the record outright, and the fact has to reach the re-attach that writes a new one.
- **Not per app**, because the bytes are not. `Workspace.path` is `apps/<id>`; a Dataset file
  attached in a second app is a second record of one thing.
- **Not beside the bytes in the Dataset**, which would be the truer fact — provenance following the
  bytes across Projects — but buys it by writing a Sage-owned file into a store Sage does not own,
  in the very folder this ADR is about. The fix for *Sage acts on data it did not write* does not
  open by writing into their data.

**The entry keeps `sage_upload`, and it is a copy with no standing of its own.** The browser has no
ledger to ask, so the control has to be drawn from something the panel holds. `_restamp_uploads`
re-derives that field from the ledger every time a manifest is read — project open and app switch,
which is the last moment before anyone can press that app's door. `source == "upload"`, the
pre-#274 spelling, is deliberately not consulted anywhere: it is a copy too, and an older one.

**The irreversible half re-asks the authority.** `delete_file` gates the unlink on the ledger, not
on the stamp it drew the control from. The stamp can be stale in a way this process cannot detect —
another Workspace on the same volume can have destroyed the file and forgotten its line since this
app was last read — and the ledger on disk is the one thing both sides share. A door that turns out
not to be one answers `bytes_removed: false`, and the panel says the data is still in the Dataset
rather than claiming a destroy that was refused.

**Entries written before all this get ADR-0048's answer, once.** A live `source: "upload"` entry IS
a record that Sage wrote those bytes, in the only place the old build kept it, so a one-time
migration reads every app's manifest and turns that spelling into ledger lines. A pre-#274
`source: "dataset"` entry gets nothing, and therefore no door, whoever wrote its bytes — there is no
record and none can be invented, which is what ADR-0048 already ruled for `added_by`. The migration
runs exactly once, marked by the ledger file's own existence, because run a second time it would
answer the same question against newer bytes.

## What this is not

It is not a claim that Sage knows which bytes are which. The ledger is keyed by **path**, and every
limit below traces to that one fact.

It is not a guard on the write door. `upload_file` still overwrites a pre-existing file at
`uploads/<name>` without asking who wrote it. That predates this and is its own decision — refuse,
rename, or warn — not a default to be chosen inside this change.

## What retires this

**Byte identity.** The day an attachment can record something that identifies the *bytes* rather
than the path — a content hash Sage computes at upload, or a Dataset-side write marker the platform
vouches for — the entry can answer on its own again, because a stale copy becomes detectable rather
than indistinguishable. That is a single condition a future reader can check, and it is the only
one: everything the ledger is standing in for reduces to *it cannot tell Sage's bytes at
`uploads/d.csv` from somebody else's bytes later written at `uploads/d.csv`.*

The removal is a procedure, not a deletion:

1. **Change the derivation, keep the field.** `_restamp_uploads` stops reading the ledger and starts
   comparing the recorded identity against the bytes on the mount. `sage_upload` keeps its name and
   its meaning; only what computes it changes. `delete_file`'s second gate compares identity too,
   rather than being dropped — a shared irreversible act should still re-ask at the moment it acts.
2. **Then, and only then, drop the ledger.** `git rm .sage/uploads.json` in each Project, delete
   `_note_sage_upload` / `_forget_sage_upload` / `_backfill_uploads_ledger` / `_sage_wrote`, and
   remove `.sage/uploads.json.*.tmp` from `_PROJECT_IGNORE`. It is not read once and kept as
   history: it holds dataset ids and paths the manifest already holds, so there is nothing in it
   worth migrating forward, and a second authority left lying beside the first is how this defect
   comes back.
3. **Do not sweep the stamps.** Committed manifests carry `sage_upload` values derived the old way.
   They are recomputed on the next read by step 1 and need no migration — but a reader who deletes
   the ledger *before* step 1 lands leaves every one of them frozen at whatever it last said, which
   is the failure this ADR exists to prevent, reintroduced by the cleanup.

## The residues, which outlive the guards that name them

Each is written beside the code that causes it. They are repeated here because whoever removes this
will be reading the ADR and not sweeping docstrings, and because **byte identity dissolves all three
at once** — which is the clearest statement of what the retirement buys.

- **A line can name bytes that left another way.** `_forget_sage_upload` covers the ways *Sage*
  empties a path. Bytes deleted from the Dataset browser, from a Workspace, or lost to a
  re-provisioned mount leave the line standing, and a file later written there by hand is stamped as
  Sage's. Narrower than the bug this ADR fixes — one exact path Sage wrote and lost, rather than a
  whole folder the person named — and not closed.
- **A mount point with nothing behind it reads as a Dataset whose file is gone.** Reachability is
  asked of the provider and of the mount root, never inferred from absence below it, so a container
  that pre-creates a mount point whose mount then failed looks like a Dataset Sage reached. It costs
  that file its door. An earlier revision probed the file's own folder for a second opinion and had
  to be removed: Sage prunes an emptied `uploads/` itself, so the probe read Sage's own tidying as
  an unreachable Dataset, kept a dead line, and told the person their data was still in the Dataset
  when it was not. Of the two ways to be wrong here, only one reaches their data — a door that
  disappears is recoverable, a door that appears over somebody else's file is not.
- **The one-time migration cannot verify what a clone cannot show it.** `public/data/` is gitignored
  and nothing rebuilds it at rehydrate, so a fresh clone has the manifest and no symlinks. Absence
  is therefore not read as proof the bytes are gone — only a *dangling* link is — which means the
  migration will note a path whose bytes had already left before the upgrade. Reading absence as
  proof instead would retire every pre-#274 door on the one shape the committed manifest exists to
  serve, which is the worse of the two.

## Consequences

- **Two spellings of one fact are now one.** `source == "upload"` no longer opens this door on any
  surface. It survives as a record of how the file arrived, and as the migration's only input.
- **A Sage upload re-attached from a DIFFERENT Project gets no door there.** The ledger is
  per-Project by choice; the cost is named rather than hidden.
- **A false negative on this door is the safe direction, and is taken every time.** An unreadable
  ledger, an unreachable Dataset, a failed note at upload: all of them cost a door and none of them
  costs an upload, a Project that will open, or a byte of the person's data.
- **The ledger is committed, and a merge can conflict in it.** Read strictly on the write path —
  a writer that degraded to empty would republish a one-line ledger and take every other file's door
  with it — and leniently on the read path, where empty costs only doors. The temp file it writes
  through is named per writer and gitignored.
- **`delete_file` can now detach without destroying, and says so.** `bytes_removed` is the first
  thing this door has ever had to report that the client could not compute itself, and `bytes_kept`
  says which of two situations kept them — a sentence naming one cause where the server has several
  is worse than one naming none, and three of these mean *Sage holds the record and kept it on
  purpose*, which is the opposite of a failure.
- **A read that loops over every app converts a per-app failure into a project-wide one.** The
  migration reads every manifest in the Project, from a path `project()` and `select_app` call
  unguarded, so a file it cannot read stops everything opening — where the same bad file, read only
  as the selected app's manifest, costs nothing until somebody selects that app. Anything reaching
  across apps from here owes a total read: not the narrowest catch that covers the case in front of
  it, but every way that read can fail.
- Language: [CONTEXT.md](../../CONTEXT.md).
