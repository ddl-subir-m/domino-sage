#!/usr/bin/env bash
# Publish THIS repo as the Sage Hub Domino App.
#
# A Domino App checks this repo out to /mnt/code and runs /mnt/code/app.sh on the Sage Environment.
# The hub's real entrypoint is baked into that image at /opt/sage/environment/hub.sh, so on Domino we
# just exec it. If the baked copy isn't present (e.g. running `bash app.sh` from this checkout off the
# Sage image), fall back to this repo's own hub.sh with SAGE_APP_HOME pointed at the checkout.
#
# See environment/HUB-AS-APP.md. The hub needs NO env vars injected: it launches child builders into
# the DOMINO_ENVIRONMENT_ID / DOMINO_HARDWARE_TIER_ID Domino injects into this App's own run.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

if [ -x /opt/sage/environment/hub.sh ]; then
  exec bash /opt/sage/environment/hub.sh
fi

# Not on the baked Sage image — run the hub straight from this checkout.
export SAGE_APP_HOME="$here"
exec bash "$here/environment/hub.sh"
