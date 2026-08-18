#!/usr/bin/env bash
# Does the LLM Gateway expose a list of its registered models?
#
# Run INSIDE a Domino workspace on cloud-dogfood.
#
# WHY THIS IS THE DECISIVE PROBE. Users deploy GenAI models in Domino, then REGISTER them
# in the LLM Gateway (with base URL, supported modes, and so on) alongside external models.
# Apps call the GATEWAY, never the endpoint or the AI Gateway directly. Sage already routes
# every model call through the gateway, so the calling path is built. What is missing is the
# ability to ENUMERATE registrations -- which is exactly what a "which LLMs are available"
# panel needs.
#
# If a list endpoint exists, the panel is live data. If it does not, Sage cannot enumerate
# registrations and this becomes a feature request for the gateway's owner rather than work
# Sage can do alone. That is a scoping fork, so it is worth one probe.
#
# openapi.json is tried FIRST: the gateway is a Python app (etanlightstone/LLM_gateway), so
# if it is FastAPI this returns every route in one call and answers the question outright.
#
# SAFETY: read-only, all GETs.
set -u

BASE="${GATEWAY_BASE_URL:?GATEWAY_BASE_URL is unset -- run this in a workspace with the Sage env}"
BASE="${BASE%/}"
ROOT="${BASE%/v1}"          # strip the OpenAI-shape suffix to reach the app root
have() { command -v "$1" >/dev/null 2>&1; }

TOK="${GATEWAY_API_KEY:-}"
if [ -z "$TOK" ]; then
  TOK="$(curl -sS -m 10 "${GATEWAY_TOKEN_URL:-http://localhost:8899/access-token}" 2>/dev/null || true)"
fi
[ -n "$TOK" ] && echo "token: <${#TOK} chars>" || echo "token: <NONE -- expect 401s>"
echo "base:  $BASE"
echo "root:  $ROOT"

get() {  # get <label> <url>
  local label="$1" url="$2" body code first
  body="$(mktemp)"
  code="$(curl -sS -m 25 -o "$body" -w '%{http_code}' \
            -H "Authorization: Bearer ${TOK}" -H 'Accept: application/json' "$url" 2>/dev/null || echo 000)"
  first="$(head -c 9 "$body" | tr 'A-Z' 'a-z')"
  # Domino SSO answers 200 with an HTML login page. Without this check an unauthenticated
  # 200 reads as success -- the exact trap that misled the GenAI probe's first run.
  case "$first" in
    '<!doctype'|'<html'*) printf '  %-34s HTTP %-4s  LOGIN PAGE (auth failed, not a real 200)\n' "$label" "$code"; rm -f "$body"; return 1 ;;
  esac
  printf '  %-34s HTTP %-4s  %s\n' "$label" "$code" "$(head -c 240 "$body" | tr -d '\n')"
  if [ "$code" = "200" ]; then cp "$body" /tmp/_gw_last.json; rm -f "$body"; return 0; fi
  rm -f "$body"; return 1
}

echo
echo "########## 1. FastAPI self-description (answers everything if present)"
for u in "$ROOT/openapi.json" "$BASE/openapi.json" "$ROOT/docs"; do
  if get "$(basename "$u")" "$u"; then
    if have jq && jq -e . /tmp/_gw_last.json >/dev/null 2>&1; then
      echo
      echo "  >>> ROUTES the gateway exposes:"
      jq -r '.paths | to_entries[] | "      " + ([(.value|keys[])]|join(",")|ascii_upcase) + " " + .key' \
        /tmp/_gw_last.json 2>/dev/null | head -60
      echo
      echo "  >>> Look for anything that lists models/endpoints/aliases. That is the panel's source."
    fi
    break
  fi
done

echo
echo "########## 2. OpenAI convention"
get "GET {base}/models" "$BASE/models" && {
  have jq && jq -r 'if .data then "      count: \(.data|length)", (.data[]?|"      - \(.id)  owned_by=\(.owned_by // "?")") else . end' /tmp/_gw_last.json 2>/dev/null | head -40
}

echo
echo "########## 3. likely registry routes"
for p in /api/models /api/endpoints /api/llms /api/aliases /api/providers /models /endpoints; do
  get "GET {root}$p" "$ROOT$p" >/dev/null 2>&1 && printf '  %-34s HTTP 200  <-- INSPECT THIS\n' "$p" \
    || printf '  %-34s no\n' "$p"
done

echo
echo "########## 4. control -- proves the token works at all"
get "GET {root}/api/usage/mine" "$ROOT/api/usage/mine" >/dev/null 2>&1 \
  && echo "  usage/mine reachable -> token is good, so 404s above are real absences" \
  || echo "  usage/mine NOT reachable -> a 401/403 here means auth failed, and the 404s prove nothing"

rm -f /tmp/_gw_last.json
cat <<'HINT'

HOW TO READ THIS
  openapi.json 200        -> the route list IS the answer. Any route listing models or
                             endpoints becomes the panel's data source.
  {base}/models 200       -> OpenAI-convention listing works; simplest possible panel feed.
  all 404 but control OK  -> the gateway has NO enumeration API. Sage cannot list
                             registrations, so the panel needs a feature request to the
                             gateway owner (Etan). That is a real scoping finding.
  LOGIN PAGE anywhere     -> auth failed; fix the token before trusting any other line.
HINT
