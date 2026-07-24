#!/usr/bin/env bash
# Sage Builder entrypoint (Phase 3). Boots the single-port orchestrator baked into the Environment.
#
# Sage's code + warm template are baked at /opt/sage (see environment/Dockerfile); the user's app
# repo mounts at /mnt/code at runtime and IS the workspace. No runtime npm/uv installs — everything
# was baked — so cold start is just "boot the server".
set -euo pipefail

# Where Sage's own code lives. Point this at a mount for a fast inner dev loop (edit on /mnt/code,
# no image rebuild): e.g. SAGE_APP_HOME=/mnt/code in a git-based sage-source project.
export SAGE_APP_HOME="${SAGE_APP_HOME:-/opt/sage}"
export SAGE_TEMPLATE="${SAGE_TEMPLATE:-$SAGE_APP_HOME/template/react-vite}"
export SAGE_OPENCODE_CWD="${SAGE_OPENCODE_CWD:-$SAGE_APP_HOME}"   # where opencode.json lives

# The workspace = the app's git checkout. In a deployed app that's the mounted app repo (/mnt/code).
# DOGFOOD OVERRIDE: if /mnt/code is the sage SOURCE repo, set this to a scratch dir instead so we
# don't treat the source tree as an app, e.g. SAGE_WORKSPACE_DIR=/tmp/sage-workspaces/app.
export SAGE_WORKSPACE_DIR="${SAGE_WORKSPACE_DIR:-/mnt/code}"

# One port; bind all interfaces so Domino's tool proxy can reach us. Prefix is derived from env.
export SAGE_CONTROL_HOST="${SAGE_CONTROL_HOST:-0.0.0.0}"
export SAGE_CONTROL_PORT="${SAGE_CONTROL_PORT:-8888}"

# Gateway (Domino AI Gateway / sovereign router). FILL IN for a real build — set GATEWAY_BASE_URL
# (+ creds; the sidecar token at :8899 is used by default and re-acquired per call). Without it the
# orchestrator still boots and serves the UI/preview, but builds can't reach a model.

# nodesource node must beat conda on PATH (inherited by the Vite + OpenCode children).
export PATH="/usr/bin:/usr/local/bin:${PATH}"
hash -r 2>/dev/null || true
echo "[sage] node=$(command -v node) $(node -v)  opencode=$(opencode --version 2>/dev/null || echo '<missing>')"
echo "[sage] app_home=$SAGE_APP_HOME workspace=$SAGE_WORKSPACE_DIR port=$SAGE_CONTROL_PORT"

cd "$SAGE_APP_HOME/backend"
exec uv run python -m sage.orchestrator.app
