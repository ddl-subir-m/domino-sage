# Gateway questions — mostly answered from the repo (etanlightstone/LLM_gateway)

Reading the gateway source answered nearly all of these. See MODELS.md for the full cheatsheet.

## Answered from the repo
- **Cost (Q1): settled — we don't read it via API.** Sage tags its calls and links to the gateway's
  own `#usage` dashboard instead. Three reasons, all from the source: only the gateway can price a
  call (per-alias custom rates live in its DB); `/api/usage/mine/*` resolves callers with
  `resolve_visitor`, which has no `dgw_` branch, so a gateway-PAT deployment gets a 401; and the
  Anthropic/Bedrock adapters don't return usage in-band at all, so a stream-parsing meter would read
  zero for `bedrock-qwen3-coder` — Sage's default implement model.
- **Per-request tags (Q2/Q3):** yes — `X-LLM-Tag-<name>: <value>` headers land in the usage `tags`
  JSON. We send seven, all `sage-`-namespaced (see MODELS.md). **`project`/`model`/`user`/`org` and
  friends are in `RESERVED_TAG_KEYS` and are silently dropped**, which is why the namespace isn't
  optional. Untagged → "unknown" bucket.
- **Guardrails (Q4/Q5/Q6):** preventive input/output egress control (regex or LLM rules,
  admin-configured per alias). Input guardrails block/redact BEFORE the provider; blocked →
  `guardrail_blocked`. Not merely detective.
- **Auth (Q7):** `Authorization: Bearer <token>` — a gateway `dgw_` token, a Domino PAT, or the
  workspace sidecar JWT at `http://localhost:8899/access-token`.
- **Models (Q8):** see MODELS.md. Sovereign tier = `Domino Platform` provider (`qwen-2-5`,
  `local-domino-llm`).
- **Base URL:** `https://<host>/apps/<id>/v1` (OpenAI-shape); also `/anthropic/v1/messages`.

## Still needed for the live spike (ask Etan / gateway owner)
1. The **host + app id** of the gateway instance we should target (to form the base URL).
2. A **`dgw_` service token** for our builder backend — or confirmation we run inside a Domino
   workspace/project and should pull the sidecar JWT from `:8899` (which also sets the project tag).
3. Confirm the exact **`/api/usage/mine`** response shape (fields for tokens, cost, tags) so the
   cost view reads it correctly.
4. Which **guardrail rules** are configured on the aliases we'll use (so we know what block/redact
   behavior to expect in the demo), and whether we can scope a rule set to the builder.

## Bugs to report (found live, 2026-08-06)

1. **Bedrock: parallel tool calls are rejected.** Any OpenAI client that batches tool calls cannot
   hold a conversation with a Bedrock-served alias (`bedrock-qwen3-coder`, `nova`). Bedrock's Converse
   API requires the `toolResult` blocks answering an assistant turn's N `toolUse` blocks to be grouped
   into the ONE following user message, but `services/provider_adapter.py` (`role == "tool"` branch,
   ~:1782) appends a separate `{"role": "user", "content": [{"toolResult": …}]}` per tool message. With
   N>1 the first is short the other ids:

       ValidationException: Expected toolResult blocks at messages.6.content for the following Ids: …

   Fix: accumulate consecutive `role == "tool"` messages and emit one user message carrying all their
   `toolResult` blocks. Sage works around it for now by serialising parallel calls before they go
   upstream (`shim/enforcement.py` `split_parallel_tool_calls`) — delete that once this lands.

2. **Provider errors are returned as HTTP 200 with a single SSE frame.** The failure above came back
   as `200` + `data: {"error": {...}}` and no `[DONE]`, so nothing on the client raises. OpenCode
   reports only "Invalid …openai-compatible-chat stream event" with no payload, and it took raw chunk
   logging to find the cause. A non-200 with the error body — or at minimum a documented error frame —
   would make this diagnosable. Sage now detects the shape and renders it (`keepalive.upstream_error`).
