#!/usr/bin/env bash
# Launcher for running the Sage Hub as a published Domino App (not a workspace tool).
#
# A Domino App deploys from a git-based project and runs THIS file (`/mnt/code/app.sh`) on the
# project's Environment + the hardware tier you pick at publish time. The hub's real entrypoint is
# baked into the Sage Environment at /opt/sage/environment/hub.sh, so this launcher just execs it.
#
# The launcher project needs nothing but this file: hub.sh sets SAGE_GIT_HOST=github.com, so the hub
# never sniffs the project's remote — it only needs Domino's global `git credential` helper for the
# GitHub token. And because the App runs ON the Sage Environment, the DOMINO_ENVIRONMENT_ID /
# DOMINO_HARDWARE_TIER_ID Domino injects into this run ARE the env/tier the hub launches child
# builders into — nothing to hardcode.
#
# Publish: see environment/HUB-AS-APP.md.
set -euo pipefail
exec bash /opt/sage/environment/hub.sh
