# Spike: Step 1 — enforcement seam + OpenCode routing coverage

Goal (PLAN.md Step 1): prove OpenCode routes **100%** of model calls through our shim, the shim
overrides the model server-side, and container egress can be locked to gateway-only.

## Prereqs (blocked on)
- Gateway base URL + API key / auth method (gateway-questions Q7).
- OpenCode installed (not yet installed on this machine — install as task 1.2.0).

## Procedure
1. **1.1** Run the shim locally forwarding to the gateway. Fill in `DominoGatewayClient.route`
   (httpx stream to `{base_url}/v1/chat/completions`, inject auth + tags). `curl` a completion
   through it → expect a real streamed response.
2. **1.2** Install OpenCode; configure `base_url = http://localhost:<shim>`; run 3 representative
   tasks (scaffold, edit, multi-step w/ tool loop + a forced retry).
3. **Coverage check:** the shim logs every inbound request. After the 3 tasks, compare the shim's
   request count against OpenCode's own turn/call log. **Pass = 100% of model calls appear in the
   shim log; 0 bypasses.**
4. **1.3** Flip the sensitivity lock; confirm a request asking for a vendor model is served the
   sovereign model (shim log shows the override).
5. **1.4** Apply the container egress allowlist (gateway + Domino API only).
6. **1.5 Bypass probe** — from inside the container, attempt direct vendor calls via: OpenCode
   shell tool `curl`, an npm `postinstall` script, a subprocess in a generated app. **Pass = all
   blocked by the allowlist.**
7. **1.6** Capture OpenCode's native event stream; map a sample to `AgentEvent`; note whether it
   exposes plan/implement phase natively (decides DESIGN Seam-3 option a vs b).

## Exit / gate
100% routing coverage + override works + egress survives the bypass probe + events mappable.
If any fail → escalate (harness swap or rethink the sovereign guarantee) before Phase 1.

## Log template
| Task | Model calls (OpenCode) | Seen by shim | Bypasses | Notes |
|------|------------------------|--------------|----------|-------|
| scaffold |  |  |  |  |
| edit |  |  |  |  |
| multi-step + retry |  |  |  |  |
