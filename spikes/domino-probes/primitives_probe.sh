#!/usr/bin/env bash
# Domino primitives discovery — run INSIDE a Domino workspace on cloud-dogfood.
#
# Data sources are researched (see DATA-SOURCES-RESEARCH.md). This probe covers the OTHER
# composable primitives the resource-browser feedback asks for: model APIs, Domino-hosted
# GenAI endpoints, gateway LLMs, and the model registry.
#
# All paths are from the PUBLIC spec (spikes/domino-probes/public-api.json, "Domino Public
# API" 6.4.0). Every filter param on these is OPTIONAL, so a bare GET lists everything the
# caller may see. Two of them are explicitly permission-keyed, the same pattern that made
# the data-source picker work:
#   /api/aigateway/v1/endpoints   "Get all active Gateway LLMs accessible by the user"
#   /api/gen-ai/beta/endpoints    "Get all Gen AI endpoints accessible by user"
#
# Why aigateway matters most: backend/sage/gateway/open_models.py hardcodes Sage's model
# list, because no gateway provider exposes a queryable /v1/models. This is a PLATFORM
# endpoint, not a gateway one, so it may retire that hardcoded list.
#
# SAFETY: read-only. Every call is a GET.
set -u

have() { command -v "$1" >/dev/null 2>&1; }
have jq || echo "NOTE: jq missing; output will be raw JSON."

T="$(curl -sS -m 10 "${DOMINO_API_PROXY:-http://localhost:8899}/access-token" 2>/dev/null || true)"
if [ -z "$T" ]; then echo "FATAL: no sidecar token from \$DOMINO_API_PROXY/access-token" >&2; exit 1; fi
H="${DOMINO_API_HOST:?DOMINO_API_HOST is unset}"

# label | path | jq filter applied to the body
probe() {
  local label="$1" path="$2" filter="$3" body code
  body="$(mktemp)"
  code="$(curl -sS -m 30 -o "$body" -w '%{http_code}' \
            -H "Authorization: Bearer $T" -H 'Accept: application/json' "${H}${path}" 2>/dev/null || echo 000)"
  printf '\n###### %s\n  GET %s -> HTTP %s\n' "$label" "$path" "$code"
  if [ "$code" = "200" ] && have jq; then
    jq -r "$filter" "$body" 2>/dev/null || { echo "  (unexpected shape, raw head:)"; head -c 400 "$body"; echo; }
  else
    head -c 500 "$body"; echo
  fi
  rm -f "$body"
}

probe "Gateway LLMs (aigateway)" "/api/aigateway/v1/endpoints?limit=50" \
  '"  count: \(.endpoints|length)", (.endpoints[]? | "  - \(.endpointName)  [\(.modelProvider)/\(.modelName)]  type=\(.endpointType)")'

probe "Domino-hosted GenAI endpoints" "/api/gen-ai/beta/endpoints" \
  'if type=="array" then "  count: \(length)", (.[]? | "  - \(.name // .endpointName // .id)") else . end'

probe "Model APIs (modelServing)" "/api/modelServing/v1/modelApis?limit=50" \
  'if .modelApis then "  count: \(.modelApis|length)", (.modelApis[]? | "  - \(.name)  id=\(.id)") 
   elif type=="array" then "  count: \(length)", (.[]? | "  - \(.name)") else . end'

probe "Model Deployments" "/api/modelServing/v1/modelDeployments?limit=50" \
  'if .modelDeployments then "  count: \(.modelDeployments|length)", (.modelDeployments[]? | "  - \(.name)  id=\(.id)")
   elif type=="array" then "  count: \(length)", (.[]? | "  - \(.name)") else . end'

probe "Registered models" "/api/registeredmodels/v1?limit=50" \
  'if .items then "  count: \(.items|length)", (.items[]? | "  - \(.name)")
   elif type=="array" then "  count: \(length)", (.[]? | "  - \(.name)") else . end'

echo
echo "###### done. Paste this back into the Sage chat."
