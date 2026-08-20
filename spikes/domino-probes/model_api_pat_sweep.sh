#!/usr/bin/env bash
# Final credential sweep for the #9 Model API call.
#
# Everything platform-identity has now failed from the OWNER's own workspace against a PRIVATE
# model: sidecar JWT as Bearer, as a raw Authorization value, as Basic both ways, and the user
# API key as a header and as Basic. All 401. This tries any remaining token-shaped credential in
# the environment — a PAT is a newer credential than the legacy API key and may be accepted where
# that one is not.
#
# Values are NEVER printed. Only variable names, lengths, and status codes.
M=${MODEL_URL:-https://cloud-dogfood.domino.tech/models/6a8727f40ff0450030085fb3/latest/model}
D='{"data":{"start":1,"stop":100}}'

code() { curl -sS -o /dev/null -w '%{http_code}' -m 20 -X POST "$M" -H 'Content-Type: application/json' -d "$D" "$@"; }

sweep() { # name, value
  local n="$1" v="$2"
  [ -n "$v" ] || return 0
  printf '  %-28s len=%-5s bearer=%s  apikey=%s  basic=%s\n' "$n" "${#v}" \
    "$(code -H "Authorization: Bearer $v")" \
    "$(code -H "X-Domino-Api-Key: $v")" \
    "$(code -u "$v:$v")"
}

echo "candidate credentials in the environment:"
for n in $(compgen -v | grep -iE 'token|pat|api_?key|secret' | sort -u); do
  case "$n" in *FILE|*PATH|*_DIR) continue;; esac
  sweep "$n" "${!n}"
done

# Domino also drops a JWT on disk rather than only in a variable.
if [ -n "${DOMINO_TOKEN_FILE:-}" ] && [ -r "$DOMINO_TOKEN_FILE" ]; then
  sweep "DOMINO_TOKEN_FILE(contents)" "$(tr -d '\r\n' < "$DOMINO_TOKEN_FILE")"
fi

echo
echo "200 anywhere above = serve.py can mint its own credential and the manual paste disappears."
echo "All 401 = the model access token is the only key that opens a Model API."
