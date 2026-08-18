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

# Domino has TWO gateways. Do not probe the wrong one:
#   LLM Gateway -- a separately deployed Domino App (GATEWAY_BASE_URL -> /apps/<id>/v1). This is
#     the one Sage routes every model call through, and it owns the model aliases. Its list is
#     Sage's own config (MODELS.md, gateway/open_models.py), NOT a Domino discovery endpoint.
#   AI Gateway  -- a built-in MLflow-based feature at /api/aigateway/v1/endpoints (same family as
#     the DOMINO_MLFLOW_DEPLOYMENTS :8767 sidecar). SAGE DOES NOT USE IT, so it is not probed here.
#     A live run returned 2 unrelated gemini endpoints and none of Sage's aliases.
# The real Domino-side model primitive is the gen-ai one below.

probe "Domino-hosted GenAI endpoints" "/api/gen-ai/beta/endpoints" \
  '(.items // .) as $i | "  count: \($i|length)",
   "  running: \([$i[]?|select(.currentVersion.status=="Running")]|length)",
   ($i[]? | "  - \(.name)  status=\(.currentVersion.status)  access=\(.generalAccess)  model=\(.currentVersion.modelSource.registeredModel.modelName)  \(.url)")'

# modelApis 403s without a project scope ("not authorized to view access configuration"),
# so scope it to this project rather than asking for a deployment-wide list.
probe "Model APIs (project-scoped)" "/api/modelServing/v1/modelApis?limit=50&projectId=${DOMINO_PROJECT_ID:-}" \
  '(.modelApis // .items // .) as $i | if ($i|type)=="array" then "  count: \($i|length)", ($i[]? | "  - \(.name)  id=\(.id)") else . end'

probe "Model Deployments" "/api/modelServing/v1/modelDeployments?limit=50" \
  '(.items // .) as $i | "  count: \($i|length)",
   ($i[]? | "  - \(.name)  state=\(.status.state)  target=\(.deploymentTargetInfo.typeName)  ops=\([.status.sharedOperations[]?.type]|join(","))")'

probe "Registered models" "/api/registeredmodels/v1?limit=50" \
  '(.items // .) as $i | if ($i|type)=="array" then "  count: \($i|length)", ($i[]? | "  - \(.name)") else . end'

echo
echo "###### done. Paste this back into the Sage chat."
