#!/usr/bin/env bash
# Domino App entrypoint for the viewer-identity probe.
#
# A Domino App checks the project out to /mnt/code and runs /mnt/code/app.sh, bound to
# 0.0.0.0:8888 behind Domino's app proxy. This probe is stdlib-only Python, so there is
# nothing to install and no build step.
#
# DO NOT publish this from the sage repo's main branch -- that repo's root app.sh is the
# Sage Hub and this would replace it. See README.md: copy both files into a throwaway
# Domino project instead.
set -euo pipefail
cd "$(dirname "$0")"
export PATH=/usr/local/bin:/usr/bin:$PATH
exec python3 probe_server.py
