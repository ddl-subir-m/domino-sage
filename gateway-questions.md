# Gateway questions — mostly answered from the repo (etanlightstone/LLM_gateway)

Reading the gateway source answered nearly all of these. See MODELS.md for the full cheatsheet.

## Answered from the repo
- **Cost via API (Q1):** yes — `/api/usage/mine` (per caller) + audit download; dashboards group
  by tag/model/user/provider.
- **Per-request tags (Q2/Q3):** yes — `X-LLM-Tag-<name>: <value>` headers land in the usage
  `tags` JSON. We'll send `X-LLM-Tag-phase|project|model`. Project also derives from
  `DOMINO_PROJECT_NAME`; untagged → "unknown" bucket.
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
