# Gateway models + API cheatsheet

Source: the Domino LLM Gateway (repo: etanlightstone/LLM_gateway). All models are reachable via
one OpenAI-compatible endpoint, selectable by the gateway **alias** name in the `model` field.

## Model catalog (aliases)

| Alias | Backing model | Provider | Tier | Cost in/out (per 1M) |
|-------|---------------|----------|------|----------------------|
| `qwen-2-5` | qwen-2-5 | **Domino Platform** | **sovereign** | $1 / $2 |
| `local-domino-llm` | Mistral-7B-Instruct-v02 | **Domino Platform** | **sovereign** | $1 / $2 |
| `bedrock-qwen3-coder` | qwen3-coder-30b | Bedrock | vendor (coder) | $1 / $2 |
| `gpt-5.4` | gpt-5.4 | OpenAI | vendor | $2.5 / $15 |
| `gpt-5.4-nano` | gpt-5.4-nano | OpenAI | vendor | $1 / $2 |
| `sonnet` | claude-sonnet-4-6 | Anthropic | vendor | $3 / $15 |
| `haiku` | claude-haiku-4-5 | Anthropic | vendor | $1 / $2 |
| `opus` / `etan-opus-4.6` | claude-opus-4-6 | Anthropic | vendor | $5 / $25 |
| `etan-opus-4.8` / `etan-take2-opus-4-8` | claude-opus-4-8 | Anthropic | vendor | $1 / $2 |
| `nova` | amazon.nova-pro-v1:0 | Bedrock | vendor | $1 / $2 |
| `domino/gemini-3.7-flash` | gemini-3.7-flash | Vertex (GCP) | vendor | $0.75 / $3.75 |

`domino/gemini-3.7-flash` carries its `domino/` prefix as part of the alias name — the whole string
is what `model` takes, and the slash is not a provider separator (see `unresolved_slots`). Agentic
turns on it need `provider.sage-gateway.options.name` pinned to `"google"` or every build fails on
its first tool result: ADR-0031.

**Sovereign tier = `Domino Platform` provider** (`qwen-2-5`, `local-domino-llm`). These run on
Domino infra and are what the sensitivity lock routes to. Everything else is a vendor API.

## Our tier mapping (sage ModelCatalog — override via env)

| Role | Default alias | Env var | Why |
|------|---------------|---------|-----|
| sovereign | `qwen-2-5` | `SAGE_MODEL_SOVEREIGN` | on-Domino, code-capable |
| plan (strong) | `gpt-5.4` | `SAGE_MODEL_PLAN` | strong reasoning for the plan phase |
| implement (cheap) | `bedrock-qwen3-coder` | `SAGE_MODEL_IMPLEMENT` | coder-tuned, cheap |
| default | `sonnet` | `SAGE_MODEL_DEFAULT` | solid general default |

These are taste calls — revisit after the spike measures which sovereign model actually produces
working React on the small tier.

## Gateway API (confirmed from the repo)

- **Base URL:** `https://<host>/apps/<id>/v1` (OpenAI-shape). `POST …/v1/chat/completions`.
  Dogfood instance: `https://apps.cloud-dogfood.domino.tech/apps/llm_gateway/v1`.
  Also an Anthropic-shape ingress at `/anthropic/v1/messages`.
- **Auth:** `Authorization: Bearer <token>` where token is a gateway token (`dgw_…`), a Domino
  PAT, or a workspace sidecar JWT from `http://localhost:8899/access-token`. (OpenAI SDK:
  `api_key="dgw_…"`. Anthropic SDK: use `auth_token=`, NOT `api_key=`.)
- **Per-request tags:** `X-LLM-Tag-<name>: <value>` → stored in the usage `tags` JSON. Keys are
  lowercased with `_`→`-`; max 20 tags, 64-char keys, 256-char values (over-long is truncated, not
  rejected). We send `sage-source`, `sage-phase`, `sage-mode`, `sage-component`, `sage-session`,
  `sage-version`, `sage-project` — all `sage-`-namespaced (see `gateway/client.py` CostLabels).
  **The bare keys `project`, `project-id`, `project-name`, `model`, `user`, `org`, `cost`, `tokens`
  are in the gateway's `RESERVED_TAG_KEYS` and are silently dropped at ingest** — no error, the tag
  just never arrives. Untagged calls land in the "unknown" bucket.
- **The gateway's own project columns are blank for Sage.** They're populated only from
  `X-Domino-Project-Id` / `X-Domino-Project` request headers (`routes/gateway.py` `_resolve_caller`),
  which Sage doesn't send. The dashboard also has no "By Project" grouping — hence the
  `sage-project` tag, which the Group By dropdown discovers automatically.
- **Cost/usage:** read it in the gateway's own dashboard (`<base minus /v1>/#usage`), which Sage
  links to. Sage does **not** compute cost: only the gateway can price a call correctly, because
  `_compute_cost` honours per-alias custom rates from its DB that no client can see, and its
  `MODEL_COST_TABLE` has no Qwen/Nova rows (they fall through to a $1/$2 default). The admin usage
  view supports `tag_filter` and `group_by=tag:<key>`; the non-admin `/mine` routes support neither.
- **Streaming usage is NOT uniformly available in-band.** `OpenAIAdapter` forces
  `stream_options:{include_usage:true}` upstream and relays the usage chunk through, but the
  Anthropic and Bedrock adapters translate to OpenAI-shape chunks and keep tokens to themselves
  (`last_stream_usage`, for their own logging). So `sonnet`/`opus`/`bedrock-qwen3-coder`/`nova`
  return no usage to the caller — a client-side meter would silently read zero for them.
- **Guardrails (preventive):** input guardrails run on the prompt *before* it reaches the
  provider (the data-egress control), output guardrails on the response before the caller reads
  it — regex or LLM rules, admin-configured per alias. A blocked request is recorded as
  `guardrail_blocked`; redaction rewrites the prompt. This is a real egress guarantee independent
  of our sovereign routing (defense in depth).

## Still to get for the live spike
- The **host + app id** of the gateway instance we target (to form the base URL).
- A **`dgw_` service token** (or confirmation we run as a Domino identity with the sidecar).
- Confirm the exact **`/api/usage/mine`** response shape for the cost view.
