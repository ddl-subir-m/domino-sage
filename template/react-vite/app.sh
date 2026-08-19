#!/usr/bin/env bash
# Publish entrypoint (Phase 5) — serves THIS app as a Domino App.
#
# A Domino App checks out the project's repo to /mnt/code and runs this file on the chosen hardware
# tier, bound to 0.0.0.0:8888 behind Domino's app proxy. This is a SEPARATE deployment from the live
# in-session preview (its own cold start): install deps, produce a production build (Vite `base` is
# relative for the build, so assets resolve under Domino's app mount path), then static-serve it.
#
# Node builds; PYTHON serves (serve.py, ADR-0002). Both scripts are Sage-owned infrastructure and
# travel together — publish refreshes them from the template, serve.py first.
set -euo pipefail
cd "$(dirname "$0")"

# Every viewer of a first publish waits out this whole script, so each stage says how far in it is
# and serve.py prints the total once it holds the socket. Grep the App log for "[sage] cold start:"
# to compare a deploy against the recorded baseline (see docs/adr/0002-python-serves-the-built-app.md).
export SAGE_APP_T0="$(date +%s)"
stage() { echo "[sage] $1 (+$(( $(date +%s) - SAGE_APP_T0 ))s)"; }

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
stage "dependencies installed"

# Rebuild public/data/ from the project's dataset mounts (attached/uploaded data is gitignored, so
# it isn't in this checkout — the committed .sage/attachments.json manifest maps it back). Must run
# BEFORE the build so Vite copies the linked files into dist/. No-op when nothing was attached.
node scripts/rehydrate-data.mjs
stage "data rehydrated"

# Production build -> dist/ (base "./" via vite.config, so it works under any app mount prefix).
npm run build
stage "build complete"

# Serve the build from dist/ on the port + host Domino's app proxy expects. The proxy strips the app's
# mount prefix BEFORE the request reaches this container, so the server serves at root — that is what
# the replaced `vite preview --base /` was for, and why serve.py needs no prefix of its own.
#
# The BROWSER still sees the prefix: a published app is framed at
# apps.<domain>/apps-internal/<app-id>/ (measured 2026-08-19). That is why the build base stays
# relative and must NOT become "/": an absolute base would ask the apps host for /assets/... with no
# app id in the path, breaking every route including the root page. Relative alone was not enough
# either — it broke any route two or more segments deep (#18) — so serve.py recovers the prefix at
# request time and stamps a <base href> into the page it serves. Stdlib-only Python, so `python3` is
# whatever the image ships (same as the viewer-identity probe's app.sh); nothing to install.
#
# CAREFUL when the query API lands on top of this (#13/#14): the PATH line above, which exists to
# beat conda's node, also puts /usr/bin/python3 ahead of the conda interpreter that carries
# domino_data + pyarrow. Stdlib-only is why that costs nothing today. Importing the Domino SDK will
# need the interpreter chosen deliberately rather than left to PATH — verify which one has it in the
# Sage Environment first, since the probe that confirmed the sidecar ran on a stock Domino image.
exec python3 serve.py --dir dist --host 0.0.0.0 --port 8888
