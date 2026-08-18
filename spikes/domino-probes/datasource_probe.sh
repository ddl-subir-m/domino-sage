#!/usr/bin/env bash
# Data Source discovery — run INSIDE a Domino workspace on cloud-dogfood, in a
# project that has the Snowflake data source attached.
#
# Answers the LIVE half of DATA-SOURCES-RESEARCH.md. The spec already gave us the
# path and the response shape; only the PREFIX, the AUTH, and the in-container
# library story need a real container.
#
# From spikes/domino-probes/dogfood-swagger.json ("Domino Data Lab API v4", OpenAPI 3.0.0):
#   GET /datasource/projects/{projectId}?authenticatedOnly&dataPlaneId
#       -> array<DataSourceDto>, operationId getDataSourcesByProject
#   GET /datasource/{dataSourceId}/authentication-status -> boolean
#   DataSourceDto: id,name,displayName,description,dataSourceType,authType,status,
#                  dataSourcePermissions{credentialType:Individual|Shared,isEveryone,userIds}
#   status enum: Pending|Active|Deleted    dataSourceType incl. SnowflakeConfig, BigQueryConfig
#
# The spec carries no basePath. This repo already calls BOTH families:
#   /api/<svc>/<ver>/...  (public, e.g. /api/apps/beta/apps)
#   /v4/...               (private, e.g. /v4/workspace/project/{id}/workspace)
# So the prefix is the open question. This probe tries the candidates.
#
# SAFETY: read-only. Every call is a GET. Nothing is created, changed, or deleted.
# Secret-looking env values are masked, but READ THE OUTPUT before you paste it back.
set -u

mask() { sed -E 's/((KEY|TOKEN|SECRET|PASSWORD|PAT|CREDENTIAL)[A-Z_]*)=.*/\1=<redacted>/I'; }
have() { command -v "$1" >/dev/null 2>&1; }
pp() { if have jq; then jq "$@"; else cat; fi; }

echo "################ 1. injected Domino env (secrets masked)"
env | grep -i domino | sort | mask
echo
echo "DOMINO_API_HOST   = ${DOMINO_API_HOST:-<unset>}"
echo "DOMINO_PROJECT_ID = ${DOMINO_PROJECT_ID:-<unset>}"
have jq || echo "NOTE: jq is not installed here; JSON prints raw."

echo
echo "################ 2. is the Domino data library present?"
python -c 'import domino_data; print("import domino_data OK, version:", getattr(domino_data, "__version__", "unknown"))' 2>&1 | head -3
pip show dominodatalab-data 2>&1 | head -6

echo
echo "################ 3. which auth works?"
TOKEN=""
if curl -sS -m 5 -o /tmp/_tok http://localhost:8899/access-token 2>/dev/null; then
  TOKEN="$(cat /tmp/_tok)"; rm -f /tmp/_tok
  echo "sidecar http://localhost:8899/access-token : token retrieved (${#TOKEN} chars)"
else
  echo "sidecar http://localhost:8899/access-token : NOT reachable"
fi
[ -n "${DOMINO_USER_API_KEY:-}" ] && echo "DOMINO_USER_API_KEY : present (${#DOMINO_USER_API_KEY} chars)" \
                                  || echo "DOMINO_USER_API_KEY : unset"

echo
echo "################ 4. LIST DATA SOURCES — find the prefix and the auth header"
PID="${DOMINO_PROJECT_ID:-}"
if [ -z "$PID" ] || [ -z "${DOMINO_API_HOST:-}" ]; then
  echo "SKIPPED: DOMINO_API_HOST or DOMINO_PROJECT_ID is unset."
else
  for PREFIX in "/v4/datasource/projects" "/api/datasource/v1/projects" "/datasource/projects" "/api/datasource/projects"; do
    URL="${DOMINO_API_HOST}${PREFIX}/${PID}"
    for AUTH in bearer apikey; do
      case "$AUTH" in
        bearer) [ -z "$TOKEN" ] && continue; H=(-H "Authorization: Bearer ${TOKEN}") ;;
        apikey) [ -z "${DOMINO_USER_API_KEY:-}" ] && continue; H=(-H "X-Domino-Api-Key: ${DOMINO_USER_API_KEY}") ;;
      esac
      CODE="$(curl -sS -m 20 -o /tmp/_ds.json -w '%{http_code}' "${H[@]}" -H 'Accept: application/json' "$URL" 2>/dev/null)"
      printf 'HTTP %-4s  %-8s  %s\n' "$CODE" "$AUTH" "$PREFIX/<projectId>"
      if [ "$CODE" = "200" ]; then
        echo "        ^^^ THIS ONE WORKS. Data sources visible to this project:"
        pp -r '[.[] | {id, name, dataSourceType, authType, status,
                       credentialType: .dataSourcePermissions.credentialType}]' /tmp/_ds.json
        echo
        echo "        --- same call with ?authenticatedOnly=true (the READY filter) ---"
        curl -sS -m 20 "${H[@]}" -H 'Accept: application/json' "${URL}?authenticatedOnly=true" \
          | pp -r 'if type=="array" then [.[] | {name, dataSourceType, status}] else . end'
        rm -f /tmp/_ds.json
        break 2
      fi
    done
  done
  rm -f /tmp/_ds.json 2>/dev/null
fi

echo
echo "################ 5. Q4 — does anything here look like an app server process?"
ls -la /mnt/app.sh /mnt/*/app.sh 2>/dev/null || echo "no app.sh under /mnt (expected in a plain workspace)"
echo
echo "################ done. Paste sections 1-5 back into the Sage chat."
