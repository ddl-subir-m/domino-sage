# Domino Datasets vs Domino Artifacts — research

Sage keeps two kinds of durable state in the project's git repo today: **conversation logs**
(`.sage/history.jsonl`, `.sage/history.md`, `.sage/threads/<id>/history.jsonl` — append-only, one line
per turn) and **generated artifacts** (`examples/<threadId>/*.png|*.table.json|*.pdf`). Two viewers of
one Domino project get two *separate* Sage Builder workspaces, so git is the only shared store. This
asks whether a Domino **Dataset** or Domino **Artifacts** should replace it.

Takeaways for a reader in a hurry:

- **Domino documents nothing about concurrent writes to a Dataset.** Not "it works", not "don't do it",
  not last-writer-wins, not locking. That is the finding, and it is stated plainly in Q1 rather than
  papered over with storage-backend inference. The one thing Domino *does* say about shared writers is
  the opposite of a guarantee: *"Domino does not serialize or isolate access to shared resources"* and
  *"Use locking mechanisms or atomic operations if multiple users write to the same resource."*
- **The premise "any project viewer sees the same files" is FALSE for Datasets.** Access is a separate
  ACL with its own roles, and Domino's own role table carries a legacy *"Relates to Project Role"*
  column explicitly marked *"only useful if you are migrating from versions of Domino earlier than 5.4
  where datasets were integrated with projects."* Domino **used to** derive Dataset access from project
  role and deliberately stopped. **This undercuts the reason for the migration.**
- **A Dataset mount is never inside the git work-tree, and its path cannot be chosen.** `/mnt/data/<name>`
  is a *sibling* of `/mnt/code`, and the mount API (`AddMountInput`) has no path field at all. So moving
  `examples/` onto a Dataset puts it outside `TurnSnapshot`'s `--work-tree` and **silently breaks the
  stop button's revert.** See Q7 — this is a hard blocker, not a caveat.
- **Domino Artifacts is not a shared live store and never can be.** `/mnt/artifacts` is hydrated at
  container start and pushed back only on a manual **Sync to Domino** — and *"Domino endpoints and Apps
  cannot persist local file system changes back to the Blob Store."* A second Builder cannot see turn N
  without a human clicking Sync and then restarting.

---

## Bottom line for scoping

**The honest answer is closer to "neither, yet" than to "pick one."**

| State | Today | Where the research lands |
|---|---|---|
| `.sage/history.jsonl`, `.sage/threads/<id>/history.jsonl` — appended per turn | git | **Dataset is possible but only after the file layout changes** to one immutable file per turn. Impossible on Artifacts. Blocked on the D-Q5 permissions probe. |
| `.sage/history.md` — full rewrite each turn | git | Derived. Stop sharing the rendered file; render per reader. |
| `examples/<threadId>/*.png`, `*.table.json`, `*.pdf` | git | **Blocked by Q7.** A Dataset mount is outside `TurnSnapshot`'s work-tree, so a stopped turn's charts would no longer be reverted. Needs a design answer before the storage question is even live. |

Three findings, in the order they kill options:

1. **Q7 kills the easy half.** `examples/` looked like the safe, obvious migration — write-once binaries,
   one writer per file, already adjacent to Sage's Dataset plumbing. But `backend/sage/workspace/snapshot.py`
   reverts a stopped turn with `git --work-tree=<workspace root> reset --hard` + `clean -fd`, and every
   Dataset mount path Domino documents is *outside* that root. Moving the charts there means a stopped
   turn leaves its PNGs behind. That is a correctness regression, not a trade-off.
2. **D-Q5 may kill the other half.** If a project collaborator does not automatically hold a Dataset
   role, a Dataset is a *narrower* sharing surface than the git repo, and the migration makes the stated
   problem worse. Domino's docs strongly imply no inheritance but never say it outright. **One live probe
   settles it. Run it before writing any code.**
3. **Domino Artifacts loses on mechanism, not on details.** Manual sync, hydrate-at-start, Apps cannot
   write at all, 10,000-file default project cap. It wins on exactly one axis — permissions follow the
   project cleanly — and that axis is the one Datasets lose.

If both probes come back badly, **stay in git** and solve the actual problem (two workspaces racing one
branch) the way Domino tells you to solve any concurrent-writer problem: *"Prefer per-user namespaces,
directories, files."* A per-workspace filename makes the git merge trivial and changes no storage.

---

# DATASETS

## D-Q1 — Does one Dataset mount read-WRITE into two workspaces in the same project at once?

### THE PLAIN ANSWER FIRST

**Domino's documentation never states whether one Dataset can be mounted read-write into two Workspaces
simultaneously, and it says nothing whatsoever about what happens on a concurrent write.** There is:

- **no** page on Dataset locking, leases, or mutual exclusion;
- **no** statement of last-writer-wins, interleaving, or torn-write behaviour;
- **no** "do not write to a Dataset from multiple executions" warning;
- **no** concurrency primitive anywhere in the `datasetrw` API — no ETag, no `If-Match`, no lock/lease
  endpoint, and in fact **no file-byte read/write endpoint at all**. The public API surface is metadata,
  grants, snapshots and tags only (verified against `spikes/domino-probes/public-api.json`); all file
  I/O goes through the mount, where the platform has no visibility.

Everything below is either (a) Domino stating that it does *not* coordinate, (b) structural evidence
that both mounts exist at once, or (c) clearly-labelled storage-backend inference. **None of it is
Domino answering the question.**

### VERIFIED (a) — Domino states, in its own words, that it does NOT serialise shared writers

From [persist-data-from-apps](https://docs.dominodatalab.com/en/latest/user_guide/b99d5d/persist-data-from-apps/),
section **Concurrency considerations**:

> *"Apps can serve multiple users at the same time, which means your app code must be designed to handle
> concurrent access safely."*
>
> **Important**
> *"Domino does not serialize or isolate access to shared resources across App users. You are responsible
> for implementing any necessary safeguards in your App code."*
>
> *"Here are important safeguards:*
> - *Use locking mechanisms or atomic operations if multiple users write to the same resource.*
> - *Prefer per-user namespaces, directories, files, or database tables to isolate individual user actions.*
> - *Watch for race conditions or state inconsistencies, especially in long-running or interactive Apps."*

and, from the same page's **Ways to persist data from an App**:

> *"Mounted Datasets: Datasets provide read/write storage and are versioned independently from your
> project files."*

**Scope caveat, stated honestly:** this passage is about *multiple users of one App process*, not two
separate executions. It is not a direct answer to D-Q1. It is included because it is the **only**
concurrency guidance in the entire Dataset corpus, it is a *"Important"* callout, and the prescription
it gives — locks, atomic ops, per-writer directories — is exactly the design decision Sage faces.

The only other place the phrase "file locks" appears anywhere in Domino's Dataset documentation is an
archived page about the per-user **scratch space** (not a Dataset), and it too pushes the problem to
user code — from
[datasets-scratch-spaces](https://archive.docs.dominodatalab.com/en/4.4/user_guide/da408f/datasets-scratch-spaces/)
(ARCHIVE, Domino 4.4), section **key properties**:

> *"If you spin up multiple, concurrent workspaces in a project, all those workspaces will see the same
> datasets scratch space."*
> *"Remember, your Scratch Space is private to you, so any file locks present are due to your actions or
> code."*

That is the closest Domino ever comes to describing two concurrent workspaces sharing one storage area,
and it says locks come from your code, not the platform.

One further caution that bears on concurrency, from
[version-data-with-snapshots](https://docs.dominodatalab.com/en/latest/user_guide/dbdbff/version-data-with-snapshots/),
section **Create a snapshot**:

> **Caution:** *"You should never modify files while a snapshot is in progress."*

with no quiescing mechanism offered, and — for Datasets specifically — snapshot duration given in the
NetApp-vs-Datasets table as *"Time scales with size (seconds to hours)."*

### VERIFIED (b) — structurally, both RW mounts do exist at the same time

Four independent facts, none of which is a concurrency guarantee, but which together mean **there is no
exclusivity mechanism to collide with**:

1. **RW is a function of the caller's role, not of exclusivity.** From
   [use-datasets-and-snapshots](https://docs.dominodatalab.com/en/latest/user_guide/6942ab/use-datasets-and-snapshots/),
   section **Mount a dataset**, Note callout:
   > *"Datasets (both imported and standard) are mounted as read-write (RW) or read-only (RO) based on
   > the role of the user running the execution."*

   Nothing conditions RW on no other execution holding it.

2. **Mounting is automatic for every execution.** Same page, section **Understand dataset paths**:
   > *"Domino executions (workspaces, jobs, apps, launchers) automatically make datasets and associated
   > snapshots from the Project available."*

3. **There is exactly ONE read-write area per Dataset, and every mount points at it.** The docs call it
   version 0 — from [define-flows](https://docs.dominodatalab.com/en/latest/user_guide/e09156/define-flows/),
   section **Flow-generated vs standalone Domino Jobs**:
   > *"version 0 of a dataset, i.e. the read-write directory is NOT considered a snapshot"*

   and the API agrees: `domino.datasetrw.api.DatasetRwDto` has a **required scalar**
   `readWriteSnapshotId` (`^[0-9a-f]{24}$`) alongside the array `snapshotIds`
   (`spikes/domino-probes/dogfood-swagger.json`). One mutable head. No per-execution branch, no
   copy-on-write, no fan-out.

4. **The storage class is required to be `ReadWriteMany`.** From
   [cluster-requirements](https://docs.dominodatalab.com/en/latest/admin_guide/25b6dc/cluster-requirements/),
   section **Long-term shared storage** — needed for project data, **Domino Datasets**, Docker images
   and backups:
   > *"This storage needs to be backed by a storage class with the following properties:*
   > - *Dynamically provisions Kubernetes PersistentVolume*
   > - *Can be accessed in ReadWriteMany mode from all nodes in the cluster*
   > - *Uses a VolumeBindingMode of Immediate"*
   >
   > *"By default, this storage class is named `dominoshared`."*

   `ReadWriteMany` is the Kubernetes access mode meaning *many pods may mount this volume read-write
   simultaneously*. Two Sage Builders are two pods.

And Domino's live semantic reinforces that the RW area is one shared present-tense location — from
[create-and-manage-datasets](https://docs.dominodatalab.com/en/latest/user_guide/0a8d11/create-and-manage-datasets/):

> *"You can modify the contents of a Dataset through the Domino application or through workload
> executions."* · *"A Dataset always reflects the most recent version of the data."*

**Conclusion for (b):** two RW mounts of one Dataset almost certainly coexist. This is a strong
structural inference from four primary sources — but it is an inference. Domino never says it.

### FLAGGED (c) — what a byte-level collision does, from the storage layer only

**This subsection is deliberately fenced off. Nothing in it is a Domino statement about Datasets.**

Domino names the backing store directly. From
[access-data-in-domino](https://docs.dominodatalab.com/en/latest/user_guide/16d9c1/access-data-in-domino/),
comparison table, row **Location**, column *Domino Datasets*:

> *"Network File System (NFS) storage or Amazon Elastic File System."*

(compare the same row for *Project artifacts*: *"Domino File System (DFS)."*) Corroborated three ways:

- [manage-data](https://docs.dominodatalab.com/en/latest/admin_guide/352624/manage-data/), **Domino Datasets**:
  > *"Datasets are network volumes mounted in the execution environment."*
- [set-up-domino-on-eks](https://docs.dominodatalab.com/en/latest/admin_guide/b5da89/set-up-domino-on-eks/),
  **Datasets storage**:
  > *"To store Datasets in Domino, you must configure an EFS (Elastic File System)."* — access point root
  > `/domino`, UID/GID 0, root permissions 777. The neighbouring **Blob storage** section covers S3, and
  > Datasets are not in it.
- [cluster-requirements](https://docs.dominodatalab.com/en/latest/admin_guide/25b6dc/cluster-requirements/):
  > *"In AWS for example, these storage requirements are handled by a class that is backed by EFS for
  > Domino Datasets, and a class that is backed by S3 for project data, backups, and Docker images."*

Note the contrast Domino draws with its **other** storage class (`dominodisk`, for ephemeral execution
volumes), which is required to be:

> *"backed by true, fully POSIX-compliance block storage (i.e., NOT NFS)"*

— Domino itself signalling that it does not regard the Dataset class as fully POSIX-compliant.

**If and only if this tenant is EFS-backed** (see UNCONFIRMED #3), AWS's own docs describe the
semantics — [EFS Features → "Data consistency in Amazon EFS"](https://docs.aws.amazon.com/efs/latest/ug/features.html)
and [EFS Quotas](https://docs.aws.amazon.com/efs/latest/ug/limits.html):

- EFS is *"through the Network File System versions 4.0 and 4.1 (NFSv4) protocol"* and *"You can access
  your EFS file system concurrently from multiple NFS clients"*
  ([how-it-works](https://docs.aws.amazon.com/efs/latest/ug/how-it-works.html)).
- Close-to-open consistency. Read-after-write consistency is offered for synchronous, **non-appending**
  writes — appending writes are excluded.
- NFSv4 byte-range locks are **advisory**; read and write operations do not check for conflicting locks.
- *"Number of locks on a single file across all instances and users: 512."*

**Answering the coordinator's three sub-questions as plainly as the evidence allows:**

| Sub-question | Answer |
|---|---|
| Last-writer-wins at *file* granularity? | **Not established.** Nothing in Domino says so. On a network filesystem the unit is the `write()` call, not the file — two writers do not "win" a whole file, they interleave at offsets. Whole-file last-writer-wins would only hold if writers used atomic rename. |
| Can partial/interleaved writes corrupt a file? | **Yes, plausibly — and specifically for the append case.** `O_APPEND` is not atomic over NFS (the client resolves end-of-file), and AWS excludes appending writes from its read-after-write guarantee. Two agents appending to one `history.jsonl` can compute the same offset and one turn silently overwrites the other. *This is inference from the storage layer, not a Domino statement, and item #2 in UNCONFIRMED is the experiment that settles it.* |
| Does Domino document ANY locking or coordination primitive? | **No. None.** Not in the docs, not in the public API, not in the `/v4` API. The only mention of locks in the whole corpus is the archived scratch-space line saying locks are *"due to your actions or code."* |

### LIVE-VERIFY

- `LIVE-VERIFY` **The head-to-head append probe — the single experiment that decides the log half.**
  Two Sage Builders, one project. Both run
  `for i in $(seq 1 5000); do echo "A-$i" >> /mnt/data/<ds>/probe.log; done` (`B-` in the second),
  simultaneously. Then `grep -c '^A-'`, `grep -c '^B-'`, and `grep -cve '^[AB]-[0-9]*$'`. A clean 10 000
  with zero malformed lines ⇒ appends survive at this size. Any loss or splice ⇒ append-only is dead.
- `LIVE-VERIFY` Whether two RW mounts actually coexist at all: start Builder A, `touch /mnt/data/<ds>/a`;
  start Builder B *without stopping A*, `touch /mnt/data/<ds>/b`; confirm both succeed and both files are
  visible from both. This is the structural claim in (b), never stated by Domino.
- `LIVE-VERIFY` Whether `flock(2)` on a Dataset path excludes across pods.
  `flock -n /mnt/data/<ds>/.lock -c 'sleep 30'` in A, then immediately in B; B must exit non-zero.
- `LIVE-VERIFY` Whether `os.replace()` is atomic across clients on the mount — the write-temp-then-rename
  pattern that would make whole-file rewrites safe. Rename atomicity across NFS clients is undocumented
  by Domino.

---

## D-Q2 — Is a Dataset mount a normal POSIX filesystem? `O_APPEND`? `tail -f`?

### VERIFIED — it is a real network filesystem, not object storage behind FUSE

Domino says so in its own vocabulary, in four places:

> *"Domino Datasets provides high-performance, versioned, and structured filesystem storage in Domino."*
> — [work-with-domino-datasets](https://docs.dominodatalab.com/en/latest/user_guide/ba5bad/work-with-domino-datasets/)

> *"Datasets are network volumes mounted in the execution environment."* · *"the data is not transferred
> to the local execution volume until user code performs read operations from the mounted volume."*
> — [manage-data](https://docs.dominodatalab.com/en/latest/admin_guide/352624/manage-data/)

> *"Domino Datasets are attached to executors as networked filesystems, removing the need to transfer
> their contents to the executor when starting a run or workspace."*
> — [datasets-best-practices](https://archive.docs.dominodatalab.com/en/4.4/user_guide/a222c9/datasets-best-practices/) (ARCHIVE 4.4)

> *"Datasets can be mounted by other Domino projects, where they are attached as a read-only network
> filesystem to that project's runs and workspaces."*
> — [how-domino-handles-large-datasets](https://docs.dominodatalab.com/en/latest/user_guide/9c819c/how-domino-handles-large-datasets/)

plus the Kubernetes evidence (a `ReadWriteMany` PersistentVolume — a kernel mount, no FUSE in the path)
and the API DTOs, which describe volumes rather than buckets: `DatasetRwStorageInfoDto{pvcName, mountPath,
subDir}` and `DatasetStorageVolumeInfoDto{pvcName, dataPlaneId, volumeType}`
(`spikes/domino-probes/dogfood-swagger.json`).

**The one place Datasets *are* exposed as objects is read-only and out-of-band:**
`GET /v4/datasets/objectstore/datasets/{datasetId}/snapshots/{snapshotId}/keys/{key}/url` (`getObjectURL`,
same spec). That surface addresses **snapshots**, not the live mount.

**So `O_APPEND` and `tail -f` are syntactically available.** `>>` works; `tail -f` works. The question is
what they guarantee.

### FLAGGED — Domino documents NONE of the semantics

The words **POSIX**, **FUSE**, **append**, **partial write**, **fsync**, and **close-to-open** do not
appear in any Dataset documentation. There is no statement about cross-execution visibility timing.
**Zero primary Domino sources.** What is known comes from the layer beneath, and only on AWS:

- **`O_APPEND` is not atomic over NFS** — the client resolves end-of-file, so two appenders race. AWS
  offers read-after-write consistency for *"non-appending writes"* only.
- **`tail -f` from a second pod lags.** NFSv4.1 attribute caching is on by default; AWS names the
  workarounds — remount with `noac`, or *"clear the attribute cache on demand by sending an NFS ACCESS
  procedure request immediately before a read."* Untuned, a follower can sit on a stale size.
- Close-to-open consistency does favour one specific pattern: a writer that **opens, writes, and closes**
  per turn is guaranteed visible to a later opener. That is the good news, and it points at file-per-turn
  rather than append-to-one-file.

### LIVE-VERIFY

- `LIVE-VERIFY` `tail -f` cross-pod lag: A appends a timestamped line per second, B follows, record the
  delay distribution. Seconds ⇒ no live-following UI; the reader must poll.
- `LIVE-VERIFY` Whether a `close()`d file in A is immediately readable in B (the close-to-open claim).

---

## D-Q3 — Durability: survives a workspace stop? Survives zero running workspaces?

### VERIFIED — yes to both. But there is a genuinely misleading doc page, and you should know about it.

**Read this trap first.** From
[manage-workspaces](https://docs.dominodatalab.com/en/latest/user_guide/0002fb/manage-workspaces/),
section **Stop a Workspace**:

> *"When you stop your Workspace, the following settings persist and will be available to you when you
> resume your workspace session: Files saved in the `/mnt` directory"*
>
> *"The following settings will not persist and will reload when you resume your stopped Workspace:
> Files outside the `/mnt` directory, including installed packages; Objects in memory; **Datasets**"*

**"Datasets" is literally listed under "will not persist."** Read against every other page the intended
meaning is that the *mount* is torn down and re-established ("reload"), not that content is lost — but
the sentence as written says otherwise and the docs never disambiguate. **Do not cite this page as
evidence of durability, and do not let anyone cite it as evidence against.**

The actual evidence, all indirect but consistent:

- A Dataset lives on a `dominoshared` PersistentVolume whose lifecycle is the deployment's, not any
  pod's ([cluster-requirements](https://docs.dominodatalab.com/en/latest/admin_guide/25b6dc/cluster-requirements/)).
- Content is editable **with no execution running at all**: *"Use the Domino UI to upload up to 50GB or
  50,000 individual files"*
  ([create-and-manage-datasets](https://docs.dominodatalab.com/en/latest/user_guide/0a8d11/create-and-manage-datasets/)).
  Something writable from a browser with nothing running is not execution-scoped.
- The recommended local workflow only makes sense if content outlives executions — from
  [work-with-local-data](https://docs.dominodatalab.com/en/latest/user_guide/305721/work-with-local-data/):
  *"Create a Dataset in your project, and write large data files to it. After the files have been written
  to the Dataset, remove them from your project files."*
- Deletion is deliberate and two-stage: marking a Dataset for deletion *"removes the Dataset and its
  associated snapshots from the originating Project and from all projects it is shared with"*, and *"A
  Domino administrator must perform the final deletion."* Nothing expires on its own.
- The only explicit "survives shutdown" sentence in the corpus is archived and about scratch spaces —
  [datasets-scratch-spaces](https://archive.docs.dominodatalab.com/en/4.4/user_guide/da408f/datasets-scratch-spaces/)
  (ARCHIVE 4.4): *"If you shutdown and launch workspaces, the datasets scratch space is exactly as you
  left it. All the contents remain"* and *"When no workspaces are running, the contents of the datasets
  scratch space can be promoted to a snapshot."*
- Sage's own live-verified note records the behaviour in practice: uploads land at
  `<writable-dataset-mount>/uploads/<name>`, are gitignored, and the **published app** rebuilds
  `public/data/` from the same mounts at `app.sh` time — *"the published app runs in the SAME Domino
  project, so the same mounts are on disk"* (local live-verified note,
  `~/.claude/projects/.../memory/sage-data-attachments.md`, "Manifest + rehydrate").

### FLAGGED

No Domino page states "Dataset content survives when no executions are running." The conclusion is
assembled. Given the `manage-workspaces` wording above, it deserves the five-minute check below rather
than assumption.

### LIVE-VERIFY

- `LIVE-VERIFY` Write a marker to `/mnt/data/<ds>/`, stop **all** workspaces, wait, read it back through
  the Domino **Data** UI with nothing running.

---

## D-Q4 — Snapshots / versions: can content be pinned, and can an older state be read back?

### VERIFIED — yes, and a snapshot is a first-class read-only mount

> *"A snapshot is a read-only, immutable record of your data at a specific point in time. You can tag
> snapshots, download them, or use them to create new versions."*
> — [version-data-with-snapshots](https://docs.dominodatalab.com/en/latest/user_guide/dbdbff/version-data-with-snapshots/)

> *"You can create as many snapshots as you need, but you cannot modify existing snapshots. Instead, you
> can create a Dataset from an existing snapshot, modify the new Dataset, and create a new snapshot."*
> — [create-and-manage-datasets](https://docs.dominodatalab.com/en/latest/user_guide/0a8d11/create-and-manage-datasets/)

**Manual, never automatic.** *"You can take a snapshot from the overview page of a NetApp Volume or
Dataset in the Domino UI"*, then *"Include all files: snapshots the entire contents"* or *"Include only
selected files: allows a partial snapshot."* CLI and REST also exist.

**The live RW area is explicitly separate from the snapshot series** — the sharpest statement is in the
Flows docs: *"version 0 of a dataset, i.e. the read-write directory is NOT considered a snapshot"*
([define-flows](https://docs.dominodatalab.com/en/latest/user_guide/e09156/define-flows/)).

**Reading an old state back is a mount, not a download.** From the path scheme (see Q7), snapshots appear
at `.../snapshots/<name>/<tag>` and `.../snapshots/<name>/<number>`, annotated *"Always mounted under the
snapshot number."* Tags are the stable handle:

> *"Tags are human-readable labels that provide a stable, friendly path to a specific snapshot when you
> mount it in an execution."* · *"Tags can be reassigned to newer snapshots as needed."* · *"Only the most
> recently added tag is used for automatic mounting in executions."*

**Cost, and why this is not a per-turn primitive.** The NetApp-vs-Datasets table gives, for Datasets:
*Snapshot speed* = *"Time scales with size (seconds to hours)"*; *Storage efficiency* = *"Snapshots
duplicate physical data at snapshot time"*; *Writable after snapshot* = *"Yes - can create a new Dataset
from a snapshot; original snapshot remains read-only."* Combined with *"never modify files while a
snapshot is in progress"*, a snapshot is **far too heavy to be a per-turn commit** and is a perfectly good
**publish-time pin**.

### VERIFIED — the API surface, reachable with Sage's existing Bearer auth

From `spikes/domino-probes/public-api.json` (`Domino Public API 6.4.0`) and the
[Platform API reference](https://docs.dominodatalab.com/en/latest/api_guide/8c929e/domino-platform-api-reference/):

| Path | operationId | Notes |
|---|---|---|
| `POST /api/datasetrw/v1/datasets/{datasetId}/snapshots` | `createSnapshot` | body `NewSnapshotV1 { relativeFilePaths: string[] }` — **required**. *"Requires Read access to the dataset and project access"* |
| `GET /api/datasetrw/v1/datasets/{datasetId}/snapshots` | `getDatasetSnapshots` | `offset`, `limit` (**defaults to 10**) |
| `GET /api/datasetrw/v1/snapshots/{snapshotId}` | `getSnapshot` | `SnapshotDetailsV1{id, datasetId, createdAt, creatorId, description, lastMounted, status}` |
| `POST /api/datasetrw/v1/datasets/{datasetId}/tags` | `addDatasetTag` | *"Tag a snapshot in this Dataset"* — `{tagName, snapshotId}`; **Update access** |
| `DELETE /api/datasetrw/v1/datasets/{datasetId}/tags/{tagName}` | `removeDatasetTag` | |
| `GET /api/datasetrw/v2/datasets` | `getDatasetsV2` | `tags` is a map `{tagName -> snapshotId}` (`DatasetRwTagsV1`) |

`SnapshotDetailsV1.status` enum: `active | markForDeletion | deletionInProgress | deleted | pending |
failed | copying`. `pending` and `copying` confirm a snapshot is asynchronous.

**Relevant negative:** there is **no file-byte endpoint** in `datasetrw`. Metadata, grants, snapshots and
tags only. All file I/O is via the mount — which is why no concurrency primitive exists (D-Q1).

Sage already parses this exact tag map: `parse_tag_snapshots` at `backend/sage/assets/provider.py:178`,
feeding `Asset.tag_snapshots` (`:48-50`).

### LIVE-VERIFY

- `LIVE-VERIFY` End-to-end snapshot latency for a realistic `examples/` tree (~20 PNGs).
- `LIVE-VERIFY` Whether `createSnapshot` accepts a directory in `relativeFilePaths` or demands every file.
- `LIVE-VERIFY` Whether a snapshot taken mid-append yields a torn line.

---

## D-Q5 — Permissions: does Dataset read access follow project sharing?

### VERIFIED — **NO. Separate ACL, three roles of its own. Domino deliberately moved away from project inheritance in 5.4.**

> *"Dataset owners can grant access to Domino users and groups. Only authorized users can import or access
> a dataset."*
> *"Dataset roles are independent of Domino's global user roles."*
> **Note** *"If you want to give all project members access to the dataset, click the Add all project
> members link."*
> **Note** *"You must restart executions to pick up permission changes."*
> — [share-datasets-securely](https://docs.dominodatalab.com/en/latest/user_guide/8f5b7e/share-datasets-securely/)

The role table, and note both which row decides mount mode **and** the legacy column:

| Role | *"Relates to Project Role"* (legacy) | Can |
|---|---|---|
| **Owner** | Dataset Author | upload/view files, update metadata · take/update/view snapshots · mark and restore for deletion · copy a snapshot to a new dataset · download · **"Mount a dataset as read-write to executions."** · edit permissions |
| **Editor** | Project Owner, Project Contributor | as Owner, **minus** editing permissions — including **read-write mount** |
| **Reader** | ResultsConsumer, ProjectImporter, LauncherUser | view files · view snapshots · copy a snapshot to a new dataset · download · **"Mount a dataset as read-only to executions."** |

> **Note** *"The `Relates to Project Role` column is only useful if you are migrating from versions of
> Domino earlier than 5.4 where datasets were integrated with projects."*

**That note is the decisive sentence in this section.** Domino is saying, in its own docs, that Dataset
access *used to* be derived from project role and that this is now legacy. The current model is explicit
grants. The *"Add all project members"* bulk-add link exists precisely because membership is not itself a
grant.

Corroborated by the API: `DatasetRwRoleV1: ["DatasetRwOwner", "DatasetRwEditor", "DatasetRwReader"]`,
granted per-target as `DatasetRwGrantV1{targetId, targetRole}` through
`POST|DELETE /api/datasetrw/v1/datasets/{datasetId}/grants` (*"Requires EditSecurity access"*), read via
`GET .../grants` → `DatasetRwGrantDetailsV1{targetId, targetName, targetRole, isOrganization}`. The `/v4`
spec adds `GET /v4/datasetrw/dataset/{datasetId}/role` (`getDatasetRwRole`) and a 10-value
`DatasetRwPermissionV1` enum (`ReadDatasetRwV2`, `UpdateDatasetRwV2`, `EditSecurityDatasetRwV2`, …).

And Domino draws the contrast itself, in one table row —
[access-data-in-domino](https://docs.dominodatalab.com/en/latest/user_guide/16d9c1/access-data-in-domino/),
row **Access control**:

> *Domino Datasets*: **"Role based."**
> *Project artifacts*: **"Per-project collaborator permissions."**

Two more operational facts:

- **Mount mode follows the executing user's Dataset role**, not the project role (D-Q1 quote).
- Mounting needs *both*: *"To mount a dataset, you'll need the appropriate Dataset role **and** you must
  be an owner or contributor on the Project you want to mount the dataset to"*
  ([use-datasets-and-snapshots](https://docs.dominodatalab.com/en/latest/user_guide/6942ab/use-datasets-and-snapshots/)).
  So a Results Consumer fails the second test regardless of Dataset role.
- The project collaborator table only marks *"Create a new Dataset in a project"* and *"Mount a shared
  Dataset in a project"* for Contributor and Owner — not Project Importer, Launcher User, or Results
  Consumer ([collaborator-permissions](https://docs.dominodatalab.com/en/latest/user_guide/7876f1/collaborator-permissions/)).

**Plainly, for the migration:** a person who can see the Sage project is not thereby able to read the
Dataset, and a **Results Consumer cannot mount one at all** — while they *can* read project files
(`Read files` = Results Consumer / Contributor / Owner). **Moving state from git to a Dataset can reduce
the set of people who can see it.**

### VERIFIED — a correction worth recording

A web-search summariser asserted that the Apps page offers an option *"Don't mount Datasets to App
filesystems"* routing access through a permission-aware Dataset API. That text **does not exist** on
[persist-data-from-apps](https://docs.dominodatalab.com/en/latest/user_guide/b99d5d/persist-data-from-apps/);
it was fabricated. The nearest real thing is the first-party
[`dominodatalab/secure-datasets`](https://github.com/dominodatalab/secure-datasets) repo, which is a
service you deploy yourself, not a product toggle. Do not design around it.

### LIVE-VERIFY — run this before writing any code

- `LIVE-VERIFY` **The decisive probe.** Create a project, let Sage create/pick its default Dataset, add a
  second user as project **Contributor**. As that user:
  `GET {DOMINO_API_HOST}/api/datasetrw/v2/datasets` and `GET {DOMINO_API_HOST}/v4/datasetrw/dataset/{id}/role`.
  Does the project's *own* Dataset appear, and with what role? Absent or `DatasetRwReader` ⇒ a shared
  read-write log on a Dataset requires Sage to call `POST .../grants`, which needs `EditSecurity` and is a
  new security-relevant surface.
- `LIVE-VERIFY` Same for a **Results Consumer**. The collaborator table implies they cannot mount at all.
- `LIVE-VERIFY` Whether *"You must restart executions to pick up permission changes"* means restart or
  full rebuild for a Sage Builder.
- `LIVE-VERIFY` Whether a published App run by A can read the Dataset when viewer B opens it. Per
  [app-security-and-identity](https://docs.dominodatalab.com/en/latest/user_guide/cb9195/app-security-and-identity/)
  the App mounts what the **publisher** can see, so this probably works — and is the same
  publisher-identity hazard already documented in `DATA-SOURCES-RESEARCH.md` Q3.

---

## D-Q7 — Where does a Dataset mount, relative to the git work-tree? **(the stop-button question)**

### VERIFIED — the mount is always a SIBLING of the working directory, never inside it

Domino publishes the path scheme for both project types.

**Git-based projects** — which is what Sage creates
(`"mainRepository": {"serviceProvider": "Github", …}`, `backend/sage/provision/domino.py:220-248`). From
[git-based-project-directory-structure](https://docs.dominodatalab.com/en/latest/user_guide/ccaee6/git-based-project-directory-structure/):

```
/mnt
├── /code                      # Git repository and default working directory
├── /data                      # Project Datasets
│   └── /{dataset-name}        # Latest version of dataset
├── /artifacts                 # Project Artifacts
├── /{external-volume-name}    # External mounted volumes
└── /imported                  # Imported Git Repos
    ├── /code
    ├── /data                  # Mounted Shared Datasets
    └── /artifacts             # Imported Project Artifacts
        └── /{imported-project-name}
```

and the RW/RO annotation, from
[use-datasets-and-snapshots](https://docs.dominodatalab.com/en/latest/user_guide/6942ab/use-datasets-and-snapshots/):

| Path (Git-based) | Mode |
|---|---|
| `/mnt/data/<name>` | **RW** |
| `/mnt/data/snapshots/<name>/<tag>` · `/…/<number>` | RO |
| `/mnt/imported/data/<name>` (from another project) | **RO** |

**DFS projects** — same page, section **Dataset paths in DFS projects**:

> *"Dataset in the project: `/domino/datasets/local/<name>` - mounted as RW; `/domino/datasets/local/snapshots/<name>/<tag>` - mounted as RO; `/domino/datasets/local/snapshots/<name>/1` - mounted as RO … Dataset imported from a different project: `/domino/datasets/<name>` - mounted as RO"*

with the tree annotated *"Read-write for owner and editor, read-only for reader"*, *"Mounted under the
latest tag"*, *"Always mounted under the snapshot number"*. The DFS working directory is elsewhere: *"By
default, the working directory is `/mnt`"*
([domino-file-system](https://docs.dominodatalab.com/en/latest/user_guide/de4abb/domino-file-system/)).

**So in both project types the Dataset mount is a sibling of the working directory, not a child of it:**

| Project type | Git / working tree | Dataset mount | Relationship |
|---|---|---|---|
| Git-based (Sage) | `/mnt/code` | `/mnt/data/<name>` | **sibling — OUTSIDE** |
| DFS | `/mnt` | `/domino/datasets/local/<name>` | **different tree — OUTSIDE** |

### VERIFIED — the mount point is fixed by Domino. You cannot choose it.

The docs frame the paths as a scheme, not a setting: *"The mounting paths in DFS projects behave
according to the following scheme"*. The API confirms it directly. From
`spikes/domino-probes/dogfood-swagger.json`, `POST /v4/datasetUi/mounts/{projectId}/imported`
(`addImportedProjectMount`):

```
AddMountInput          : { datasetId, useLatest, useTag?, useId? }        required: [datasetId, useLatest]
MountConfigViewModel   : { datasetId, useLatest, useTag?, useId?, path }  required: [datasetId, useLatest, path]
```

**The request has no `path` field.** `path` appears only in the *response*, and is required there — i.e.
the server assigns it and tells you. `DatasetRwProjectMountDto.mountPathsForProject` is likewise a
read-back array. There is no create-time or update-time path parameter anywhere in the mount API.

A note on a near-miss: `GET /v4/workspaces/{workspaceId}/project/{projectId}/getWritableProjectMounts`
returns `WritableProjectMounts{mainGitMount, importedGitMounts, mainDfsMount}` and is summarised as
*"Gets the writable mounts for this workspace. This includes the main git repo, dfs mount, and imported
git repos."* **Datasets are not in that structure.** It describes the git/DFS mounts the frontend can
sync, not Dataset mounting, and it is not a lever for relocating a Dataset.

One more caveat for this repo: `resolve_mount_roots` at `backend/sage/assets/provider.py:28` honours
`DOMINO_DATASET_MOUNT_PATH` / `DOMINO_MOUNT_PATHS`. **Those are Sage's own probe/test overrides, not
Domino-injected variables** — neither appears in Domino's documentation nor in the live workspace env
dump recorded in `DATA-SOURCES-RESEARCH.md` Q2. Setting them changes where Sage *looks*, never where
Domino *mounts*.

### VERIFIED — Artifacts DOES have a POSIX mount, and it is also outside the work-tree

Artifacts is not upload-only. From
[work-with-project-artifacts](https://docs.dominodatalab.com/en/latest/user_guide/56938d/work-with-project-artifacts/):

> *"Your Artifacts are accessible at the path `/mnt/artifacts`."* · *"if you have a file under Artifacts
> in your project called `job_output.json`, you can refer to it in your code as
> `/mnt/artifacts/job_output.json`."*
> **Tip** *"Replace `/mnt` with the `$DOMINO_WORKING_DIR` environment variable to make your code more
> portable."*

It behaves as an ordinary POSIX path inside one container — Domino's own first-party SAS harness
duplicates a running log into it
([`CDISC01_Study/domino.sas`](https://github.com/dominodatalab/CDISC01_Study/blob/prod/domino.sas):
`/* Duplicate log to /mnt/artifacts/logs */`). But `/mnt/artifacts` is a **sibling of `/mnt/code`**, so
it is outside the git work-tree exactly as `/mnt/data` is. There is **no** `DOMINO_ARTIFACTS` env var —
the documented variable is `DOMINO_WORKING_DIR` and the path is the literal `/mnt/artifacts`.

### The consequence for Sage — VERIFIED against the code, and it is a blocker

`backend/sage/workspace/snapshot.py` is explicit about its boundary:

```python
self._git_dir = workspace_root / ".sage" / "snapshots" / ".git"
…
["git", f"--git-dir={self._git_dir}", f"--work-tree={self._root}", *args]
```

with `_EXCLUDE = ["node_modules", "dist", ".sage", ".git", ".DS_Store"]` written to `info/exclude`, and

```python
def discard_changes(self) -> None:
    self._run("reset", "-q", "--hard", "HEAD")
    self._run("clean", "-fd", "-q")
```

`self._root` is the workspace root, which in a Builder is `/mnt/code`
(`export SAGE_WORKSPACE_DIR="${SAGE_WORKSPACE_DIR:-/mnt/code}"`, `environment/app.sh:23`). Therefore:

| Sage state | Path today | Inside the revert work-tree? |
|---|---|---|
| `.sage/history.jsonl`, `.sage/threads/…` | `/mnt/code/.sage/…` | **No — already excluded** via `_EXCLUDE`. Stop never reverted the log, by design. |
| `examples/<threadId>/*.png` | `/mnt/code/examples/…` | **Yes.** `reset --hard` + `clean -fd` removes a stopped turn's charts. |
| Attached data (today) | symlinks in `/mnt/code/public/data/…` → `/mnt/data/…` | Symlink is inside but **gitignored**, and `clean -fd` (no `-x`) leaves ignored files alone. Survives. |
| **`examples/` moved to a Dataset** | `/mnt/data/<ds>/examples/…` | **NO — outside the work-tree entirely.** |

**So the conversation-log half is unaffected by Q7** (it is already outside stop-revert on purpose), and
**the `examples/` half is blocked by it**. Moving generated PNGs/PDFs onto a Dataset means a stopped turn
leaves its charts on the mount, visible in the UI, unreferenced and unremovable by the stop button. That
is a silent correctness regression.

**And the obvious workaround is a half-fix.** Symlinking `/mnt/code/examples/<threadId>/x.png` →
`/mnt/data/<ds>/…` puts the *link* inside the work-tree, so `clean -fd` would delete the link (it is not
gitignored, unlike `public/data/`) — but the **bytes on the mount survive**. You get a dangling half-revert
and a slow leak of orphaned PNGs. Any real design has to give `TurnSnapshot` an explicit second cleanup
path for mount-resident artifacts, keyed on the turn id. That is new machinery, and it should be costed
before the migration is called cheap.

### LIVE-VERIFY

- `LIVE-VERIFY` Confirm the literal mount path in a live Sage Builder: `ls -la /mnt` and
  `readlink -f /mnt/data/*`. The docs give the scheme; nothing has confirmed it on this tenant for a
  Sage-created project. (Sage's own `DEFAULT_DATASET_MOUNT_ROOTS` already assumes it, unverified.)
- `LIVE-VERIFY` Whether `/mnt/artifacts` exists at all in a Sage Builder. The directory-structure doc says
  Git-based projects have it, but Sage never created an artifacts folder and it may be absent or empty.
- `LIVE-VERIFY` Whether `git clean -fd` from `TurnSnapshot` follows a symlink into `/mnt/data` (it should
  not — git treats a symlink as a file — but this is the exact behaviour the half-fix above depends on).

---

# ARTIFACTS

**Disambiguation first.** "Artifacts" names five things in Domino. Only one is a candidate.

| Concept | Is it this? | Where |
|---|---|---|
| **Project Artifacts** — non-code output; in Git-based projects a special Artifacts folder in DFS, mounted at `/mnt/artifacts` | **YES** | nav pane → Artifacts |
| MLflow / Experiment artifacts (`mlflow.log_artifact`) | No — experiment-scoped, upload API through an MLflow proxy | Experiments |
| Flow Artifacts (`Artifact(name=…, type=DATA/MODEL/REPORT)`) | No — and Flows explicitly *"Snapshots of the project code and artifacts (results) are not taken at the end of the job"* | Flows |
| Guardrails "flow artifact files" | No — governance bundles | `/v4/guardrails/*` |
| Job `artifactsInfo` | The API view of Project Artifacts | `/v4/jobs/job/{jobId}/artifactsInfo` |

> *"An artifact is a file whose purpose is not source code or a data set. Artifacts usually contain the
> output from your data analysis jobs, such as plots, charts, serialized models, and so on."*
> *"In a Git-based project, artifacts are stored in a special Artifacts folder in the DFS."*
> — [work-with-project-artifacts](https://docs.dominodatalab.com/en/latest/user_guide/56938d/work-with-project-artifacts/)

## A-Q1 — Two workspaces writing at once? What happens on conflict?

### VERIFIED — it is not a shared mount at all. It is a per-execution hydrated copy.

> *"When you first start a task in Domino that spins up a new compute resource to run your code, Domino
> hydrates the local file system on that compute resource with your Project files."* This happens when
> you start a Workspace (*"but not [when] you resume a paused Workspace"*), when a Job runs, and when
> *"a Domino endpoint [or] App starts (or restarts)."*
> — [file-syncing-and-persistence](https://docs.dominodatalab.com/en/latest/user_guide/b4f02f/file-syncing-and-persistence/)

Two workspaces hold two **independent copies** taken at two different revisions. Nothing is shared while
they run.

### FLAGGED — conflict behaviour is genuinely undocumented

**No Domino page states what happens when two executions sync conflicting artifact changes.** The only
conflict machinery Domino documents is **Git's**, for the `/mnt/code` half
([sync-changes-in-a-workspace](https://docs.dominodatalab.com/en/latest/user_guide/262fef/sync-changes-in-a-workspace/):
*"**Force my changes** overwrites remote files with changes in your workspace"*), and the CLI's
side-by-side view for `domino download`. Neither applies to DFS artifacts.

The nearest concurrency statement is a performance warning, repeated twice on the artifacts page:

> *"accessing project artifacts in your executions can impact their performance. This is because many
> events can trigger a project file sync, and running executions must wait for the sync to complete before
> they can access the data again."*

So concurrent artifact activity does not corrupt — it **stalls** other executions.

## A-Q2 — Live POSIX + append + `tail -f`, or write-then-sync?

### VERIFIED — write-then-sync, and the sync is manual

> *"When a Job completes, or when you explicitly sync work within a Workspace session, Domino persists a
> new revision of your files to the Blob store."*
> *"**Note that Domino endpoints and Apps cannot persist local file system changes back to the Blob
> Store.**"*
> — [file-syncing-and-persistence](https://docs.dominodatalab.com/en/latest/user_guide/b4f02f/file-syncing-and-persistence/)

> *"In the navigation pane of your workspace, click **File Changes**. Under **Artifacts**, expand **File
> Changes**. Enter a commit message. Click **Sync to Domino**. Domino saves your artifacts to the Domino
> File System (DFS)."*
> — [work-with-project-artifacts](https://docs.dominodatalab.com/en/latest/user_guide/56938d/work-with-project-artifacts/)

> *"Changes to files in the `/mnt` directory of your workspace will be synced to the Domino File System
> (DFS). Changes to files outside of the `/mnt` directory will not be synced."*
> — [sync-changes-in-a-workspace](https://docs.dominodatalab.com/en/latest/user_guide/262fef/sync-changes-in-a-workspace/)

Also, on Git-based projects specifically: *"Domino only synchronizes and saves artifacts to the Domino
File System (DFS)"* and *"In Git-based projects, you must manually sync code or push it to the Git
repository"*
([git-based-projects](https://docs.dominodatalab.com/en/latest/user_guide/910370/git-based-projects/)).

**Inside one execution `/mnt/artifacts` is an ordinary POSIX path** (see Q7 and the first-party SAS
example). `>>` and `tail -f` work *locally, in that container*. They convey nothing to a second workspace
until a sync **and** that workspace's next container start.

**Verdict for A-Q2: the shared-log use case is not merely unsafe here, it is impossible.**

## A-Q3 — Durability

### VERIFIED — durable once synced, immutable, never garbage-collected

> *"When persisting changes, **Domino will never destroy information.** In that sense, the Blob Store is
> an immutable revisioned file store. For example, if you edit a file, Domino adds the new version but
> doesn't delete the old one. Or if you delete a file, Domino notes that the latest version of your Project
> has it deleted, but the previous version is still accessible by reverting to a past state."*
> — [file-syncing-and-persistence](https://docs.dominodatalab.com/en/latest/user_guide/b4f02f/file-syncing-and-persistence/)

Backing store, same page: **AWS → S3**; **on-prem or other cloud → NFS-compatible NAS**. (Azure and GCP
rows are blank in the source table. The table's column header reads *"Dataset storage implementation"*
while the prose is unambiguously about the Blob Store for Project files — **treat that header as
mislabeled**; it contradicts the Datasets = EFS/NFS statements in D-Q1(c).)

**Ceilings that matter for a per-turn log:**

> *"By default, you can store 10,000 files in a Domino project and you might exceed the limit."*
> *"By default, you can only transfer individual files that are 8 GB to and from your Domino project files."*
> — [work-with-project-artifacts](https://docs.dominodatalab.com/en/latest/user_guide/56938d/work-with-project-artifacts/)

> *"Domino isn't designed [for] high performance if [there are] files more than ~10GB in total size [or]
> more than ~100,000 individual files."*
> — [file-syncing-and-persistence](https://docs.dominodatalab.com/en/latest/user_guide/b4f02f/file-syncing-and-persistence/)

And Domino's own head-to-head, from
[access-data-in-domino](https://docs.dominodatalab.com/en/latest/user_guide/16d9c1/access-data-in-domino/):

| Row | Domino Datasets | Project artifacts |
|---|---|---|
| Location | *"Network File System (NFS) storage or Amazon Elastic File System."* | *"Domino File System (DFS)."* |
| Access control | *"Role based."* | *"Per-project collaborator permissions."* |
| Intended data sizes | *"Up to ~1TB per Dataset and hundreds of TB across Datasets."* | *"Up to ~10GB."* |
| Limitations | *"Snapshots must be managed to minimize storage costs."* | *"Not performant at scale of data size or many thousands of files."* |

**Unsynced work is not durable** — stated only obliquely, about the non-`/mnt` case: *"changes to files
outside the `/mnt` directory will not persist if you stop your workspace and resume the workspace at a
later time."* Whether an **unsynced** `/mnt/artifacts` change survives a stop is not stated (UNCONFIRMED #9).

## A-Q4 — Versions

### VERIFIED — versioned by project revision, addressed by `commitId`

The whole model is one DTO, from `spikes/domino-probes/dogfood-swagger.json`:

```
ArtifactsInfoDto      : { startState: ArtifactsStartStateDto, endState: ArtifactsObjectDto, changes: string[] }
ArtifactsStartStateDto: { projectArtifacts: ArtifactsObjectDto, importedProjectArtifacts: ArtifactsObjectDto[] }
ArtifactsObjectDto    : { commitId: string, projectName: string, ownerName: string }
```

behind `GET /v4/jobs/job/{jobId}/artifactsInfo` (`getArtifactsInfo`). **A job's artifacts are a diff
between two commit ids.** Older state is reachable by revision — the public API exposes commit-addressed
file content at `GET /api/projects/v1/projects/{projectId}/files/{commitId}/{path}/content`.

**But there is no artifact product API.** `Domino Public API 6.4.0` contains **zero** paths whose name
includes `artifact` (verified against the vendored `spikes/domino-probes/public-api.json`, 220 paths).
"Artifacts" appears there only as a value in the `ProjectTemplateSourceProjectComponent` enum — a
first-class *project component* with no CRUD of its own. Everything artifact-shaped lives on `/v4`.

## A-Q5 — Permissions

### VERIFIED — follows project sharing, no second ACL. **This is where Artifacts wins.**

> *"Project artifacts [are the] files in [your] project… [Their] visibility [and] accessibility [is]
> controlled [by the] project owner."* · *"Some roles provide read-only access [to] project artifacts,
> while others provide read/write access."*
> — [work-with-project-files](https://docs.dominodatalab.com/en/latest/user_guide/d95a3c/work-with-project-files/)

The per-role table is unambiguous
([collaborator-permissions](https://docs.dominodatalab.com/en/latest/user_guide/7876f1/collaborator-permissions/)):
**Read files** → Results Consumer, Contributor, Owner. **Write files** → Contributor, Owner. And
*"Collaborators on a Project will all see the same materials within the Project"*
([collaborate-on-a-project](https://docs.dominodatalab.com/en/latest/user_guide/d7731d/collaborate-on-a-project/)).

There is **no per-artifact or per-directory ACL**. That is exactly the property the migration wanted —
and Datasets do not have it.

---

# VERDICT (Q6)

**Split them — and the split is currently "one blocked, one gated", not "two green lights."**

### `examples/<threadId>/` binaries → **Dataset in principle, BLOCKED by Q7 in practice**

Write-once, one writer per file, read by a second Builder and the published App: shaped exactly for a
`ReadWriteMany` mount, and Sage already has the plumbing (`assets/provider.py:23`,
`orchestrator/service.py:5346`, `template/react-vite/scripts/rehydrate-data.mjs`). It also gets a
chart-per-turn out of git, which is a real win.

**But every Dataset mount path Domino documents is outside `TurnSnapshot`'s `--work-tree`**, so a stopped
turn would leave its PNGs on the mount. Symlinking back in is a half-fix that leaks bytes. **This needs a
turn-scoped cleanup path for mount-resident files before it is a migration rather than a regression.**
Domino Artifacts is not a substitute: the published App can never write one, sync is manual, and the
10,000-file default cap is a ceiling a chart-per-turn product will find.

### Conversation log → **Dataset, only after the file shape changes, and only if D-Q5 clears**

`.sage/history.jsonl` as an appended file is unsafe on a Dataset (Domino documents no coordination at all;
`O_APPEND` is not atomic over NFS) and **impossible** on Artifacts. Q7 does *not* block this half —
`.sage/` is already outside stop-revert by design.

The fix is not a different store, it is the layout Domino itself prescribes — *"Prefer per-user
namespaces, directories, files"*: **one immutable file per turn**,
`<mount>/threads/<threadId>/<ts>-<turnId>.json`, written once and never reopened. Every write becomes a
create-and-close, which is the case close-to-open consistency handles cleanly, and the shared offset
disappears. `.sage/history.md` stops being shared state and becomes a per-reader render.

### When the answer is "neither — stay in git"

If the D-Q5 probe shows a project collaborator does **not** automatically hold at least `DatasetRwReader`
on the project's own Dataset, a Dataset is a *narrower* sharing surface than the git repo and the
migration makes the stated problem worse. Then either:

- keep the log in git and solve the real problem (two workspaces racing a branch) with a per-workspace
  filename so the merge is trivial — the same "partition by writer" move applied to the store you already
  have; or
- have Sage call `POST /api/datasetrw/v1/datasets/{id}/grants` at project-creation time to grant every
  collaborator `DatasetRwEditor`. That needs `EditSecurity` on the dataset and is a new,
  security-relevant surface. It deserves its own decision, not a line in a migration.

**Nothing here justifies moving both kinds of state to one place.** They have different writers, lifetimes
and concurrency shapes, and the two Domino stores are good at opposite things: Datasets share live bytes
but gate them behind a second ACL; Artifacts share permissions cleanly but cannot share live bytes at all.

---

# UNCONFIRMED — what could not be sourced, and the experiment that settles each

Ordered so each answer changes whether the next matters.

| # | Claim I could not source from a primary Domino doc | Why it matters | The experiment |
|---|---|---|---|
| 1 | **Whether a project collaborator automatically gets a Dataset role on the project's own Dataset.** Domino documents explicit grants, an *"Add all project members"* bulk link, and a legacy *"Relates to Project Role"* column — all implying no inheritance. The negative is never stated. | **Decides whether the migration helps or hurts.** If not, a Dataset is narrower than git. | Two users, one project. Add B as Contributor. As B: `GET {DOMINO_API_HOST}/api/datasetrw/v2/datasets` and `GET {DOMINO_API_HOST}/v4/datasetrw/dataset/{id}/role`. Repeat with B as **Results Consumer**. |
| 2 | **What two simultaneous `O_APPEND` writers to one Dataset file produce.** Domino documents no ordering, locking, or write semantics of any kind. | Decides whether the append-only log survives at all or must become file-per-turn. | Two Builders, one project. Both run `for i in $(seq 1 5000); do echo "X-$i" >> /mnt/data/<ds>/probe.log; done` (X = A / B) at once. Count `^A-`, `^B-`, and lines matching neither. |
| 3 | **Whether one Dataset can be RW-mounted into two live workspaces at all.** Inferred from four sources (role-based mounting, automatic mounting, single `readWriteSnapshotId`, `ReadWriteMany`) but never stated. | Prerequisite for #2. | Start Builder A, `touch /mnt/data/<ds>/a`. Start B without stopping A, `touch /mnt/data/<ds>/b`. Both must succeed and be mutually visible. |
| 4 | **Whether this tenant's Dataset storage is EFS/NFS.** Domino names EFS only for AWS; other backings are unstated. | Every AWS-sourced semantic in D-Q1(c) and D-Q2 is conditional on it. | `GET {DOMINO_API_HOST}/v4/datasetrw/storage` (`getDatasetStorages`) and `/v4/datasetrw/storage/rpc/get-available-volumes` → `DatasetStorageVolumeInfoDto.volumeType`. |
| 5 | **The literal mount path in a live Sage Builder.** Domino publishes the scheme; nothing has confirmed it on this tenant for a Sage-created git-based project. Sage's `DEFAULT_DATASET_MOUNT_ROOTS` already assumes it. | Q7's whole argument rests on `/mnt/data` being a sibling of `/mnt/code`. | In a Builder: `ls -la /mnt`, `mount \| grep -E 'mnt/(code\|data\|artifacts)'`, `readlink -f /mnt/data/*`. |
| 6 | **Whether `/mnt/artifacts` exists in a Sage Builder at all.** The directory-structure doc says Git-based projects have it; Sage has never created an artifacts folder. | Only matters if Artifacts stays a candidate — it should not. | `ls -la /mnt/artifacts` in a Builder. |
| 7 | **Whether `flock(2)` on a Dataset path excludes across pods.** Undocumented by Domino; EFS locks are advisory and capped at 512/file. | The only way to keep an appended file if #2 comes back dirty. | `flock -n /mnt/data/<ds>/.lock -c 'sleep 30'` in A, then immediately in B. B must exit non-zero. |
| 8 | **Whether `os.replace()` is atomic across clients on a Dataset mount.** Undocumented. | Decides whether write-temp-then-rename can safely replace a whole file. | A loops `write tmp; os.replace(tmp, target)` 1000×; B loops `open(target).read()` and asserts it always parses. |
| 9 | **`tail -f` cross-pod lag.** NFSv4.1 attribute caching is default-on; Domino documents nothing. | Decides whether a live-following reader is possible or the UI must poll. | A appends a timestamped line per second; B follows; record the delay distribution. |
| 10 | **Dataset snapshot latency for a realistic `examples/` tree**, and whether `createSnapshot` accepts directories in `relativeFilePaths`. Docs give only *"seconds to hours."* | Decides whether publish-time pinning needs a progress UI, and the call shape. | ~20 PNGs on the mount; time `POST /api/datasetrw/v1/datasets/{id}/snapshots` to `status == active`. Try a directory path, then explicit files. |
| 11 | **Whether a snapshot taken during a write yields a torn file.** Domino says *"never modify files while a snapshot is in progress"* but not what happens if you do. | Decides whether Sage must quiesce turns around a publish-time snapshot. | Continuous appender running; snapshot mid-flight; mount `.../snapshots/<name>/<n>` and check the last line parses. |
| 12 | **Whether an UNSYNCED `/mnt/artifacts` change survives a workspace stop/resume.** Stated only for files *outside* `/mnt`. | Only matters if Artifacts stays a candidate. | Write `/mnt/artifacts/probe.txt`, do **not** sync, stop, resume, `cat`. |
| 13 | **When a synced artifact becomes visible to an already-running second execution.** The docs' one hint — *"running executions must wait for the sync to complete before they can access the data again"* — implies eventual visibility without a restart, contradicting hydrate-at-start. | If artifacts *do* refresh live, A-Q2's verdict softens. Worth one test before dismissing Artifacts. | Two workspaces running. A writes `/mnt/artifacts/probe.txt` and syncs. B, **without restarting**, polls `ls -la /mnt/artifacts/`. |
| 14 | **Whether *"You must restart executions to pick up permission changes"* means restart or full rebuild** for a Sage Builder. | Decides the UX of granting a second viewer access. | Grant B `DatasetRwEditor` while B's Builder runs. B: `touch /mnt/data/<ds>/x`. Then restart B's workspace and retry. |
| 15 | **Whether `git clean -fd` follows a symlink into `/mnt/data`.** It should not — git treats a symlink as a file — but the half-fix in Q7 depends on it. | Decides whether symlinking artifacts back into the work-tree is even a partial answer. | Symlink `/mnt/code/examples/t/x.png` → `/mnt/data/<ds>/x.png`; run `TurnSnapshot.discard_changes()`; check the target still exists. |
| 16 | **Whether Dataset content survives with ZERO running executions.** Assembled from PV lifecycle, browser upload, and the deletion workflow; never stated. Worse, `manage-workspaces` lists *"Datasets"* under *"will not persist and will reload."* | Foundational. Cheap to settle, and the ambiguous doc page makes it worth settling. | Write a marker to `/mnt/data/<ds>/`, stop all workspaces, wait, read it back through the Domino **Data** UI. |

**Unresolved contradictions in Domino's own docs, recorded rather than resolved:**

- **Snapshot automation.** [manage-data](https://docs.dominodatalab.com/en/latest/admin_guide/352624/manage-data/)
  says *"Any data written to an output Dataset is saved by Domino as a new snapshot"*, which conflicts with
  the manual snapshot workflow throughout the user guide. Likely stale admin-guide text describing the old
  input/output Datasets model.
- **Size limits.** Current docs say *"Up to ~1TB per Dataset"*; archived 4.4 best-practices says *"There is
  no limit to the size of any individual file stored in a Domino Dataset."* Both primary; never reconciled.
- **The Blob Store table** on `file-syncing-and-persistence` has the column header *"Dataset storage
  implementation"* over prose about Project files, with Azure and GCP cells blank.

**Explicitly not inferred anywhere above:** Domino publishes **no** statement about concurrent writes to a
Dataset from two executions, **no** statement about append or streaming semantics on a Dataset, and **no**
statement about conflicting artifact syncs. Every concurrency claim in this document is either (a) Domino
saying it does *not* serialise and that locking is your job, (b) a clearly-fenced Kubernetes/AWS fact about
the named backing store, or (c) an item in the table above.

---

## Sources

### Domino documentation — Datasets
- [work-with-domino-datasets](https://docs.dominodatalab.com/en/latest/user_guide/ba5bad/work-with-domino-datasets/) — "high-performance, versioned, and structured filesystem storage"; cross-project mounting
- [create-and-manage-datasets](https://docs.dominodatalab.com/en/latest/user_guide/0a8d11/create-and-manage-datasets/) — latest-version semantics; read-only snapshots; 50GB/50,000 UI upload; deletion workflow; filename restrictions
- [use-datasets-and-snapshots](https://docs.dominodatalab.com/en/latest/user_guide/6942ab/use-datasets-and-snapshots/) — **the RW/RO mount-path scheme for DFS and Git-based projects**; "mounted as RW or RO based on the role of the user running the execution"; automatic mounting for workspaces/jobs/apps/launchers; mount requires Dataset role **and** project owner/contributor; unmount semantics
- [version-data-with-snapshots](https://docs.dominodatalab.com/en/latest/user_guide/dbdbff/version-data-with-snapshots/) — snapshot immutability; manual creation; tags and reassignment; "never modify files while a snapshot is in progress"; the NetApp-vs-Datasets table (snapshot speed "seconds to hours", "snapshots duplicate physical data")
- [share-datasets-securely](https://docs.dominodatalab.com/en/latest/user_guide/8f5b7e/share-datasets-securely/) — **the separate ACL**; Owner/Editor/Reader table; RW-vs-RO by role; "Add all project members"; "restart executions to pick up permission changes"; the pre-5.4 legacy column
- [access-data-in-domino](https://docs.dominodatalab.com/en/latest/user_guide/16d9c1/access-data-in-domino/) — **the Datasets-vs-Project-artifacts comparison table**: Location, Access control, Intended data sizes, Limitations
- [how-domino-handles-large-datasets](https://docs.dominodatalab.com/en/latest/user_guide/9c819c/how-domino-handles-large-datasets/) — "read-only network filesystem"; 10,000-file project limit; Dataset quotas
- [work-with-local-data](https://docs.dominodatalab.com/en/latest/user_guide/305721/work-with-local-data/) — the write-to-Dataset-then-remove-from-project-files workflow
- [persist-data-from-apps](https://docs.dominodatalab.com/en/latest/user_guide/b99d5d/persist-data-from-apps/) — **"Domino does not serialize or isolate access to shared resources"**; locking / atomic-ops / per-user-directory safeguards; "Datasets provide read/write storage"
- [define-flows](https://docs.dominodatalab.com/en/latest/user_guide/e09156/define-flows/) — "version 0 of a dataset, i.e. the read-write directory is NOT considered a snapshot"
- [manage-workspaces](https://docs.dominodatalab.com/en/latest/user_guide/0002fb/manage-workspaces/) — the ambiguous "Datasets … will not persist and will reload" list
- [domino-file-system](https://docs.dominodatalab.com/en/latest/user_guide/de4abb/domino-file-system/) — DFS tree; "By default, the working directory is `/mnt`"
- [datasets-scratch-spaces](https://archive.docs.dominodatalab.com/en/4.4/user_guide/da408f/datasets-scratch-spaces/) (ARCHIVE 4.4) — "multiple, concurrent workspaces … will see the same datasets scratch space"; "any file locks present are due to your actions or code"; "exactly as you left it"
- [datasets-best-practices](https://archive.docs.dominodatalab.com/en/4.4/user_guide/a222c9/datasets-best-practices/) (ARCHIVE 4.4) — "attached to executors as networked filesystems"; no file-count/size limit; tags for consumer stability

### Domino documentation — Artifacts, project files, permissions
- [work-with-project-artifacts](https://docs.dominodatalab.com/en/latest/user_guide/56938d/work-with-project-artifacts/) — artifact definition; **`/mnt/artifacts`**; `$DOMINO_WORKING_DIR`; Sync to Domino; 10,000-file / 8GB defaults; Datasets impose no file-count limit
- [git-based-project-directory-structure](https://docs.dominodatalab.com/en/latest/user_guide/ccaee6/git-based-project-directory-structure/) — **the `/mnt` tree** (`/code`, `/data`, `/artifacts`, `/imported/…`)
- [git-based-projects](https://docs.dominodatalab.com/en/latest/user_guide/910370/git-based-projects/) — "Domino only synchronizes and saves artifacts to the DFS"; manual code push; no Git LFS
- [file-syncing-and-persistence](https://docs.dominodatalab.com/en/latest/user_guide/b4f02f/file-syncing-and-persistence/) — hydrate-at-start; persist-on-complete-or-sync; **Apps cannot persist**; immutable revisioned Blob Store; S3 / NFS-compatible NAS; ~10GB / ~100,000 files
- [sync-changes-in-a-workspace](https://docs.dominodatalab.com/en/latest/user_guide/262fef/sync-changes-in-a-workspace/) — what Sync covers; Force my changes; the non-`/mnt` persistence warning
- [work-with-project-files](https://docs.dominodatalab.com/en/latest/user_guide/d95a3c/work-with-project-files/) — artifact visibility controlled by the project owner
- [collaborator-permissions](https://docs.dominodatalab.com/en/latest/user_guide/7876f1/collaborator-permissions/) — Read/Write files by role; Dataset create/mount by role
- [collaborate-on-a-project](https://docs.dominodatalab.com/en/latest/user_guide/d7731d/collaborate-on-a-project/) — "Collaborators will all see the same materials"
- [app-security-and-identity](https://docs.dominodatalab.com/en/latest/user_guide/cb9195/app-security-and-identity/) — Apps mount what the creator can access
- [track-and-monitor-experiments](https://docs.dominodatalab.com/en/latest/user_guide/da707d/track-and-monitor-experiments/) — MLflow artifacts (the *other* "artifacts")
- [define-flow-artifacts](https://docs.dominodatalab.com/en/latest/user_guide/6a57f5/define-flow-artifacts/) — Flow Artifacts (a third "artifacts")

### Domino admin documentation
- [cluster-requirements](https://docs.dominodatalab.com/en/latest/admin_guide/25b6dc/cluster-requirements/) — `dominoshared` = dynamically provisioned PV, **`ReadWriteMany` from all nodes**, used for Domino Datasets; EFS-backed on AWS; `dominodisk` = "true, fully POSIX-compliance block storage (i.e., NOT NFS)"
- [set-up-domino-on-eks](https://docs.dominodatalab.com/en/latest/admin_guide/b5da89/set-up-domino-on-eks/) — "To store Datasets in Domino, you must configure an EFS"; access point root `/domino`, 777
- [manage-data](https://docs.dominodatalab.com/en/latest/admin_guide/352624/manage-data/) — "Datasets are network volumes mounted in the execution environment"
- [domino-platform-api-reference](https://docs.dominodatalab.com/en/latest/api_guide/8c929e/domino-platform-api-reference/) — the `DatasetRw` endpoint list

### Domino API specs (vendored in this repo; fetched unauthenticated at HTTP 200)
- `/Users/subirmansukhani/Desktop/domino-sage/spikes/domino-probes/public-api.json` — `Domino Public API 6.4.0`, 220 paths. `datasetrw` v1/v2, `DatasetRwRoleV1`, `DatasetRwGrantV1`, `DatasetRwPermissionV1`, `SnapshotDetailsV1`, `NewSnapshotV1`, `DatasetRwTagsV1`. **Zero paths contain `artifact`; no file-byte endpoint in `datasetrw`.**
- `/Users/subirmansukhani/Desktop/domino-sage/spikes/domino-probes/dogfood-swagger.json` — `Domino Data Lab API v4 4.0.0`, 735 paths, `servers: [{url: "/v4"}]`. `DatasetRwDto.readWriteSnapshotId`, `DatasetRwStorageInfoDto.pvcName`, `DatasetStorageVolumeInfoDto.volumeType`, **`AddMountInput` (no `path`) vs `MountConfigViewModel` (`path` required)**, `WritableProjectMounts`, `ArtifactsInfoDto`/`ArtifactsObjectDto`, `getScratchSpaceOrDefault`, `getNumberOfActiveReadOnlySnapshots`.

### Storage layer (AWS; applies only if this tenant's Dataset class is EFS — UNCONFIRMED #4)
- [EFS — How it works](https://docs.aws.amazon.com/efs/latest/ug/how-it-works.html) — NFSv4.0/4.1; concurrent access from multiple NFS clients
- [EFS — Features → "Data consistency in Amazon EFS"](https://docs.aws.amazon.com/efs/latest/ug/features.html) — close-to-open consistency; read-after-write for **non-appending** writes; NFSv4 byte-range locks are advisory and not checked by read/write; `noac` / forced ACCESS
- [EFS — Quotas](https://docs.aws.amazon.com/efs/latest/ug/limits.html) — 512 locks per file across all instances and users

### Domino first-party code
- [`dominodatalab/CDISC01_Study` → `domino.sas`](https://github.com/dominodatalab/CDISC01_Study/blob/prod/domino.sas) — writes a running log into `/mnt/artifacts/logs/`, confirming `/mnt/artifacts` is an ordinary POSIX path *inside* one execution
- [`dominodatalab/secure-datasets`](https://github.com/dominodatalab/secure-datasets) — a self-deployed permission-aware Dataset service; **not** a product toggle (see the correction in D-Q5)

### This repo
- `backend/sage/workspace/snapshot.py:21-25,53,63` — `TurnSnapshot` uses `--git-dir=<root>/.sage/snapshots/.git --work-tree=<root>`; `_EXCLUDE = ["node_modules","dist",".sage",".git",".DS_Store"]`; `discard_changes()` = `reset --hard HEAD` + `clean -fd`
- `environment/app.sh:23` — `SAGE_WORKSPACE_DIR` defaults to `/mnt/code`; the workspace **is** the git checkout
- `backend/sage/provision/domino.py:220-248` — Sage creates **git-based** Domino projects (`mainRepository.serviceProvider: "Github"`)
- `backend/sage/assets/provider.py:23` — `DEFAULT_DATASET_MOUNT_ROOTS = ("/domino/datasets/local", "/mnt/data", "/mnt/imported/data")`; `:28` `resolve_mount_roots` and its Sage-invented `DOMINO_DATASET_MOUNT_PATH`/`DOMINO_MOUNT_PATHS` overrides; `:178` `parse_tag_snapshots`; `:48-50` `Asset.tag_snapshots`
- `backend/sage/orchestrator/service.py:5346` — uploads gate on `os.access(mount_path, os.W_OK)`; `:5358-5371` default-dataset selection
- `backend/sage/workspace/manager.py:246,299` — `.sage/history.jsonl`, `.sage/history.md`; `backend/sage/workspace/threads.py:140` — `.sage/threads/<id>/history.jsonl`
- `backend/sage/shim/chat_paths.py:83-85` — `examples/<threadId>/<slug>.png` and `<slug>.table.json` are the artifact contract
- `template/react-vite/scripts/rehydrate-data.mjs:19` — the published app resolves the same mount roots

### Local live-verified notes (the user's own observations against a real Domino tenant — trustworthy, but not vendor spec)
- `~/.claude/projects/-Users-subirmansukhani-Desktop-domino-sage/memory/sage-data-attachments.md` — uploads land on a writable Dataset mount; `public/data/` is gitignored; the published app rehydrates from the same mounts because it runs in the same project. Carries its own still-open `LIVE-VERIFY` on whether a newly created project-owned Dataset auto-mounts in both the Builder and the published App — **a prerequisite for everything here.**
- `~/.claude/projects/.../memory/sage-31-pending-live-verify.md`, `sage-43-series-live-verify.md` — live-verification status of adjacent work
- `/Users/subirmansukhani/Desktop/domino-sage/DATA-SOURCES-RESEARCH.md` — Q2's live workspace env dump (which contains **no** `DOMINO_DATASET_MOUNT_PATH`), and Q3 for the App-runs-as-publisher identity model referenced in D-Q5
