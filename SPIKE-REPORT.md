---
doc: Phase-0 spike report — sage
status: complete (conditional green-light)
date: 2026-07-20
---

# Phase-0 spike report

**Question Phase 0 had to answer:** can we enforce the sovereign / zero-vendor guarantee
through OpenCode, and can we test the system deterministically?

**Answer: yes on the mechanism; the airtight containment half is a platform dependency.**
Green-light to build Phase 1, with one tracked open item (egress allowlist).

## What held (verified live in a Domino workspace, cloud-dogfood)

| Item | Result |
|------|--------|
| 1.1 Enforcement shim in front of the gateway | ✅ `curl`/OpenCode stream real completions via the shim |
| 1.2 OpenCode routes model calls through the shim | ✅ every model call hits the shim (incl. retries/tool-loop); OpenCode configured with only the `sage-gateway` provider |
| 1.3 Server-side model override | ✅ `requested=gpt-5.4 -> resolved=qwen-2-5 (sensitivity, locked=True)`, gateway 200; override holds across a turn's multiple calls |
| 2.1 Deterministic FakeGatewayClient | ✅ integration tests assert override + tagging with no network |
| 2.3 Gateway contract | ✅ resolved from the repo (base `/apps/<id>/v1`, `Bearer` token, `X-LLM-Tag-*`, `/api/usage/mine`, preventive guardrails) — see MODELS.md |

The sovereign switch reduces to setting the `model` field because both tiers live under one
OpenAI-compatible gateway. Cost now tags with the real Domino project (was "unknown").

## What's open

**1.4 / 1.5 — container egress allowlist (the containment half). BLOCKED on platform.**
The hard "zero direct-to-vendor" guarantee requires locking workspace/container egress so only
the gateway host is reachable. We do NOT control the workspace egress policy — this needs the
platform/infra team. Until then:
- **Mitigation we control (in place):** OpenCode has only the `sage-gateway` provider (no other
  API keys), so its model path has nowhere else to go.
- **Residual risk (documented, not closed):** OpenCode's shell tool (needed for `npm`/file ops)
  could in principle `curl` a vendor directly. Only a network allowlist closes this. Required
  before this is production-safe for regulated customers.
- **Owner:** platform/infra. **Ask:** egress allowlist on the builder container = {gateway host,
  Domino API host} only.

**1.6 — event-stream normalization. DEFERRED to Phase 1.** Mapping OpenCode's events to our
`AgentEvent` type is UI groundwork, not part of the guarantee. Do it when we build the preview UI.

**2.2 — record/replay model harness. DEFERRED** until we have an E2E flow to stabilize (Phase 1).

## Decision
Proceed to Phase 1 (warm template + live preview). Track the egress allowlist as a platform
dependency that gates production-readiness (not Phase 1 build). No architectural change needed.
