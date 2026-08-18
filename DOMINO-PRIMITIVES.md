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
| **LLM Gateway registrations** | `GET /api/aliases` + `/accessible` | **Yes — full control plane** | The real model primitive |
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

### ANSWERED — the gateway has a full control plane API

Verified live 2026-08-18. `GET {root}/openapi.json` returns 200 unauthenticated-shape JSON:
**"Domino LLM Gateway — Next-generation LLM Gateway control plane and proxy API", v2.0.11,
OpenAPI 3.1.0.** So the panel is live data, not a feature request.

The routes that matter for a resource browser:

| Route | Returns | Use |
|-------|---------|-----|
| `GET /api/aliases` | 4 records, full detail | **The panel feed** |
| `GET /api/aliases/accessible` | `{accessible_ids, byot_alias_ids}` | **The permission filter** — id sets only, NOT a listing |
| `GET /api/providers` | 2 records | Provider config + health |
| `GET /api/alias-groups` | — | Grouping |
| `GET /v1/models` | 4 ids | OpenAI-convention listing. Simplest feed, least metadata. |
| `GET /v1/whoami` | `{resolved_identity}` | Resolves the caller |

**The panel needs two calls, not one.** `/api/aliases` for detail, `/api/aliases/accessible`
for which of them this user may use — then intersect. This is the same
permission-keyed shape as data sources, just split across two endpoints.
`byot_alias_ids` marks a distinct class: bring-your-own-token aliases.

### The alias field contract (from a live `GET /api/aliases`)

```
identity     id, name, display_name, description, status
provider     provider_id, provider_name, provider_type, provider_model, endpoint_url
capability   capabilities, api_version, auth_mode, inference_params
cost         cost_mode, effective_costs, custom_{input,output,cache_read,cache_write}_cost
budget       budget_limit, budget_period
cache        cache_enabled, cache_per_user, cache_ttl_seconds
resilience   fallback_chain, fallback_timeout_ms, fallback_triggers, retry_config
grouping     groups
secrets      extra_headers_has_value, extra_headers_masked   (already masked)
```

Three of these are panel-grade and were not anticipated:

- **`capabilities`** — which modes an alias supports. This is the field that lets a picker
  say "this one does embeddings" rather than offering every alias for every job.
- **`effective_costs`** — resolved per-alias cost. A picker can show cost *before* the user
  chooses, which connects to the existing gateway cost-tag work.
- **`display_name`** — a real human label. Note the contrast with hosted GenAI endpoints,
  where `displayName` was the connector *type*, not a name.

### Providers — how Domino-hosted models get in

`GET /api/providers` returned 2, and the first confirms the registration flow:

| name | provider_type |
|------|---------------|
| **Domino Platform** | `domino_platform` |
| OpenRouter | `openai` |

So a Domino-hosted GenAI endpoint reaches the gateway through the `domino_platform`
provider. Provider records also carry **`health_status`** and **`last_health_check`** —
a readiness signal, the gateway's analogue of a data source's `status` or an endpoint's
`Running`. A picker should surface it.

### VERIFY — this gateway has only 4 aliases, and 3 of Sage's tiers are not among them

`/api/aliases` and `/v1/models` agree: **4 aliases**, all `provider_type: openai` —
`gpt-5.4`, `GLM-5.2`, `deepseek-v4-flash-0731`, `mimo-v2.5`. Since `/api/aliases` is the
detail listing rather than a permission-filtered view, this is not a grants artefact: the
other names are absent.

`.env.example` sets `SAGE_MODEL_SOVEREIGN=qwen-2-5`,
`SAGE_MODEL_IMPLEMENT=bedrock-qwen3-coder`, `SAGE_MODEL_DEFAULT=sonnet`. **None exist here.**
Only `SAGE_MODEL_PLAN=gpt-5.4` does.

Before concluding it is broken, note the URLs differ: `.env.example` points at
`/apps/llm_gateway/v1`, while the live workspace `GATEWAY_BASE_URL` was
`/apps/bda1c28f-b516-4df0-a00f-97176c9ff46c/v1`. So there may be more than one gateway
deployment, and Sage's real target may carry the full alias set. **Check which gateway
`backend/.env` points at, and run `/api/aliases` against that one.** If it is the same
deployment, three of four model tiers reference aliases that do not exist.

It is a much larger surface than expected. Also present: `/v1/embeddings`, `/v1/responses`,
`/anthropic/v1/messages` (+ `count_tokens`), a files API, sync and batch APIs
(`/v1/batches`, `/anthropic/v1/messages/batches`), a guardrails and guardrail-evaluator
admin API, and a full usage API matching `gateway-questions.md`.

**Embeddings and batches are unclaimed capability.** Neither appears in Sage's current
design, and both are directly useful to a built app — embeddings for search over attached
data, batches for bulk work that would time out inline.

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
