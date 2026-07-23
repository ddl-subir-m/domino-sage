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
# Phase 2: the builder is bound to ONE workspace dir. Keep it off the git mount here (scratch,
# fine to lose between sessions) — in real deploys this is the Domino project's own volume.
export SAGE_WORKSPACE_DIR="${SAGE_WORKSPACE_DIR:-/tmp/sage-workspaces/app}"

echo "[verify] host=${SAGE_CONTROL_HOST} port=${SAGE_CONTROL_PORT} workspace=${SAGE_WORKSPACE_DIR}"
echo "[verify] prefix (env-derived): /${DOMINO_PROJECT_OWNER:-?}/${DOMINO_PROJECT_NAME:-?}/notebookSession/${DOMINO_RUN_ID:-?}"

# Prefer a system Node (nodesource) over conda's, which commonly shadows it at an OLDER version on
# Domino images. This PATH is inherited by the orchestrator and the Vite child it spawns.
export PATH="/usr/bin:/usr/local/bin:${PATH}"
hash -r 2>/dev/null || true
echo "[verify] node candidates:"; which -a node 2>/dev/null || true
echo "[verify] using node=$(command -v node) $(node -v 2>/dev/null)  npm=$(npm -v 2>/dev/null)"

# vite@8 (rolldown) requires Node >=20.19. Hard-fail with a clear message rather than let Vite die
# with an opaque native-binding error downstream.
ver="$(node -v 2>/dev/null | sed 's/^v//')"; major="${ver%%.*}"; minor="$(printf '%s' "$ver" | cut -d. -f2)"
if [ "${major:-0}" -lt 20 ] || { [ "${major}" = "20" ] && [ "${minor:-0}" -lt 19 ]; }; then
  echo "[verify] ERROR: Node ${ver:-<none>} is too old (need >=20.19, recommend 22)." >&2
  echo "[verify] The Environment's nodesource Node isn't first on PATH (conda shadows it)." >&2
  exit 1
fi

# Template deps: the workspace manager symlinks the template's node_modules into each new workspace,
# so `npm run dev` needs them present AND complete (rolldown's platform-native binary included).
# Reinstall clean when missing or when the Node version changed since the last install — a partial
# install done under a different Node omits the optional platform binary. (Phase 3 bakes this into
# the image; here it's a one-time cost on the persistent mount.)
TMPL=../template/react-vite
marker="${TMPL}/node_modules/.sage-node-version"
if [ ! -f "${marker}" ] || [ "$(cat "${marker}" 2>/dev/null)" != "$(node -v)" ]; then
  echo "[verify] (re)installing template deps clean for $(node -v)…"
  rm -rf "${TMPL}/node_modules"
  ( cd "${TMPL}" && npm install --include=optional --no-fund --no-audit )
  node -v > "${marker}"
fi

# Run the real orchestrator. uv syncs backend/pyproject.toml deps into a venv on first run.
exec uv run python -m sage.orchestrator.app
