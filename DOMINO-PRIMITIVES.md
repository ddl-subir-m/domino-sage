# Domino composable primitives — verified inventory

Companion to `DATA-SOURCES-RESEARCH.md`, which covers data sources in depth. This one
covers the **other** primitives the resource-browser feedback asks Sage to surface.

Every fact below was verified live on cloud-dogfood (project `Sage`) on 2026-08-18 with
`spikes/domino-probes/primitives_probe.sh` and `genai_endpoint_probe.py`, unless marked
OPEN. The inventory itself comes from the public spec's own tag list
(`spikes/domino-probes/public-api.json`, "Domino Public API" 6.4.0).

## What counts as composable

Composable means **a built app can call it at runtime**.

| Primitive | Endpoint | Use proven? | Verdict |
|-----------|----------|-------------|---------|
| Data sources | `/api/datasource/v1/datasources` | Yes — query works | Build this first |
| **Hosted GenAI endpoints** | `/api/gen-ai/beta/endpoints` | **Yes — callable, OpenAI-compatible** | Best second |
| Model APIs | `/api/modelServing/v1/modelApis` | n/a — none exist in this project | Accessible, nothing to test |
| Model Deployments | `/api/modelServing/v1/modelDeployments` | No | Reachable, untested |
| Registered models | `/api/registeredmodels/v1` | n/a — discovery only | Done |
| Datasets | `/api/datasetrw/v1` | Already shipped in Sage | — |

**Not primitives.** Environments and hardware tiers (build config); projects, users, orgs,
PATs, service accounts (plumbing); cost, billing tags, audit trail (governance — Sage
already links gateway usage); jobs and HPC (batch, async); custom metrics and alerts
(monitoring); extensions (Domino's own plugin installer — relevant to how *Sage* ships,
not to apps).

**Excluded by decision.** Domino's built-in **AI Gateway** (`/api/aigateway/v1`) is an
MLflow-based feature Sage does not plan to use. It is NOT the gateway Sage routes through —
that is a separately deployed **LLM Gateway** App (`GATEWAY_BASE_URL` -> `/apps/<id>/v1`),
which owns Sage's model aliases. So "which LLMs are available" reads Sage's own config
(`MODELS.md`), not a Domino discovery endpoint, and `gateway/open_models.py` being
hardcoded is correct rather than a gap.

## Hosted GenAI endpoints — the integration rules

Self-hosted vLLM models, each with a public URL of the form
`https://apps.<host>/endpoints/<vanityUrl>/`.

**1. The served model id is NOT Domino's model name.** This is the trap.

vLLM served `id: "."` (it was started from a local path) while Domino reported
`modelSource.registeredModel.modelName: "qwen-2-5-14b"`. Calling with Domino's name returns
`404 {"message":"The model qwen-2-5-14b does not exist"}`.

**Always resolve the id from `GET <url>/v1/models` first** — in the picker, and in any code
the build agent generates. Anything else 404s.

**2. Auth is required, and an unauthenticated call looks like success.** The sidecar bearer
token (`$DOMINO_API_PROXY/access-token`) works: `/v1/models` returned 200 in 0.28s. Without
it the request also returns **200 — but the body is a Keycloak login page in HTML**, not
JSON. Any client must check the body, not just the status.

**3. Status is the gating field.** Of 18 endpoints, **exactly 1 was `Running`**. The rest
were `Stopped` (12), `Failed` (4), or `BuildFailed` (1). A picker that ignores
`currentVersion.status` mostly offers dead endpoints.

**4. Cross-project access works.** The target was `generalAccess: Consumer` in a project
owned by someone else, and it answered. **Consumer grants call rights, not just
visibility.**

**5. OPEN — can `generalAccess: Viewer` be called, or only seen?** Untested: the only
`Running` endpoint was `Consumer`. This matters, because **13 of 18 endpoints are `Viewer`**.
If Viewer is view-only, the usable pool is 5 endpoints, not 18, and the picker must filter
on access as well as status. Settle this from the docs or by asking an endpoint owner — it
does not need a probe.

## Model APIs — the 403 was a probe bug, not a permission wall

An unscoped `GET /api/modelServing/v1/modelApis` returns
`403 "not authorized to view access configuration"`. Adding `projectId` returns **200 with
`count: 0`** — the endpoint is fine, the `Sage` project simply has no model APIs. Always
scope this call to a project.

`modelDeployments` needs no scope: 1 deployment, `state: RUNNING`, target
`SagemakerModelDeployment v1`.

## Model Deployments carry a gift worth using

The deployment response embeds an `examples` array of **runnable Python with the real IDs
already filled in** — fetch scoped credentials, build the boto3 session, invoke the
endpoint. Operation types seen: `INVOKE_ENDPOINT`, `DOWNLOAD_INSTANCE_LOGS`,
`DOWNLOAD_BUILD_LOGS`.

So the resource browser could hand the build agent **Domino's own official snippet** for
the primitive the user picked, instead of Sage inventing integration code.

`GET /modelDeployments/{id}/credentials/{operationType}` vends short-lived AWS STS
credentials (~1 hour). Note the limits: they are **SageMaker-specific**, and they are
fetched through `DOMINO_API_PROXY` — the **publisher's** sidecar in a published app. So they
scope *what a credential can do*, not *whose* it is. They do not change app identity.

## Identity, across all primitives

A published Domino App runs as **its publisher, regardless of viewer**. Identity
propagation exists but is selectable **only by admins at publish time**, and Sage's users
are not admins — so per-viewer identity is structurally unavailable to Sage-published apps,
not merely deferred.

The accepted design is therefore **creator-access inheritance**: the app uses its creator's
access, and viewers inherit it. Two guards make that safe, and both are enforceable because
Sage publishes the app:

1. Runtime querying only where the credential is **shared**, never personal.
2. **Never publish a resource-querying app as `PUBLIC`.** Authenticated at minimum.

See `DATA-SOURCES-RESEARCH.md` for why guard 2 is a requirement rather than polish.
