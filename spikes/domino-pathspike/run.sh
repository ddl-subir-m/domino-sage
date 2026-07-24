#!/usr/bin/env bash
# Phase-0 spike launcher (STEP 3). Starts the Vite dev server + the FastAPI Path-A proxy on
# the pluggable-tool port. Domino's pluggable-tools.yaml invokes this as the tool `start`.
set -euo pipefail
cd "$(dirname "$0")"

# Domino preserves the proxy prefix (httpProxy rewrite:false). Confirmed via probe.py (STEP 2)
# that it renders as /<owner>/<project>/notebookSession/<runId>, derivable from env. Domino also
# sends it per-request in the `x-script-name` header (auto-detect option for the real
# orchestrator). Override by exporting SAGE_BASE_PREFIX before launch.
export SAGE_BASE_PREFIX="${SAGE_BASE_PREFIX:-/${DOMINO_PROJECT_OWNER}/${DOMINO_PROJECT_NAME}/notebookSession/${DOMINO_RUN_ID}}"
export PORT="${PORT:-8888}"

echo "[spike] SAGE_BASE_PREFIX='${SAGE_BASE_PREFIX}'  PORT=${PORT}"

# 1) Vite dev server (internal, 5173). Inherits SAGE_BASE_PREFIX for base + HMR config.
( cd app && npm install --no-fund --no-audit && npm run dev ) &
VITE_PID=$!
trap 'kill "${VITE_PID}" 2>/dev/null || true' EXIT

# 2) FastAPI Path-A proxy on the tool port. uv pulls the deps inline (no venv setup needed).
uv run --with fastapi --with 'uvicorn[standard]' --with httpx --with websockets \
  uvicorn server:app --host 0.0.0.0 --port "${PORT}"
