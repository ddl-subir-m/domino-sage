#!/usr/bin/env bash
# Final credential sweep for the #9 Model API call. Runs from ANYWHERE — laptop or workspace.
#
# Everything platform-identity has already failed from the owner's own workspace against a
# private model: sidecar JWT as Bearer, as a raw Authorization value, as Basic both ways, and
# the user API key as a header and as Basic. All 401, and the sidecar token is bare, so none of
# those failed on a doubled prefix.
#
# Two things are still untried, and this covers both:
#   - a PAT, which is a newer credential than the legacy API key
#   - Basic with an EMPTY username, which is the shape the Domino SDK itself uses
#     (`HTTPBasicAuth("", api_key)` in domino/authentication.py). Every earlier Basic attempt
#     sent the credential as BOTH fields, so this shape has never actually been tested.
#
# Values are NEVER printed. Only names, lengths, and status codes.
#
#   DOMINO_PAT=... ./model_api_pat_sweep.sh
#   MODEL_TOKEN=... DOMINO_PAT=... ./model_api_pat_sweep.sh    # adds a known-good control
set -u

M=${MODEL_URL:-https://cloud-dogfood.domino.tech/models/6a8727f40ff0450030085fb3/latest/model}
D=${MODEL_BODY:-'{"data":{"start":1,"stop":100}}'}

code() { curl -sS -o /dev/null -w '%{http_code}' -m 20 -X POST "$M" -H 'Content-Type: application/json' -d "$D" "$@"; }

sweep() { # name, value
  local n="$1" v="$2"
  [ -n "$v" ] || return 0
  printf '  %-24s len=%-5s bearer=%s  apikey=%s  basic(v:v)=%s  basic(:v)=%s\n' "$n" "${#v}" \
    "$(code -H "Authorization: Bearer $v")" \
    "$(code -H "X-Domino-Api-Key: $v")" \
    "$(code -u "$v:$v")" \
    "$(code -u ":$v")"
}

# A known-good credential proves the URL, body and harness are right, so a 401 below means the
# credential was refused rather than the script being wrong.
if [ -n "${MODEL_TOKEN:-}" ]; then
  echo "control — the model's own access token:"
  sweep "MODEL_TOKEN" "$MODEL_TOKEN"
  echo
fi

echo "candidates:"
found=0
for n in DOMINO_PAT DOMINO_USER_API_KEY DOMINO_API_KEY DOMINO_TOKEN; do
  v="${!n:-}"
  [ -n "$v" ] && { sweep "$n" "$v"; found=1; }
done

# Anything else token-shaped that happens to be exported.
for n in $(compgen -v | grep -iE 'token|pat|api_?key' | sort -u); do
  case "$n" in DOMINO_PAT|DOMINO_USER_API_KEY|DOMINO_API_KEY|DOMINO_TOKEN|MODEL_TOKEN|*FILE|*PATH|*_DIR|*URL) continue;; esac
  sweep "$n" "${!n:-}" && found=1
done

# Domino also drops a JWT on disk rather than only in a variable.
if [ -n "${DOMINO_TOKEN_FILE:-}" ] && [ -r "$DOMINO_TOKEN_FILE" ]; then
  sweep "DOMINO_TOKEN_FILE" "$(tr -d '\r\n' < "$DOMINO_TOKEN_FILE")"
  found=1
fi

[ "$found" = 1 ] || echo "  (nothing found — run as: DOMINO_PAT=... $0)"

echo
echo "200 anywhere = serve.py can mint its own credential and the manual paste disappears."
echo "All 401     = the model access token is the only key that opens a Model API."
