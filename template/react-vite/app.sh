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

# The PATH line above, which exists to beat conda's node, also puts /usr/bin/python3 ahead of the
# conda interpreter that carries domino_data + pyarrow. That cost nothing while serve.py was
# stdlib-only; now that it runs Data Source queries (#14), the interpreter is chosen deliberately
# rather than left to PATH. serve.py itself imports and serves under any of them — the SDK import is
# late and local — so a wrong choice here costs the queries, not the app.
#
# Chosen BEFORE the build, not just before serve: the same library fetches the attached data that
# lives in Datasets this App has not mounted, and that has to land before Vite bakes public/ into
# dist/. A missing interpreter still costs only data, never the build.
#
# Candidates, in order. First is the Sage venv, which the Environment build ASSERTS can import the
# library; then conda, which is where the Domino base image's own copy lives; then whatever PATH
# says, which is the answer on an image whose system python has it. SAGE_APP_PYTHON overrides the
# lot, for an app deployed on an Environment none of this describes.
#
# find_spec, not a real import: importing domino_data pulls pandas and pyarrow, and paying that up to
# four times before the port is bound would show up as cold start (ADR-0002). serve.py makes the real
# import in the background once it is serving, and logs which interpreter answered.
SAGE_PYTHON="${SAGE_APP_PYTHON:-}"
if [ -z "$SAGE_PYTHON" ]; then
  for candidate in /opt/sage/backend/.venv/bin/python /opt/conda/bin/python3 /opt/conda/bin/python \
                   "$(command -v python3 || true)"; do
    [ -n "$candidate" ] && [ -x "$candidate" ] || continue
    if "$candidate" -c 'import importlib.util,sys; sys.exit(0 if importlib.util.find_spec("domino_data.data_sources") else 1)' 2>/dev/null; then
      SAGE_PYTHON="$candidate"
      break
    fi
  done
fi
if [ -z "$SAGE_PYTHON" ]; then
  # Not fatal: an app that reads no Data Source is every app the template produced before #14, and it
  # serves exactly as well. One that does read one says so per query, in a sentence, to the viewer.
  SAGE_PYTHON="$(command -v python3)"
  echo "[sage] no interpreter here carries the Domino data library; Data Source queries will fail"
fi
echo "[sage] python: $SAGE_PYTHON"

# Rebuild public/data/ from the committed .sage/attachments.json manifest (attached/uploaded data is
# gitignored, so it isn't in this checkout). Must run BEFORE the build so Vite copies the files into
# dist/. Two steps, because a mount answers for some Datasets and not others: the first links what
# this App's hardware already has on disk, the second downloads the rest — anything shared from
# another project, or added to the project after this execution started. Both no-op when nothing
# was attached, and neither can fail the publish.
node scripts/rehydrate-data.mjs
"$SAGE_PYTHON" scripts/rehydrate_data.py || echo "[sage] data fetch skipped"
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
# request time and stamps a <base href> into the page it serves.
#

exec "$SAGE_PYTHON" serve.py --dir dist --host 0.0.0.0 --port 8888
