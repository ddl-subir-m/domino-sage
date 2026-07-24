#!/usr/bin/env bash
# App-publish discovery — run inside the GIT-BASED Domino project workspace whose
# app you want to publish (Phase 5).
#
# Answers ONE question Phase 5's "Publish" flow depends on: the exact create body
# Domino's PUBLIC apps API accepts for a git-based app, and whether creating an app
# with a `version` ALSO launches it (so publish is one call, not create-then-start).
#
# Endpoint (public "beta" apps family — same one the hub already uses for projects,
# NOT the /v4 private modelProducts route):
#   POST   {DOMINO_API_HOST}/api/apps/beta/apps            create (+launch a version)
#   POST   {DOMINO_API_HOST}/api/apps/beta/apps/{id}/versions   republish
#   GET    {DOMINO_API_HOST}/api/apps/beta/apps?projectId=...   list
#   DELETE {DOMINO_API_HOST}/api/apps/beta/apps/{id}      cleanup
#
# Contract confirmed from the generated public client (AppCreationRequest):
#   required: name, projectId, version{...}, visibility
#   version (AppVersionCreationRequest): environmentId, hardwareTierId, gitRef{type,value}
#   entryPoint optional (defaults to the repo's app.sh)
#   gitRef.type in {head, branches, commitId, tags, custom}; visibility in
#   {PRIVATE?/AUTHENTICATED/GRANT_BASED/GRANT_BASED_STRICT/PUBLIC}
#
# SAFETY: read-only by default (lists existing apps + prints the exact create body
# it WOULD send, token redacted). Set PROBE_CREATE=1 to actually create a throwaway
# app and immediately delete it (EXIT trap deletes even on partial success). The
# sidecar token is never echoed.
#
# Usage (in the workspace):
#   bash /mnt/code/spikes/domino-probes/app_publish_probe.sh                 # discovery only
#   PROBE_CREATE=1 bash /mnt/code/spikes/domino-probes/app_publish_probe.sh  # + create/cleanup
#
# This is a bash wrapper that shells to the co-located Python for clean JSON handling.
set -uo pipefail
exec uv run --with httpx "$(dirname "$0")/app_publish_probe.py" "$@"
