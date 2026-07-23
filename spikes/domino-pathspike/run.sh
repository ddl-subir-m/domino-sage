#!/usr/bin/env bash
# Phase-0 spike launcher (STEP 3). Starts the Vite dev server + the FastAPI Path-A proxy on
# the pluggable-tool port. Domino's pluggable-tools.yaml invokes this as the tool `start`.
set -euo pipefail
cd "$(dirname "$0")"

# ── set this from STEP 2 (probe.py -> /whoami -> received_path, minus the trailing slash) ──
# If STEP 2 shows the prefix comes from env vars, compute it here instead of hardcoding, e.g.:
#   export SAGE_BASE_PREFIX="/${DOMINO_PROJECT_OWNER}/${DOMINO_PROJECT_NAME}/${DOMINO_RUN_ID}"
export SAGE_BASE_PREFIX="${SAGE_BASE_PREFIX:-}"
export PORT="${PORT:-8888}"

echo "[spike] SAGE_BASE_PREFIX='${SAGE_BASE_PREFIX}'  PORT=${PORT}"

# 1) Vite dev server (internal, 5173). Inherits SAGE_BASE_PREFIX for base + HMR config.
( cd app && npm install --no-fund --no-audit && npm run dev ) &
VITE_PID=$!
trap 'kill "${VITE_PID}" 2>/dev/null || true' EXIT

# 2) FastAPI Path-A proxy on the tool port. uv pulls the deps inline (no venv setup needed).
uv run --with fastapi --with 'uvicorn[standard]' --with httpx --with websockets \
  uvicorn server:app --host 0.0.0.0 --port "${PORT}"
