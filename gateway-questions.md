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

   **Update 2026-09-08 — this is provider-wide, not a Bedrock quirk.** The same envelope comes back
   from the Domino Platform (vLLM) provider: `qwen-2-5` with `tool_choice: auto` answers `200` with
   `data: {"error": {..., "code": 400}}` and no `[DONE]`. The gateway has the status code in hand —
   it prints it inside the payload — and sends 200 anyway. So this is the shared error path, not an
   adapter detail, which raises its priority: every provider we add inherits it.

3. **Streaming tool calls are cut at exactly 60.0s, silently (found 2026-09-08).** A streaming request
   with a tool defined gets ~5 preamble chunks (<1KB: role, tool-call id, function name), then nothing
   for **exactly 60.0 seconds**, then the socket closes. Status is `200`, no terminal `finish_reason`
   ever arrives, no `[DONE]`, no error frame — so nothing on the client raises. This is the fault
   `shim/keepalive.py` `pump()` already warns about ("ended the stream with no finish_reason").

   Deterministic, 13/13, at concurrency 1, 4 and 8 alike — **load is not a factor**, which is the one
   thing the earlier live note (`keepalive.py`, "a gateway under load") got wrong. The A/B, same model
   and same ~2400-word prompt, `bedrock-qwen3-coder`:

       tools ON    5 chunks    0 KB   max inter-chunk gap 60.0s   fin=null   cut at 61.2s
       tools OFF 316 chunks   53 KB   max inter-chunk gap  0.3s   fin=stop   ok  at 65.7s

   The control runs LONGER than the failing case and survives, so this is an idle timer, not a
   duration cap. Mechanism: the gateway does not stream tool-call argument deltas — it emits the
   preamble and then buffers the whole `content` argument, during which the connection carries no
   bytes. At 60s of silence the connection is torn down, and since `200` and several chunks are
   already on the wire, no error can be reported. Whether the timer is the gateway's upstream read
   or an ingress proxy in front of it is not distinguishable from the client; both predict exactly
   what was measured, and the gateway's own logs for these request ids will say which.

   Impact: any tool call whose arguments take over 60s to generate is impossible — `write_file` with
   a real-sized file is the common case, and it is why Sage builds fail. Because the truncation is
   indistinguishable from completion, it surfaces four layers away as OpenCode's "Invalid JSON input
   for openai-chat tool call write". Same Bedrock tool path as bug 1 above.

   Not universal. The same probe across the four aliases we use, identical prompt and tools:

       gpt-5.4              8242 chunks  2768 KB  gap  3.2s  fin=tool_calls   ok     43.2s
       bedrock-qwen3-coder     5 chunks     0 KB  gap 60.0s  fin=null         cut    61.2s
       sonnet                  6 chunks     1 KB  gap   n/a  fin=null         cut   302.8s
       qwen-2-5                1 chunk      0 KB  gap   n/a  BadRequestError         0.4s

   `gpt-5.4` streams tool-call argument deltas properly and is the workaround: point
   SAGE_MODEL_IMPLEMENT at it (check the per-alias rate on the #usage dashboard first — implement is
   our heaviest caller). `sonnet` fails the same way on a different timer, ~300s rather than 60s.
   `qwen-2-5` is an unrelated fault, bug 6 below. Caveat on the two `n/a` gaps: the probe measures
   silence BETWEEN chunks and never the trailing silence before the close, so sonnet's real stall is
   ~297s and reads as 0.4s.

   Fix (either one alone stops it): stream the tool-call argument deltas rather than buffering them,
   or emit SSE keepalive comments (`: ping`) while buffering. Raising the 60s idle timeout is a
   distant third.

4. **Non-streaming generation longer than ~60s is impossible (found 2026-09-08).** The same 60s idle
   timer kills any `stream: false` call, because such a call is one unbroken silence by construction.
   The gateway retries 3 times, each attempt capped at 60s, then returns `502 Provider error after 3
   attempts: unknown` at a fixed **180.5s** (6 samples, 0.3s spread). Measured on
   `bedrock-qwen3-coder`: 1800 words in → 57.1s and 59.9s, ok; 2400 words in → 502 every time. The
   identical 2400-word request **succeeds when streamed** (87.3s, 91.9s, `fin=stop`). One 108.9s
   success is the retry showing its work: 60s attempt timed out, retry finished in 48.9s.

   Consequence for us: **Sage must never call this gateway with `stream: false`.** The 502 body says
   only "unknown", and the caller waits three minutes to receive it.

5. **An expired token is returned as a Keycloak login page with HTTP 200 (found 2026-09-08).** The
   workspace sidecar JWT (`:8899/access-token`) has a 300s lifetime. Once it expires, the gateway
   answers `200` with `Content-Type: text/html` and a Keycloak `class="login-pf"` page instead of
   `401`. On a JSON endpoint every client parses that as a corrupt response, not as an auth failure —
   for an SSE client it is indistinguishable from bug 3. Sage is not exposed (`gateway/client.py:215`
   calls `self._token_provider()` per request, so each call mints a fresh token), but any consumer
   that holds a token for more than five minutes is.

6. **The sovereign alias cannot do tool calls — two vLLM flags are unset (found 2026-09-08).** Any
   request to `qwen-2-5` carrying `tools` fails in 0.4s with a single frame and no `[DONE]`:

       data: {"error": {"message": "\"auto\" tool choice requires --enable-auto-tool-choice and
       --tool-call-parser to be set", "type": "BadRequestError", "param": null, "code": 400}}

   Those are vLLM server launch flags, so the sovereign deployment was started without tool calling
   enabled. The model is not the limitation — Qwen 2.5 supports tool calls. The fix is a restart of
   that deployment with `--enable-auto-tool-choice --tool-call-parser hermes` (`hermes` is what vLLM
   documents for Qwen 2.5; the deployment owner should confirm it against their vLLM version).

   This blocks the sovereign tier outright. Sage cannot implement anything without tool calls, and no
   amount of gateway timeout work changes that. It is the highest-value ask in this file, and the
   cheapest: a deployment flag, not code.
