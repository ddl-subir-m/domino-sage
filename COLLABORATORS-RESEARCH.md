# Collaborators — research

Research to answer three questions before any "share this with a colleague" feature is scoped:
who can Sage offer to pick from, how does it add them to the Project, and does that one act also
let them open the published App.

Most of what follows is read from **first-party API specs and Domino's own doc pages**. A later
read-only probe pass against the live **cloud-dogfood** deployment (2026-09-03) settled the read
half and **contradicted the docs on the write path** — see
[Live probe results](#live-probe-results-2026-09-03), which overrides any claim below it conflicts
with. Write calls were not probed. Claims a spec or doc page settles are marked as such;
everything else carries **`LIVE-VERIFY`** and names the exact call that would close it.

---

## Live probe results (2026-09-03)

Read-only `GET`/`OPTIONS` against `https://cloud-dogfood.domino.tech`, authenticated as
`subir_mansukhani`. **Where this section and the doc-sourced sections below disagree, this one
wins.** No write call was probed — see the LIVE-VERIFY list.

**Auth, for anyone repeating these.** The token is a Keycloak JWT, and the header matters:
`Authorization: Bearer <token>` returns 200, while `X-Domino-Api-Key: <token>` returns **403**.
Doc pages that name `X-Domino-Api-Key` describe the classic API-key path, not this token.

### VERIFIED — the directory works, and the caller is not an admin

| Call | Result |
|---|---|
| `GET /api/users/v1/users` | `200`, **10** records — the documented default limit, confirmed |
| `GET /api/users/v1/users?limit=500` | `200`, **397** records |
| `GET /v4/users` | `200`, **397** records — same set, no limit needed |
| `GET /v4/users?query=subir` | `200`, **1** record — prefix match works |
| `GET /api/users/v1/users?userName=subir_mansukhani` | `200`, **10** unfiltered records — the parameter is **ignored**, do not use it |

**The admin question is answered: the caller is not a platform admin.** Its JWT carries
`realm_access.roles` of only `offline_access`, `uma_authorization`, `default-roles-dominorealm`,
and `user_groups` of `/roles/GovernanceAdmin` and `/roles/Practitioner`. Neither is a SysAdmin
grant. So a normal practitioner reads the directory, and the feature is **"pick a person from a
list"**, not "type an email address".

**Org scoping did not bite here.** Both routes returned the full 397-user tenant, not a narrowed
org slice. That does not disprove org scoping on other tenants; it does mean it is not a blocker on
this one.

The record shape is exactly the `Person` Sage already parses:

```json
{"id": "...", "idpId": "...", "firstName": "Subir", "lastName": "Mansukhani",
 "fullName": "Subir Mansukhani", "userName": "subir_mansukhani",
 "email": "subir.mansukhani@dominodatalab.com", "avatarUrl": ""}
```

### VERIFIED — the existing collaborators read is real

`GET /v4/projects/66a821b2ecadae7f043a5171/collaborators` → `200`, a bare JSON **array** of the
`Person` shape above. This is the call `provider.py:1109` already makes. **ADR-0007 recorded that it
had never run against a real Domino; as of 2026-09-03 it has, and it works.**

`GET /v4/projects` → `200`, 15 projects, each carrying `id`, `name`, `visibility`, `ownerUsername`.

### VERIFIED — the write path exists, and the public API is the one to build against

A first pass read a `404` on `GET`/`OPTIONS` of the public collaborators route and concluded the
path was absent. **That was wrong, and the reason is worth recording:** the 404 is *verb-scoped*.
The public API registers only `POST` and `DELETE` on that path, so `GET` and `OPTIONS` 404 while
`POST` works. This matches the spec finding in section 2c that the public API has no GET for
collaborators. **Never infer a path's existence from `OPTIONS` on this platform.**

An empty-body `POST` to each candidate (2026-09-03, on the private `quick-start` project — an
empty body names no person, so it cannot add anybody):

| Call | Status | Validation error names |
|---|---|---|
| `POST /api/projects/v1/projects/{id}/collaborators` | **`400`** | `obj.id`, `obj.role` — both `error.path.missing` |
| `POST /v4/projects/{id}/collaborators` | `500` | `'collaboratorId' is undefined on object: {}` |
| `POST /v4/projects/{id}/addManyCollaborators` | `400` | `obj.collaboratorUsernameOrEmails` |

**Build against the public route.** It confirms section 2a's `{id, role}` body exactly, and it is
the only one of the three that answers a bad request with a `400` instead of a `500` — the v4 route
lets a validation failure escape as a server error, which is a poor surface to put a user-facing
picker on.

Note the field-name split, which is a real trap when reading these routes side by side: the public
route wants **`id`**, the v4 route wants **`collaboratorId`**, and the bulk route wants
**`collaboratorUsernameOrEmails`** (usernames or emails, not ids).

Still unverified: the **success** shape, the accepted `role` values, and idempotency. An empty body
cannot reach them — only a real add can. See probes 3 and 4.

### VERIFIED — the full add/read/remove cycle, run end to end

2026-09-03, on the private `quick-start` project, adding the service account
`repro-practitioner-sa` (`6a8f594e0fdcc91dd93b8e2a`) and removing it again. Every step below is a
real response body, and step 6 confirmed the project was left as it was found.

**1. Add — `POST /api/projects/v1/projects/{pid}/collaborators` `{"id","role"}` → `200`.**
Lowercase `contributor` is accepted. The body echoes what was written:

```json
{"collaborator": {"id": "6a8f...", "role": "contributor"}, "metadata": {"requestId": "...", "notices": []}}
```

**2. The casing split is real.** The write takes `contributor`; the project record reads back
`"projectRole": "Contributor"`. Two spellings of one value, and anything comparing them must fold
case.

**3. `collaboratorId` is the plain user id.** Same 24-hex value that was posted, and the same one
`DELETE` accepts. There is no separate collaborator id space.

**4. The two reads disagree about the owner — this is the trap.**

| Call | Owner included? | The entry's user-id key |
|---|---|---|
| `GET /v4/projects/{pid}/collaborators` | **Yes** — the owner is in the array | `id` |
| `GET /v4/projects` → `.collaborators` | **No** — collaborators only | **`collaboratorId`** |

So "who is on this Project" has two different answers depending on which call you make. The
name-bearing read includes the owner; the role-bearing read does not. A UI that joins them must
treat the owner as a row with no `projectRole` rather than dropping it.

**The join key is the second half of this trap, and the id column above was added after it bit.**
The two reads spell the same person's id differently, and this section did not originally say so —
it named the array but never its contents, so an implementer reading it alongside section 2c (where
the doc-sourced `ProjectV1` shows `collaborators: [{id, role}]`) reasonably guessed `id` and joined
on it. That match silently finds nobody: no error, no missing row, every role just blank. See the
implementation addendum at the end of this file for the full route-by-route spelling.

**5. Adding twice is NOT idempotent — `400`.** And the error is a different shape from the
validation error, carrying `errors` rather than `message`:

```json
{"requestId": "...", "errors": ["User 6a8f... is already part of project 66a8..."]}
```

The condition is identifiable **only by the English sentence**. There is no code, no field, nothing
structural. See the note in the scoping section.

**6. Remove — `DELETE /api/projects/v1/projects/{pid}/collaborators/{userId}` → `200`**
`{"success": true, "metadata": {...}}`. The follow-up read showed only the owner remaining.

### VERIFIED — `GRANT_BASED_STRICT` is real, and in use

`GET /api/apps/beta/apps` → `200`, 302 apps under `items`. Visibility values across the tenant:

| Value | Count |
|---|---|
| `AUTHENTICATED` | 264 |
| `GRANT_BASED` | 37 |
| `GRANT_BASED_STRICT` | **1** |

So the fourth enum value is not theoretical — an App on this deployment is set to it today. That
confirms the `publish_guard` finding below is a live defect, not a spec curiosity.

---

## Takeaways for a reader in a hurry

- **The user directory exists and is NOT admin-only.** `GET /api/users/v1/users` is documented
  *"Get all users visible to the current user. Required permissions: `None`"*. The feature is not
  blocked on an admin grant. It is, however, **org-scoped** — the sibling single-user route says
  *"subject to org-scoped visibility, same as the list-users endpoint"* — so a picker shows the
  people the caller can already see, not the whole tenant.
- **But the public listing has no search.** Its only parameters are `offset` and `limit`, and
  `limit` **defaults to 10**. Typeahead lives on the v4 route instead: `GET /v4/users?query=<prefix>`
  (`listUsers`, *"Optional filter for a user name (returns usernames starting with this query)"*).
  A picker is one of those two, not both.
- **Adding a collaborator is one POST**, and the permission it needs — `ManageCollaborators` — maps
  to the **Contributor and Owner** project roles per Domino's own permission table. A Sage user who
  created the Project is its Owner, so the common case works.
- **The published App needs NO second call.** Domino's *Publish and share an App* page states of
  the Restricted setting, verbatim: *"Only users you explicitly list and Project collaborators can
  view or edit the App"*. `GRANT_BASED` **is** "Restricted", already verified live in this repo.
  Adding a project collaborator grants App access.
- **One caveat that changes the default role.** Domino's collaborator permission table grants
  *"View an App"* to Launcher User, Results Consumer, Contributor and Owner — **every role except
  Project Importer**. So a share flow must not default to `projectImporter`; `resultsConsumer` is
  the smallest role that sees the App.
- **A bug fell out of this.** The App visibility enum has a fourth value, `GRANT_BASED_STRICT`,
  which is not in `publish_guard.ALLOWED_VISIBILITY`. An App set to it would be **refused at
  re-publish** with a sentence that says the app "can be opened by people who are not signed in" —
  the opposite of what STRICT means. See [Finding: `GRANT_BASED_STRICT`](#finding-grant_based_strict-is-a-false-refusal-in-publish_guard).

---

## Bottom line for scoping

**One call:**

- Add a person to the Project: `POST /api/projects/v1/projects/{projectId}/collaborators`
  `{id, role}` — **body live-verified 2026-09-03** (an empty body 400s naming exactly `id` and
  `role`). Prefer it over the v4 route, which 500s on a bad body. Under Sage's `GRANT_BASED` App this also gives them the App. No App call needed.
- Remove them: `DELETE /api/projects/v1/projects/{projectId}/collaborators/{collaboratorId}`.
- List who is on it: `GET /v4/projects/{projectId}/collaborators` — **already implemented**
  (`backend/sage/resources/provider.py:1109-1136`), surfaced at `GET /api/members`
  (`backend/sage/orchestrator/app.py:2228`).

**Two calls, only if a "share the App but not the Project" case is in scope:**

- App-only grant is a separate, optional axis: `PATCH /api/apps/beta/apps/{appId}` with
  `accessStatuses: [{userId, status: "ALLOWED"}]`, or v4
  `POST /v4/modelProducts/{appId}/grantAccess {userId}`. The spec says `accessStatuses` is a
  *metadata* field "updated in-place without restarting", so this does not bounce a running App.
  **Sage does not currently send `accessStatuses` at publish** (`backend/sage/provision/domino.py:441-448`),
  so a Sage App's explicit grant list is empty and every viewer today arrives via the Project.

**Unknown / must be probed before committing:**

1. ~~Whether a non-admin key really gets a useful list back from either user endpoint.~~
   **CLOSED 2026-09-03: it does — 397 users, caller holds no SysAdmin role.** Bind to
   `GET /v4/users` (`?query=` typeaheads, no param returns all).
2. Whether re-adding an existing collaborator is idempotent or a 400/409. Neither spec says.
3. Whether `modelProductId` == the beta-apps `id`, if the v4 grant route is used.
4. `GRANT_BASED_STRICT` semantics (inferred, undocumented).
5. Whether `POST /v4/projects/{id}/addManyCollaborators` (username/email, no role field) assigns a
   default role, and which.

**Write-side gap in Sage's own code (not a Domino question, but it is scope):**
`DominoResourceProvider` has `_get`/`_post` wrappers only — no DELETE
(`backend/sage/resources/provider.py:1323-1345`; the underlying `_send` takes a method string, so
this is one line). `ControlPlane` has `_get`/`_post`/`_delete` but no PATCH
(`backend/sage/provision/domino.py:218-228`).

---

## Source provenance

Two OpenAPI specs, served **unauthenticated** from any deployment's `/assets/`, fetched from
cloud-dogfood on **2026-08-18** by `spikes/domino-probes/fetch_api_specs.sh`:

| File | Spec | Title / version | Paths | Server |
|---|---|---|---|---|
| `spikes/domino-probes/public-api.json` | OpenAPI 3.0.3 | Domino Public API 6.4.0 | 220 | (none; paths are absolute `/api/...`) |
| `spikes/domino-probes/swagger.json` | OpenAPI 3.0.0 | Domino Data Lab API v4 4.0.0 | 735 | `/v4` |

Both blobs are ~2 MB and **gitignored** — a fresh clone must re-run the fetch script. The script's
own header states the rule this document follows: *"public-api.json … BUILD AGAINST THIS.
swagger.json … internal /v4 surface … Useful reference, NOT a stability contract."*
(`spikes/domino-probes/fetch_api_specs.sh`)

**Auth.** Both specs declare the same two schemes globally: `X-Domino-Api-Key` (header) and
`Authorization` (header). No per-operation `security` override on any endpoint in this document.
Sage sends `Authorization: Bearer <token>` on every Domino call
(`backend/sage/resources/provider.py:1365`, `backend/sage/provision/domino.py:200`). Both control
planes were confirmed to open with a workspace Keycloak JWT from off-Domino
(`DOMINO-PRIMITIVES.md:245-268`); the `X-Domino-Api-Key` shape was **403** for the same JWT, so use
Bearer.

---

## 1. Listing users a Sage user could pick from

### 1a. `GET /api/users/v1/users` — the public directory (RECOMMENDED)

| | |
|---|---|
| operationId | `getVisibleUsers` |
| Summary | *"Get all users visible to the current user"* |
| Description | *"Retrieves all users visible to the current user. **Required permissions: `None`**"* |
| Query params | `offset` (int, *"How many users from the start to skip. Defaults to 0."*), `limit` (int, *"Max number of users to fetch. **Defaults to 10.**"*) |
| 200 | `PaginatedUserEnvelopeV1` `{ users: UserV1[], metadata: MetadataV1 & PaginationV1 }` |
| Errors | 400, 401 `ErrorV1`, 403 `ErrorV1`, 404, 500 |

`UserV1` — required: `id`, `userName`, `fullName`, `firstName`, `lastName`, `avatarUrl`.
Optional: `email`, `companyName`, `phoneNumber`, `idpId`, `roles: string[]`.

`PaginationV1` carries `limit`, `offset`, `totalCount` — so a picker can page deterministically.

Source: `spikes/domino-probes/public-api.json`, `paths["/api/users/v1/users"].get`.

**The permission answer, plainly: not admin-only.** *Required permissions: `None`* is Domino's own
wording on the operation. The admin user directory is a different, clearly separated surface —
`GET /v4/admin/user-management/users` and `/v4/admin/user-management/users-with-usage` — and the
existence of that separate path is corroborating evidence that `getVisibleUsers` is the ordinary-user
route.

**The real constraint is scoping, not role.** The sibling operation
`GET /api/users/v1/user/{userId}` (`getUser`) is documented *"Required permissions: `None`
(subject to org-scoped visibility, same as the list-users endpoint)."* That parenthetical is the
only statement anywhere about what "visible to the current user" means, and it says the answer is
shaped by organization membership. `LIVE-VERIFY` — on a tenant where the Sage user belongs to no
org, this list could be tiny or could be everyone; nothing in the spec or docs says which.

**There is no name or email filter.** A "type a name to find a colleague" UI on this endpoint has
to fetch pages and filter client-side, and `limit` defaulting to 10 means the naive call looks
almost empty. That is the single most important shape fact for the picker design.

### 1b. `GET /v4/users` — the typeahead (v4, unstable surface)

| | |
|---|---|
| operationId | `listUsers` |
| Summary | *"retrieves a list of users"* |
| Query params | `userId: string[]` *"Optional list of user identifiers to select the previously known users"*; `userName: string` *"Optional filter for an exact user name"*; **`query: string`** *"Optional filter for a user name (returns usernames starting with this query)"*; `listOnlyUsers: boolean` |
| 200 | `domino.common.user.Person[]` — a bare array, no envelope, no pagination |
| Errors | 400, 401, 403, 404, 500 |

`Person` — required: `id`, `userName`, `fullName`, `firstName`, `lastName`, `avatarUrl`.
Optional: `email`, `companyName`, `phoneNumber`, `idpId`.

Source: `spikes/domino-probes/swagger.json`, `paths["/users"].get` (server `/v4`).

This is a **prefix search on username**, which is the shape a picker actually wants, and it returns
the same `Person` record Sage already parses in `list_collaborators`
(`backend/sage/resources/provider.py:1123-1135`). The cost is that it is the internal v4 surface,
which the repo's own fetch script says is not a stability contract.

**Recommendation:** if the picker needs typeahead, use `GET /v4/users?query=`; if it can present a
paged list, use the public `GET /api/users/v1/users`. Do not build against both.
`LIVE-VERIFY` — neither has been called from Sage.

### 1c. Not this

- `GET /v4/admin/user-management/users` — admin surface. Do not reach for it.
- `POST /v4/users` (`createUser`, body `{email, roles}`) — creates a user. Out of scope, and
  almost certainly admin-gated.

---

## 2. Adding, listing and removing a Project collaborator

### 2a. Add — public API (RECOMMENDED)

```
POST /api/projects/v1/projects/{projectId}/collaborators
Authorization: Bearer <token>
Content-Type: application/json

{"id": "<userId or organizationId>", "role": "contributor"}
```

| | |
|---|---|
| operationId | `addCollaboratorToProject` |
| Summary | *"Add a collaborator to this project"* |
| Description | *"Add a collaborator to this project. **Required permissions: `ManageCollaborators`**"* |
| Path param | `projectId` — *"Project ID"* |
| Body | `ProjectCollaboratorV1` — **required** `id`, `role` |
| `id` | *"userId of collaborating user **or organization**"*, example `662604702b7e5d347dbe7a908` |
| `role` | enum: `contributor`, `launcherUser`, `resultsConsumer`, `projectImporter` — **note: no `owner`** |
| 200 | `ProjectCollaboratorEnvelopeV1` `{ collaborator: {id, role}, metadata }` |
| Errors | 400 `{error}`, 401 `ErrorV1`, 403 `ErrorV1` *"the caller lacks permission to perform this action"*, 404 `{error}`, 500 `{error}` |

Source: `spikes/domino-probes/public-api.json`,
`paths["/api/projects/v1/projects/{projectId}/collaborators"].post` and
`components.schemas.ProjectCollaboratorV1`.

Two things worth naming:

- **The `id` may be an organization.** The schema description says so explicitly. A picker that
  only ever offers users is fine, but a picker that shows orgs would use the same call.
- **The public role enum has no `owner`.** Ownership transfer is a different operation, and
  Domino's docs confirm *"Transfer project ownership"* is Owner-only
  ([collaborator-permissions](https://docs.dominodatalab.com/en/latest/user_guide/7876f1/collaborator-permissions/)).

### 2b. Add — v4 equivalents

| Call | Body | Notes |
|---|---|---|
| `POST /v4/projects/{projectId}/collaborators` | `domino.projects.api.CollaboratorDTO` `{collaboratorId*, projectRole*}` | `projectRole` enum is PascalCase and **does** include `Owner`: `ProjectImporter`, `Contributor`, `ResultsConsumer`, `LauncherUser`, `Owner`. `200` with no body. |
| `POST /v4/projects/{projectId}/addManyCollaborators` | `{collaboratorUsernameOrEmails*: string[], welcomeMessage?: string}` | Bulk, keyed on **username or email** rather than id — no directory lookup needed. **Carries no role field.** `200 success`, no body. |
| `PATCH /v4/projects/{projectId}/collaboratorRole` | `CollaboratorDTO` `{collaboratorId, projectRole}` | `updateCollaboratorProjectRole`. **No public-API equivalent** — changing an existing collaborator's role is v4-only. |

Source: `spikes/domino-probes/swagger.json`, `paths["/projects/{projectId}/collaborators"].post`,
`paths["/projects/{projectId}/addManyCollaborators"].post`,
`paths["/projects/{projectId}/collaboratorRole"].patch`.

`addManyCollaborators` is attractive for a "paste some emails" flow and is what Domino's own
"welcome message" UI is presumably wired to, but **`LIVE-VERIFY` which role it assigns** — if it
defaults to `ProjectImporter`, those people will not see the App (see §3c).

### 2c. List the current collaborators — three shapes, pick by what you need

| Call | Returns | Has names? | Has roles? |
|---|---|---|---|
| `GET /v4/projects/{projectId}/collaborators` (`getProjectCollaborators`) | `Person[]` | **yes** | no |
| `GET /v4/projects/{projectId}/projectSettingsCollaborators` (`getProjectSettingsCollaborators`) | `[{collaborator: PersonDTO, role, participantNotificationSettings}]` | **yes** | **yes** |
| `GET /api/projects/v1/projects/{projectId}` (`getProjectById`, *"Required permissions: `ListProject`"*) | `ProjectEnvelopeV1` → `ProjectV1.collaborators: [{id, role}]` — *"List of collaborators, if any"* | no | yes |

`role` on `projectSettingsCollaborators` is the PascalCase enum
(`ProjectImporter | Contributor | ResultsConsumer | LauncherUser | Owner`);
`participantNotificationSettings` is
`AllRuns | RunsStartedByUserThatFailed | RunsThatFailed | Never | RunsStartedByUser`.

Source: `spikes/domino-probes/swagger.json` `paths["/projects/{projectId}/collaborators"].get` and
`paths["/projects/{projectId}/projectSettingsCollaborators"].get`;
`spikes/domino-probes/public-api.json` `paths["/api/projects/v1/projects/{projectId}"].get`.

**Sage already uses the first one.** `DominoResourceProvider.list_collaborators` calls
`GET /v4/projects/{id}/collaborators` and maps `fullName`/`userName`/`avatarUrl` into `Person`,
degrading to `[]` rather than raising (`backend/sage/resources/provider.py:1109-1136`). It is
surfaced as `GET /api/members` (`backend/sage/orchestrator/app.py:2228`,
`backend/sage/orchestrator/service.py:2708`).

**It has never been run against a real Domino.** ADR-0007 says so in as many words: *"Not
live-verified. The collaborators call is built from `spikes/domino-probes/swagger.json` and tested
against a stubbed server. It has never run against a real Domino deployment."*
(`docs/adr/0007-the-plan-document-is-durable-the-handoff-is-not.md:79-83`). Treat the existing code
path as a well-sourced guess, not as evidence.

**If a management UI is built, switch to `projectSettingsCollaborators`** — showing a collaborator
list without roles means the user cannot tell who can edit from who can only look, and the role is
exactly what decides App access.

### 2d. Remove

```
DELETE /api/projects/v1/projects/{projectId}/collaborators/{collaboratorId}
```

| | |
|---|---|
| operationId | `removeCollaboratorFromProject` |
| Description | *"Remove a collaborator from the project. **Required permissions: `ManageCollaborators`**"* |
| Path params | `projectId` *"ID of the project to remove collaborator from"*, `collaboratorId` *"ID of the collaborator to remove"* |
| 200 | `DeleteEnvelopeV1` `{success: boolean, metadata}` |
| Errors | 400, 401, 403, 404, 500 |

v4 equivalent: `DELETE /v4/projects/{projectId}/collaborators/{collaboratorId}` (`removeCollaborator`).

Source: `spikes/domino-probes/public-api.json`
`paths["/api/projects/v1/projects/{projectId}/collaborators/{collaboratorId}"].delete`;
`spikes/domino-probes/swagger.json` same path under `/v4`.

Note the public API is **asymmetric**: it has POST and DELETE for collaborators but **no GET**. A
full add/list/remove feature is either "public API for writes + v4 for the read" (what Sage would
end up with today, since the read is already v4) or all-v4.

### 2e. Idempotency — `LIVE-VERIFY`, and it matters

**Neither spec says what happens when you add a collaborator who is already on the Project.** The
documented responses are 200 / 400 / 401 / 403 / 404 / 500; there is no 409, and no note about
upsert semantics on either the public or the v4 route. Two plausible behaviours — silent role
overwrite, or a 400 — lead to different UI. Do not guess: a "share" button that 400s on a second
click is a bad enough failure to be worth one probe.

### 2f. Who is allowed to do it

`ManageCollaborators` is the API-side permission name. Domino's user-facing permission table maps
it to roles ([collaborator-permissions](https://docs.dominodatalab.com/en/latest/user_guide/7876f1/collaborator-permissions/)):

- *"Invite a collaborator"* — **Contributor and Owner**
- *"Manage collaborator permissions"* — **Contributor and Owner**

So: Project Owner (the usual Sage case, since Sage creates the Project) and Contributor can share;
Results Consumer, Launcher User and Project Importer cannot, and will get a **403 `ErrorV1`**. That
403 needs a real sentence in the UI — "you can't add people to a Project you're only a viewer on;
ask <owner> to add them" — rather than a generic failure.

One admin config key can also take the whole feature away:
`frontend.restrictCollaboratorsToOrganizations` — *"If `true`, when adding Project collaborators,
you can only specify groups, not individuals."*
([access-controls-and-collaboration](https://docs.dominodatalab.com/en/cloud/user_guide/22a752/access-controls-and-collaboration/)).
`LIVE-VERIFY` — a user picker would be wrong on a deployment with that flag set, and there is no
documented API to read the flag. The 400 from the add call is probably the only signal.

---

## 3. The published App axis

### 3a. Verified: `GRANT_BASED` is "Restricted (project collaborators)"

Already established in this repo, read back from a live app on cloud-dogfood on 2026-08-20:

| Control | Field value |
|---|---|
| Dropdown: *Restricted (project collaborators)* | `visibility: "GRANT_BASED"` — what Sage sets at create |
| Dropdown: *Anyone in Domino* | `visibility: "AUTHENTICATED"` |
| Checkbox: *Globally discoverable* | the separate top-level `discoverable` flag |

`DATA-SOURCES-RESEARCH.md:1070-1080`. Sage sets `GRANT_BASED` at publish
(`backend/sage/provision/domino.py:433, 441-448`) and reads it back before re-publishing
(`backend/sage/provision/domino.py:548-564`).

### 3b. Answered: adding a Project collaborator DOES grant the App — no second call

Domino's *Publish and share an App* page, describing the **Restricted** setting, verbatim:

> **"Only users you explicitly list and Project collaborators can view or edit the App"**

([publish-and-share-an-app, /en/latest](https://docs.dominodatalab.com/en/latest/user_guide/cd0095/publish-and-share-an-app/);
the same sentence appears on the [/en/cloud](https://docs.dominodatalab.com/en/cloud/user_guide/cd0095/publish-and-share-an-app/) copy)

That is the whole answer to question 3. The grant set for a `GRANT_BASED` App is the **union** of
two things — the explicit `accessStatuses` list, and the Project's collaborators. Sage's Apps are
`GRANT_BASED`, so a person added to the Project can open the App.

Corroborating, from the same doc: the two sharing modes are only *Restricted* and *Anyone in
Domino* (matching the two-option dropdown this repo already read live), and *"Domino enforces access
and sharing at the proxy level before any request reaches your App logic."*

Corroborating from the permission table: *"View an App"* is a listed **project collaborator**
permission ([collaborator-permissions](https://docs.dominodatalab.com/en/latest/user_guide/7876f1/collaborator-permissions/)).
It would not be a project-role permission if App access were unrelated to project membership.

**One apparent contradiction, resolved.** The cloud page
[access-controls-and-collaboration](https://docs.dominodatalab.com/en/cloud/user_guide/22a752/access-controls-and-collaboration/)
says *"Apps are permissioned independently of their parent Project."* Read in context that sentence
is about the App's **visibility mode** being independent of the **Project's visibility** setting —
a Private Project can host an `AUTHENTICATED` App and vice versa — not about the grant set. The
publish-and-share page is the specific one and is unambiguous. `LIVE-VERIFY` settles it for good
(probe 4 below), and it is worth settling: this repo has already been burned by assuming Domino
inherits permissions from a Project, in the Datasets case, where inheritance was **dropped in 5.4**
(`DATASETS-VS-ARTIFACTS-RESEARCH.md:481-507, 851-874`).

### 3c. The role you pick decides whether they see the App

From the permission table
([collaborator-permissions](https://docs.dominodatalab.com/en/latest/user_guide/7876f1/collaborator-permissions/)):

| Role | *View an App* | *Publish / unpublish an App* | *Invite users to an App* | *Invite a collaborator* |
|---|---|---|---|---|
| Project Importer | **no** | no | no | no |
| Launcher User | yes | no | no | no |
| Results Consumer | yes | no | no | no |
| Contributor | yes | yes | yes | yes |
| Owner | yes | yes | yes | yes |

**`resultsConsumer` is the right default for a "share my app" flow.** It is the smallest role that
can view the App, it is read-only, and Domino's own docs name Private-project + Results Consumer as
the *"most secure"* configuration
([collaborator-permissions](https://docs.dominodatalab.com/en/latest/user_guide/7876f1/collaborator-permissions/)).
`contributor` should be a deliberate second option, not the default — it also hands over the ability
to edit files, publish Apps and add further collaborators.

### 3d. The separate App-only axis, if it is ever needed

The App's own grant list is real, is writable, and is a **different** call. Use it only for "let
this person see the App without putting them in the Project".

**Public API — recommended shape:**

```
PATCH /api/apps/beta/apps/{appId}
{"accessStatuses": [{"userId": "<id>", "status": "ALLOWED"}]}
```

- `AppUpdateRequest` — *"All fields optional; omitted fields preserve existing values."*
- The spec classifies the fields by side effect, verbatim: *"Metadata fields (name, description,
  entryPoint, visibility, accessStatuses, discoverable) are updated in-place without restarting."*
  So a grant does **not** bounce a running App. (Contrast: reproducibility fields create a new
  version and stop the instance.)
- `AppAccessStatus` = `{userId*: string, status*: "ALLOWED" | "DENIED" | "PENDING"}`.
- `AppAccessControl` = `{visibility*, accessStatuses*, discoverable*}` where
  `visibility` enum is **`AUTHENTICATED | GRANT_BASED | GRANT_BASED_STRICT | PUBLIC`**.
- Response is the full `AppResponse`, which carries `accessStatuses` back.

Source: `spikes/domino-probes/public-api.json` `paths["/api/apps/beta/apps/{appId}"].patch`,
`components.schemas.AppUpdateRequest`, `.AppAccessStatus`, `.AppAccessControl`.

`LIVE-VERIFY` — `accessStatuses` is an array in a PATCH body, which almost certainly means
**replace, not append**. A read-modify-write would then be mandatory, and a naive one-element PATCH
would silently revoke everyone else. Probe before writing any code against it.

**v4 equivalents** (same object, older name — Apps are `modelProducts` in v4):

| Call | Body | Response |
|---|---|---|
| `POST /v4/modelProducts/{id}/grantAccess` | `{userId*, redirect?}` | `DominoId` (string) |
| `POST /v4/modelProducts/{id}/denyAccess` | `{userId*, redirect?}` | `DominoId` |
| `POST /v4/modelProducts/{id}/invite` (`bulkGrantAccess`) | `{emails*: string[]}` | `{succeeded: string[], failed: [{email, errorMessage}]}` |
| `POST /v4/modelProducts/{id}/uninvite` | `{email*}` | `DominoId` |
| `POST /v4/modelProducts/{id}/visibility` | `{visibility*: PUBLIC\|AUTHENTICATED\|GRANT_BASED\|GRANT_BASED_STRICT}` | `DominoId` |
| `GET /v4/modelProducts/consumer/{id}/access` (`canAccess`) | — | `ConsumerModelProduct` incl. `appAccessStatus: ALLOWED\|PENDING\|REQUESTABLE\|NOT_ALLOWED`; **401** returns `GrantAccessRequired` *"Current user does not have access to this model product but may request it"* |

Source: `spikes/domino-probes/swagger.json`, the `/modelProducts/...` paths and
`domino.nucleus.modelproduct.models.*` schemas.

`grantAccess` / `invite` are the nicer single-purpose calls (append semantics, no read-modify-write,
and `invite` takes **emails** so no directory lookup) but they are v4. `LIVE-VERIFY` — **is
`modelProductId` the same id as the beta-apps `id`?** This repo already established that a beta App
record carries two id spaces, a 24-hex `id` and a UUID `vanityUrl`
(`DATA-SOURCES-RESEARCH.md`, addendum 4). `modelProducts` is very likely keyed on the 24-hex `id`,
but nothing here proves it, and guessing wrong is a 404 on a sharing action.

Also present, for completeness: `POST /api/apps/beta/apps/{appId}/access/requests`
(`requestAccessToApp`) is the *requester's* side of the "Pending requests / Accept / Deny" flow the
docs describe. Sage is not the requester, so this is out of scope.

### Finding: `GRANT_BASED_STRICT` is a false refusal in `publish_guard`

`publish_guard.ALLOWED_VISIBILITY = {"GRANT_BASED", "PRIVATE", "AUTHENTICATED"}`
(`backend/sage/resources/publish_guard.py:52`). The App visibility enum, per both specs, has **four**
values — `AUTHENTICATED`, `GRANT_BASED`, **`GRANT_BASED_STRICT`**, `PUBLIC`
(`public-api.json` `components.schemas.AppAccessControl.visibility`; `swagger.json`
`domino.nucleus.modelproduct.models.VisibilityPatch`). `GRANT_BASED_STRICT` is not in the allow
list, so `open_visibility` returns `True` for it
(`backend/sage/resources/publish_guard.py:115-128`), and a re-publish of an App set to it is
refused with:

> "This app can be opened by people who are not signed in to {platformName} (its visibility is
> GRANT_BASED_STRICT) …"
> (`backend/sage/resources/publish_guard.py:231-242`)

From its name and its position in the enum, `GRANT_BASED_STRICT` is **more** restrictive than
`GRANT_BASED` — almost certainly "explicitly listed users only, project collaborators excluded",
which is exactly the App-only sharing mode §3d describes. So the sentence is not merely a
false refusal, it says the opposite of the truth.

This is the failure mode the module's own comment anticipated and priced as acceptable — *"a
deployment spelling one of the allowed settings differently costs one report and one entry, not a
hole"* (`backend/sage/resources/publish_guard.py:49-51`) — and the guard fails **closed**, so
nothing is exposed. It is cheap to fix (one entry), and it becomes reachable the moment anything
sets STRICT. `LIVE-VERIFY` the semantics before adding it, so the fix is not another guess.

Note that cloud-dogfood's sharing dropdown offers only two settings
(`DATA-SOURCES-RESEARCH.md:1070-1080`), so STRICT may not be reachable from the UI there at all —
which is why nobody has hit this.

---

## LIVE-VERIFY list

**Updated 2026-09-03 after the probe pass.** Probes 1, 2 and W1 are CLOSED — see
[Live probe results](#live-probe-results-2026-09-03). The directory, the read, and the write
*contract* are all settled. What remains needs a **real add** against a throwaway project, which no
empty-body probe can reach: the success shape, the role vocabulary, idempotency, and whether the
person can then open the App.

Ordered by how much a wrong answer would reshape the feature. All need a real
`DOMINO_API_HOST` + Bearer JWT; the recipe for running against real Domino from a laptop is in
`DOMINO-PRIMITIVES.md:245-272` (Bearer, **not** `X-Domino-Api-Key` — the latter 403s).

~~**W1. Which path actually accepts the write?**~~ **CLOSED 2026-09-03.**
`POST /api/projects/v1/projects/{id}/collaborators` returns `400` naming `id` and `role`. Build
against it. Full result in [Live probe results](#live-probe-results-2026-09-03).

1. ~~**Does a plain non-admin key get a usable user list?**~~ **CLOSED 2026-09-03.** Yes.
   `GET /api/users/v1/users?limit=500` → `200`, 397 users, as a caller holding no SysAdmin role.
   The picker is viable; the feature does not fall back to email entry.

2. ~~**Does the typeahead route work, and does it agree?**~~ **CLOSED 2026-09-03.** Yes.
   `GET /v4/users?query=subir` → `200`, 1 matching record, same `Person` shape as the v1 route.
   Bind the picker to `/v4/users` — it typeaheads *and* returns all 397 with no `query`, so one
   route serves both the search and the full list.

3. **Is adding a collaborator idempotent?**
   `POST {api}/api/projects/v1/projects/{pid}/collaborators {"id":"<uid>","role":"resultsConsumer"}`
   → expect `200 {collaborator:{id,role}, metadata}`. **Then send the identical call again**, and
   then a third with `"role":"contributor"`. Record all three statuses and bodies. Determines
   whether the UI must pre-check membership and whether re-add is an upsert.

4. **Does the new collaborator actually see the App? (question 3, settled empirically)**
   With a second account: `GET {api}/api/apps/beta/apps/{appId}` on a Sage-published
   `GRANT_BASED` App **before** the add — expect 403/404 — and again **after** probe 3 — expect
   `200` with `visibility: "GRANT_BASED"`. Also `GET {api}/v4/modelProducts/consumer/{appId}/access`
   as that account, expecting `appAccessStatus: "ALLOWED"` after. This is what turns the doc
   sentence into a verified fact and kills the "permissioned independently" ambiguity in §3b.

5. **Is a Sage App's explicit grant list empty?**
   `GET {api}/api/apps/beta/apps/{appId}` on an App Sage published and nobody re-shared; read
   `accessStatuses`. Expect `[]`. Confirms that today every viewer arrives via the Project, and
   that §3d is genuinely an optional second axis rather than something already in play.

6. **What does `GRANT_BASED_STRICT` mean?**
   `POST {api}/v4/modelProducts/{appId}/visibility {"visibility":"GRANT_BASED_STRICT"}` on a
   throwaway App, then repeat probe 4's read as the collaborator account. If the collaborator loses
   access, STRICT = explicit list only, and `publish_guard.ALLOWED_VISIBILITY` gains one entry plus
   its own refusal sentence. Do this on a throwaway App — the visibility change is not something
   Sage can set back.

7. **Is `modelProductId` the beta-apps `id`?**
   `GET {api}/v4/modelProducts/{appId}` using the 24-hex `id` from
   `GET /api/apps/beta/apps/{appId}`. Expect `200` with a matching `name`. Only needed if the v4
   grant/invite routes are chosen over the public PATCH.

8. **Does `PATCH accessStatuses` replace or append?**
   On a throwaway App with two `ALLOWED` entries, PATCH one entry, then GET. If the other entry is
   gone, every grant needs a read-modify-write. Only needed if §3d is in scope.

9. **What role does `addManyCollaborators` assign?**
   `POST {api}/v4/projects/{pid}/addManyCollaborators {"collaboratorUsernameOrEmails":["<email>"]}`
   then `GET {api}/v4/projects/{pid}/projectSettingsCollaborators` and read `role`. If it is
   `ProjectImporter`, the bulk route is unusable for App sharing (§3c) and the per-user POST is the
   only path.

10. **Does `frontend.restrictCollaboratorsToOrganizations` bite on this tenant?**
    Only reachable indirectly: attempt probe 3 with an individual user id and see whether it 400s
    with an org-related message. Low priority — it is a deployment-config edge, not the common case.

---

## Implementation addendum (2026-09-03, verified live during the build)

Written while implementing the feature this research scoped, against the same `cloud-dogfood` and
the same `quick-start` project. Everything here is a real response, and **it overrides the sections
above where they disagree** — including the probe results, which are older than these by hours.

### The project record has three spellings, and they are not interchangeable

Every one of these answers the same question — who is on this project, in which role — and the
field names differ per route. This is the single most expensive fact in this document:

| Call | Status | `ownerId` | Collaborator entry |
|---|---|---|---|
| `GET /api/projects/v1/projects/{pid}` | `200` | yes | `{"id", "role": "contributor"}` — lowercase |
| `GET /api/projects/beta/projects` (listing) | `200` | yes | `{"id", "role": "contributor"}` — lowercase |
| `GET /v4/projects/{pid}` | `200` | yes | `{"collaboratorId", "projectRole": "Contributor"}` — Pascal |
| `GET /api/projects/beta/projects/{pid}` | **`404`** | — | there is no single-project read on the beta route |

Two consequences worth stating separately:

1. **The casing split recorded in step 2 is route-specific, not global.** `/v4` reads back
   `Contributor`; the public API and the beta listing read back `contributor`. A UI that shows the
   raw platform value shows a different string depending on which read it made. Sage reads `/v4`,
   so it shows `Contributor` — which is what the design assumed.
2. **`_is_member` in `provider.py` is correct and must not be "fixed" to match `/v4`.** It reads
   `c.get("id")` against the *beta listing*, which is the route that uses `id`. Probed against a
   real account: all 6 collaborator-only projects matched, none dropped.

### `/api/projects/v1/...` does have a GET — the "no GET" finding was sub-path-scoped

Section 2c and the scoping note say the public API has no GET for collaborators. That is true of
the **`/collaborators` sub-path** and only of it. `GET /api/projects/v1/projects/{pid}` answers
`200` with both `ownerId` and the collaborator list. The verb-scoped 404 is real; it just does not
generalise to the whole prefix, which is the mistake the original wording invites.

### What Sage was built against

- Names: `GET /v4/projects/{pid}/collaborators`
- Roles + `ownerId`: `GET /v4/projects/{pid}` — the **single-project** read, not the listing.
  Searching `GET /v4/projects` for the project (as the handoff's table suggested) is a paging bug
  waiting for a builder who belongs to enough projects: the listing answers `200` without their
  project in it, and a healthy project then reports as a failed read.
- Add / remove: the public routes, exactly as sections 1 and 6 record them.

Confirmed end to end on `quick-start` with `repro-practitioner-sa`: added, read back as
`Contributor`, added again without raising (the 400 re-read path), refused correctly for an id
Domino does not know, removed. The project was left as it was found.
