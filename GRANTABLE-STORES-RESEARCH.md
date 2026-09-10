# Which Domino stores can be granted to one person — and which Sage can write to

Answers issue **#225**, a child of map **#223** ("A conversation is stored where its audience is").

**Probed:** the live Domino REST API on `https://cloud-dogfood.domino.tech`.
**Date:** 2026-09-09.
**Credential:** the Keycloak JWT in `backend/.env` (`GATEWAY_API_KEY`), sent as
`Authorization: Bearer <jwt>`. Caller identity `subir_mansukhani`,
id `66a821b1e77f2b566a1e5534`, roles `["GovernanceAdmin","Practitioner"]`
(`GET /v4/users/self`, **200**). Not a platform admin; the 403s below are data.

**Constraint: read-only.** Every call in this document is a `GET`. Nothing was
created, shared, modified or deleted. Questions only a write can settle are listed
in *What only a write could settle*, each with the exact write.

**Sources.** Live JSON (quoted, trimmed); the platform's own OpenAPI documents
(`spikes/domino-probes/public-api.json`, 220 paths; `spikes/domino-probes/swagger.json`,
the v4 private spec, 735 paths, `servers: [{url: "/v4"}]`); this repo's own code and
its two prior probes, `PERMISSIONS-RESEARCH.md` and `DATASETS-VS-ARTIFACTS-RESEARCH.md`.
Domino documentation is cited only where it is the sole owner of a fact, and is
labelled where it is.

**Legend.** **VERIFIED** — response observed this session. **INFERRED** — spec or
first-party docs only, not observed. **UNANSWERABLE READ-ONLY** — needs a write, or a
second identity's token.

---

## The answer in one table

"Grantable to one person" means: a per-principal record naming one human, which does
**not** require that human to hold a Project role. "Sage can write" means: from inside
a running Sage Builder workspace, on the ordinary turn path.

| Store | Grant record | Names a non-collaborator? | Sage can write from a workspace? | Credential | Durable across restart? | Write cost per turn |
|---|---|---|---|---|---|---|
| **Domino Dataset** (`datasetrw`) | `{targetId, targetName, targetRole, isOrganization}` — `DatasetRwOwner / Editor / Reader` | **Yes — VERIFIED** | **Yes** — POSIX RW mount at `/mnt/data/<name>`, and a 3-call chunked upload API | mount: the workspace's own identity; API: user JWT/PAT | **Yes — VERIFIED** (bytes read back 43 days later) | ~68 KB `write()` to an RWX volume, **0 API calls, 0 git** |
| **Data Mount / External Data Volume** (`datamount`) | `{users[], projects[], readOnly, isPublic}` on `DataMountDto` | **Yes — INFERRED** (`users[]` is a bare id list, no project term) | Yes, if `readOnly:false` — a POSIX mount at a **chosen** `mountPath` | the workspace's own identity | Yes — INFERRED (a pre-existing PVC) | one `write()` |
| **Data Source** (SQL / connector) | `{isEveryone, userAndOrganizationIds[]}` — no roles | **Yes — VERIFIED** (record has no project term) | Yes in principle (the credential can write); Sage's own SQL is read-only by policy | the Data Source's own credential (Individual or Shared) | Yes — external system | 1 SQL round trip |
| **The Project's git repo / DFS** (today's store) | Project collaborator `{id, role}` | **No** — Project role *is* the grant | **Yes** — this is what Sage does now | Domino git credential (`https`, github.com) | Yes, once committed **and pushed** | ~500 B push at turn 100 (ADR-0006) |
| **A second (imported) git repo** | **None in Domino.** Domino stores a credential mapping only | n/a — the audience is the *provider's* (GitHub's), not Domino's | Yes — `/mnt/imported/code/<name>` is a writable mount | per-account Domino git credential | Yes, once pushed | ~1 push |
| **App-scoped storage** | — | — | **Does not exist.** The only App-scoped blob is the thumbnail (one image, upsert-only) | — | — | — |
| **The object store** | **None of its own** — it is the Dataset's backing store, keyed on the Dataset ACL | inherits the Dataset's answer | **Read-only** via API (`GET .../keys`), and only under a snapshot | user JWT | inherits the Dataset | n/a (no write route) |
| **Domino Artifacts** (`/mnt/artifacts`) | **None** — "per-project collaborator permissions" | **No** | No — hydrated at container start, pushed back on a manual Sync only; **Apps cannot persist back** | — | **No** for live state | n/a |

**The answer to #225.** Exactly one store satisfies all of "grantable to one named
person", "not gated on a Project role", "Sage writes it on the ordinary turn path",
"durable", and "cheap": the **Domino Dataset**. The Data Mount is the only other
candidate that clears the grant bar, and it is admin-provisioned — a Practitioner
cannot create one, so Sage cannot self-serve one per Conversation.

---

## 1. Domino Dataset — the only candidate that clears every bar

### The grant record

`GET /api/datasetrw/v1/datasets/{datasetId}/grants` (public) and
`GET /v4/datasetrw/dataset/{datasetId}/grants` (v4, bare array). Live, on the `Sage`
project's default Dataset `6a5e8b05242fc543ed242832`:

```json
[{"targetId":"66a821b1e77f2b566a1e5534","targetName":"subir_mansukhani",
  "targetRole":"DatasetRwOwner","isOrganization":false}]
```

Vocabulary `DatasetRwOwner | DatasetRwEditor | DatasetRwReader`; the target may be a
user *or* an organization (`isOrganization`). Caller-scoped reads:
`GET /v4/datasetrw/dataset/{id}/role` → `["DatasetRwOwner"]`, and
`GET /v4/datasetrw/dataset/{id}/permissions` → the 7-value permission list.
**VERIFIED.** Write routes (not executed): additive `POST` / `DELETE` on the public
`/grants`, whole-list `PUT` on the v4 one — both require `EditSecurity`.

### The grant names people with no Project role — and now, at the byte level

`PERMISSIONS-RESEARCH.md` Q2 established that a Dataset **grant record** survives with
no Project role. This probe goes one level further and shows the **content** API obeys
the same ACL, which is the fact a conversation store actually needs.

Dataset `66a93956e77f2b566a1e5a6b` (`llama3_1_peft`) sits in project
`66a93955e77f2b566a1e5a67`, which `GET /api/projects/v1/projects/{id}` reports as
**404 — could not be found** for this caller (Q2). Against that Dataset, this session:

```
GET /v4/datasetrw/dataset/66a93956e77f2b566a1e5a6b/role            → 200 ["DatasetRwOwner"]
GET /v4/datasetrw/datasets/66a93956e77f2b566a1e5a6b                → 200 full record
GET /v4/datasetrw/snapshots/66a93956e77f2b566a1e5a6b               → 200 [{version:0, isReadWrite:true, …}]
GET /v4/datasetrw/files/66a93956e77f2b566a1e5a6a?path=             → 200 {"directorySize":"0 B","rows":[]}
GET /v4/datasetrw/snapshot/66a93956e77f2b566a1e5a6a/files/recursive?path=  → 200
```

**VERIFIED.** A directory listing of a Dataset's live read/write area answered on the
Dataset grant alone, in a Project the caller cannot resolve. The recipient of a shared
Conversation therefore does not need a Builder, a mount, or a Project role — they need
a Dataset grant and an HTTP call.

### Sage can write it, and already does

Two independent write paths.

**(a) The POSIX mount — the one Sage uses.** `GET /v4/datasetrw/mounts-v2/{projectId}/local`
on the Sage project, live:

```json
{"datasetId":"6a5e8b05242fc543ed242832","snapshotId":"6a5e8b05242fc543ed242831",
 "versionNumber":0,"name":"Sage","uniqueName":"dataset-Sage-6a5e8b05242fc543ed242832",
 "mountPathsForProject":["/mnt/data/Sage"],
 "dataPlanes":[{"name":"Local","isLocal":true,"status":{"state":"Healthy"}}]}
```

**VERIFIED.** Sage's own code already writes there. `backend/sage/assets/provider.py:26`
holds the mount roots (`/domino/datasets/local`, `/mnt/data`, `/mnt/imported/data`);
`backend/sage/orchestrator/service.py:14805` and `:14821` pick a target by
`os.access(mount_path, os.W_OK)`, and `:12866` reports `writable` to the UI from the
same test; `_SAGE_UPLOAD_PREFIXES` (`service.py:1070`) is the `uploads/` and
`sensitive/` subfolders Sage writes into.

**The end-to-end proof, live.** `sage-data-explorer` (`6a6a212536ee676bc960c94d`), whose
version-0 snapshot `6a6a212536ee676bc960c94c` carries `isReadWrite: true`:

```
GET /v4/datasetrw/snapshot/6a6a212536ee676bc960c94c/files/recursive?path=   → 200
  {"directorySize":"11.8 K","rows":[{…"uploads", isDirectory:true},
    {…"uploads/synthetic_adverse_events.csv", sizeInBytes:12106,
       lastModified:1785341469421}]}
GET /v4/datasetrw/snapshot/6a6a212536ee676bc960c94c/file/raw
      ?path=uploads/synthetic_adverse_events.csv                            → 200
  Date,Drug,Severity,Event,Outcome
  2025-12-30,Lisinopril,Mild,Nausea,Recovered
  …
```

**VERIFIED, and this is the load-bearing observation of the whole document.** A Sage
Builder wrote those bytes into a Dataset's read/write area from inside a running
workspace on 2026-07-28. The workspace is long gone. The bytes came back over HTTP,
against the Dataset ACL, on 2026-09-09 — 43 days and many container lifetimes later.
Write from the workspace, read by the audience, durable: all three, observed.

**(b) The HTTP upload — no mount required.** A 3-call chunked upload keyed on
`datasetId` only, with no project id anywhere in it:

| Step | Route | Spec summary |
|---|---|---|
| start | `POST /v4/datasetrw/datasets/{datasetId}/snapshot/file/start` | *"Initializes chunked file uploads"* → returns an upload key |
| chunk | `POST /v4/datasetrw/datasets/{datasetId}/snapshot/file` | *"Sends file upload chunks"* (`resumableChunkNumber`, `resumableTotalChunks`, `checksum`, …) |
| end | `GET /v4/datasetrw/datasets/{datasetId}/snapshot/file/end/{uploadKey}` | *"Checks for destination consistency and… moves uploaded files to specified dataset directory"* (`targetRelativePath`) |

**VERIFIED** as routes in the v4 spec; **INFERRED** (not executed — it is a write) that
they land in the version-0 read/write area, which is what the `datasets/{datasetId}`
(not `snapshot/{snapshotId}`) keying and the `end` summary both say.

### Durability

The read/write area is a distinct, addressable object: `version: 0`,
`isReadWrite: true`, its own `resourceId` and `datasetStorageId`
(`GET /v4/datasetrw/snapshots/{datasetId}`, **VERIFIED** on four Datasets). The
43-day survival above is the durability evidence. `DATASETS-VS-ARTIFACTS-RESEARCH.md`
adds the structural reason (a `ReadWriteMany` network volume).

### Cost per turn to write

A `write()` of the turn's bytes to an NFS/EFS-class mount. **Zero Domino API calls,
zero git objects, zero push.** At Sage's measured ~68 KB of log per user turn
(ADR-0006), that is 68 KB of NFS I/O against today's 500 bytes of push payload plus
68 KB of local disk. The **byte** cost is the same; the **network and git-object** cost
goes to zero, and the ~11 MB `read_history()` re-parse ADR-0006 left open is untouched
by the move.

### The two constraints that shape any design on this

1. **RW only in the owning Project.** Domino's own path table
   ([use-datasets-and-snapshots], quoted at `DATASETS-VS-ARTIFACTS-RESEARCH.md:548`):
   `/mnt/data/<name>` is **RW**; `/mnt/data/snapshots/…` is RO; and
   `/mnt/imported/data/<name>` — a Dataset from *another* Project — is **RO**.
   So the Conversation store must be a Dataset **owned by the Project the Builder runs
   in**. Sharing the Dataset outward is a grant; mounting someone else's Dataset back
   in is read-only. **INFERRED** (Domino docs; not live-verified here — no second
   Project's Dataset is mounted in any workspace I can start read-only).
2. **Domino does not serialize writers.** *"Domino does not serialize or isolate
   access to shared resources"* (persist-data-from-apps, quoted in ADR-0006). There is
   no ETag, no `If-Match`, no lock or lease anywhere in `datasetrw`. ADR-0006 already
   drew the consequence: an appended log would have to become **one immutable file per
   turn** first. That conclusion is unchanged, and it is now the price of the audience
   axis rather than a reason to stay put.

---

## 2. Data Mount / External Data Volume — a seventh grant surface, missing from `PERMISSIONS-RESEARCH.md`

`PERMISSIONS-RESEARCH.md` Q1 lists six per-principal grant surfaces. There is a
seventh, and it is the only one besides the Dataset that is a *store*.

The v4 spec carries a 12-route `datamount` family. `DataMountDto`:

```json
{"id","name","description",
 "volumeType": ["Nfs","Smb","Efs","Generic"],
 "pvcName","pvId","mountPath",
 "users": ["<24-hex user id>"],      // ← the grant
 "projects": ["<24-hex project id>"],
 "readOnly": bool, "isPublic": bool, "isRegistered": bool,
 "dataPlanes": [...]}
```

**The grant record is `users[]` plus `isPublic`** — a bare list of user ids with **no
project term in it at all**, and `projects[]` is a *separate, independent* attachment
list. That is a genuine per-person grant on a writable POSIX volume, and unlike a
Dataset its `mountPath` is **chosen at registration** (`CreateDataMountRequest.mountPath`,
validated by `GET /v4/datamount/isMountPathValid`) rather than assigned by the server.
**VERIFIED** (schema) / **INFERRED** (that a `users[]` entry needs no Project role — the
record has nowhere to express one).

Live, this session:

```
GET /v4/datamount/users/66a821b1e77f2b566a1e5534        → 200 []      # I hold none
GET /v4/datamount/projects/6a5e8b03242fc543ed24282d     → 200 []      # Sage project has none
GET /v4/datamount/projects/inaccessible/6a5e8b03242fc543ed24282d → 200 []
GET /v4/datamount/paths/6a5e8b03242fc543ed24282d        → 200 "/mnt"
GET /v4/datamount/all                                   → 403
  {"required":["RegisterDataMounts"],"missing":["RegisterDataMounts"]}
GET /v4/datamount/isMountPathValid?mountPath=/mnt/sage-conversations → 403
  {"required":["RegisterDataMounts"],"missing":["RegisterDataMounts"]}
```

**VERIFIED.** A Practitioner can read their own data mounts and their Project's, and is
refused the registration surface. **`POST /v4/datamount` (`registerDataMount`) is gated
on the platform permission `RegisterDataMounts`, which this identity does not hold** —
so Sage cannot create one, and cannot create one per Conversation. It also requires a
pre-existing Kubernetes PVC (`pvcName`), which is infrastructure, not an API object.

The related consumer side is real and already visible: `externalVolumeMounts` /
`externalVolumeMountIds` appear on `LaunchWorkspaceInputs`, `CreateWorkspaceRequest`,
`StartJobRequest`, `AppInstanceDetails` and `StartParams` — a volume is selected by id
when a workspace, job or App starts. **VERIFIED** (schemas).

**Verdict.** Correct grant shape, correct write path, wrong provisioning model. A Data
Mount is an admin-issued piece of infrastructure; a Conversation store has to be
something Sage can create on the ordinary path. Worth naming in the ADR as the
enterprise-deployment variant, not as the mechanism.

---

## 3. Data Source — grantable per person, writable, but not a document store

`GET /api/datasource/v1/datasources?limit=10`, live:

```json
{"id":"65eed6d0a2b63048591607f7","name":"test","dataSourceType":"SnowflakeConfig",
 "authType":"Basic","credentialType":"Individual",
 "permissions":{"isEveryone":true,"userAndOrganizationIds":[]}}
```

**VERIFIED.** The grant record is `DataSourcePermissionsV1 = {isEveryone,
userAndOrganizationIds[]}` — per person or per organization, **no roles**, and no
project term, so it can name a non-collaborator. `DataSourceDto` does carry
`projectIds` and `addedToProjectTimeMap`, but those are attachment, not permission —
exactly the same shape split as the Data Mount.

Two things disqualify it as the Conversation store:

- **The grain is the whole connector.** There is no per-row, per-table or per-schema
  grant. Sharing one Conversation would share every Conversation in the table.
- **Sage's own SQL is read-only by policy.** `DATA-SOURCES-RESEARCH.md:1007` records
  it exactly: *"The credential can write; Sage's SQL cannot."* Making it writable is a
  new security surface, not a store choice.

One incidental finding worth recording: Domino auto-registers each Dataset as a Data
Source of `dataSourceType: "DatasetConfig"`, named `dataset-<name>-<id>` with
`authType: "NoAuth"` and a `config` of `{datasetID, snapshotID}` — visible live in the
same listing, and the same composite handle `backend/sage/assets/provider.py:59`
already builds. **VERIFIED.** The Dataset and the Data Source surfaces meet here; the
grant that governs it is still the Dataset's.

---

## 4. The Project's git repo and DFS — today's store, and the one that cannot be narrowed

This is where `ThreadStore` puts everything today: `backend/sage/workspace/threads.py:120`
roots every path at the workspace, `:126` `.sage/threads.json`, `:129`
`.sage/threads/<id>/`, `:135` `examples/<id>/`, `:607` the thread scan. Saving is
`commit_all` / `push` (`backend/sage/workspace/git.py:95,120,131`).

**Its grant is the Project role, and there is no other.** `GET /v4/projects/{id}/projectSettingsCollaborators`
returns `{collaborator, role}` over `contributor | launcherUser | resultsConsumer |
projectImporter` plus the implicit Owner. There is no per-file, per-folder or per-branch
grant anywhere in either spec. To let one named person read one Conversation you must
give them a role on the Project, which gives them **every** Conversation in it, plus the
code, plus the commit history. That is the leak #223 exists to close.

Domino says the same thing in its own comparison table
([access-data-in-domino], quoted at `DATASETS-VS-ARTIFACTS-RESEARCH.md`): *Domino
Datasets*: **"Role based."** *Project artifacts*: **"Per-project collaborator
permissions."**

There is also a byte-level write API for it —
`POST /v4/projects/{projectId}/commits/head/files/{path}` (`uploadFile`,
`multipart/form-data`, *"uploads a file to the head commit of the project's
repository"*, **201**) — but it is one commit per call and it is still Project-scoped,
so it changes the cost and not the audience. **VERIFIED** (spec; not executed).

**Cost, for comparison:** ADR-0006 measured 500 bytes of push payload per turn at turn
100 (287 bytes at turn 10), against 64 KB for a single chart PNG. Git is *cheap*. It is
the audience that is wrong, which is exactly the axis ADR-0006 says it never weighed.

---

## 5. A second (imported) git repo — the audience is GitHub's, not Domino's

Routes exist and are real: `GET,POST /v4/projects/{projectId}/gitRepositories`
(`addGitRepo`, body `{uri, name, ref, serviceProvider}` — spec example
`https://github.com/acme/my-repo.git`), plus browse/branches/commits/raw and
`PUT /v4/projects/{projectId}/gitRepositories/{repositoryId}/ref`. Live on the Sage
project: `GET /v4/projects/6a5e8b03242fc543ed24282d/gitRepositories` → **200 `[]`**,
and the public `GET /api/projects/v1/projects/{id}/repositories` → **200**
`{"repositories":[],"metadata":{…"totalCount":0}}`. **VERIFIED.**

**Domino holds no grant record for it.** What Domino stores is a *credential mapping*
(`GET,PUT,DELETE /v4/projects/{projectId}/repository/{repoId}/credentialMapping`) and a
per-account credential. Live:

```
GET /v4/accounts/66a821b1e77f2b566a1e5534/gitcredentials  → 200
[{"id":"66a848d1ecadae7f043a537f","name":"Subir DDL","gitServiceProvider":"github",
  "domain":"github.com","fingerprint":"3b:0f:…:11","protocol":"https"}]
```

**VERIFIED.** So "who can read this repo" is answered by GitHub, with GitHub's
vocabulary, against GitHub identities — which are not Domino user ids and may not exist
for a given Domino user at all. A design that stores Conversations in a second repo
inherits a second identity system and a per-user credential that Domino only *proxies*.
(This repo already knows the credential is fragile: ADR-0033, "a git credential is
proven by use, not by identity".)

Writability from the workspace is fine — `getWritableProjectMounts` is summarised in
the spec as *"Gets the writable mounts for this workspace. This includes the main git
repo, dfs mount, and imported git repos"* (`WritableProjectMounts{mainGitMount,
importedGitMounts, mainDfsMount}`) and the mount root is `/mnt/imported/code`
(`DATASETS-VS-ARTIFACTS-RESEARCH.md:528` reproduces Domino's `/mnt` tree). **VERIFIED**
(spec summary) — the writability is not the problem; the grant is.

---

## 6. App-scoped storage — it does not exist

Every App-facing route across both specs, checked by inventory: `apps` / `modelProducts`
offer versions, instances, logs, real-time logs, views, thumbnails, access requests,
grant/deny/invite/uninvite, visibility, start/stop, timeseries, totals, vanity URLs.
**There is no route that stores App-scoped data.** **VERIFIED** by full path inventory
of all 955 paths.

The single exception is a decorative one: `GET,POST,DELETE /api/apps/beta/apps/{appId}/thumbnail`
(*"Create or replace an App's thumbnail"*, `getAppThumbnail` returns
`application/octet-stream`). It is one image, upsert-only, with no path and no
versioning. It is not a store. **VERIFIED** (spec).

An App's filesystem is the Project's files plus whatever Datasets it mounts —
`mountDatasets` is `true` on all 305 live Apps, and `PERMISSIONS-RESEARCH.md` Q4 already
established it is a **runtime mount switch, not a grant**. Anything an App wants to
persist has to go to a Dataset; that is Domino's own advice
(*"Mounted Datasets: Datasets provide read/write storage"*, persist-data-from-apps).

**Consequence for #223.** "The Conversation lives with the App" is not available as a
storage decision. The App's audience (`permissionsData.accessRequestStatuses`, live and
re-verified this session on `Ask` — `{"681285aad58d16494212c8f2":"ALLOWED"}` with
`visibility: GRANT_BASED`) is a real per-person grant, but it grants *access to a
running process*, not to any bytes.

---

## 7. The object store — the Dataset's backing store, not a surface of its own

Three routes, all `GET`, all nested under a Dataset **and** a snapshot:

```
GET /v4/datasets/objectstore/datasets/{datasetId}/snapshots/{snapshotId}/keys
GET /v4/datasets/objectstore/datasets/{datasetId}/snapshots/{snapshotId}/keys/{key}
GET /v4/datasets/objectstore/datasets/{datasetId}/snapshots/{snapshotId}/keys/{key}/url
```

Live, on the Sage Dataset's read/write snapshot:

```
GET …/keys                            → 400 {"message":"Missing parameter: prefix"}
GET …/keys?prefix=                    → 400 {"message":"Missing parameter: page_size"}
GET …/keys?prefix=&page_size=10       → 200 {"keys":[]}
```

**VERIFIED.** It answers, it pages, and it is **read-only** — there is no `PUT`, `POST`
or `DELETE` anywhere in the family. It has **no ACL of its own**: it is reached through
a `datasetId`, so the grant that governs it is the Dataset's. `PERMISSIONS-RESEARCH.md`
Q1 reached the same conclusion from the route inventory ("snapshots have… object-store
key routes, but no ACL of their own"); this adds the live 200 and the read-only
finding.

The admin-side storage surface is closed to a Practitioner:
`GET /v4/datasetrw/storage/rpc/get-available-volumes` → **403**,
`"does not have 'perform_dataset_actions_as_admin' permission on 'platform:domino'"`.
**VERIFIED.**

---

## 8. Domino Artifacts (`/mnt/artifacts`) — no grant, no API, cannot hold live state

`/mnt/artifacts` is a real POSIX path and a sibling of `/mnt/code`
(`DATASETS-VS-ARTIFACTS-RESEARCH.md:528` and following; `backend/sage/tools/dataset_probe.py:58`
already probes `("/mnt/artifacts", "/mnt/imported/artifacts")`). But:

- **No API.** A full-text sweep of both specs for artifact routes returns only
  `GET /v4/jobs/job/{jobId}/artifactsInfo`, two `guardrails` bundle routes and
  `POST /v4/datasetrw/dataset/{datasetId}/snapshotFromFlowsArtifacts`. There is no
  project-artifacts read, write, list or grant route at all. **VERIFIED** by inventory.
- **No grant record.** Domino's own access-control table says *Project artifacts*:
  **"Per-project collaborator permissions."** Same audience as the git repo, so it
  narrows nothing.
- **Not durable for live state.** ADR-0006 already rejected it on this: `/mnt/artifacts`
  is hydrated at container start and pushed back only on a manual Sync, and *"Domino
  endpoints and Apps cannot persist local file system changes back to the Blob Store."*

---

## 9. Checked and rejected as stores

| Surface | Why not |
|---|---|
| **AI Gateway endpoint** (`{isEveryoneAllowed, userIds[]}`) | A real per-person grant, but it grants *inference*. No bytes at rest. |
| **Model Deployment** (`{id, role}` — `CONSUMER`/`OWNER`) | A real per-person grant on a *service*. No document storage. |
| **MLflow** (`/v4/mlflow/*`, 5 read routes) | Execution provenance, scoped to a run/project. No grant record of its own. |
| **Project Goals / comments** (`projectManagement`, 54 routes) | Genuinely writable prose with `assignableUsers` — but the container is the Project, so the audience is the Project's. |
| **Per-user "scratch space"** | Domino 4.4 concept. `GET /v4/datasetUi/scratchspace/{projectId}/files` → **404** on this deployment (HTML "Not Found" page, i.e. no such route). **VERIFIED** — dead. |

---

## Where this changes `PERMISSIONS-RESEARCH.md`

Nothing in that document is contradicted. Three things are extended, and one sentence
in `DATASETS-VS-ARTIFACTS-RESEARCH.md` needs correcting.

1. **Six grant surfaces are seven.** The `datamount` family (Data Mount / External Data
   Volume) is a per-principal grant surface with its own record (`users[]` + `isPublic`),
   its own routes, and — uniquely — a *writable POSIX volume at a chosen mount path*
   behind it. It is missing from Q1's table. It is admin-gated (`RegisterDataMounts`),
   which is presumably why it did not surface: `GET /v4/datamount/all` 403s, but
   `GET /v4/datamount/users/{userId}` and `/datamount/projects/{projectId}` both answer
   200 to a Practitioner.

2. **Q2 is now true at the byte level, not only at the record level.** Q2 proved a
   Dataset *grant* survives with no Project role. This document proves the *content*
   routes obey it: a directory listing of the read/write area of a Dataset in an
   unresolvable Project returned **200**. That is the fact the store decision rests on.

3. **`DATASETS-VS-ARTIFACTS-RESEARCH.md` line 76 needs a scope fix.** It says there is
   *"no file-byte read/write endpoint at all"* for Datasets. That is true of
   `public-api.json`, which is what it was checked against, and its own sentence says so
   — but it reads as a claim about the platform, and the **v4 spec has both halves**:
   the 3-call chunked upload (`snapshot/file/start` → `snapshot/file` → `snapshot/file/end`)
   and the reads (`files/{snapshotId}`, `snapshot/{id}/files/recursive`,
   `snapshot/{id}/file/raw`, `/file/preview`, `/file/meta`), all live-verified above.
   The concurrency conclusion drawn there is unaffected — there is still no ETag, no
   `If-Match`, and no lock anywhere in the family — but "all file I/O goes through the
   mount, where the platform has no visibility" is not correct, and a recipient reading
   a shared Conversation will be using exactly those API routes.

---

## What only a write could settle

Each names the exact write.

1. **Does a new Dataset grant for a stranger succeed?**
   Carried forward from `PERMISSIONS-RESEARCH.md` item 1, and it is now the gating
   question for the whole design rather than a curiosity.
   → `POST /api/datasetrw/v1/datasets/{datasetId}/grants` with
   `{"targetId":"<user with no role on the owning project>","targetRole":"DatasetRwReader"}`,
   then re-`GET` the grants and have that user call
   `GET /v4/datasetrw/dataset/{datasetId}/role` and
   `GET /v4/datasetrw/snapshot/{rwSnapshotId}/files/recursive?path=`.
   The second call is the one that matters: a `Reader` must be able to *list and read
   the read/write area*, not only the snapshots.

2. **Can a `DatasetRwReader` read the read/write area, or only snapshots?**
   Every read I performed was as `DatasetRwOwner`. Domino's role table grants a Reader
   *"view files · view snapshots · download"* and a **read-only mount**, which reads as
   yes — but `version 0` is documented elsewhere as *"NOT considered a snapshot"*, so
   "view snapshots" may not cover it. If a Reader cannot see version 0, every shared
   Conversation must be frozen into a snapshot first, which changes the design.
   → Same write as (1); the answer is in the second user's `files/recursive` response.
   **This is the single highest-value unknown in this document.**

3. **Does the chunked upload write the read/write area or create a snapshot?**
   → `POST /v4/datasetrw/datasets/{datasetId}/snapshot/file/start`, one chunk, then the
   `end` call; then diff `GET /v4/datasetrw/snapshots/{datasetId}` (does a new snapshot
   appear?) and `GET /v4/datasetrw/files/{rwSnapshotId}?path=` (did the file land in
   version 0?).

4. **Is a second Project's Dataset really RO when mounted?**
   Domino's docs say `/mnt/imported/data/<name>` is RO. Not observed — no Sage workspace
   currently mounts another Project's Dataset.
   → `POST /v4/datasetrw/{projectId}/shared/{datasetId}` to link a Dataset into a second
   Project, start a workspace there, and `touch /mnt/imported/data/<name>/probe`.

5. **Two Builders appending to one Dataset — what actually happens?**
   Domino publishes no ordering, locking or atomicity guarantee, and states the
   opposite. Structurally both RW mounts exist at once
   (`DATASETS-VS-ARTIFACTS-RESEARCH.md`, VERIFIED (b)).
   → Two Builders in one Project, both appending to one file on `/mnt/data/<name>`,
   then read the file back. Design around the result, not around the hope.

6. **Can a Practitioner register a Data Mount at all, ever?**
   `RegisterDataMounts` is missing from `GovernanceAdmin` + `Practitioner`. Whether any
   non-admin role carries it is a question about role definitions this token cannot
   read.
   → `POST /v4/datamount` as a platform admin, or a read of the role catalogue with an
   admin token (which would answer it read-only).

7. **Whether the platform holds Dataset grants naming a non-collaborator today.**
   Carried forward unchanged from `PERMISSIONS-RESEARCH.md` item 7 — every Dataset this
   credential can read has exactly one grant. A platform-admin token would answer it
   read-only.

---

## Endpoint index (everything called in this document)

All `GET`. All observed this session unless marked *(spec only)*.

```
/v4/users/self                                                  200
/v4/datasetrw/mounts-v2/{projectId}/local                       200
/v4/datasetrw/dataset/{datasetId}/grants                        200
/v4/datasetrw/dataset/{datasetId}/role                          200
/v4/datasetrw/dataset/{datasetId}/permissions                   200
/v4/datasetrw/dataset/{datasetId}/connection-snippets           200
/v4/datasetrw/datasets/{datasetId}                              200
/v4/datasetrw/snapshots/{datasetId}                             200   isReadWrite:true, version 0
/v4/datasetrw/files/{snapshotId}?path=                          200
/v4/datasetrw/snapshot/{snapshotId}/files/recursive?path=       200
/v4/datasetrw/snapshot/{snapshotId}/file/raw?path=…             200   raw bytes
/api/datasetrw/v2/datasets?limit=100[&offset=]                  200
/v4/datasetrw/storage/rpc/get-available-volumes                 403   perform_dataset_actions_as_admin
/v4/datasets/objectstore/datasets/{d}/snapshots/{s}/keys?prefix=&page_size=10   200 {"keys":[]}
/v4/datamount/users/{userId}                                    200   []
/v4/datamount/projects/{projectId}                              200   []
/v4/datamount/projects/inaccessible/{projectId}                 200   []
/v4/datamount/paths/{projectId}                                 200   "/mnt"
/v4/datamount/all                                               403   RegisterDataMounts
/v4/datamount/isMountPathValid?mountPath=…                      403   RegisterDataMounts
/api/datasource/v1/datasources?limit=10                         200
/v4/projects/{projectId}/gitRepositories                        200   []
/api/projects/v1/projects/{projectId}/repositories              200   totalCount 0
/v4/accounts/{userId}/gitcredentials                            200
/v4/modelProducts/{modelProductId}                              200   GRANT_BASED + accessRequestStatuses
/v4/projects/{projectId}/collaborators                          200
/v4/datasetUi/scratchspace/{projectId}/files                    404   route does not exist
/v4/externalDataVolumes                                         404
/api/externalDataVolume/v1/externalDataVolumes                  404
POST /v4/datasetrw/datasets/{datasetId}/snapshot/file/start     (spec only)
POST /v4/datasetrw/datasets/{datasetId}/snapshot/file           (spec only)
GET  /v4/datasetrw/datasets/{datasetId}/snapshot/file/end/{key} (spec only)
POST /v4/projects/{projectId}/commits/head/files/{path}         (spec only)
POST /v4/projects/{projectId}/gitRepositories                   (spec only)
POST /v4/datamount                                              (spec only)
GET  /v4/workspaces/{wsId}/project/{projectId}/getWritableProjectMounts  (spec only)
GET/POST/DELETE /api/apps/beta/apps/{appId}/thumbnail           (spec only)
```

## In-repo citations

- `backend/sage/assets/provider.py:26` — `DEFAULT_DATASET_MOUNT_ROOTS`; `:34`
  `resolve_mount_roots`; `:59` `dataset_unique_name` → `dataset-<name>-<id>`
- `backend/sage/orchestrator/service.py:1070` `_SAGE_UPLOAD_PREFIXES`; `:12866`,
  `:14805`, `:14821` — the `os.access(mount_path, os.W_OK)` writability test
- `backend/sage/workspace/threads.py:118,120,126,129,135,607` — `ThreadStore` roots
- `backend/sage/workspace/git.py:95,120,131` — `commit_all`, `push`, `commit_and_push`
- `backend/sage/tools/dataset_probe.py:58` — `_ARTIFACT_ROOTS`
- `docs/adr/0006-conversation-logs-and-artifacts-stay-in-git.md` — the 68 KB/turn and
  500 B/push measurements, and the three prior rejections
- `PERMISSIONS-RESEARCH.md` Q1, Q2, Q4, Q5 — the six grant surfaces and the
  Project-role/Dataset-grant decoupling
- `DATASETS-VS-ARTIFACTS-RESEARCH.md:76` (the scope fix above), `:448-449` (the Dataset
  role table), `:528-548` (Domino's `/mnt` tree and the RW/RO modes)
- `DATA-SOURCES-RESEARCH.md:1007` — *"The credential can write; Sage's SQL cannot."*
