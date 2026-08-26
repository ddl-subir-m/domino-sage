#!/usr/bin/env bash
# Publish THIS repo as the Sage Workbench Domino App (Chat + Build).
#
# A Domino App checks this repo out to /mnt/code and runs /mnt/code/app.sh on the Sage Environment.
# The workbench is the orchestrator, baked at /opt/sage; we exec that entrypoint with App-proxy
# settings. If the baked copy isn't present (e.g. `bash app.sh` off the Sage image), fall back to
# this checkout.
#
# Domino's App proxy strips the mount prefix before the request reaches this process, so we must
# not bake the workspace notebookSession path into Vite. SAGE_PROXY_MODE=app makes the prefix empty.
# /mnt/code is this Sage repo — not a user app — so Chat/Build use a scratch workspace.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

export SAGE_PROXY_MODE="${SAGE_PROXY_MODE:-app}"
export SAGE_CONTROL_HOST="${SAGE_CONTROL_HOST:-0.0.0.0}"
export SAGE_CONTROL_PORT="${SAGE_CONTROL_PORT:-8888}"

# Published from the Sage source project, or a checkout that *is* Sage: don't treat it as an app.
if [ -d /mnt/code/backend/sage/orchestrator ] || [ -d "$here/backend/sage/orchestrator" ]; then
  export SAGE_WORKSPACE_DIR="${SAGE_WORKSPACE_DIR:-/tmp/sage-workspaces/app}"
  mkdir -p "$SAGE_WORKSPACE_DIR"
fi

if [ -x /opt/sage/environment/app.sh ]; then
  export SAGE_APP_HOME="${SAGE_APP_HOME:-/opt/sage}"
  # Python still runs from the baked venv. The entrypoint script comes from this checkout so an
  # App republish can keep `uv run --extra domino` without waiting on an Environment rebuild.
  if [ -f "$here/environment/app.sh" ]; then
    exec bash "$here/environment/app.sh"
  fi
  exec bash /opt/sage/environment/app.sh
fi

export SAGE_APP_HOME="${SAGE_APP_HOME:-$here}"
exec bash "$here/environment/app.sh"
