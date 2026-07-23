#!/usr/bin/env bash
# Git-credential discovery — run inside a GIT-BASED Domino project workspace.
#
# Answers the two questions that decide the git-based "New app" flow:
#   (A) Does Domino inject creds that authorize `git push` to the project repo?  -> push --dry-run
#   (B) Can we EXTRACT the user's token to call a provider API (create a repo)?  -> git credential fill
# Plus: how creds are wired (helper / files / env) and whether the v4 API exposes git credentials.
#
# READ-ONLY: no commits, no pushes (push is --dry-run only). Secrets are REDACTED, so the whole
# output is safe to paste back.
#
#   bash /mnt/code/spikes/domino-probes/git_discovery.sh
set -uo pipefail

redact() {
  sed -E \
    -e 's#(https?://)[^@/]+@#\1<REDACTED>@#g' \
    -e 's#(ghp_|gho_|glpat-|github_pat_)[A-Za-z0-9_-]+#<REDACTED-TOKEN>#g' \
    -e 's#(password=)[^ ]+#\1<REDACTED>#Ig'
}

hr() { echo; echo "===== $* ====="; }

hr "1. git-related env (values redacted)"
env | grep -iE '^(DOMINO|GIT)' | sort | sed -E 's#=(.+)$#=<value len=\1>#' \
  | awk -F'len=' '{ if (NF>1){ n=length($2)-1; print $1 "len=" n ">"} else print }'

hr "2. git config (credential helper + identity)"
{ git config --list --show-origin 2>/dev/null || git config --list 2>/dev/null; } | redact

hr "3. credential material on disk (names only, no contents)"
for f in ~/.git-credentials /etc/gitconfig ~/.config/git/credentials; do
  [ -e "$f" ] && echo "present: $f" || echo "absent:  $f"
done
echo "~/.ssh:"; ls -1 ~/.ssh 2>/dev/null || echo "  (none)"

hr "4. locate the project git repo"
REPO=""
for d in /mnt/code /repos/* /mnt/*/ ; do
  if [ -d "${d%/}/.git" ]; then REPO="${d%/}"; break; fi
done
echo "repo dir: ${REPO:-<none found>}"
if [ -z "$REPO" ]; then
  echo "No git repo found — is this a GIT-BASED project? (DFS projects won't have one.)"
  exit 0
fi

hr "5. remote + branch (url redacted)"
git -C "$REPO" remote -v | redact
BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)"; echo "branch: $BRANCH"
REMOTE_URL="$(git -C "$REPO" remote get-url origin 2>/dev/null)"
echo "origin (redacted): $(printf '%s' "$REMOTE_URL" | redact)"

hr "6. can we EXTRACT a token? (git credential fill — secret length only)"
# Parse protocol+host from the remote so `git credential fill` asks the right helper.
proto="$(printf '%s' "$REMOTE_URL" | sed -E 's#^([a-z]+)://.*#\1#')"
host="$(printf '%s' "$REMOTE_URL"  | sed -E 's#^[a-z]+://([^/@]*@)?([^/]+)/.*#\2#')"
echo "asking helper for: protocol=${proto:-https} host=${host:-<unknown>}"
if [ -n "${host:-}" ]; then
  printf 'protocol=%s\nhost=%s\n\n' "${proto:-https}" "$host" | git credential fill 2>/dev/null | while IFS= read -r line; do
    case "$line" in
      password=*) echo "password=<REDACTED len=$(( ${#line} - 9 ))>  <-- if len>0, we CAN get a token" ;;
      username=*) echo "$line" ;;
      *) echo "$line" | redact ;;
    esac
  done
else
  echo "could not parse host from remote"
fi

hr "7. does push AUTH work? (dry-run — nothing is pushed)"
git -C "$REPO" push --dry-run 2>&1 | redact | head -20

hr "8. v4 API: any git-credential endpoints? (sidecar token)"
API="${DOMINO_API_HOST:-}"; SIDE="${DOMINO_API_PROXY:-http://localhost:8899}"
TOK="$(curl -s "${SIDE%/}/access-token" 2>/dev/null)"
[ -n "$TOK" ] && AUTH="Authorization: Bearer ${TOK#Bearer }" || AUTH=""
for p in \
  /v4/users/self \
  /v4/accounts/self/gitCredentials \
  /v4/gitCredentials \
  /account/gitCredentials \
  /v4/gitRepos ; do
  code="$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH" "${API%/}$p" 2>/dev/null)"
  echo "GET $p -> $code"
done
echo
echo "(paste sections 5–8 back — that's what decides the flow)"
