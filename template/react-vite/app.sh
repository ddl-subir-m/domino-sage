#!/usr/bin/env bash
# Publish entrypoint (Phase 5) — serves THIS app as a Domino App.
#
# A Domino App checks out the project's repo to /mnt/code and runs this file on the chosen hardware
# tier, bound to 0.0.0.0:8888 behind Domino's app proxy. This is a SEPARATE deployment from the live
# in-session preview (its own cold start): install deps, produce a production build (Vite `base` is
# relative for the build, so assets resolve under Domino's app mount path), then static-serve it.
set -euo pipefail
cd "$(dirname "$0")"

# Our node (official tarball at /usr/local/bin, v22) must beat BOTH conda's node and the base image's
# stale /usr/bin/node (Debian bookworm ships v18.19.1). /usr/local FIRST — the same order the
# Environment Dockerfile and environment/app.sh use. Getting this backwards is not a soft failure:
# node 18 lacks node:util's styleText, so `vite build` dies with "The requested module 'node:util'
# does not provide an export named 'styleText'" and the published App crash-loops. (Node <20.19 also
# fails vite@8/rolldown's engine check, which makes npm SILENTLY skip their platform-native optional
# binding @rolldown/binding-linux-x64-gnu — the other way this shows up.)
export PATH=/usr/local/bin:/usr/bin:$PATH

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
