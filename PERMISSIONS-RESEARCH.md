# Domino permission primitives — live probe

**Probed:** the live Domino REST API on `https://cloud-dogfood.domino.tech`.
**Date:** 2026-09-09.
**Credential:** the Keycloak JWT in `backend/.env` (`GATEWAY_API_KEY`), sent as
`Authorization: Bearer <jwt>`. Caller identity: `subir_mansukhani`,
id `66a821b1e77f2b566a1e5534`, system roles `["GovernanceAdmin","Practitioner"]`
(`GET /v4/users/self`, 200). **Not** a platform admin — several probes below return
403 for that reason, and those 403s are recorded as data, not as failures.

**Constraint: read-only.** Every call in this document is a `GET`. No POST, PUT,
PATCH or DELETE was issued, nothing was created, shared, modified or deleted, and no
mutating UI action was taken. Questions that only a write can settle are listed in
*What could not be answered read-only*, with the exact write that would settle each.

**Sources used.** Live JSON responses (quoted, trimmed); the platform's own OpenAPI
documents committed in this repo — `spikes/domino-probes/public-api.json` (the public
API, 220 paths) and `spikes/domino-probes/swagger.json` (the v4 private spec,
735 paths, `servers: [{url: "/v4"}]`). No blog posts, no secondary summaries.
Domino documentation is cited in exactly one place (Q3), flagged as such, and only
where it agrees with a live observation.

**Two base paths coexist.** Public routes are `/api/...`; the v4 private spec's paths
are relative to `/v4`. Several objects appear in both with different names — an App is
a `modelProduct` in v4. A 404 is usually the wrong prefix, not a missing route.

**Paging.** Lists default to `limit=10`. Everything below was paged out
(`/api/apps/beta/apps` → 305 apps over 4 pages; `/api/projects/beta/projects` → 16).

---

## Legend

- **VERIFIED** — the response was observed on cloud-dogfood, this session.
- **INFERRED** — the OpenAPI spec says so; not observed live.
- **OPEN / UNANSWERABLE READ-ONLY** — needs a write, or a second identity's token.

---

## Q1. What is the smallest thing Domino can grant to a person?

**Not the Project.** Domino has at least six *independent* per-principal grant
surfaces, each with its own record, its own endpoint, and its own role vocabulary.
The Project is one of them, not the root of the others.

Observed live, all on this host:

| Resource | Grant record | Role / status vocabulary | Route |
|---|---|---|---|
| Project | `{id, role}` | `contributor`, `launcherUser`, `resultsConsumer`, `projectImporter` (+ implicit Owner) | `GET /v4/projects/{id}/projectSettingsCollaborators` |
| **Dataset** | `{targetId, targetName, targetRole, isOrganization}` | `DatasetRwOwner`, `DatasetRwEditor`, `DatasetRwReader` | `GET /api/datasetrw/v1/datasets/{id}/grants` |
| App | `{userId: status}` map | `ALLOWED`, `DENIED`, `PENDING` | `GET /v4/modelProducts/{id}` → `permissionsData.accessRequestStatuses` |
| Data Source | `{isEveryone, userAndOrganizationIds[]}` | boolean + id list (no roles) | `GET /api/datasource/v1/datasources` |
| AI Gateway endpoint | `{isEveryoneAllowed, userIds[]}` | boolean + id list | `GET /api/aigateway/v1/endpoints/{name}/permissions` |
| Model Deployment | `{id, role}` | `CONSUMER`, `OWNER` | `ModelDeploymentCollaborator` (public spec) |

Live grant record, my `quick-start` default Dataset
(`GET /api/datasetrw/v1/datasets/66a821b3ecadae7f043a5175/grants`, **200**):

```json
{"grantDetails":[{"targetId":"66a821b1e77f2b566a1e5534",
                  "targetName":"subir_mansukhani",
                  "targetRole":"DatasetRwOwner",
                  "isOrganization":false}]}
```

Live effective-permission read for the same Dataset
(`GET /v4/datasetrw/dataset/66a821b3ecadae7f043a5175/permissions`, **200**):

```json
["DeleteDatasetRwV2","EditPropertyValuesDatasetRwV2","EditSecurityDatasetRwV2",
 "EditTaxonomyTagsDatasetRwV2","ListDatasetRwV2","ReadDatasetRwV2","UpdateDatasetRwV2"]
```

That is the finest resolution the API exposes for data. **Nothing finer than a whole
Dataset exists.** There is no grant route on a snapshot, a path, a prefix or a file:
across all 735 v4 paths and 220 public paths, the only `grants` / `permissions` /
`role` routes under `datasetrw` are `/datasetrw/dataset/{datasetId}/{grants,permissions,role}`
— all keyed on `datasetId`. Snapshots have download, preview, file-listing and
object-store key routes, but no ACL of their own. **VERIFIED** (route inventory) /
**INFERRED** (that snapshots therefore inherit the Dataset ACL — the spec offers no
alternative, but I did not observe a snapshot denial).

**The grantee is not always a person.** `DatasetRwGrantDetailsV1` carries an
`isOrganization` boolean, `DataSourcePermissionsV1` names its list
`userAndOrganizationIds`, and `ProjectCollaboratorV1.id` is documented as
"userId of the collaborating user **or organization**". Organizations are real
principals here: `GET /v4/organizations` returns e.g.
`{"id":"6a553e6a242fc543ed23d4b9","name":"Sales_Executives",
"organizationUserId":"6a553e6a242fc543ed23d4b8","members":[{"id":"…","role":"Member"|"Admin"}]}`
— note `organizationUserId`, a user-shaped id that a grant can target. And an org
appears live as a Project collaborator: `tech-gtm`
(id `699f34f9bfad0a198a78f2ea`) holds `role: "Contributor"` on
`andrea_lowe/Pharmacovigilance`. **VERIFIED.**

**Answer:** The Project is *not* the floor — the smallest grantable unit is a single resource: one Dataset (three roles), one App (per-person allow/deny), one Data Source, one Gateway endpoint or one Model Deployment; nothing finer than a whole Dataset exists for data, and the grantee may be a user *or* an organization.

---

## Q2. Can a single Dataset be shared with someone who is not a Project collaborator?

**Yes, and the grant is fully decoupled from the Project.** Proved live, in the
strongest available form: a Dataset grant I hold survives on a Dataset whose Project
I have **no role in at all** and cannot even read.

Dataset `66a93956e77f2b566a1e5a6b` (`llama3_1_peft`) belongs to project
`66a93955e77f2b566a1e5a67`.

Project side — I have nothing:

```
GET /api/projects/v1/projects/66a93955e77f2b566a1e5a67          → 404
  {"errors":["Project with id 66a93955e77f2b566a1e5a67 could not be found"]}
GET /v4/projects/66a93955e77f2b566a1e5a67/collaborators         → 200
  [ lexie_sadashivapeth ]        # subir_mansukhani is NOT in the list
```

Dataset side — I have everything:

```
GET /api/datasetrw/v1/datasets/66a93956e77f2b566a1e5a6b/grants  → 200
  {"grantDetails":[{"targetId":"66a821b1e77f2b566a1e5534",
                    "targetName":"subir_mansukhani",
                    "targetRole":"DatasetRwOwner","isOrganization":false}]}
GET /v4/datasetrw/dataset/66a93956e77f2b566a1e5a6b/permissions  → 200
  ["DeleteDatasetRwV2","EditPropertyValuesDatasetRwV2","EditSecurityDatasetRwV2",
   "EditTaxonomyTagsDatasetRwV2","ListDatasetRwV2","ReadDatasetRwV2","UpdateDatasetRwV2"]
```

The Dataset also still appears in my identity-scoped list
(`GET /api/datasetrw/v2/datasets`, paged to 100). **VERIFIED.** A Dataset grant is an
independent ACL entry; it neither requires nor implies a Project role.

**The converse is also true and is the sharper half.** A Dataset grant is *not*
seeded from Project collaborators. My project `Sage`
(`6a5e8b03242fc543ed24282d`) has `etan_lightstone` as a **Contributor**
(`GET /v4/projects/6a5e8b03242fc543ed24282d/projectSettingsCollaborators`, 200).
Its default Dataset's grant list contains one entry — me:

```
GET /api/datasetrw/v1/datasets/6a5e8b05242fc543ed242832/grants  → 200
  {"grantDetails":[{"targetName":"subir_mansukhani","targetRole":"DatasetRwOwner",…}]}
```

Sweeping every Dataset I can see across all 16 visible projects, **every single one has
exactly one grant: me, `DatasetRwOwner`** — including
`mrm_portal_autodoc` (`69fa32e09dd6fd108f2ec4ef`), which lives inside
`nick_goble/mrm-portal`. The Project's *owner* is not in his own Dataset's grant list;
its creator is. **VERIFIED.** The grant is seeded from the Dataset's `author`, not
from the Project.

**The endpoints.**

| Purpose | Route | Notes |
|---|---|---|
| Read grants | `GET /api/datasetrw/v1/datasets/{datasetId}/grants` | Requires `List` on the dataset. Returns `grantDetails[]` with `targetName` resolved. |
| Read grants (v4) | `GET /v4/datasetrw/dataset/{datasetId}/grants` | Same payload, bare array, no envelope. |
| Add one grant | `POST /api/datasetrw/v1/datasets/{datasetId}/grants` | Body `DatasetRwGrantV1` = `{targetId, targetRole}`. Requires `EditSecurity`. **Additive** ("Add a grant to a dataset's existing sequence of grants"). |
| Remove one grant | `DELETE /api/datasetrw/v1/datasets/{datasetId}/grants` | Same body. Requires `EditSecurity`. |
| Replace all grants (v4) | `PUT /v4/datasetrw/dataset/{datasetId}/grants` | Body `{grants: [...]}` — whole-list replace. |
| My effective permissions | `GET /v4/datasetrw/dataset/{datasetId}/permissions` | Caller-scoped `DatasetRwPermissionV1[]`. |
| My role | `GET /v4/datasetrw/dataset/{datasetId}/role` | Caller-scoped, e.g. `["DatasetRwOwner"]`. |

The `POST`/`DELETE` pair is additive and the `PUT` is a replace — prefer the public
`POST` for adding a person, since the v4 `PUT` will silently revoke anyone omitted.
**INFERRED** from the spec's own wording; not executed (read-only).

**Answer:** Yes — `POST /api/datasetrw/v1/datasets/{datasetId}/grants` with `{targetId, targetRole ∈ {DatasetRwOwner, DatasetRwEditor, DatasetRwReader}}`; the grant record is an independent per-principal ACL entry, it is seeded from the Dataset's creator rather than from the Project, and it demonstrably survives with no Project role whatsoever.

---

## Q3. Apps carry `visibility: GRANT_BASED` and two sharing axes — what are they?

The two axes are **`visibility`** (an enum) and **`discoverable`** (a boolean). They
are separate top-level fields and they control different things. Live across all 305
Apps on the host:

```
visibility     AUTHENTICATED 264 | GRANT_BASED 40 | GRANT_BASED_STRICT 1
discoverable   false 255 | true 50
cross          (AUTHENTICATED,false) 243  (AUTHENTICATED,true) 21
               (GRANT_BASED,true)     28  (GRANT_BASED,false)  12
               (GRANT_BASED_STRICT,true) 1
```

All four combinations occur — they are genuinely orthogonal. **VERIFIED.**

### Axis 1 — `visibility`: who is allowed to open the App

Enum, from `AppAccessControl` (public spec) and
`domino.nucleus.modelproduct.models.VisibilityPatch` (v4 spec), identically:
`PUBLIC | AUTHENTICATED | GRANT_BASED | GRANT_BASED_STRICT`. **VERIFIED** in both specs;
`AUTHENTICATED`, `GRANT_BASED` and `GRANT_BASED_STRICT` observed live.

For `GRANT_BASED`, the allowed set is the **union of two things**: the App's own
per-person grant records, *and* the Project's collaborators. Both halves observed:

**Explicit grants are real, enumerable, per-person records — and can name a
non-collaborator.** My App `Ask` (`6a2c49bba2c110254ed44d68`) lives in my project
`loom`, whose only collaborator is me
(`GET /v4/projects/6a2b08c613f0f03684b8cdf0/collaborators` → `[subir_mansukhani]`).
Its grant map names someone else entirely:

```
GET /v4/modelProducts/6a2c49bba2c110254ed44d68                  → 200
  "permissionsData": {
    "visibility": "GRANT_BASED",
    "accessRequestStatuses": {"681285aad58d16494212c8f2": "ALLOWED"},
    "pendingInvitations": [],
    "discoverable": true,
    "appAccessStatus": "ALLOWED"
  }
GET /api/users/v1/user/681285aad58d16494212c8f2                 → 200  nick_goble
```

`nick_goble` holds an App grant on an App in a Project he is not a collaborator on.
**VERIFIED** — App grants are per-person records, independent of Project membership.

**Project collaboration alone also opens the App.** On `agnes_youn`'s
`Pharmacovigilance` App (`699f976dfca1e769538d3a35`), the grant map is
`{"66db673ef8c00466687c5500": "ALLOWED"}` (= `andrea_lowe`) — I am **not** in it — yet
the same response reports `"appAccessStatus": "ALLOWED"` for me. I am a `Contributor`
on that Project. **VERIFIED.** (Domino's own publish-and-share page states the same
union in prose: *"Only users you explicitly list as Project collaborators can view or
edit the App"* — cited here only because it agrees with the live record.)

The App's grant map also carries a third slot, `pendingInvitations: []` — a list of
**email strings**, not user ids (`PermissionsData` schema; `POST /v4/modelProducts/{id}/invite`
and `/uninvite` take `{email}`). So an App can be granted to someone who does not yet
have a resolvable Domino user id. **INFERRED** — schema-level; every live App I could
read had an empty list.

Per-person statuses are `ALLOWED | DENIED | PENDING` (`AppAccessStatus`); the caller's
own derived verdict is a fourth, wider enum
`ALLOWED | PENDING | REQUESTABLE | NOT_ALLOWED` (`PermissionsData.appAccessStatus`).

### Axis 2 — `discoverable`: who can *find* the App and ask

`discoverable` does not grant anything. It decides whether the App shows up in the
global App listing for people who are not allowed to open it. Live evidence, exact:

- All **12** `GRANT_BASED, discoverable=false` Apps visible to me are in the 16
  Projects I hold a role in. Zero exceptions.
- All **27** `GRANT_BASED` Apps I can see from *outside* my Projects are
  `discoverable=true`.
- And they are listed but not readable. For each of six of them,
  `GET /v4/modelProducts/{id}` → **403** and `GET /v4/modelProducts/consumer/{id}` → **401**.

**VERIFIED.** `discoverable=true` puts the row in the catalogue; `visibility` still
guards the door. That matches the UI label the prior notes recorded ("all Domino users
can find this App and request access") and the matching public route
`POST /api/apps/beta/apps/{appId}/access/requests` — *"Request access to an App"*.

### Reading trap — the list endpoint lies about grants

`GET /api/apps/beta/apps` returns an `accessStatuses` array that is **caller-scoped**.
For `Ask` the list returned `accessStatuses: []`, while the single-object GETs on the
same App returned the real grant:

```
GET /api/apps/beta/apps            → Ask: "accessStatuses": []                       # WRONG for enumeration
GET /api/apps/beta/apps/6a2c49bb…  → "accessStatuses":[{"userId":"681285aa…","status":"ALLOWED"}]
GET /api/apps/v1/apps/6a2c49bb…    → "accessControl":{"visibility":"GRANT_BASED",
                                       "accessStatuses":[{"userId":"681285aa…","status":"ALLOWED"}],
                                       "discoverable":true}
GET /v4/modelProducts/6a2c49bb…    → "permissionsData":{…"accessRequestStatuses":{"681285aa…":"ALLOWED"}…}
```

**VERIFIED, and directly contrary to what the list response suggests.** Never
enumerate App grants from the list route; use the single-App GET.

`GRANT_BASED_STRICT` exists but is nearly unused (1 of 305, `sameer_wadkar`'s
`test-app-2`). Its record is closed to me:
`GET /v4/modelProducts/688a4912504dde352f13f0b5` → **403**,
`{"required":["AccessApp"],"missing":["AccessApp"]}` — a clean read of the underlying
permission name, but no view of how STRICT differs from GRANT_BASED. **OPEN.**

**Answer:** The two axes are `visibility` (`PUBLIC | AUTHENTICATED | GRANT_BASED | GRANT_BASED_STRICT`), which decides who may open the App — for `GRANT_BASED` that is the union of enumerable per-person `accessStatuses` records, email `pendingInvitations`, and the Project's collaborators — and the independent boolean `discoverable`, which only decides whether non-grantees see the App listed and can request access.

---

## Q4. Can an App grant and a Dataset share be driven from one action?

**No. They are two independent grants, on two unrelated records, reached by two
unrelated endpoints, using two unrelated role vocabularies.**

| | App | Dataset |
|---|---|---|
| Record | `permissionsData.accessRequestStatuses` — `{userId: status}` map on the modelProduct | `grantDetails[]` — `{targetId, targetRole, isOrganization}` on the dataset |
| Vocabulary | `ALLOWED / DENIED / PENDING` | `DatasetRwOwner / DatasetRwEditor / DatasetRwReader` |
| Write route | `PATCH /api/apps/beta/apps/{appId}` (`accessStatuses`) or `POST /v4/modelProducts/{id}/grantAccess` | `POST /api/datasetrw/v1/datasets/{datasetId}/grants` |
| Seeded from | Project collaborators (union) | The Dataset's `author` only |

No route in either spec takes both an App id and a Dataset id. **VERIFIED** by
inventory of all 955 paths across the two specs.

**Four candidate binders, and why none of them is one.**

1. **Project collaborator.** This binds the App but *not* the Dataset. Adding a
   collaborator puts a person inside `GRANT_BASED`'s union (Q3, verified), and does
   **nothing** for Datasets (Q5, verified). It is a one-and-a-half-sided binder, which
   is the worst kind — it looks like it worked.
2. **`mountDatasets`.** Every App carries a top-level boolean `mountDatasets`; it is
   `true` on all 305 Apps live. The public spec defines it as *"App-level default for
   whether to mount Domino Datasets into the App's **runtime**"*, overridable
   per-version (`AppVersionContent.mountDatasets`). It is a **compute-side** switch
   about the App process's filesystem, not a viewer-side grant — it changes what the
   App container can read, never what a viewer of the App is permitted. **VERIFIED**
   (field, live values) / **INFERRED** (its runtime effect, from the spec's own wording).
3. **`extendedIdentityPropagationToAppsEnabled`.** A per-version boolean, `false` on
   my Apps live, `default: false` in `AppVersionCreationRequest`. Named as if it
   forwards the viewer's identity into the App — which would make the viewer's own
   Dataset grants the ones that matter at runtime. Neither spec documents its semantics
   beyond `{"type":"boolean"}`; the only related fields are session-expiry knobs on
   `PrincipalWithFeatureFlags`. **OPEN** — the name is the only evidence, and a name is
   not a source.
4. **Sharing a Dataset *into* a Project.** `POST /v4/datasetrw/{projectId}/shared/{datasetId}`
   (v4) / `POST /api/projects/v1/projects/{projectId}/shared-datasets` (public) links a
   Dataset to a second Project. The record it produces is
   `SharedDatasetRwEntryV1 = {projectId, sharedDatasetIds[]}` — a **Project→Dataset**
   link with no principal in it at all. It carries no grant. `GET .../shared-datasets`
   on my `quick-start` Project returns **404**
   `"sharedDatasetRwEntry for project … not found"`, and
   `GET /v4/datasetrw/mounts-v2/{projectId}/shared` returns `[]` for every Project I
   hold. **VERIFIED.**

**The nearest thing to a binder is the Organization.** One `organizationUserId` is a
legal target for a Project collaborator add, a Dataset grant (`isOrganization: true`),
a Data Source permission (`userAndOrganizationIds`) and a Model Deployment
collaborator. Managing membership in one place therefore fans out to every resource
already granted to that org. That is still N grants to create once — it only makes the
*population* single-sourced, not the grant. **VERIFIED** that orgs are accepted
principals on Projects (live: `tech-gtm` as Contributor) and declared principals on
Datasets and Data Sources (schema).

**Answer:** Always two independent grants — no endpoint in either spec accepts an App and a Dataset together, `mountDatasets` is a runtime mount switch rather than a grant, and the only primitive that binds them at all is the Organization, which unifies the *audience* across resources but still requires each grant to be made separately.

---

## Q5. When a Project adds a collaborator, what do they get — and is the default Dataset included?

**They do not get the Dataset. Not even at the highest collaborator role.**

This is the sharpest result of the probe, and it contradicts what the default Dataset's
own description says about itself.

I am a **`Contributor`** — the top non-owner role — on `andrea_lowe/Pharmacovigilance`
(`698f96027018494482441590`):

```
GET /v4/projects/698f96027018494482441590/projectSettingsCollaborators   → 200
  … {"collaborator":{"userName":"subir_mansukhani",…},"role":"Contributor"} …
```

That Project's default Dataset exists — the authorization error names its id — and I
am refused:

```
GET /v4/datasetrw/datasets/name/Pharmacovigilance
      ?projectOwner=andrea_lowe&projectName=Pharmacovigilance                → 403
{"success":false,
 "message":"Your role does not authorize you to perform this action",
 "debugMessage":"user:66a821b1e77f2b566a1e5534 is not authorized to perform this
   action. Subject 'user:66a821b1e77f2b566a1e5534' does not have 'list' permission
   on 'dataset:698f96037018494482441595'"}
```

Not `list` — the weakest permission in the vocabulary. Reproduced on a second Project
where I am also a `Contributor`, `lexie_sadashivapeth/llama3testing`:

```
GET /v4/datasetrw/datasets/name/llama3testing
      ?projectOwner=lexie_sadashivapeth&projectName=llama3testing           → 403
  … does not have 'list' permission on 'dataset:66a7a358e77f2b566a1e536b'
```

Control, same route, my own Project — **200**, full record. The route works; the
permission is what fails. **VERIFIED.**

Every Dataset-facing listing agrees, and they all show the Datasets as simply absent
rather than denied — which is how this failure will present in a UI:

```
GET /v4/datasetrw/mounts-v2/698f96027018494482441590/local     → 200  []
GET /v4/datasetrw/mounts-v2/698f96027018494482441590/shared    → 200  []
GET /v4/datasetrw/datasets-v2?projectIdsToInclude=698f9602…    → 200  []
GET /api/datasetrw/v2/datasets?projectIdsToInclude=698f9602…   → 200  {"datasets":[]}
GET /v4/datasetrw/snapshots/project/698f96027018494482441590   → 200  []
```

Meanwhile the *Project-side* permissions on the same Project are fine —
`GET /v4/datasetrw/principal/698f96027018494482441590/manage` → `true`, and
`GET /v4/datasetrw/canAddDataset/698f96027018494482441590` → `true`. I may create a
new Dataset there. I may not list the one that is already there. **VERIFIED.**

The two dimensions are named as separate fields in the API itself. `datasets-v2`
takes `includeHasProjectAccess`, and returns:

```json
{"datasetRwDto":{"id":"6a5e8b05242fc543ed242832","name":"Sage",
                 "author":"66a821b1e77f2b566a1e5534",
                 "ownerUsernames":["subir_mansukhani"], …},
 "projectInfo":{"projectId":"6a5e8b03242fc543ed24282d","hasProjectAccess":true}}
```

`hasProjectAccess` is a field *about* the row, not the reason the row is there.
`ownerUsernames` is derived from the grant list, not from the Project. **VERIFIED.**

### So what does a collaborator get?

| | Included by a Project collaborator add? |
|---|---|
| Project files / repos / commits | Yes — `GET /api/projects/v1/projects/{id}` and the repo routes answer 200 for Projects I collaborate on. **VERIFIED.** |
| Apps published in that Project, when `GRANT_BASED` | Yes — `appAccessStatus: ALLOWED` on `Pharmacovigilance` with no grant record naming me. **VERIFIED** (Q3). |
| Ability to create *new* Datasets in the Project | Yes — `canAddDataset` → `true`. **VERIFIED.** |
| **The Project's existing Datasets — including the default one** | **No.** 403 on `list`. **VERIFIED.** |
| The Project's Data Sources | Governed by the Data Source's own `permissions` object, not by Project role. All 39 Data Sources visible to me are `isEveryone: true`; exactly one carries an explicit id list. **VERIFIED** (records) / **INFERRED** (that Project role is irrelevant to them — no Data Source route references a Project). |

The available collaborator roles are `contributor`, `launcherUser`, `resultsConsumer`,
`projectImporter` (`ProjectCollaboratorV1`, public spec), plus the implicit Owner. The
403 above was produced at `contributor`, so no lower role can do better.

**Answer:** A collaborator gets the Project's files, repos, its `GRANT_BASED` Apps, and the right to create new Datasets — but **not** the Project's existing Datasets, and specifically **not** the default Dataset: at `Contributor`, the highest non-owner role, `list` on that Dataset is refused with a 403, and the Dataset silently disappears from every mount and listing route rather than appearing as denied.

---

## What could not be answered read-only

Each item names the exact write that would settle it.

1. **Does creating a Dataset grant for a non-collaborator actually succeed?**
   I proved a *pre-existing* grant survives with no Project role (Q2). I did not prove
   the server accepts a *new* one for a stranger.
   → `POST /api/datasetrw/v1/datasets/{datasetId}/grants` with
   `{"targetId":"<a user with no role on the owning project>","targetRole":"DatasetRwReader"}`,
   then re-`GET` the grants and have that user call
   `GET /v4/datasetrw/dataset/{datasetId}/role`.

2. **Does `PATCH /api/apps/beta/apps/{appId}` `accessStatuses` replace or append?**
   The body is an array and the spec says only *"omitted fields preserve existing
   values"* — which is about *fields*, not about array elements. A naive one-element
   PATCH may revoke everyone else.
   → `PATCH` a two-grant App with a one-element `accessStatuses`, then `GET` it back.
   Until then, treat it as **replace** and read-modify-write.

3. **What does `GRANT_BASED_STRICT` add over `GRANT_BASED`?**
   Only one instance exists on the host and it 403s
   (`{"required":["AccessApp"],"missing":["AccessApp"]}`).
   → `POST /v4/modelProducts/{id}/visibility` `{"visibility":"GRANT_BASED_STRICT"}` on an
   App I own, then compare `permissionsData` and a non-collaborator's `appAccessStatus`
   against the `GRANT_BASED` baseline.

4. **What does `extendedIdentityPropagationToAppsEnabled` actually propagate?**
   Undocumented in both specs beyond `{"type":"boolean"}`. This is the single most
   load-bearing unknown for any App-plus-Dataset design.
   → `POST /api/apps/v1/apps/{appId}/versions` with the flag `true`, start it, and have
   a second identity open the App and attempt a Dataset read.

5. **Does a Dataset grant alone let a viewer read the Dataset through a running App?**
   Requires two identities and a live App session; no GET can express it.
   → Grant `DatasetRwReader` to a second user, have that user open the App, observe
   whether the mount is readable under their session.

6. **Does `POST /v4/datasetrw/{projectId}/shared/{datasetId}` change anyone's grants?**
   Every `shared` listing I hold is empty, so I never saw the record populated.
   → Link one of my Datasets into a second Project and diff
   `GET /api/datasetrw/v1/datasets/{datasetId}/grants` before and after.

7. **Whether the platform holds Dataset grants naming someone who is not a
   collaborator on the owning Project.** Every Dataset readable by this credential has
   exactly one grant — me. Reading a stranger's Dataset grant list requires `List` on
   that Dataset, which I do not have (see the 403s in Q5). Not a write problem; a
   credential-scope problem. A platform-admin token would answer it read-only.

---

## What this means for a design that has to grant an App and a Dataset together

Facts and constraints only.

- **Two grants, always.** No single endpoint in the public API or the v4 API accepts
  an App and a Dataset. Any "share this App and its data with Alice" action is at
  minimum two calls, against two services, with two different role vocabularies, and
  they can fail independently. There is no transaction across them.

- **The obvious shortcut is a trap.** Adding Alice as a Project collaborator *does*
  open a `GRANT_BASED` App and *does not* open the Project's Datasets — including the
  Project's own default Dataset. The half that fails fails silently: the Dataset
  vanishes from `mounts-v2/{projectId}/local`, from `datasets-v2`, and from
  `/api/datasetrw/v2/datasets` with a **200 and an empty array**, not a 403. Code that
  checks for errors will see success.

- **`Contributor` is not a workaround.** The 403 in Q5 was produced at the highest
  non-owner role. There is no collaborator role that carries Dataset access.

- **A Dataset grant is durable and standalone.** It needs no Project role to be
  created against, and it keeps working when the grantee has no Project role and cannot
  even resolve the Project (verified: 404 on the Project, full `DatasetRwOwner` on the
  Dataset). Revoking a Project role does not revoke it — which is a leak risk in the
  other direction, and means an offboarding path has to walk Datasets separately.

- **Enumerate grants from the right route.** `GET /api/apps/beta/apps` returns a
  caller-scoped `accessStatuses` and will report `[]` for an App that has grants. Use
  `GET /api/apps/beta/apps/{appId}`, `GET /api/apps/v1/apps/{appId}`
  (`accessControl.accessStatuses`) or `GET /v4/modelProducts/{appId}`
  (`permissionsData.accessRequestStatuses`) — all three return the real list.

- **Additive vs replace matters.** The public Dataset grant route is additive
  (`POST` adds one, `DELETE` removes one). The v4 route is a whole-list `PUT`. The App
  route is a `PATCH` carrying a whole array, replace-vs-append unproven. Two of the
  three write shapes can wipe other people's access if used naively.

- **Roles do not line up.** An App grant is `ALLOWED | DENIED | PENDING`; a Dataset
  grant is `DatasetRwOwner | DatasetRwEditor | DatasetRwReader`; a Project role is
  `contributor | launcherUser | resultsConsumer | projectImporter`; a Data Source is
  a boolean plus an id list. Any unified "share" affordance has to invent its own
  vocabulary and map it onto four others.

- **Organizations are the one shared handle.** One `organizationUserId` is a legal
  target on Projects (live), Datasets (`isOrganization`), Data Sources
  (`userAndOrganizationIds`) and Model Deployments. Granting to an org once per
  resource makes subsequent membership changes a single edit in one place. It does not
  reduce the number of grants; it reduces the number of times you have to make them.

- **A Dataset is the floor.** There is no snapshot-, path- or file-level grant. Any
  design that wants to expose part of a Dataset has to do it by producing a narrower
  Dataset, not by narrowing a grant.

- **`mountDatasets` is not a grant.** It is `true` on all 305 live Apps and governs
  what the App *process* mounts, not what a viewer is permitted. Whether the viewer's
  own grants ever reach the runtime depends on
  `extendedIdentityPropagationToAppsEnabled`, which is `false` by default, undocumented
  in both specs, and item 4 in the unanswered list above.
