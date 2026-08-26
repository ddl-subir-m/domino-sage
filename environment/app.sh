#!/usr/bin/env bash
# Sage Builder entrypoint. Boots the single-port orchestrator (Chat + Build) baked into the Environment.
#
# Sage's code + warm template are baked at /opt/sage (see environment/Dockerfile); the user's app
# repo mounts at /mnt/code at runtime and IS the workspace. No runtime npm/uv installs — everything
# was baked — so cold start is just "boot the server".
set -euo pipefail

# Where Sage's own code lives. Point this at a mount for a fast inner dev loop (edit on /mnt/code,
# no image rebuild): e.g. SAGE_APP_HOME=/mnt/code in a git-based sage-source project.
export SAGE_APP_HOME="${SAGE_APP_HOME:-/opt/sage}"
# The warm template is ALWAYS the baked /opt/sage copy — its node_modules are baked there (Dockerfile
# npm ci) and nowhere else. Deliberately NOT tied to SAGE_APP_HOME: the fast dev loop points that at
# /mnt/code to edit backend code, but the mount's template carries no warm deps, so following it there
# would boot the preview cold. Override SAGE_TEMPLATE explicitly only to iterate on the template itself.
export SAGE_TEMPLATE="${SAGE_TEMPLATE:-/opt/sage/template/react-vite}"
export SAGE_OPENCODE_CWD="${SAGE_OPENCODE_CWD:-$SAGE_APP_HOME}"   # where opencode.json lives

# The workspace = the app's git checkout. In a Sage Builder workspace that's the mounted app repo
# (/mnt/code). DOGFOOD OVERRIDE: if /mnt/code is the sage SOURCE repo, set this to a scratch dir
# instead so we don't treat the source tree as an app, e.g. SAGE_WORKSPACE_DIR=/tmp/sage-workspaces/app.
# The published Workbench App (root app.sh) sets SAGE_PROXY_MODE=app and a scratch dir before execing us.
export SAGE_WORKSPACE_DIR="${SAGE_WORKSPACE_DIR:-/mnt/code}"
export SAGE_PROXY_MODE="${SAGE_PROXY_MODE:-workspace}"

# One port; bind all interfaces so Domino's tool/app proxy can reach us. Prefix is derived from env
# (notebookSession when SAGE_PROXY_MODE=workspace; empty when app — nginx already stripped the mount).
export SAGE_CONTROL_HOST="${SAGE_CONTROL_HOST:-0.0.0.0}"
export SAGE_CONTROL_PORT="${SAGE_CONTROL_PORT:-8888}"

# Gateway (Domino AI Gateway / sovereign router). FILL IN for a real build — set GATEWAY_BASE_URL
# (+ creds; the sidecar token at :8899 is used by default and re-acquired per call). Without it the
# orchestrator still boots and serves the UI/preview, but builds can't reach a model.

# Our node (official tarball at /usr/local/bin, v22) must beat BOTH conda's node and the base
# image's stale /usr/bin/node (Debian bookworm ships v18.19.1, which lacks node:util styleText and
# hard-fails rolldown/vite). /usr/local first — this PATH is inherited by the Vite + OpenCode children.
export PATH="/usr/local/bin:/usr/bin:${PATH}"
hash -r 2>/dev/null || true
echo "[sage] node=$(command -v node) $(node -v)  opencode=$(opencode --version 2>/dev/null || echo '<missing>')"
echo "[sage] app_home=$SAGE_APP_HOME workspace=$SAGE_WORKSPACE_DIR port=$SAGE_CONTROL_PORT proxy=$SAGE_PROXY_MODE"

cd "$SAGE_APP_HOME/backend"
exec uv run python -m sage.orchestrator.app
