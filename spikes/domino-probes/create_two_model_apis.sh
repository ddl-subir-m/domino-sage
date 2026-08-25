#!/usr/bin/env bash
# Deploy the TWO Model APIs the remaining live checks need. A thin wrapper around
# create_model_api.sh, run twice.
#
# Why two and not one: #34's open half is "the built app can call only one Model API, however many
# are bound". One deployed model cannot disprove that. Two can — and #9's criterion 4 (build,
# publish, get a prediction in a browser) rides along on the same pair.
#
# Run INSIDE a Domino workspace, in the project that should own them. Auth and project come from
# the workspace sidecar, so there is nothing to configure.
#
# COMMIT FIRST. A Model API deploys from a commit, not from the workspace disk. model_churn.py and
# model_priority.py must be pushed before this runs — the inner script stops with the exact git
# command if they are not.
#
# NOT read-only: this deploys compute and it costs money while it runs. Both delete commands are
# printed at the end. Use them when the checks are done.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"

# Sequential, not parallel. The first build pulls the environment image and the second reuses it,
# so running them one after the other costs little more than one build and keeps the output
# readable when something fails.
echo "=== 1/2  sage-probe-churn  (model_churn.py) ==="
MODEL_NAME="${CHURN_NAME:-sage-probe-churn}" MODEL_FILE=model_churn.py MODEL_FUNC=predict \
  "$HERE/create_model_api.sh"

echo
echo "=== 2/2  sage-probe-priority  (model_priority.py) ==="
MODEL_NAME="${PRIORITY_NAME:-sage-probe-priority}" MODEL_FILE=model_priority.py MODEL_FUNC=predict \
  "$HERE/create_model_api.sh"

cat <<'EOF'

=== Both deployed. What to do with them ===

1. Open each Model API's Overview page and copy its SAMPLE REQUEST. Sage parses the URL and the
   token out of that snippet — it cannot guess either.

2. Paste one snippet per Model API into Sage's Resources rail. Each paste makes a Binding.

3. Ask for one app that calls BOTH, naming them per call site, e.g.

     score every customer in @<data-source> with @sage-probe-churn,
     and triage the tickets with @sage-probe-priority

   The two take different arguments on purpose, so a call wired to the wrong model answers with
   the wrong keys instead of quietly looking right.

Test each by hand first, with a model access token from the Overview page:

  curl -sS -u "<token>:<token>" -H 'Content-Type: application/json' \
    -d '{"data":{"tenure_months":3,"monthly_spend":129.5,"support_tickets":4}}' \
    "$DOMINO_API_HOST/models/<churn-id>/latest/model"
  # -> {"risk": 0.85, "band": "high", "drivers": ["new account", "high spend", "repeat support contact"]}

  curl -sS -u "<token>:<token>" -H 'Content-Type: application/json' \
    -d '{"data":{"subject":"Cannot log in","body":"urgent, whole team is blocked","customer_tier":"enterprise"}}' \
    "$DOMINO_API_HOST/models/<priority-id>/latest/model"
  # -> {"priority": "P1", "score": 7, "matched": ["urgent", "blocked"]}

Basic auth with the token as BOTH user and password is the shape that answers 200 — see the
credential table in #9. Bearer does not.
EOF
