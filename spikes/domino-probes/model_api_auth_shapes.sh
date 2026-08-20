#!/usr/bin/env bash
# Last auth-shape sweep for the #9 Model API call. Rules out a double-prefixed header
# before we conclude the sidecar token cannot invoke a model.
M=https://cloud-dogfood.domino.tech/models/6a8727f40ff0450030085fb3/latest/model
D='{"data":{"start":1,"stop":100}}'
T="$(curl -sS "${DOMINO_API_PROXY:-http://localhost:8899}/access-token")"

case "$T" in Bearer\ *) echo "NOTE: sidecar token ALREADY carries a 'Bearer ' prefix";; *) echo "NOTE: sidecar token is bare";; esac
echo "token length: ${#T}"

try() { # label, then curl args
  local label="$1"; shift
  printf '  %-28s -> HTTP %s\n' "$label" \
    "$(curl -sS -o /dev/null -w '%{http_code}' -m 20 -X POST "$M" -H 'Content-Type: application/json' -d "$D" "$@")"
}

try "Authorization: <raw>"      -H "Authorization: $T"
try "Basic b64(sidecar:sidecar)" -u "$T:$T"
try "Basic b64(sidecar:)"        -u "$T:"

# A real user API key is a different credential from the sidecar JWT, so test it too if you have one.
if [ -n "${DOMINO_USER_API_KEY:-}" ]; then
  try "X-Domino-Api-Key: userkey" -H "X-Domino-Api-Key: $DOMINO_USER_API_KEY"
  try "Basic b64(userkey:userkey)" -u "$DOMINO_USER_API_KEY:$DOMINO_USER_API_KEY"
else
  echo "  (set DOMINO_USER_API_KEY to also test a real user API key)"
fi
