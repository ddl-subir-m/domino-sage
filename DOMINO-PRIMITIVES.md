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
| Hosted GenAI endpoints | `/api/gen-ai/beta/endpoints` | Callable, but **not the call path** | Discovery only — see below |
| **LLM Gateway registrations** | the gateway App's own API | **OPEN — see below** | The real model primitive |
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
which owns Sage's model aliases. Users register both Domino-hosted and external models in the **LLM Gateway** and call
that; they do not call the AI Gateway directly. So "which LLMs are available" is a question
for the LLM Gateway's registry, not for any `/api/aigateway/v1` endpoint.

## The call path is the LLM Gateway, not the endpoint

**Corrected 2026-08-18 by the user.** Earlier revisions of this file treated hosted GenAI
endpoints as a direct call target. That is not how Domino is used:

1. A user **deploys** a GenAI model in Domino -> `/api/gen-ai/beta/endpoints`, a vLLM
   endpoint with a public URL.
2. That endpoint is then **registered in the LLM Gateway** along with external models —
   base URL, which modes are supported, and so on.
3. Apps and agents **call the LLM Gateway**, which routes to the Domino-hosted endpoint or
   to an external provider. **They do not call the endpoint, or the AI Gateway, directly.**

So the composable primitive for Sage is **what is registered in the LLM Gateway**, and
hosted GenAI endpoints sit *upstream* of it. Sage already routes every model call through
the gateway, which means the calling path is built — what is missing is the ability to
**enumerate** what the gateway has registered.

### OPEN, and now the decisive question

**Does the LLM Gateway expose a list of its registrations?** That list is exactly what a
"which LLMs are available" panel needs. If it exists, the panel is live data. If not, Sage
cannot enumerate registrations and this is a feature request for the gateway's owner rather
than work Sage can do alone.

Note what `open_models.py` actually is, since it is easy to over-read: its
`OPEN_WEIGHT_MODELS` is the catalog for **openai mode** (DeepSeek, Qwen, Moonshot, called
direct with per-model base URLs and keys). In **Domino gateway mode** there is no such
catalog — the docstring says "the gateway owns model routing", and Sage sends an alias from
`SAGE_MODEL_*` env. So the hardcoded list is not a stand-in for the gateway's registry.

Probe: `spikes/domino-probes/gateway_models_probe.sh`.

### What direct calls established anyway — still useful at REGISTRATION time

These facts do not change; they just apply to whoever registers an endpoint in the gateway,
not to an app calling it.

**The served model id is NOT Domino's model name.** vLLM served `id: "."` (started from a
local path) while Domino reported
`modelSource.registeredModel.modelName: "qwen-2-5-14b"`. Calling with Domino's name returns
`404 "The model qwen-2-5-14b does not exist"`. So a gateway registration must carry the id
from `GET <url>/v1/models`, not the Domino metadata name. If Sage ever helps a user
register an endpoint, this is the field that will bite.

**Auth is required, and an unauthenticated call looks like success.** The sidecar bearer
token works (`/v1/models` 200 in 0.28s). Without it the request still returns **200 — with
a Keycloak login page in HTML**. Clients must inspect the body, not just the status.

**Status is the gating field.** Of 18 endpoints, exactly **1** was `Running` — 12 `Stopped`,
4 `Failed`, 1 `BuildFailed`. Relevant to a "what could I register?" view.

**Cross-project access works.** The target was `generalAccess: Consumer` in another user's
project and it answered, so Consumer grants call rights, not just visibility.

**OPEN — can `generalAccess: Viewer` be called, or only seen?** 13 of 18 are `Viewer`, so
this sizes the pool at 5 or 18. Lower priority now that direct calling is not the path.

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
