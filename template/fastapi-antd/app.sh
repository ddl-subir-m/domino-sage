#!/usr/bin/env bash
# Publish entrypoint — serves THIS app as a Domino App (#490).
#
# A Domino App checks out the project's repo to /mnt/code and runs this file on the chosen hardware
# tier, bound to 0.0.0.0:8888 behind Domino's app proxy. There is no build: the page and everything
# it loads are files under static/, and Python serves them (sage_serve.py, mounted by app.py). So the
# cold start is the data rehydrate and uvicorn binding its port, and nothing else.
#
# app.sh, sage_serve.py, sage_queries.py and scripts/rehydrate_data.py are Sage-owned infrastructure
# and travel together — publish refreshes them from the template, the Python files first.
set -euo pipefail
cd "$(dirname "$0")"

# Every viewer of a first publish waits out this whole script; each stage says how far in it is, and
# sage_serve.py prints the total once uvicorn holds the socket. Grep the App log for "[sage] cold
# start:" to compare a deploy against the recorded baseline (docs/adr/0002-python-serves-the-built-app.md).
export SAGE_APP_T0="$(date +%s)"
stage() { echo "[sage] $1 (+$(( $(date +%s) - SAGE_APP_T0 ))s)"; }

# Which python serves. Two things have to be importable: fastapi and uvicorn, without which nothing
# serves at all, and — for an app that reads a Data Source — the Domino data library, without which
# every query says so to the viewer in a sentence. Candidates in order: the Sage venv, whose
# Environment build ASSERTS all three; conda, where the Domino base image's own copy of the data
# library lives; whatever PATH says. SAGE_APP_PYTHON overrides the lot, for an app deployed on an
# Environment none of the rest describes.
#
# find_spec, not a real import: importing the data library pulls pandas and pyarrow, and paying that
# up to four times before the port is bound would show up as cold start (ADR-0002). sage_serve.py
# makes the real import in a background thread once it is serving, and logs which interpreter
# answered.
SAGE_PYTHON="${SAGE_APP_PYTHON:-}"
serves() {
  "$1" -c 'import importlib.util,sys; sys.exit(0 if importlib.util.find_spec("fastapi") and importlib.util.find_spec("uvicorn") else 1)' 2>/dev/null
}
reads_data() {
  "$1" -c 'import importlib.util,sys; sys.exit(0 if importlib.util.find_spec("domino_data.data_sources") else 1)' 2>/dev/null
}
if [ -z "$SAGE_PYTHON" ]; then
  fallback=""
  for candidate in /opt/sage/backend/.venv/bin/python /opt/conda/bin/python3 /opt/conda/bin/python "$(command -v python3 || true)"; do
    [ -n "$candidate" ] && [ -x "$candidate" ] || continue
    serves "$candidate" || continue
    if reads_data "$candidate"; then SAGE_PYTHON="$candidate"; break; fi
    [ -n "$fallback" ] || fallback="$candidate"
  done
  if [ -z "$SAGE_PYTHON" ] && [ -n "$fallback" ]; then
    # Not fatal: an app that reads no Data Source serves exactly as well. One that does says so per
    # query, in a sentence, to the viewer.
    SAGE_PYTHON="$fallback"
    echo "[sage] no interpreter here carries both a web server and the Domino data library; Data Source queries will fail"
  fi
fi
if [ -z "$SAGE_PYTHON" ]; then
  echo "[sage] no interpreter here can import fastapi and uvicorn, so this app cannot serve"
  exit 1
fi
echo "[sage] python: $SAGE_PYTHON"

# Rebuild public/data/ from the committed .sage/attachments.json manifest (attached/uploaded data is
# gitignored, so it isn't in this checkout). Links what the App's hardware already has mounted, then
# downloads the rest through the Domino data library. No-op when nothing was attached; cannot fail
# the publish.
"$SAGE_PYTHON" scripts/rehydrate_data.py || echo "[sage] data fetch skipped"
stage "data rehydrated"

# Serve on the port and host Domino's app proxy expects. The proxy strips the app's mount prefix
# BEFORE a request reaches this container, so the server serves at the root; the BROWSER still sees
# the prefix, which is why sage_serve.py stamps a <base href> into the page it serves.
exec "$SAGE_PYTHON" -m uvicorn app:app --host 0.0.0.0 --port 8888
