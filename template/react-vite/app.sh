#!/usr/bin/env bash
# Publish entrypoint (Phase 5) — serves THIS app as a Domino App.
#
# A Domino App checks out the project's repo to /mnt/code and runs this file on the chosen hardware
# tier, bound to 0.0.0.0:8888 behind Domino's app proxy. This is a SEPARATE deployment from the live
# in-session preview (its own cold start): install deps, produce a production build (Vite `base` is
# relative for the build, so assets resolve under Domino's app mount path), then static-serve it.
set -euo pipefail
cd "$(dirname "$0")"

# Domino's App launcher prepends conda's node (v20.18) to PATH, shadowing the nodesource Node 22 the
# Environment baked at /usr/bin. vite@8/rolldown require Node >=20.19, and npm SILENTLY skips their
# platform-native optional binding (@rolldown/binding-linux-x64-gnu) when the running node fails that
# engine check — then `vite build` dies with "Cannot find native binding". Force the baked Node 22
# ahead of conda's, the same PATH override the Environment Dockerfile bakes.
export PATH=/usr/bin:/usr/local/bin:$PATH

# The agent may have added dependencies during the build session, so install from the lockfile.
npm ci

# Rebuild public/data/ from the project's dataset mounts (attached/uploaded data is gitignored, so
# it isn't in this checkout — the committed .sage/attachments.json manifest maps it back). Must run
# BEFORE the build so Vite copies the linked files into dist/. No-op when nothing was attached.
node scripts/rehydrate-data.mjs

# Production build -> dist/ (base "./" via vite.config, so it works under any app mount prefix).
npm run build

# Static-serve the build on the port + host Domino's app proxy expects. `--base /` overrides the
# dev-preview base: `vite preview` otherwise re-reads vite.config as a "serve" command and would mount
# under the preview prefix, 404-ing the relative-base build. Domino strips the app's mount prefix, so
# the built app's relative "./assets" URLs resolve correctly against this root-served build.
exec npx vite preview --base / --host 0.0.0.0 --port 8888 --strictPort
