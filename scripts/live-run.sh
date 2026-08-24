#!/usr/bin/env bash
# Walk the checks that are waiting on a human, and record what was run.
#
# Everything still open on this repo is verification against real Domino, not missing code.
# This script does the part a laptop can do — is the tree green, and does the deployed image
# actually contain the code you are about to verify — then walks the acceptance boxes GitHub
# still has unticked, groups them by what you must have on hand, and writes the session up
# under docs/live-runs/. A box you skip is written down as skipped: that is the whole lesson
# of #25, where boxes that lived only in a comment dropped out of the count.
#
# usage:
#   scripts/live-run.sh                 # checks, walk the boxes, write the doc
#   scripts/live-run.sh --report        # checks and the box list only. No prompts, no file.
#   scripts/live-run.sh --label snowflake   # name the doc docs/live-runs/<date>-snowflake.md
set -euo pipefail

cd "$(dirname "$0")/.."
REPORT_ONLY=0
LABEL="waiting-on-a-human"
while [ $# -gt 0 ]; do
  case "$1" in
    --report) REPORT_ONLY=1 ;;
    --label) LABEL="$2"; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; }
bad()  { printf '  \033[31mSTOP\033[0m  %s\n' "$*"; }

# ---------------------------------------------------------------- local checks

bold "Local checks — a failed live run should not be your laptop's fault"

if [ -n "$(git status --porcelain)" ]; then
  warn "working tree is dirty. A live run should test a commit, not a desk."
else
  ok "working tree clean at $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"
fi

git fetch --quiet origin 2>/dev/null || true
BEHIND=$(git rev-list --count HEAD..@{u} 2>/dev/null || echo 0)
AHEAD=$(git rev-list --count @{u}..HEAD 2>/dev/null || echo 0)
if [ "$AHEAD" != "0" ]; then
  bad "$AHEAD commit(s) not pushed. The Environment builds from the remote, so it cannot see them."
elif [ "$BEHIND" != "0" ]; then
  warn "$BEHIND commit(s) behind origin. Pull before you verify."
else
  ok "in sync with origin"
fi

if [ "$REPORT_ONLY" = "0" ]; then
  if (cd backend && uv run pytest -q >/tmp/sage-live-run-tests.log 2>&1); then
    ok "$(tail -2 /tmp/sage-live-run-tests.log | grep -Eo '[0-9]+ passed.*' | head -1)"
  else
    bad "test suite is red. See /tmp/sage-live-run-tests.log"
  fi
else
  warn "tests not run (--report)"
fi

# Is the image you are about to verify against older than the code you are verifying?
# The Environment git-clones this repo at build time and bakes it to /opt/sage, so nothing
# under backend/ or template/ reaches Domino until SAGE_CACHE_BUST moves and the Environment
# is rebuilt. A stale image verifies last week's code and reports it as this week's.
BUST=$(grep -oE 'SAGE_CACHE_BUST=[^ ]+' environment/Dockerfile | head -1 | cut -d= -f2)
BUST_COMMIT=$(git log -1 --format=%H -G'^ARG SAGE_CACHE_BUST=' -- environment/Dockerfile)
BAKED=$(git log --oneline "$BUST_COMMIT..HEAD" -- backend template opencode.json ':(exclude)backend/tests' || true)
if [ -n "$BAKED" ]; then
  bad "the image bakes code newer than SAGE_CACHE_BUST=$BUST. These commits are NOT on Domino:"
  echo "$BAKED" | sed 's/^/          /'
  echo "          Fix: bump ARG SAGE_CACHE_BUST in environment/Dockerfile, push, rebuild the Environment."
else
  ok "SAGE_CACHE_BUST=$BUST covers every baked commit"
fi
echo
echo "  On Domino, before anything else, confirm the image is the one you just described:"
echo "      grep SAGE_CACHE_BUST /opt/sage/environment/Dockerfile   # expect $BUST"
echo

# ---------------------------------------------------------------- the open boxes

# What you must have on hand for a box. This is the only knowledge the script adds; the
# boxes themselves come from GitHub, so they cannot drift out of step with the issues.
needs_for() {
  local issue="$1" text="$2"
  case "$issue|$text" in
    *"not the publisher"*|*"second Domino user"*) echo "A SECOND DOMINO USER" ;;
    *restart*)                                    echo "A WORKSPACE" ;;
    *samples.json*)                               echo "SNOWFLAKE + A PUBLISH" ;;
    *Snowflake*|*"sample\` statement"*)           echo "A SNOWFLAKE DATA SOURCE" ;;
    9\|*)                                         echo "A DEPLOYED MODEL API + A PUBLISH" ;;
    31\|*)                                        echo "A BUILDER WITH TWO BINDINGS" ;;
    32\|*)                                        echo "A DATA-SOURCE APP IN PREVIEW" ;;
    *)                                            echo "UNSORTED" ;;
  esac
}

ISSUES=$(gh issue list --state open --json number -q '.[].number' | sort -n)
BOXES=()
for n in $ISSUES; do
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    BOXES+=("$n|$(needs_for "$n" "$line")|$line")
  done < <(gh issue view "$n" --json body -q .body \
            | awk '/^[[:space:]]*- \[ \]/{p=1; printf "\n%s", $0; next} p&&/^[[:space:]]+[^-[:space:]]/{printf " %s", $0; next} {p=0} END{print ""}' \
            | sed -E 's/^[[:space:]]*- \[ \][[:space:]]*//; s/[[:space:]]+/ /g' )
done

bold "Waiting on a human — ${#BOXES[@]} unticked acceptance boxes across $(echo "$ISSUES" | wc -w | tr -d ' ') open issues"
echo
# Sorted by what you must have on hand, so the list reads as sessions rather than as issues.
IFS=$'\n' SORTED=($(printf '%s\n' "${BOXES[@]}" | sort -t'|' -k2,2 -k1,1n))
unset IFS
BOXES=("${SORTED[@]}")
CURRENT=""
for entry in "${BOXES[@]}"; do
  issue="${entry%%|*}"; rest="${entry#*|}"; need="${rest%%|*}"; text="${rest#*|}"
  [ "$need" = "$CURRENT" ] || { echo; printf "\n  \033[1m── needs %s\033[0m\n" "$need"; CURRENT="$need"; }
  printf '     #%-3s %s\n' "$issue" "$text"
done
echo
echo "  Grouped that way, the whole backlog is 4 sessions: one Snowflake builder, one publish,"
echo "  one second-user open, one model-API build. Nothing here is blocked on code."
echo

[ "$REPORT_ONLY" = "1" ] && exit 0

# ---------------------------------------------------------------- the walkthrough

DOC="docs/live-runs/$(date +%F)-$LABEL.md"
bold "Walking ${#BOXES[@]} boxes. y = passed, n = failed, s = skipped (recorded as unrun), q = stop."
echo

TTY=/dev/stdin; [ -t 0 ] && TTY=/dev/tty
RESULTS=()
for entry in "${BOXES[@]}"; do
  issue="${entry%%|*}"; rest="${entry#*|}"; need="${rest%%|*}"; text="${rest#*|}"
  echo
  printf '\033[1m#%s\033[0m  %s\n' "$issue" "$text"
  printf '      needs: %s\n' "$need"
  printf '      https://github.com/ddl-subir-m/domino-sage/issues/%s\n' "$issue"
  verdict=""
  while [ -z "$verdict" ]; do
    read -r -p "      [y/n/s/q] " a <"$TTY" || a=q
    case "$a" in
      y) verdict="passed" ;; n) verdict="FAILED" ;; s) verdict="not run" ;;
      q) verdict="stopped" ;;
    esac
  done
  [ "$verdict" = "stopped" ] && break
  read -r -p "      what was seen (one line, enter to leave blank): " note <"$TTY" || note=""
  RESULTS+=("| #$issue | ${text} | $verdict | ${note:-—} |")
done

{
  echo "# Live run — $LABEL"
  echo
  echo "Ran $(date +%F) against $(git rev-parse --short HEAD), image SAGE_CACHE_BUST=$BUST."
  [ -n "$BAKED" ] && echo && echo "**The image was stale when this ran.** Commits baked but not deployed:" \
    && echo && echo '```' && echo "$BAKED" && echo '```'
  echo
  echo "| Issue | Box | Verdict | What was seen |"
  echo "|---|---|---|---|"
  printf '%s\n' "${RESULTS[@]}"
  echo
  echo "Boxes not listed here were never reached."
} > "$DOC"

echo
bold "Written: $DOC"
echo "Tick the boxes that passed on the issues themselves — a doc nobody greps is not a tick."
