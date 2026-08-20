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
# Matches spikes/domino-probes/model.py — predict(score). Domino's own sample snippet sends
# {"start":1,"stop":100} instead, which is BOILERPLATE rather than anything read off the deployed
# function, so it 400s against a model that does not happen to take start and stop.
D=${MODEL_BODY:-'{"data":{"score":0.9}}'}

# READ THE CODES CAREFULLY. 401 is refused at the door. 400 is the opposite result: the credential
# was ACCEPTED and the model itself rejected the body. A 400 here is a pass, not a failure.

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

# Say up front which of the two credentials that actually matter are present. Without this the
# run reads as a clean negative when in truth neither was ever tested — a variable set on its own
# shell line is not exported, so a child process never sees it.
present() { # name, what is lost when it is missing
  local n="$1" v="${!1:-}"
  if [ -n "$v" ]; then printf '  %-12s present, len=%s\n' "$n" "${#v}"
  else printf '  %-12s MISSING — %s\n' "$n" "$2"; fi
}
present MODEL_TOKEN "no control, so a 401 below cannot be told from a broken harness"
present DOMINO_PAT  "the whole point of this run; pass it INLINE, not on its own shell line"
echo

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
echo "200 or 400 anywhere = that credential AUTHENTICATED (400 just means the body was wrong),"
echo "                      so serve.py could mint its own and the manual paste disappears."
echo "All 401             = the model access token is the only key that opens a Model API."
