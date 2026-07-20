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
  Also an Anthropic-shape ingress at `/anthropic/v1/messages`.
- **Auth:** `Authorization: Bearer <token>` where token is a gateway token (`dgw_…`), a Domino
  PAT, or a workspace sidecar JWT from `http://localhost:8899/access-token`. (OpenAI SDK:
  `api_key="dgw_…"`. Anthropic SDK: use `auth_token=`, NOT `api_key=`.)
- **Per-request tags:** `X-LLM-Tag-<name>: <value>` → stored in the usage `tags` JSON. We send
  `X-LLM-Tag-phase`, `X-LLM-Tag-project`, `X-LLM-Tag-model`. Project also derives from
  `DOMINO_PROJECT_NAME`; untagged calls land in the "unknown" bucket.
- **Cost/usage API:** `/api/usage/mine` (per caller) + audit download `/aigateway/audit/download`.
  Dashboards group by tag/model/user/provider.
- **Guardrails (preventive):** input guardrails run on the prompt *before* it reaches the
  provider (the data-egress control), output guardrails on the response before the caller reads
  it — regex or LLM rules, admin-configured per alias. A blocked request is recorded as
  `guardrail_blocked`; redaction rewrites the prompt. This is a real egress guarantee independent
  of our sovereign routing (defense in depth).

## Still to get for the live spike
- The **host + app id** of the gateway instance we target (to form the base URL).
- A **`dgw_` service token** (or confirmation we run as a Domino identity with the sidecar).
- Confirm the exact **`/api/usage/mine`** response shape for the cost view.
