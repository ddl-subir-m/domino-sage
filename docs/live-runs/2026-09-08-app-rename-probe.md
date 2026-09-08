# 2026-09-08 · Can a rename reach the deployed App? (#219)

**Answer: yes.** `PATCH /api/apps/beta/apps/{id}` with `{"name": …}` renames a deployed App in
place. It merges rather than replaces, and the URL does not move. #219 takes its
"if the API can rename" branch.

Run from a laptop against `cloud-dogfood`, as `subir_mansukhani`, with `backend/.env`'s
`GATEWAY_API_KEY` as a Bearer token (a JWT good to 2027-01-01).

## Settled first, touching nothing

- **The beta apps API answers a laptop.** `GET /v4/users/self` and `GET /api/apps/beta/apps` both
  200. #219 assumed a Builder was needed; that is true only of Sage's own publish path, where
  `_build_control_plane` takes the sidecar token unconditionally.
- **`PATCH` is routed; `PUT`, `POST` and `OPTIONS` are not.** Probed on a nil ObjectId
  (`000000000000000000000000`), so nothing could change: `PATCH` answered 400 `Cannot find app with
  nil bson id` — the handler ran — while the other three gave the router's 404 `Public api endpoint
  … not found`. There is no `Allow` header to read on this API.
- **The body schema cannot be read off the errors.** The route resolves the App before it validates
  the body, so `{}`, `{"nonsense":1}`, `{"name":"x"}` and `{"entryPoint":"app.sh"}` all return the
  same id error. A well-formed unused id gives `404 No app found with id …`.

## The App

Published through the Workbench (running as the `sageHub` pluggable workspace tool) expressly for
this probe.

| | |
|---|---|
| id | `6aa09d1d4fd92a76b288b70d` |
| project | `sage-subir-mansukhani-66a821b1-2` |
| name before | `support tickets` |
| entryPoint | `apps/app_1a08261c334003009d91e/app.sh` |
| visibility | `GRANT_BASED` |
| version | `6aa09d1d4fd92a76b288b710`, Running |
| url | `https://apps.cloud-dogfood.domino.tech/apps-internal/6aa09d1d4fd92a76b288b70d/` |

## What the API did

`PATCH /api/apps/beta/apps/6aa09d1d4fd92a76b288b70d` with body `{"name":"Sage rename probe B"}`:

| | |
|---|---|
| status | **200** |
| response | the whole App, with the new name |
| minimal body accepted? | **yes** — no need to send the App back |
| partial body destructive? | **no.** Full before/after diff was one line: `name`. Zero fields lost. It merges. |
| url moved? | **no** |
| needed a republish? | **no.** The App stayed Running and was never redeployed. |
| list endpoint agrees? | **yes** — `GET /api/apps/beta/apps?projectId=…` reports the new name, so the record the Domino UI lists from is the one that changed. |

So a rename is one call, it is safe with a partial body, and it costs no deployment.

## What the UI did

Eyeballed by Subir, same session, no republish and no reload trickery:

| page | says the new name? |
|---|---|
| the App's own page (`/modelproducts/{id}?scope=project`) | **yes** |
| the Project's App list (`/u/{owner}/{project}/apps`) | **yes** |
| the manage page Publish links to (`…/apps/{id}/{versionId}/details/overview`) | **yes** |

So all three surfaces read the record `PATCH` writes. The divergence #219 describes is fixable
with one API call at each of Sage's two write points, and nothing else.

## Not run

- Nothing was checked for a **classic**, UI-created App. Those live in another id space and never
  appear in the beta list, so this says nothing about them.
- Renaming twice, and renaming while a publish is in flight, were not tried.
- Whether `PATCH` accepts other fields (`visibility`, `entryPoint`, `description`) was not probed.
  Only `name` was sent.

## What it means for #219

Acceptance criterion 2 is the live branch: **both writers must carry the name to the
deployment** — Publish's name field and the `…` menu's Rename. `rename_app`
(`service.py:4399`) writes `displayName` plus the rail's conversation tags and stops there; it
now has an API call to make. A rename made while an App is unpublished must still reach Domino at
its first publish, which `publish_app` already does, since it sets the name at creation.

Criterion 4 still stands and still needs building: when the Domino side of a rename fails, the
local rename must survive and Sage must say that only half of it happened.

Reproduce with `scripts/app-rename-probe.sh`.
