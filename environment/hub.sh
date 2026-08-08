#!/usr/bin/env bash
# Sage Hub entrypoint (Phase 4). The "New app" control plane — lists the user's Sage apps and
# provisions new ones (private repo -> seeded git-based Domino project -> launched Sage Builder).
#
# Runs from the SAME baked image as the builder (/opt/sage); only the entrypoint differs. Launch it
# as the `sageHub` pluggable tool. It needs no git-based project of its own — the GitHub token comes
# from Domino's global `git credential` helper (the user's account credential), not from a checkout.
set -euo pipefail

export SAGE_APP_HOME="${SAGE_APP_HOME:-/opt/sage}"
# Seed source: always the baked /opt/sage template (warm node_modules baked there only). Not tied to
# SAGE_APP_HOME so a fast-loop /mnt/code override for backend code doesn't drag the seed off the mount.
export SAGE_TEMPLATE="${SAGE_TEMPLATE:-/opt/sage/template/react-vite}"
# The git host to provision against (which Domino credential to use). Explicit host = no git-based
# project required. Unset it to fall back to sniffing SAGE_HUB_GIT_CWD's origin (legacy deploy).
export SAGE_GIT_HOST="${SAGE_GIT_HOST:-github.com}"
export SAGE_HUB_GIT_CWD="${SAGE_HUB_GIT_CWD:-/mnt/code}"

# One port; bind all interfaces so Domino's tool proxy can reach us. Prefix is derived from env.
export SAGE_CONTROL_HOST="${SAGE_CONTROL_HOST:-0.0.0.0}"
export SAGE_CONTROL_PORT="${SAGE_CONTROL_PORT:-8888}"

# The hub creates child workspaces reusing THIS workspace's environment + hardware tier (Domino
# injects DOMINO_API_HOST / DOMINO_ENVIRONMENT_ID / DOMINO_HARDWARE_TIER_ID). No gateway needed here
# — the hub only calls the v4 control plane + the git provider API, not a model.

# /usr/local first, so our Node 22 tarball beats conda's node and the base image's stale
# /usr/bin/node (v18.19.1) — same order as environment/app.sh and the Dockerfile. The hub itself
# only needs git + uv, but the `node -v` line below should report what a child would actually get.
export PATH="/usr/local/bin:/usr/bin:${PATH}"
hash -r 2>/dev/null || true
echo "[hub] node=$(command -v node) $(node -v)  git_cwd=$SAGE_HUB_GIT_CWD port=$SAGE_CONTROL_PORT"

cd "$SAGE_APP_HOME/backend"
exec uv run python -m sage.hub.app
