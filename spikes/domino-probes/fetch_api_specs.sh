#!/usr/bin/env bash
# Fetch Domino's two API specs. Both are served UNAUTHENTICATED from any deployment's
# /assets/, so this needs no token and runs from anywhere (verified against cloud-dogfood).
#
# Why a script instead of vendored files: they are ~2 MB each and move with every Domino
# release. Fetch on demand; .gitignore keeps the blobs out of git. Same approach as Domino's
# own automl-service/scripts/download_api_specs.sh.
#
# Which spec answers what:
#   public-api.json  "Domino Public API"      -- the stable /api/<svc>/v1 families. BUILD
#                    AGAINST THIS. Data sources: GET /api/datasource/v1/datasources
#                    (getAccessibleAndActiveDataSources) -- permission-keyed, so it lists
#                    what the user can actually use, attached to a project or not.
#   swagger.json     "Domino Data Lab API v4" -- the internal /v4 surface. Richer: it carries
#                    enums the public spec leaves as free-form strings (e.g. dataSourceType's
#                    35 connector values, which the picker's allowlist is drawn from). Useful
#                    for reference, NOT a stability contract.
#
# See DATA-SOURCES-RESEARCH.md for what these established.
#
# Usage:  bash spikes/domino-probes/fetch_api_specs.sh [host]
#         DOMINO_HOST=https://your.domino.host bash spikes/domino-probes/fetch_api_specs.sh
set -euo pipefail

HOST="${1:-${DOMINO_HOST:-https://cloud-dogfood.domino.tech}}"
HOST="${HOST%/}"
OUT="$(cd "$(dirname "$0")" && pwd)"

for spec in public-api.json swagger.json; do
  url="${HOST}/assets/${spec}"
  tmp="${OUT}/.${spec}.part"

  code="$(curl -sS -m 180 -o "$tmp" -w '%{http_code}' "$url" 2>/dev/null || echo 000)"
  if [ "$code" != "200" ]; then
    rm -f "$tmp"; echo "FAIL  ${spec}  HTTP ${code}  ${url}" >&2; exit 1
  fi
  # A login redirect answers 200 with HTML, so confirm it is really JSON before overwriting.
  if [ "$(head -c 1 "$tmp")" != "{" ]; then
    rm -f "$tmp"; echo "FAIL  ${spec}  HTTP 200 but not JSON (login page?)  ${url}" >&2; exit 1
  fi

  mv "$tmp" "${OUT}/${spec}"
  title="$(command -v jq >/dev/null 2>&1 && jq -r '.info.title + " " + .info.version' "${OUT}/${spec}" || echo "?")"
  printf 'ok    %-16s %9s bytes  %s\n' "$spec" "$(wc -c < "${OUT}/${spec}" | tr -d ' ')" "$title"
done

cat <<'HINT'

Both specs are in spikes/domino-probes/ (gitignored). Example query:

  jq -r '.paths["/api/datasource/v1/datasources"].get.operationId' spikes/domino-probes/public-api.json
  jq -r '.components.schemas["domino.datasource.api.DataSourceDto"].properties.dataSourceType.enum[]' \
     spikes/domino-probes/swagger.json
HINT
