#!/usr/bin/env bash
# Repo-provisioning discovery — run inside a GIT-BASED Domino project workspace.
#
# Answers the ONE question Phase 4's repo-provisioning adapters depend on:
#   For THIS git provider, what is the exact create-repo (and delete-repo cleanup)
#   API shape, and does the Domino-injected HTTPS token authorize it?
#
# It does this end-to-end against the LIVE provider API:
#   (1) detect provider + host from the origin remote and pick the matching adapter
#   (2) extract the token via `git credential fill` (LENGTH-ONLY echo, never the value)
#       + report GitHub OAuth scopes (safe to show) from the x-oauth-scopes response header
#   (3) CREATE a throwaway PRIVATE repo  sage-probe-<epoch>  and capture status + key fields
#   (4) immediately DELETE it (self-cleaning; an EXIT trap deletes it even on partial success)
#
# Prereqs already CONFIRMED by git_discovery.sh (do NOT re-verify here): the injected
# credential is extractable via `git credential fill`, carries repo+delete_repo scope, and
# `git push` is pre-authorized.
#
# SAFETY: DRY_RUN=1 is the DEFAULT — it PRINTS the exact curl it would run (with the
# Authorization header REDACTED, --data shown) and creates NOTHING. Re-run with DRY_RUN=0 to
# actually create + delete. The token is never echoed and never written to disk. Output is
# paste-safe (secrets are redacted / length-only).
#
# Usage:
#   # 1. preview only — inspect the exact requests, nothing is written:
#   bash /mnt/code/spikes/domino-probes/repo_provision_probe.sh
#   # 2. real run — create + immediately delete a throwaway private repo:
#   DRY_RUN=0 bash /mnt/code/spikes/domino-probes/repo_provision_probe.sh
#   # optional: force an adapter when host auto-detection can't tell (GHE etc.):
#   PROVIDER=github-enterprise DRY_RUN=0 bash .../repo_provision_probe.sh
#
# Provider coverage:
#   github            — TESTED-FIRST, the only provider we can dogfood right now (fully wired)
#   github-enterprise — same API shape, base https://<host>/api/v3     [UNVERIFIED]
#   gitlab / gitlab-ee— POST /api/v4/projects                          [UNVERIFIED]
#   bitbucket-cloud   — POST /2.0/repositories/{workspace}/{slug}      [UNVERIFIED]
#   bitbucket-dc      — POST /rest/api/1.0/projects/{key}/repos        [UNVERIFIED]
set -uo pipefail

DRY_RUN="${DRY_RUN:-1}"
PROVIDER="${PROVIDER:-auto}"           # auto | github | github-enterprise | gitlab | gitlab-ee | bitbucket-cloud | bitbucket-dc
EPOCH="$(date +%s)"
REPO_NAME="sage-probe-${EPOCH}"        # unmistakably disposable throwaway name
REPO_DESC="THROWAWAY Sage Phase-4 provisioning probe — safe to delete"

redact() {
  sed -E \
    -e 's#(https?://)[^@/]+@#\1<REDACTED>@#g' \
    -e 's#(ghp_|gho_|glpat-|github_pat_|bbdc-)[A-Za-z0-9_-]+#<REDACTED-TOKEN>#g' \
    -e 's#(password=)[^ ]+#\1<REDACTED>#Ig'
}

hr() { echo; echo "===== $* ====="; }

# ---- report accumulators (filled as we go; summarized at the end) ------------
PROVIDER_DETECTED=""
CREATE_STATUS="(not attempted)"
DELETE_STATUS="(not attempted)"
SUM_FULL_NAME=""
SUM_CLONE_URL=""
SUM_PRIVATE=""

# ---- per-adapter request state (set by select_adapter) -----------------------
API_BASE=""            # provider API root
ACCEPT="application/json"
EXTRA_HDR=""           # a NON-SECRET extra header (e.g. GitHub API version); shown verbatim
AUTH_HDR=""            # real auth header (name: value) — NEVER printed
AUTH_HDR_DISPLAY=""    # redacted form, safe to print

# ---- self-cleaning safety net ------------------------------------------------
CREATED_DELETE_URL=""  # set the instant a repo exists; cleared once deleted
CREATED_LABEL=""
cleanup() {
  # Fires on any exit. If a repo was created but not yet deleted (e.g. the script
  # died between create and delete), make a best-effort delete so we never leak repos.
  if [ -n "$CREATED_DELETE_URL" ]; then
    echo
    echo "[trap] leftover repo detected ($CREATED_LABEL) — attempting cleanup delete"
    http_call DELETE "$CREATED_DELETE_URL"
    echo "[trap] cleanup delete status: $HTTP_CODE"
    CREATED_DELETE_URL=""
  fi
}
trap cleanup EXIT

# ---- helpers -----------------------------------------------------------------
# http_call METHOD URL [DATA]
#   Executes a request. Sets globals HTTP_CODE and HTTP_BODY. HTTP_BODY is treated as
#   potentially sensitive and is NEVER printed raw — only extracted fields are shown.
HTTP_CODE=""
HTTP_BODY=""
http_call() {
  local method="$1" url="$2" data="${3:-}" raw
  if [ -n "$data" ]; then
    raw="$(curl -sS -w $'\n%{http_code}' -X "$method" \
      -H "$AUTH_HDR" -H "Accept: $ACCEPT" ${EXTRA_HDR:+-H "$EXTRA_HDR"} \
      --data "$data" "$url" 2>&1)"
  else
    raw="$(curl -sS -w $'\n%{http_code}' -X "$method" \
      -H "$AUTH_HDR" -H "Accept: $ACCEPT" ${EXTRA_HDR:+-H "$EXTRA_HDR"} \
      "$url" 2>&1)"
  fi
  HTTP_CODE="${raw##*$'\n'}"
  HTTP_BODY="${raw%$'\n'*}"
}

# preview_call METHOD URL [DATA] — prints the exact curl with the token REDACTED.
preview_call() {
  local method="$1" url="$2" data="${3:-}"
  echo "    curl -sS -X $method \\"
  echo "      -H '$AUTH_HDR_DISPLAY' \\"
  echo "      -H 'Accept: $ACCEPT' \\"
  [ -n "$EXTRA_HDR" ] && echo "      -H '$EXTRA_HDR' \\"
  [ -n "$data" ] && echo "      --data '$data' \\"
  echo "      '$url'"
}

# json_field BODY JQ_PATH GREP_KEY — pull ONE field out without dumping the body.
json_field() {
  local body="$1" jqpath="$2" key="$3"
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$body" | jq -r "$jqpath // empty" 2>/dev/null
  else
    # careful, single-field grep: matches "key": "value" or "key": <number/bool>
    printf '%s' "$body" \
      | grep -oE "\"$key\"[[:space:]]*:[[:space:]]*(\"[^\"]*\"|true|false|[0-9]+)" \
      | head -1 \
      | sed -E "s/.*:[[:space:]]*\"?([^\"]*)\"?$/\1/"
  fi
}

# ==============================================================================
hr "0. mode"
echo "DRY_RUN=$DRY_RUN   (1 = preview only / no writes;  0 = actually create + delete)"
echo "PROVIDER=$PROVIDER (auto = detect from remote)"
echo "throwaway repo name: $REPO_NAME"
if command -v jq >/dev/null 2>&1; then echo "jq: present (clean field extraction)"; else echo "jq: absent (falling back to grep)"; fi

# ==============================================================================
hr "1. locate the project git repo"
REPO=""
for d in /mnt/code /repos/* /mnt/*/ ; do
  if [ -d "${d%/}/.git" ]; then REPO="${d%/}"; break; fi
done
echo "repo dir: ${REPO:-<none found>}"
if [ -z "$REPO" ]; then
  echo "No git repo found — is this a GIT-BASED project? (DFS projects won't have one.)"
  exit 0
fi

REMOTE_URL="$(git -C "$REPO" remote get-url origin 2>/dev/null)"
echo "origin (redacted): $(printf '%s' "$REMOTE_URL" | redact)"
proto="$(printf '%s' "$REMOTE_URL" | sed -E 's#^([a-z]+)://.*#\1#')"
host="$(printf '%s' "$REMOTE_URL"  | sed -E 's#^[a-z]+://([^/@]*@)?([^/]+)/.*#\2#')"
# path after host: owner/repo(.git) — used for workspace/project scoping (Bitbucket)
path="$(printf '%s' "$REMOTE_URL" | sed -E 's#^[a-z]+://([^/@]*@)?[^/]+/##; s#\.git$##')"
owner="$(printf '%s' "$path" | sed -E 's#/.*$##')"
echo "protocol: ${proto:-<none>}   host: ${host:-<none>}   owner/workspace: ${owner:-<none>}"
if [ -z "$host" ]; then echo "could not parse host from remote — aborting"; exit 0; fi

# ==============================================================================
hr "2. extract token via git credential fill (LENGTH ONLY — never the value)"
cred_out="$(printf 'protocol=%s\nhost=%s\n\n' "${proto:-https}" "$host" | git credential fill 2>/dev/null)"
CRED_USER="$(printf '%s' "$cred_out" | sed -n 's/^username=//p')"
CRED_PASS="$(printf '%s' "$cred_out" | sed -n 's/^password=//p')"
echo "username len=${#CRED_USER}   password len=${#CRED_PASS}"
# Domino may put the PAT in EITHER field; prefer password, fall back to username.
TOKEN="${CRED_PASS:-$CRED_USER}"
if [ -z "$TOKEN" ]; then
  echo "no token returned by credential helper — cannot call the provider API. Aborting."
  exit 0
fi
echo "using token from: $([ -n "$CRED_PASS" ] && echo password || echo username)  (len=${#TOKEN})"

# ==============================================================================
hr "3. detect provider + select adapter"
detected="$PROVIDER"
if [ "$detected" = "auto" ]; then
  case "$host" in
    github.com)     detected="github" ;;
    gitlab.com)     detected="gitlab" ;;
    bitbucket.org)  detected="bitbucket-cloud" ;;
    *)
      # enterprise hosts rarely announce themselves; best-effort by substring.
      if   printf '%s' "$host" | grep -qi 'gitlab';    then detected="gitlab-ee"
      elif printf '%s' "$host" | grep -qi 'bitbucket'; then detected="bitbucket-dc"
      else detected="unknown"
      fi
      ;;
  esac
fi
PROVIDER_DETECTED="$detected"
echo "provider: $detected  (host=$host)"

# select_adapter fills API_BASE / ACCEPT / EXTRA_HDR / AUTH_HDR(+DISPLAY).
# Auth value lives ONLY in AUTH_HDR; AUTH_HDR_DISPLAY is the redacted form we print.
select_adapter() {
  local redacted="<REDACTED-TOKEN len=${#TOKEN}>"
  case "$detected" in
    github)
      API_BASE="https://api.github.com"
      ACCEPT="application/vnd.github+json"
      EXTRA_HDR="X-GitHub-Api-Version: 2022-11-28"
      AUTH_HDR="Authorization: Bearer ${TOKEN}"
      AUTH_HDR_DISPLAY="Authorization: Bearer ${redacted}"
      ;;
    github-enterprise)
      API_BASE="https://${host}/api/v3"
      ACCEPT="application/vnd.github+json"
      EXTRA_HDR="X-GitHub-Api-Version: 2022-11-28"
      AUTH_HDR="Authorization: Bearer ${TOKEN}"
      AUTH_HDR_DISPLAY="Authorization: Bearer ${redacted}"
      ;;
    gitlab|gitlab-ee)
      API_BASE="https://${host}/api/v4"
      ACCEPT="application/json"
      EXTRA_HDR=""
      # GitLab authenticates via the PRIVATE-TOKEN header — which IS the secret.
      AUTH_HDR="PRIVATE-TOKEN: ${TOKEN}"
      AUTH_HDR_DISPLAY="PRIVATE-TOKEN: ${redacted}"
      ;;
    bitbucket-cloud)
      API_BASE="https://api.bitbucket.org"
      ACCEPT="application/json"
      EXTRA_HDR=""
      # Basic auth: base64(user:token). Encoded blob still contains the secret -> redact.
      local basic; basic="$(printf '%s:%s' "${CRED_USER:-x-token-auth}" "$TOKEN" | base64 | tr -d '\n')"
      AUTH_HDR="Authorization: Basic ${basic}"
      AUTH_HDR_DISPLAY="Authorization: Basic <REDACTED-BASIC len=${#basic}>"
      ;;
    bitbucket-dc)
      API_BASE="https://${host}"
      ACCEPT="application/json"
      EXTRA_HDR=""
      AUTH_HDR="Authorization: Bearer ${TOKEN}"
      AUTH_HDR_DISPLAY="Authorization: Bearer ${redacted}"
      ;;
  esac
}

if [ "$detected" = "unknown" ]; then
  echo
  echo "UNRECOGNIZED HOST — refusing to write. Here are the endpoints each adapter WOULD use"
  echo "for host '$host'. Re-run with PROVIDER=<one of them> once you know which it is:"
  echo "  PROVIDER=github-enterprise -> POST https://${host}/api/v3/user/repos ; DELETE .../repos/{owner}/{repo}"
  echo "  PROVIDER=gitlab-ee         -> POST https://${host}/api/v4/projects   ; DELETE .../projects/{id}"
  echo "  PROVIDER=bitbucket-dc      -> POST https://${host}/rest/api/1.0/projects/{key}/repos ; DELETE same/{slug}"
  echo "  PROVIDER=bitbucket-cloud   -> POST https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}"
  echo
  echo "(stopping before any create/delete — nothing was written.)"
  exit 0
fi

select_adapter
echo "adapter API base: $API_BASE"

# ==============================================================================
# Per-provider probe functions. Each: previews (always), then on DRY_RUN=0
# creates -> extracts fields -> deletes -> reports. GitHub is the tested-first,
# fully-wired path; the rest are UNVERIFIED best-effort transcriptions of the
# documented shapes, structured identically so confirming them later is a diff.
# ==============================================================================

probe_github() {  # covers github + github-enterprise (identical API shape)
  local create_url="$API_BASE/user/repos"
  local body="{\"name\":\"$REPO_NAME\",\"private\":true,\"auto_init\":false,\"description\":\"$REPO_DESC\"}"

  hr "4. scopes (GitHub x-oauth-scopes header — safe to show)"
  if [ "$DRY_RUN" = "1" ]; then
    echo "  [DRY_RUN] would GET $API_BASE/user and grep response headers for oauth scopes"
  else
    curl -sS -o /dev/null -D - -H "$AUTH_HDR" -H "Accept: $ACCEPT" "$API_BASE/user" 2>/dev/null \
      | grep -iE '^x-(accepted-)?oauth-scopes:' || echo "  (no x-oauth-scopes header returned)"
  fi

  hr "5. CREATE throwaway private repo  ($detected)"
  echo "  POST $create_url"
  preview_call POST "$create_url" "$body"
  if [ "$DRY_RUN" = "1" ]; then
    echo "  [DRY_RUN] not executed — re-run with DRY_RUN=0 to actually create."
    return 0
  fi

  http_call POST "$create_url" "$body"
  CREATE_STATUS="$HTTP_CODE"
  echo "  -> HTTP $HTTP_CODE"
  SUM_FULL_NAME="$(json_field "$HTTP_BODY" '.full_name' 'full_name')"
  SUM_CLONE_URL="$(json_field "$HTTP_BODY" '.clone_url'  'clone_url')"
  SUM_PRIVATE="$(json_field "$HTTP_BODY" '.private'   'private')"
  echo "  full_name : ${SUM_FULL_NAME:-<none>}"
  echo "  clone_url : ${SUM_CLONE_URL:-<none>}"
  echo "  private   : ${SUM_PRIVATE:-<none>}"
  if [ -z "$SUM_FULL_NAME" ]; then
    echo "  create did not return a full_name (HTTP $HTTP_CODE) — nothing to delete."
    return 0
  fi

  # arm the cleanup net the instant the repo exists
  CREATED_DELETE_URL="$API_BASE/repos/$SUM_FULL_NAME"
  CREATED_LABEL="$SUM_FULL_NAME"

  hr "6. DELETE throwaway repo (cleanup)"
  echo "  DELETE $CREATED_DELETE_URL"
  preview_call DELETE "$CREATED_DELETE_URL"
  http_call DELETE "$CREATED_DELETE_URL"
  DELETE_STATUS="$HTTP_CODE"
  echo "  -> HTTP $HTTP_CODE  (204 = deleted)"
  CREATED_DELETE_URL=""   # disarm trap; we handled it
}

probe_gitlab() {  # UNVERIFIED — POST /api/v4/projects
  local create_url="$API_BASE/projects"
  # project path defaults to the name; namespace = the token's default (personal).
  local body="{\"name\":\"$REPO_NAME\",\"path\":\"$REPO_NAME\",\"visibility\":\"private\",\"description\":\"$REPO_DESC\"}"

  hr "5. CREATE throwaway private project  ($detected)  [UNVERIFIED]"
  echo "  POST $create_url"
  preview_call POST "$create_url" "$body"
  if [ "$DRY_RUN" = "1" ]; then echo "  [DRY_RUN] not executed."; return 0; fi

  http_call POST "$create_url" "$body"
  CREATE_STATUS="$HTTP_CODE"
  echo "  -> HTTP $HTTP_CODE"
  local pid
  pid="$(json_field "$HTTP_BODY" '.id' 'id')"
  SUM_FULL_NAME="$(json_field "$HTTP_BODY" '.path_with_namespace' 'path_with_namespace')"
  SUM_CLONE_URL="$(json_field "$HTTP_BODY" '.http_url_to_repo'    'http_url_to_repo')"
  SUM_PRIVATE="$(json_field "$HTTP_BODY" '.visibility' 'visibility')"
  echo "  id                  : ${pid:-<none>}"
  echo "  path_with_namespace : ${SUM_FULL_NAME:-<none>}"
  echo "  http_url_to_repo    : ${SUM_CLONE_URL:-<none>}"
  echo "  visibility          : ${SUM_PRIVATE:-<none>}"
  if [ -z "$pid" ]; then echo "  no project id (HTTP $HTTP_CODE) — nothing to delete."; return 0; fi

  CREATED_DELETE_URL="$API_BASE/projects/$pid"
  CREATED_LABEL="gitlab project id=$pid"

  hr "6. DELETE throwaway project (cleanup)  [UNVERIFIED]"
  echo "  DELETE $CREATED_DELETE_URL"
  preview_call DELETE "$CREATED_DELETE_URL"
  http_call DELETE "$CREATED_DELETE_URL"
  DELETE_STATUS="$HTTP_CODE"
  echo "  -> HTTP $HTTP_CODE  (202/204 = accepted/deleted)"
  CREATED_DELETE_URL=""
}

probe_bitbucket_cloud() {  # UNVERIFIED — POST /2.0/repositories/{workspace}/{slug}
  local ws="${owner:-<workspace>}"
  local create_url="$API_BASE/2.0/repositories/$ws/$REPO_NAME"
  local body="{\"scm\":\"git\",\"is_private\":true,\"description\":\"$REPO_DESC\"}"

  hr "5. CREATE throwaway private repo  ($detected)  [UNVERIFIED]"
  echo "  POST $create_url   (workspace parsed from remote: $ws)"
  preview_call POST "$create_url" "$body"
  if [ "$DRY_RUN" = "1" ]; then echo "  [DRY_RUN] not executed."; return 0; fi

  http_call POST "$create_url" "$body"
  CREATE_STATUS="$HTTP_CODE"
  echo "  -> HTTP $HTTP_CODE"
  SUM_FULL_NAME="$(json_field "$HTTP_BODY" '.full_name' 'full_name')"
  SUM_CLONE_URL="$(json_field "$HTTP_BODY" '.links.clone[0].href' 'href')"
  SUM_PRIVATE="$(json_field "$HTTP_BODY" '.is_private' 'is_private')"
  echo "  full_name : ${SUM_FULL_NAME:-<none>}"
  echo "  clone href: ${SUM_CLONE_URL:-<none>}"
  echo "  is_private: ${SUM_PRIVATE:-<none>}"
  # delete uses the same {workspace}/{slug} path regardless of create success shape
  CREATED_DELETE_URL="$create_url"
  CREATED_LABEL="$ws/$REPO_NAME"

  hr "6. DELETE throwaway repo (cleanup)  [UNVERIFIED]"
  echo "  DELETE $CREATED_DELETE_URL"
  preview_call DELETE "$CREATED_DELETE_URL"
  http_call DELETE "$CREATED_DELETE_URL"
  DELETE_STATUS="$HTTP_CODE"
  echo "  -> HTTP $HTTP_CODE  (204 = deleted)"
  CREATED_DELETE_URL=""
}

probe_bitbucket_dc() {  # UNVERIFIED — Data Center: POST /rest/api/1.0/projects/{key}/repos
  local key="${owner:-<PROJECTKEY>}"   # DC project KEY (parsed owner is a best guess)
  local create_url="$API_BASE/rest/api/1.0/projects/$key/repos"
  local body="{\"name\":\"$REPO_NAME\",\"scmId\":\"git\",\"public\":false}"

  hr "5. CREATE throwaway private repo  ($detected)  [UNVERIFIED — DISTINCT from Bitbucket Cloud]"
  echo "  POST $create_url   (project key guessed from remote: $key — verify!)"
  preview_call POST "$create_url" "$body"
  if [ "$DRY_RUN" = "1" ]; then echo "  [DRY_RUN] not executed."; return 0; fi

  http_call POST "$create_url" "$body"
  CREATE_STATUS="$HTTP_CODE"
  echo "  -> HTTP $HTTP_CODE"
  local slug
  slug="$(json_field "$HTTP_BODY" '.slug' 'slug')"
  SUM_FULL_NAME="$key/${slug:-$REPO_NAME}"
  SUM_CLONE_URL="$(json_field "$HTTP_BODY" '.links.clone[0].href' 'href')"
  SUM_PRIVATE="private (public=false requested)"
  echo "  slug      : ${slug:-<none>}"
  echo "  clone href: ${SUM_CLONE_URL:-<none>}"
  if [ -z "$slug" ]; then echo "  no slug (HTTP $HTTP_CODE) — nothing to delete."; return 0; fi

  CREATED_DELETE_URL="$create_url/$slug"
  CREATED_LABEL="$key/$slug"

  hr "6. DELETE throwaway repo (cleanup)  [UNVERIFIED]"
  echo "  DELETE $CREATED_DELETE_URL"
  preview_call DELETE "$CREATED_DELETE_URL"
  http_call DELETE "$CREATED_DELETE_URL"
  DELETE_STATUS="$HTTP_CODE"
  echo "  -> HTTP $HTTP_CODE  (202/204 = accepted/deleted)"
  CREATED_DELETE_URL=""
}

case "$detected" in
  github|github-enterprise) probe_github ;;
  gitlab|gitlab-ee)         probe_gitlab ;;
  bitbucket-cloud)          probe_bitbucket_cloud ;;
  bitbucket-dc)             probe_bitbucket_dc ;;
esac

# ==============================================================================
hr "7. SUMMARY — what Phase 4's adapter needs"
echo "provider detected : $PROVIDER_DETECTED"
echo "api base          : $API_BASE"
echo "create status     : $CREATE_STATUS"
echo "delete status     : $DELETE_STATUS"
echo "repo full name    : ${SUM_FULL_NAME:-<n/a in DRY_RUN>}"
echo "https clone url    : ${SUM_CLONE_URL:-<n/a in DRY_RUN>}"
echo "private flag      : ${SUM_PRIVATE:-<n/a in DRY_RUN>}"
if [ "$DRY_RUN" = "1" ]; then
  echo
  echo "(DRY_RUN preview only — nothing was created or deleted. Re-run with DRY_RUN=0 to confirm the live shape.)"
fi
echo
echo "(paste sections 3–7 back — that's what confirms the Phase 4 adapter contract for this provider)"
