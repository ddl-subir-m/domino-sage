#!/usr/bin/env bash
# Phase-1 verification launcher — the REAL sage orchestrator as a Domino pluggable tool.
#
# Unlike the Phase-0 spike (which ran its own throwaway app+Vite), this starts the actual
# orchestrator on ONE port. The orchestrator itself spawns Vite (via the supervisor) when you
# create a project, mounts the preview under /preview, strips Domino's proxy prefix, and bakes
# that same prefix into Vite's base — the Phase-1 mechanics we're here to confirm.
#
# No gateway / OpenCode needed for the core check: OpenCode starts lazily on the first *build*,
# so creating a project + rendering the preview + HMR all work without them.
set -euo pipefail
cd "$(dirname "$0")/../../backend"   # -> /mnt/code/backend

# The orchestrator derives the Domino proxy prefix from env itself (domino_base_prefix); no export
# needed. Bind all interfaces so Domino's tool proxy can reach us; match httpProxy.port below.
export SAGE_CONTROL_HOST="${SAGE_CONTROL_HOST:-0.0.0.0}"
export SAGE_CONTROL_PORT="${SAGE_CONTROL_PORT:-8888}"
# Keep generated workspaces off the git mount (scratch, fine to lose between sessions).
export SAGE_WORKSPACES="${SAGE_WORKSPACES:-/tmp/sage-workspaces}"

echo "[verify] host=${SAGE_CONTROL_HOST} port=${SAGE_CONTROL_PORT} workspaces=${SAGE_WORKSPACES}"
echo "[verify] prefix (env-derived): /${DOMINO_PROJECT_OWNER:-?}/${DOMINO_PROJECT_NAME:-?}/notebookSession/${DOMINO_RUN_ID:-?}"

# Template deps: the workspace manager symlinks the template's node_modules into each new
# workspace, so `npm run dev` needs them present. (Phase 3 bakes this into the image; until then
# we install once at runtime — only the first boot pays, it persists on the mount.)
if [ ! -d ../template/react-vite/node_modules ]; then
  echo "[verify] installing template deps (first boot only)…"
  ( cd ../template/react-vite && npm install --no-fund --no-audit )
fi

# Run the real orchestrator. uv syncs backend/pyproject.toml deps into a venv on first run.
exec uv run python -m sage.orchestrator.app
