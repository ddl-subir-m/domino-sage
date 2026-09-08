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

## Tool-call streaming — check this before picking an alias (measured 2026-09-08)

Not every alias can stream a tool call's **arguments**, and one that can't is unusable for building.
The gateway buffers the whole argument instead of streaming deltas, the connection goes quiet, and a
60s idle limit tears it down with no finish_reason and no `[DONE]` — the answer just stops
(`gateway-questions.md` bug 3). Measured with `scripts/gateway-stream-probe.py`: one `write_file`
call whose `content` is ~2400 words.

| Alias | Chunks | Payload | Longest gap | Result |
|-------|--------|---------|-------------|--------|
| `gpt-5.4` | 8242 | 2768 KB | 3.2s | **ok** — `finish_reason=tool_calls` in 43.2s |
| `bedrock-qwen3-coder` | 5 | 0 KB | 60.0s | **cut** at 61.2s, no finish_reason |
| `sonnet` | 6 | 1 KB | ~297s | **cut** at 302.8s, no finish_reason (a ~300s limit, not 60s) |
| `qwen-2-5` | 1 | 0 KB | — | **rejected** in 0.4s: vLLM tool flags unset (bug 6) |

`gpt-5.4` is the only alias confirmed usable for agentic turns today. The cut is deterministic —
13 reproductions out of 13, at concurrency 1, 4 and 8 alike — so it is a property of the alias, not
of load, and retrying does not help. Untested aliases are unknown, not safe: re-run the probe rather
than assuming, and re-run this whole table once the gateway ships a fix.

## Our tier mapping (sage ModelCatalog — override via env)

| Role | Default alias | Env var | Why |
|------|---------------|---------|-----|
| sovereign | `qwen-2-5` ⚠️ | `SAGE_MODEL_SOVEREIGN` | on-Domino, code-capable — **but tool calls are switched off in its vLLM deployment (bug 6)** |
| plan (strong) | `gpt-5.4` | `SAGE_MODEL_PLAN` | strong reasoning for the plan phase; also the only alias that streams tool calls |
| implement (cheap) | `bedrock-qwen3-coder` ⚠️ | `SAGE_MODEL_IMPLEMENT` | coder-tuned, cheap — **but cannot stream tool calls, so builds die at 60s. Point this at `gpt-5.4`** |
| default | `sonnet` ⚠️ | `SAGE_MODEL_DEFAULT` | solid general default — fine for chat, cannot stream tool calls |

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
