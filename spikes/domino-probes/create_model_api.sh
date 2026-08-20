#!/usr/bin/env bash
# Create one Model API in Domino — the prerequisite for the #9 browser-call probe.
#
# Why this exists: #9 asks whether a published app can call a Model API from the BROWSER. The
# dogfood Sage project has zero Model APIs deployed, so there is nothing to probe against. This
# deploys one throwaway model so the probe has a target.
#
# Run INSIDE a Domino workspace, in the project that should own the Model API. Auth comes from the
# workspace sidecar and the project from the environment, so there is nothing to configure. Every
# id it needs — environment, hardware tier — it resolves from the project itself.
#
# NOT read-only, unlike the other probes here: it deploys compute and it costs money while it runs.
# The last line prints the one command that deletes it. Use it when the probe is done.
#
# Override any of these if the defaults are wrong:
#   MODEL_NAME  MODEL_FILE  MODEL_FUNC  ENVIRONMENT_ID  HARDWARE_TIER_ID
set -eu

command -v jq >/dev/null || { echo "FATAL: jq is required." >&2; exit 1; }

H="${DOMINO_API_HOST:?DOMINO_API_HOST is unset — run this inside a Domino workspace}"
P="${DOMINO_PROJECT_ID:?DOMINO_PROJECT_ID is unset — run this inside a Domino workspace}"
T="$(curl -sS -m 10 "${DOMINO_API_PROXY:-http://localhost:8899}/access-token" || true)"
[ -n "$T" ] || { echo "FATAL: no sidecar token from \$DOMINO_API_PROXY/access-token" >&2; exit 1; }

NAME="${MODEL_NAME:-sage-probe-model}"
FILE="${MODEL_FILE:-model.py}"
FUNC="${MODEL_FUNC:-predict}"
CODE_DIR="${DOMINO_WORKING_DIR:-/mnt/code}"

# Domino asks for the token on every call because it expires quickly, so re-read it per request
# rather than trusting the one read above to still be good by the polling loop.
api() {
  local verb="$1" path="$2" body="${3:-}" tok
  tok="$(curl -sS -m 10 "${DOMINO_API_PROXY:-http://localhost:8899}/access-token")"
  if [ -n "$body" ]; then
    curl -sS -m 60 -X "$verb" "${H}${path}" \
      -H "Authorization: Bearer $tok" -H 'Content-Type: application/json' -H 'Accept: application/json' \
      -d "$body"
  else
    curl -sS -m 60 -X "$verb" "${H}${path}" \
      -H "Authorization: Bearer $tok" -H 'Accept: application/json'
  fi
}

###### 1. the model file
# Deliberately trivial. This model exists to be CALLED, not to be right: the probe is about whether
# a browser can reach it at all, so anything more would only add ways for the build to fail.
if [ ! -f "$CODE_DIR/$FILE" ]; then
  cat > "$CODE_DIR/$FILE" <<'PY'
def predict(score=0.5):
    """One number in, one verdict out."""
    score = float(score)
    return {"score": score, "risky": score > 0.7}
PY
  echo "wrote $CODE_DIR/$FILE"
else
  echo "using existing $CODE_DIR/$FILE"
fi

# A Model API deploys from a COMMIT, not from the workspace disk. An uncommitted file builds into a
# "module not found" failure ten minutes from now, so stop here instead — the message is cheaper.
if git -C "$CODE_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  if [ -n "$(git -C "$CODE_DIR" status --porcelain -- "$FILE")" ]; then
    echo
    echo "STOP: $FILE is not committed. The build reads the repo, not this disk. Run:"
    echo "  git -C $CODE_DIR add $FILE && git -C $CODE_DIR commit -m 'probe model' && git -C $CODE_DIR push"
    exit 1
  fi
fi

###### 2. environment and hardware tier, resolved from the project
ENV_ID="${ENVIRONMENT_ID:-$(api GET "/v4/projects/$P/useableEnvironments" | jq -r '.currentlySelectedEnvironment.id // empty')}"
[ -n "$ENV_ID" ] || { echo "FATAL: could not resolve an environment. Set ENVIRONMENT_ID." >&2; exit 1; }

# Cheapest tier wins. This model does arithmetic; paying for a GPU to answer the probe would be silly.
HW_ID="${HARDWARE_TIER_ID:-$(api GET "/v4/projects/$P/hardwareTiers" \
  | jq -r '[.[].hardwareTier | select(.centsPerMinute != null)] | sort_by(.centsPerMinute) | .[0].id // empty')}"
[ -n "$HW_ID" ] || { echo "FATAL: could not resolve a hardware tier. Set HARDWARE_TIER_ID." >&2; exit 1; }

echo "project=$P  environment=$ENV_ID  hardwareTier=$HW_ID"

###### 3. create and deploy
REQ="$(jq -n --arg name "$NAME" --arg env "$ENV_ID" --arg hw "$HW_ID" \
             --arg proj "$P" --arg file "$FILE" --arg func "$FUNC" '{
  name: $name,
  description: "Throwaway model for the #9 browser-call probe. Safe to delete.",
  environmentId: $env,
  hardwareTierId: $hw,
  isAsync: false,
  strictNodeAntiAffinity: false,
  environmentVariables: [],
  version: {
    projectId: $proj,
    source: { type: "File", file: $file, function: $func },
    logHttpRequestResponse: true,
    monitoringEnabled: false,
    shouldDeploy: true
  }
}')"

RESP="$(api POST "/api/modelServing/v1/modelApis" "$REQ")"
ID="$(printf '%s' "$RESP" | jq -r '.id // empty')"
if [ -z "$ID" ]; then
  echo "FATAL: create failed. Domino said:" >&2
  printf '%s\n' "$RESP" >&2
  exit 1
fi
echo "created Model API $NAME  id=$ID"

###### 4. wait for it to come up
# A first build pulls the environment image, so ten minutes is normal rather than a sign of trouble.
echo "waiting for it to run (Ctrl-C is safe — it keeps building)..."
for _ in $(seq 1 80); do
  STATUS="$(api GET "/api/modelServing/v1/modelApis/$ID" | jq -r '.activeVersion.deployment.status // "Pending"')"
  printf '  %s\n' "$STATUS"
  case "$STATUS" in
    Running) break ;;
    Failed|Error) echo "FATAL: deployment ended as $STATUS. Check the build logs in the Domino UI." >&2; exit 1 ;;
  esac
  sleep 15
done

cat <<EOF

Model API is $STATUS.
  id:      $ID
  UI:      $H/models/$ID/overview
  invoke:  $H/models/$ID/latest/model   <-- the #9 probe target (host is the MAIN host, not apps.)

Get a model access token in the UI (Settings -> Access, or the Overview page's sample call) to
invoke it by hand first. Whether a BROWSER on apps.<host> can reach that URL — CORS, and whether a
session cookie is accepted in place of that token — is exactly what the probe has to answer.

Delete it when you are done:
  curl -sS -X DELETE "$H/api/modelServing/v1/modelApis/$ID" \\
    -H "Authorization: Bearer \$(curl -sS \${DOMINO_API_PROXY:-http://localhost:8899}/access-token)"
EOF
