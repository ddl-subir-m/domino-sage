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

### VERIFIED — what `capabilities` and `effective_costs` actually contain

Both fields were named above but never opened. Probed on Sage's own gateway (2026-08-19, all 12
registrations), building the Resource Browser's LLM Alias rows (#5):

**`effective_costs` is a flat `{"input": float, "output": float}` map** — not nested, not
per-token-class. No unit is reported anywhere in the record.

**The unit is USD per 1M tokens** — confirmed for Sage's purposes, and the API sends nothing to
say so. The figures agree: `sonnet` reads `3 / 15`, `opus` `5 / 25`, `gpt-5.4` `2.5 / 15`, which are
the vendors' published per-1M-token USD rates. A picker can print a currency.

**`{"input": 1.0, "output": 2.0}` is the gateway falling back, not a price.** Six unrelated
aliases report exactly that (`bedrock-qwen3-coder`, `qwen-2-5`, `gpt-5.4-nano`, `haiku`,
`local-domino-llm`, `nova`). `cost_mode` is `"default"` on **all 12**, so it cannot tell a set
rate from an unset one, and value-matching `{1, 2}` would guess. Sage shows the numbers verbatim
and points the tooltip at the gateway's Usage & cost dashboard, which prices real calls.

**`capabilities` is less discriminating than it looks.** `streaming` and `responses` appear on
**12 of 12**, so they separate nothing; `chat` and `tools` on almost all; only `vision` (1) and
`embeddings` actually pick a model out. A capability chip is worth showing, but it is not a filter.

Two smaller shapes a row has to survive: `display_name` often equals `name` (`bedrock-qwen3-coder`),
and `description` is often `""` — or a copy of the name (`gpt-5.4`).

### Providers — how Domino-hosted models get in

`GET /api/providers` returned 2, and the first confirms the registration flow:

| name | provider_type |
|------|---------------|
| **Domino Platform** | `domino_platform` |
| OpenRouter | `openai` |

So a Domino-hosted GenAI endpoint reaches the gateway through the `domino_platform`
provider. Provider records also carry **`health_status`** and **`last_health_check`** —
which look like a readiness signal, the gateway's analogue of a data source's `status` or an
endpoint's `Running`. **They are not one. Do not surface them.** Measured 2026-08-21: see
"health_status is stale and wrong" below.

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
   alias `status` would label a dead model "active". That much still holds — but the
   remedy named here was wrong. **Do not use the provider's health field either**; it was
   measured on 2026-08-21 and is stale and wrong (below). Endpoint status comes from
   `GET /api/gen-ai/beta/endpoints`, joined on `url`.
2. **Sage has no model-availability preflight.** There are `/healthz` endpoints
   (`shim/app.py:69`, `hub/app.py:133`) but no check that the configured aliases resolve. So
   a stopped sovereign endpoint surfaces as an opaque mid-build failure rather than a clear
   startup error — the same shape as the mid-build network errors already seen.

**Recommendation:** deploy Sage's own sovereign endpoint in the Sage project so it owns the
lifecycle, and add a preflight that resolves every configured `SAGE_MODEL_*` alias against
`/v1/models` at startup. The preflight is small and would convert a confusing mid-build
failure into a legible one.

### MEASURED 2026-08-21 — how preflight can see a stopped endpoint (#21)

`spikes/domino-probes/alias_endpoint_join_probe.py`, run on cloud-dogfood against
`/apps/llm_gateway/v1`. 14 aliases, 8 accessible, 18 endpoints, 1 Running.

**Status costs one call, deployment-wide.** `GET /api/gen-ai/beta/endpoints` answered **200
unscoped** for a normal (non-admin) caller. `projectId` is an optional query parameter, so it
is not a call per slot or per Binding. Response is `ModelEndpointsListingV1 {items: [...]}`;
status lives at `currentVersion.status`, and `currentVersion` is **optional** — an endpoint
with no version has no status at all. The enum (`ModelEndpointStatusV1`) is `Building,
BuildFailed, Starting, Running, Stopping, Stopped, Failed, Unknown` — richer than
stopped-versus-gone, and it separates three different remedies: start it, replace it, wait.

**The join key is `url`, and it arrives free.** The alias record already carries
`endpoint_url`; `provider.py:772` already fetches `/api/aliases` and the parse at `:592`
throws the field away. Matching is on the endpoint's **`url`**, after stripping the trailing
`/v1` from `endpoint_url` — measured `{'id': 0, 'url': 2, 'vanityUrl': 0}`. The line above
that calls it "that vanity id" is loose; the value sits in a `url` path and matches `url`.

**Missing the join is the normal case, not an edge case.** 12 of 14 aliases carry no
`endpoint_url` at all — every `anthropic`, `bedrock`, `vertex` and `openai` one. Only
`qwen-2-5` and `local-domino-llm` join. So "not a hosted endpoint" must be a first-class
answer that reads as *unknown*, never as *stopped*.

**`health_status` is stale and wrong — the alternative is rejected.** The field is populated
(`healthy`, `error`, `unknown`), which is why it looked usable. But the `qwen-2-5-14b` vllm
provider reports **`health='error'` while its endpoint is `Running`**, off a
`last_health_check` of 2026-07-20 — a month stale. `Domino Platform` reports `healthy` with
`last_health_check: None`, never checked at all. A source that calls a working model broken is
worse than no source. It is also per-provider, not per-alias, so it is the wrong granularity
regardless.

**Still open: does a stopped endpoint's alias stay in `/v1/models`?** One case exists —
`local-domino-llm` points at the `Stopped` `Mistral-7B-Instruct-v02` and is absent from
`/v1/models` — but it is **confounded**: that alias is also absent from
`/api/aliases/accessible`, so the caller holds no grant for it and the absence says nothing
about status. Settle it by having `local-domino-llm` granted to the Sage caller and re-running;
it already points at a stopped endpoint, so no endpoint needs stopping. Do **not** stop
`qwen-2-5` for the experiment: it is `Consumer` access in another team's project and the only
Running endpoint of 18.

**This does not gate the work.** Either answer needs the same listing and the same `url` join.
If the alias stays in `/v1/models`, preflight needs a new check. If it drops out,
`unresolved_slots` already fires — but its message says the alias is not registered and tells
the creator to register it, when the alias *is* registered and the endpoint is merely stopped.
Wrong remedy, same fix required to tell the two apart.

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

**One exception, and it is the whole reason it is worth having:** anything the app's *browser*
calls runs as the **viewer**, because it carries the viewer's own Domino session cookie. That
does not extend to the app's server, and it only reaches hosts the app's page is same-origin
with — which, for a Domino App, includes every other Domino App. See the next section.

The accepted design is therefore **creator-access inheritance**: the app uses its creator's
access, and viewers inherit it. Two guards make that safe, and both are enforceable because
Sage publishes the app:

1. Runtime querying only where the credential is **shared**, never personal.
2. **Never publish a resource-querying app as `PUBLIC`.** Authenticated at minimum.

See `DATA-SOURCES-RESEARCH.md` for why guard 2 is a requirement rather than polish.

## VERIFIED — the browser path: same-origin calls run as the viewer

Probed live on cloud-dogfood, 2026-08-19, from a signed-in Chrome tab. This is what issue #7
is built on, and it is the one place a Sage-published app escapes creator-access inheritance.

A published app is served from `apps.<domino-host>/apps-internal/<id>/`. The LLM Gateway is
another Domino App **on that same host**, at `apps.<domino-host>/apps/llm_gateway/v1`. So a
`fetch` from the app's page is same-origin: **no CORS, no preflight, no key in the page, and
no server hop.** Cookies alone, with **no `Authorization` header and no CSRF token**:

| Request | Result |
|---|---|
| `GET /v1/models` | 200, 6 aliases. **`id` IS the alias name** — the value a call's `model` field takes |
| `POST /v1/chat/completions` | 200, a real completion |
| the same, plus `X-LLM-Tag-sage-*` headers | 200, and the tags land |
| the same, plus `"stream": true` | 200 `text/event-stream` |
| `GET /v1/whoami` | resolves the **browsing** user, not the app's publisher |
| `GET /api/usage/tags` | `sage-component=built-app` is there, and queryable |

**The catch: a browser call carries no project context.** `/v1/whoami` returns an empty
`project_name` for it, so the gateway's first-class per-project columns are blank for this
traffic. A `sage-project` tag is the only thing that says which app the spend came from —
and tag keys the gateway reserves (`user`, `model`, `alias`, `project`, `cost`, …) are
**silently dropped**, so every key has to be namespaced.

**Two failure modes worth knowing before you debug them.** A signed-out session is served an
**HTML login page with a 200**, not a 401 — a body that will not parse means the session, not
the gateway. And route names are not guessable: `GET /apps/llm_gateway/openapi.json` and read
the real paths (`/apps/llm_gateway/api/usage/mine` is a 404; the real one is
`/api/usage/mine/logs`).

**Why this is better than a server hop, not just cheaper.** A hop would spend the publisher's
grant on every viewer — the exact sharing that creator-access inheritance has to be guarded
against. From the browser, each viewer spends their own grant and the usage log attributes it
to them. The cost is that availability becomes a property of the **viewer**: an app that skips
an on-load availability check works perfectly for the creator who picked the model and fails on
a button press for the colleague they sent it to.

## VERIFIED — the browser path does NOT carry over to Model APIs

Probed live on cloud-dogfood, 2026-08-20, against a running Model API
(`/models/6a8727f40ff0450030085fb3/latest/model`). This is the counterpart to the section
above, and it settles issue #9: the same-origin recipe that makes #7 work **cannot be
repeated for a Model API**, for two independent reasons.

A Model API is not an App. It is served from the **main** host, and the apps ingress does not
route to it — `apps.<host>/models/<id>/latest/model` is a **404** where the main host answers
**401** on the identical path. So there is no same-origin variant to fall back to. Every call
from a published app's page is cross-origin.

**The cross-origin call is allowed, but only uncredentialed.** The preflight succeeds:

| Request | Result |
|---|---|
| `OPTIONS`, `Origin: apps.<host>`, `Access-Control-Request-Method: POST` | **204** — preflight passes |
| response headers | `access-control-allow-origin: *`, `allow-methods: POST`, `allow-headers: authorization,content-type` |
| `POST`, no credentials | **401**, `www-authenticate: Basic realm="closed site"` |
| `POST`, with a `Cookie` header | **401**, headers byte-identical — no `Vary: Origin`, no `Allow-Credentials` |

**`Access-Control-Allow-Origin: *` is the wall, and it is a spec-level one.** A wildcard with
no `Access-Control-Allow-Credentials: true` means a browser will **refuse** the response to any
`fetch(..., {credentials: 'include'})`. The viewer's Domino session cookie therefore cannot
reach a Model API from a page — not "is rejected by Domino", but *is never sent, and the
response is discarded even if it were*. The headers are identical across a 204 preflight, an
unauthenticated 401, and a cookie-bearing 401, which says this is static ingress config rather
than anything that turns on when a credential shows up.

**The credential it wants is a shared secret, and Domino's own sample puts it in the page.**
`www-authenticate: Basic` means the model access token. The snippet Domino shows on the model's
Overview page is a static HTML file with `var accessToken = "..."` in plain sight, sent as
`Basic btoa(token + ":" + token)`. So the browser call is not merely possible, it is the vendor's
documented pattern — and it is uncredentialed in the CORS sense, which is why `ACAO: *` does not
block it.

**But it authenticates the token, not the person.** Every viewer of the page presents the same
secret and gets identical access, and that secret is readable in devtools and replayable from
anywhere until it is rotated. The token is at least narrow — one model, revocable — but it is not
an identity.

**So #9's premise does not hold, and criterion 2 turns out to be a means rather than an end.**
The issue asks for the browser call *so that* each viewer's own Model API permissions apply. The
browser call does not deliver that; nothing does. Once per-viewer identity is off the table, the
browser buys nothing over a server hop except exposure of the secret — the access granted is the
same shared access either way. A server hop keeps the token out of the page and is therefore
strictly better on every axis that survives.

**Neither path is automatable today.** There is no model-access-token endpoint in either spec —
not in `public-api.json`, not in the v4 swagger. (`/modelDeployments/{id}/credentials` is the
SageMaker STS one, unrelated.) The token is copied by hand from the model's Overview page, so any
generated Model API call needs the creator to paste a secret into Sage first. **Confirmed end to end, 2026-08-20:** `Basic base64(token:token)` with the model's own access
token returns **200** with a real result. The same token as `Bearer`, as `X-Domino-Api-Key`, and
as Basic with an empty username all return **401** — so the endpoint accepts exactly one
credential in exactly one shape.

Read the codes carefully when probing this: **401 is refused at the door, 400 is authenticated**
and means only that the model disliked the body. A 400 is a pass. Domino's sample snippet sends
`{"data":{"start":1,"stop":100}}`, which 400s against any model that does not take `start` and
`stop`, and that is worth its own note below.

**No credential Sage can obtain programmatically opens a Model API.** Every platform identity was
swept in all four shapes, from the owner's own workspace, against a private model:

| Credential | Bearer | `X-Domino-Api-Key` | Basic `(v:v)` | Basic `(:v)` |
|---|---|---|---|---|
| Model access token | 401 | 401 | **200** | 401 |
| Sidecar JWT (`/access-token`) | 401 | 401 | 401 | 401 |
| User API key | 401 | 401 | 401 | 401 |
| Personal access token (PAT) | 401 | 401 | 401 | 401 |

The sidecar token is bare, so none of those failed on a doubled `Bearer ` prefix. The PAT mattered
most because `POST /api/pat/v1/tokens` can mint one from a published app's sidecar — it is refused
too, so that door is shut.

**Model invocation is a separate auth domain from the Domino REST API, and the docs only describe
the latter.** Domino's documentation says to authenticate with the `X-Domino-Api-Key` header, and
that is correct — for the platform API. The same key in the same header returns **200** on
`/v4/users/self` and **401** on a Model API, in the same shell, seconds apart. So the 401s above are
genuine refusals rather than a stale credential, and the header is not merely unsupported at the
model endpoint: its CORS preflight allows exactly `authorization, content-type`, so a browser could
not send `X-Domino-Api-Key` cross-origin even if the model wanted it.

The SDK is not a way round it either. `dominodatalab` 1.4.8 has no method that fetches a model
access token and none that invokes a model; its model surface is publish, versions and export. It
wraps the REST API, and the REST API does not vend the secret: `ModelApiAccessToken` is metadata
only — `id`, `name`, `created`, `createdBy`, `lastGenerated`, `lastGeneratedBy`, never the value.
`lastGenerated` implies a generate action, but no route in any of the three specs performs one, and
`ModelApiUpdateRequest` cannot set tokens either.

The Overview page does not fetch it either. Sniffed live in the browser: a full load of
`/models/{id}/overview` makes no call that returns a token — the only same-origin traffic is
`activeStatus` polling and `v4/users/notifications/unreadStatus`. The token is **server-rendered
into the document**. Scanning the returned HTML, the only 64-character blob that is not New Relic
telemetry sits *outside* every `<script>` tag and appears 15 times, once per language sample — it is
markup in the code snippet, not data the page fetched. Nothing mints it client-side: none of the
page's 24 same-origin bundles holds a route string matching `accessToken`, `access-token`,
`modelApiKey` or `apiKey`.

So the manual paste is not a gap in the public spec that some internal endpoint quietly fills. There
is no endpoint.

**So a model access token is copied by hand from the Overview page, and there is no alternative.**
Any generated Model API call — browser or server — requires the creator to paste a secret into
Sage. That is a product decision to take deliberately, not a detail to absorb: it is a manual
credential step in a tool whose pitch is that you describe an app and it appears.

What Sage records today — the Binding — is unaffected either way.

**One id, not two.** VERIFIED live 2026-08-20: `GET /api/modelServing/v1/modelApis/{id}` answers 200
for the id taken straight out of `/models/{id}/overview`, and its `id` field equals it. So the id the
Resource browser lists (`parse_model_apis` reads `rec["id"]`) is the same id that appears in the
invocation URL a creator pastes, which is what lets Sage tell a snippet copied from the wrong
Overview tab from the right one. `activeVersion.id` is a *different* id and is not in that URL — the
version segment is `latest` or `activeVersion.number`.

Reopening this needs a **platform** change on the model ingress: echo the request `Origin` with
`Access-Control-Allow-Credentials: true`, *and* accept a Domino session as a credential for model
invocation. Both, not either.

## The Model API sample snippet does not know the model's signature

Domino's Overview page shows a ready-to-run snippet for every Model API. Its body is
**boilerplate**: `{"data": {"start": 1, "stop": 100}}`, regardless of what the deployed function
takes. Against `predict(score)` it returns
`400 {"error":{"message":"predict() got an unexpected keyword argument 'start'"}}`.

So the platform does not expose the input shape anywhere a caller can read it. The listing carries
no schema (`source.file` and `source.function` only), the snippet carries a placeholder, and there
is no signature endpoint. **Anything that generates a call to a Model API has to learn the input
shape from the creator** — which is exactly the "creator pastes one sample request" design chosen
for #9 before any of this was probed. That decision now has evidence behind it rather than being
the least-bad guess.
