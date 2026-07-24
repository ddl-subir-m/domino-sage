#!/usr/bin/env bash
# Sage Hub entrypoint (Phase 4). The "New app" control plane — lists the user's Sage apps and
# provisions new ones (private repo -> seeded git-based Domino project -> launched Sage Builder).
#
# Runs from the SAME baked image as the builder (/opt/sage); only the entrypoint differs. Launch it
# as the `sageHub` pluggable tool in a dedicated git-based hub project (its /mnt/code checkout is
# where the ambient GitHub credential lives — used to create repos via the API).
set -euo pipefail

export SAGE_APP_HOME="${SAGE_APP_HOME:-/opt/sage}"
export SAGE_TEMPLATE="${SAGE_TEMPLATE:-$SAGE_APP_HOME/template/react-vite}"
# Where to read the ambient git credential + detect the provider (the hub project's own checkout).
export SAGE_HUB_GIT_CWD="${SAGE_HUB_GIT_CWD:-/mnt/code}"

# One port; bind all interfaces so Domino's tool proxy can reach us. Prefix is derived from env.
export SAGE_CONTROL_HOST="${SAGE_CONTROL_HOST:-0.0.0.0}"
export SAGE_CONTROL_PORT="${SAGE_CONTROL_PORT:-8888}"

# The hub creates child workspaces reusing THIS workspace's environment + hardware tier (Domino
# injects DOMINO_API_HOST / DOMINO_ENVIRONMENT_ID / DOMINO_HARDWARE_TIER_ID). No gateway needed here
# — the hub only calls the v4 control plane + the git provider API, not a model.

export PATH="/usr/bin:/usr/local/bin:${PATH}"
hash -r 2>/dev/null || true
echo "[hub] node=$(command -v node) $(node -v)  git_cwd=$SAGE_HUB_GIT_CWD port=$SAGE_CONTROL_PORT"

cd "$SAGE_APP_HOME/backend"
exec uv run python -m sage.hub.app
