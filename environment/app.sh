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
# Create it. The default (/mnt/code) is always mounted and this is a no-op there, but the dogfood
# override above names a scratch dir that does not exist yet, and only the App route created it —
# root app.sh mkdirs before exec'ing us, the sageBuilder tool start does not. So the same override
# that works for a published App left the tool route pointing at nothing.
mkdir -p "$SAGE_WORKSPACE_DIR"

# Let git repack the workspace on its own. Sage commits `.sage/history.jsonl` every turn, and each
# commit writes a new full loose object for it: 100 turns leaves ~86MB loose that packs to ~2.5MB.
# Auto-gc never fires on its own here because its default threshold is 6700 loose objects and a
# turn makes ~3. Config does not travel with a clone, so this has to run against the checkout
# itself, once per Builder start. Failure is not fatal — a dogfood override can point
# SAGE_WORKSPACE_DIR at a scratch dir that is not a repo at all.
git -C "$SAGE_WORKSPACE_DIR" config gc.auto 50 2>/dev/null || true
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

# --- Optional: refresh Sage's own code at boot, instead of rebuilding the Environment ----------
# Off by default. Set SAGE_SELF_UPDATE=1 (a project- or Environment-level variable) and a workspace
# pulls the current $SAGE_REV over the baked clone before it boots, so a pure code change costs a
# workspace restart rather than an image build. This moves code only; dependencies stay baked.
#
# Verified in a real Sage Builder 2026-09-06 (app project sage-<user>-<id>, image at a30c519):
#   - /opt/sage is a git checkout owned by ubuntu and writable; `fetch --depth 1` took 1s.
#   - /opt/sage carries NO credential of its own — `credential fill` there answered with 0 bytes.
#     Domino wires a credential PER REPOSITORY, into the checkout's own .git/config, so the only
#     token in the container is the workspace repo's. We borrow it, as provision/credentials.py does.
#   - That token DID read domino-sage, because it is an account-wide classic PAT (ghp_, 40 chars).
#     A fine-grained PAT scoped to the app repo alone will not, and this must then fall back.
#   - The clone's refspec is pinned (+refs/heads/main:refs/remotes/origin/main), which is why the
#     ref is passed to `fetch` explicitly rather than relying on the configured one.
#
# Three rules hold this together:
#   1. Fail open. `set -e` is on, so every step sits in an `if` or ends `|| true`. A container that
#      cannot reach the host boots the image it already has — the pre-existing behaviour.
#   2. `reset --hard`, NEVER `git clean`. The baked node_modules (202MB) and .venv are gitignored,
#      so a reset leaves them alone while a clean would delete both and leave the workspace unbootable.
#   3. The borrowed token is never exported and never reaches argv — it is set for the single `git
#      fetch` that needs it, so it lands in neither `ps` nor the orchestrator's environment. That
#      matters: sage-chat runs with `bash: allow`, so this process's env is readable by asking.
if [ "${SAGE_SELF_UPDATE:-0}" = "1" ]; then
  _su_skip=""
  if [ ! -d "$SAGE_APP_HOME/.git" ]; then
    _su_skip="$SAGE_APP_HOME is not a git checkout"
  elif [ "$SAGE_APP_HOME" = "$SAGE_WORKSPACE_DIR" ] || [ "$SAGE_APP_HOME" = "/mnt/code" ]; then
    # The fast dev loop points SAGE_APP_HOME at the mount you edit by hand. Resetting that would
    # throw away uncommitted work, so self-update is for the baked clone and nothing else.
    _su_skip="app home is the checkout you edit"
  elif [ -n "$(git -C "$SAGE_APP_HOME" status --porcelain -uno 2>/dev/null || true)" ]; then
    # Tracked files only: OpenCode leaves untracked state under SAGE_OPENCODE_CWD, which a reset
    # would not touch anyway. A modified TRACKED file means someone is hand-patching the image.
    _su_skip="tracked files are modified"
  fi

  if [ -n "$_su_skip" ]; then
    echo "[sage] self-update skipped: $_su_skip"
  else
    _su_host="${SAGE_GIT_HOST:-github.com}"
    _su_rev="${SAGE_REV:-}"
    if [ -z "$_su_rev" ]; then
      # SAGE_REV is a build ARG the Dockerfile never promotes to ENV, so it is unset at runtime.
      # The clone was made with `--branch`, so the checked-out branch names the same ref.
      _su_rev="$(git -C "$SAGE_APP_HOME" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
    fi
    if [ "$_su_rev" = "HEAD" ]; then _su_rev=main; fi

    # Borrow the workspace checkout's credential. Same sweep order as provision/credentials.py: the
    # mounted checkout first, because that is the only place Domino wired a helper.
    _su_tok=""
    for _su_dir in "$SAGE_WORKSPACE_DIR" /mnt/code; do
      if [ -z "$_su_tok" ] && [ -d "$_su_dir" ]; then
        _su_tok="$(printf 'protocol=https\nhost=%s\n\n' "$_su_host" \
          | GIT_TERMINAL_PROMPT=0 GIT_ASKPASS= git -C "$_su_dir" credential fill 2>/dev/null \
          | sed -n 's/^password=//p' || true)"
      fi
    done

    _su_was="$(git -C "$SAGE_APP_HOME" rev-parse --short HEAD 2>/dev/null || echo '?')"
    # Hash the template lockfile either side of the reset. A code-only update is free, but one that
    # moved the template's dependencies leaves the baked node_modules stale and needs a real rebuild.
    _su_lock="$SAGE_APP_HOME/template/react-vite/package-lock.json"
    _su_lock_was="$(md5sum "$_su_lock" 2>/dev/null | cut -d' ' -f1 || true)"

    if [ -z "$_su_tok" ]; then
      echo "[sage] self-update: no HTTPS credential for $_su_host here — staying on $_su_was"
    elif ! SAGE_SU_TOKEN="$_su_tok" GIT_TERMINAL_PROMPT=0 git -C "$SAGE_APP_HOME" \
           -c credential.helper= \
           -c credential.helper='!f(){ echo username=x-access-token; echo "password=$SAGE_SU_TOKEN"; }; f' \
           fetch --depth 1 origin "$_su_rev" >/dev/null 2>&1; then
      echo "[sage] self-update: fetch of $_su_rev failed — staying on $_su_was"
    elif ! git -C "$SAGE_APP_HOME" reset --hard FETCH_HEAD >/dev/null 2>&1; then
      echo "[sage] self-update: reset failed — staying on $_su_was"
    else
      echo "[sage] self-update: $_su_was -> $(git -C "$SAGE_APP_HOME" rev-parse --short HEAD) ($_su_rev)"
      if [ "$(md5sum "$_su_lock" 2>/dev/null | cut -d' ' -f1 || true)" != "$_su_lock_was" ]; then
        echo "[sage] WARNING: the template lockfile moved. The baked node_modules are now STALE and"
        echo "[sage]          the preview may fail to start. Rebuild the Environment to clear this."
      fi
      # Cheap: measured at 0s when the lock is unchanged. It needs PyPI when it is not, so a failure
      # here is survivable — the venv the image built is still the one we boot with.
      if ! (cd "$SAGE_APP_HOME/backend" && uv sync --extra domino >/dev/null 2>&1); then
        echo "[sage] self-update: uv sync failed (offline?) — running the baked venv"
      fi
    fi
    unset _su_tok _su_dir _su_host _su_rev _su_was _su_lock _su_lock_was
  fi
  unset _su_skip
fi

cd "$SAGE_APP_HOME/backend"
# `--extra domino` is the same extra the image `uv sync`s. Plain `uv run` re-syncs the venv to
# default deps only and drops `dominodatalab-data`, which is how a builder that passed the
# Dockerfile import check still told the UI "the Domino data library is not installed here".
exec uv run --extra domino python -m sage.orchestrator.app
