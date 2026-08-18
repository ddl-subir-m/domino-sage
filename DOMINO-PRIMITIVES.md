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

### RESOLVED — Sage's gateway is fine; the scare was the wrong deployment

Two gateway deployments exist on cloud-dogfood, both reporting v2.0.11:

| where | `GATEWAY_BASE_URL` | aliases | Sage's 4 defaults present? |
|-------|--------------------|---------|----------------------------|
| `backend/.env` | `/apps/llm_gateway/v1` | 12 registered, 6 accessible | **yes, all 4** |
| live workspace env | `/apps/bda1c28f-.../v1` | 4 | only `gpt-5.4` |

On Sage's own gateway, `/v1/models` returns `gpt-5.4`, `bedrock-qwen3-coder`, `opus`,
`sonnet`, `qwen-2-5`, `etan-opus-4.6`. The code defaults
(`shim/app.py:51-56`: `qwen-2-5` for the three sovereign slots, `gpt-5.4` for plan,
`bedrock-qwen3-coder` for implement, `sonnet` for ask) all resolve. **No drift.**

The workspace env pointing at a different, sparser gateway is worth knowing, but it is a
project-env fact, not a Sage bug.

### 12 registered vs 6 accessible — the panel MUST filter

`/api/aliases` returned **12**; `/v1/models` returned **6**. Registered but not accessible
to this caller: `etan-opus-4.8`, `etan-take2-opus-4-8`, `gpt-5.4-nano`, `haiku`,
`local-domino-llm`, `nova`.

A panel fed from `/api/aliases` alone would offer **twice** the models that actually work.
This is the data-source permission lesson again, now with a 2:1 ratio.

**The clean recipe — and it is simpler than intersecting id sets:**

```
GET /v1/models    -> the accessible set (already filtered for this caller)
GET /api/aliases  -> enrich each by name: capabilities, effective_costs,
                     display_name, provider_type, status
join on id/name
```

`/api/aliases/accessible` (`{accessible_ids, byot_alias_ids}`) is then only needed if you
want the BYOT distinction, since `/v1/models` already encodes accessibility.

### Providers — the hosted-endpoint path is visible, and it has two shapes

`GET /api/providers` returned **6**:

| name | provider_type |
|------|---------------|
| `ANTHROPIC_API_KEY` | `anthropic` |
| `anthropic-etan` | `anthropic` |
| `bedrock` | `bedrock` |
| `openai_rnd` | `openai` |
| **`Domino Platform`** | **`domino_platform`** |
| **`qwen-2-5-14b`** | **`vllm`** |

So a Domino-hosted model enters the gateway as a `domino_platform` or `vllm` provider. Note
the second provider's name — `qwen-2-5-14b` — is exactly the
`modelSource.registeredModel.modelName` of the one **`Running`** hosted GenAI endpoint found
earlier. The registration flow is not theoretical here; it is in use.

### CONFIRMED — Sage's sovereign tier rides an endpoint Sage does not own

`GET /api/aliases` on Sage's gateway, for the `qwen-2-5` alias:

```json
{
  "id": "50840d140ec646b69cdf6ff6b0fc2ac0",
  "provider_name": "Domino Platform",
  "provider_type": "domino_platform",
  "provider_model": "qwen-2-5",
  "endpoint_url": "https://apps.cloud-dogfood.domino.tech/endpoints/308f788c-be6e-45a3-8731-21b32cb40cee/v1",
  "status": "active",
  "capabilities": ["chat", "tools"]
}
```

That vanity id is **exactly** the one hosted GenAI endpoint found `Running` — `qwen-2-5`,
registered model `qwen-2-5-14b`, `generalAccess: Consumer`, in project
**`IT-Triage-NVIDIA-Agents` owned by `andrea_lowe`**.

`qwen-2-5` is Sage's alias for **all three sovereign slots**
(`shim/app.py:51-53`). So the sovereign guarantee — Sage's zero-vendor story, see
`SPIKE-REPORT.md` — currently depends on another team's vLLM endpoint staying up. Sage can
call it (`Consumer` grants call rights) but has no control over its lifecycle, and **17 of
18 hosted endpoints were `Stopped` or `Failed`** when surveyed, because they cost GPU.

Good news in the same record: `capabilities: ["chat", "tools"]` confirms the sovereign model
supports tool calling, which OpenCode needs — and it confirms `capabilities` is a real,
populated field a picker can rely on.

**Two gaps this exposes.**

1. **Alias `status` does not reflect endpoint health.** `status: "active"` is alias
   configuration. Nothing suggests it flips when the endpoint behind it stops — that is what
   the provider record's `health_status` and `last_health_check` are for. So a picker showing
   alias `status` would label a dead model "active". Use the provider's health field.
   (Unverified in the stopped case; it would need the endpoint stopped to prove.)
2. **Sage has no model-availability preflight.** There are `/healthz` endpoints
   (`shim/app.py:69`, `hub/app.py:133`) but no check that the configured aliases resolve. So
   a stopped sovereign endpoint surfaces as an opaque mid-build failure rather than a clear
   startup error — the same shape as the mid-build network errors already seen.

**Recommendation:** deploy Sage's own sovereign endpoint in the Sage project so it owns the
lifecycle, and add a preflight that resolves every configured `SAGE_MODEL_*` alias against
`/v1/models` at startup. The preflight is small and would convert a confusing mid-build
failure into a legible one.

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
