# Questions for the Domino AI Gateway team — sage builder (Step 2.3)

Context: we're building an internal AI app builder that routes all model calls through the
Domino AI Gateway. Confirmed from the gateway UI: it's OpenAI-compatible, fronts both vendor and
sovereign models (by `model` field), and its Logs & Audit + Usage & Cost pages already capture
per-request cost/tokens/user/model/latency/status and aggregate by tag (project), model, user,
and provider. These questions cover only what's still unknown.

## Cost / usage — is the dashboard data reachable programmatically?
1. Is the cost/usage data on the Logs & Audit and Usage & Cost pages available via an **API**
   (so our builder can show a per-project cost view in-app), or is it dashboard-UI only? If API:
   endpoint, auth, granularity (per-request? per-tag?), and freshness (real-time vs batched).
2. Can we set an **arbitrary per-request tag** beyond project — specifically `phase=plan` vs
   `phase=implement` — and filter/group by it on the usage page or via the API? This is what
   makes the auto-mode per-phase savings view possible.
3. Confirmed: untagged requests land in the "unknown" tag bucket. To avoid that, what's the
   exact mechanism to set tags on a request (header, body field, per-key config)? We'll tag
   every request with project + phase.

## Guardrails / data-leak detection (still unknown — Logs shows HTTP status, not guardrail events)
4. Does the gateway run **guardrail / sensitive-data-leak detection**, and can we **receive
   those events** as a caller (webhook, stream, response flag, or log API)?
5. When a guardrail fires, is the request **blocked** before it reaches the model, or is it a
   **post-hoc detection** (the request already went through)? Decides our UI wording.
6. What's in a guardrail event payload (what was detected, which request/asset, severity,
   blocked vs occurred)?

## Auth / access
7. How does a service (our builder backend, running as the end user's Domino identity)
   **authenticate** to the gateway — API key, Domino token pass-through, service account? And
   does auth scope the tags/user attribution automatically?

## Models / routing
8. Confirm the **list of models** (vendor + sovereign) and their exact `model` identifiers.
9. Is model selection **policy-gated** (can a caller request any registered model)? We override
   the `model` field to force the sovereign model on sensitive data — that override must always
   succeed.

## Operational
10. Rate limits / quotas per caller, and recommended **retry/backoff**.
11. Failure modes (timeout, 429, 5xx, model-unavailable) and how they surface, so we handle them
    as human-readable system errors.
