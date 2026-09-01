"""Git provider detection + credential extraction (Phase 4.1).

Two things the provision service needs from the container's git setup:
  - which provider/host the user's Domino git credential targets (picks the adapter), and
  - the HTTPS token behind that credential (the one thing `git push` can't do for us: call the
    provider's REST API to *create* a repo).

The token is obtained via `git credential fill` — the mechanism confirmed by git_discovery.sh. It is
returned to the caller and used in-memory only; NOTHING here logs or persists it. The same token
authenticates both the create-repo API call AND the seed push (the seeder pushes from a throwaway
temp repo that inherits no credential helper, so ambient auth isn't available there — see seed.py).

WHERE it is asked for matters, which is why extraction sweeps directories instead of asking once.
Domino wires a credential PER REPOSITORY (a project can import repos owned by different credentials),
so the helper config that authorizes a push can live in the checkout's own `.git/config` rather than
anywhere global. The orchestrator's process cwd is Sage's baked code at /opt/sage/backend, which is
not that checkout — asking there answers for the wrong repo, or for no repo at all. So we ask from
inside the mounted checkout first, and fall back to a credential Domino embedded straight into the
origin URL, which `git credential fill` never reports because it is not a helper at all.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

# host -> provider key understood by the adapter registry (Phase 4.1 ships github; the rest are the
# provider keys the probe covers, wired as adapters land).
_KNOWN_HOSTS = {
    "github.com": "github",
    "gitlab.com": "gitlab",
    "bitbucket.org": "bitbucket-cloud",
}


@dataclass(frozen=True)
class GitRemote:
    provider: str  # "github" | "gitlab" | … | "unknown"
    host: str
    owner: str  # first path segment (owner / workspace / project-key)
    protocol: str  # "https" | "ssh" | …


def detect_provider(host: str) -> str:
    """Map a git host to a provider key. Enterprise hosts don't announce themselves, so fall back to
    a substring guess; "unknown" means the caller must ask the user which provider it is."""
    if host in _KNOWN_HOSTS:
        return _KNOWN_HOSTS[host]
    low = host.lower()
    if "github" in low:
        return "github-enterprise"
    if "gitlab" in low:
        return "gitlab-ee"
    if "bitbucket" in low:
        return "bitbucket-dc"
    return "unknown"


_SCP_LIKE = re.compile(r"^(?P<user>[^@]+@)?(?P<host>[^:/]+):(?P<path>.+)$")
_URL_LIKE = re.compile(r"^(?P<proto>[a-z]+)://(?P<user>[^@/]+@)?(?P<host>[^/]+)/(?P<path>.*)$")


def parse_remote(url: str) -> GitRemote | None:
    """Parse an origin remote (https:// or scp-like git@host:owner/repo) into host/owner/provider.

    Returns None when the URL can't be parsed (nothing to provision against)."""
    url = url.strip()
    proto = "https"
    m = _URL_LIKE.match(url)
    if m:
        proto = m.group("proto")
        host = m.group("host")
        path = m.group("path")
    else:
        m = _SCP_LIKE.match(url)
        if not m:
            return None
        proto = "ssh"
        host = m.group("host")
        path = m.group("path")
    owner = path.split("/", 1)[0] if path else ""
    return GitRemote(provider=detect_provider(host), host=host, owner=owner, protocol=proto)


def _origin_url(cwd: str) -> str | None:
    r = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=cwd, capture_output=True, text=True, check=False
    )
    return r.stdout.strip() or None if r.returncode == 0 else None


def remote_for(cwd: str) -> GitRemote | None:
    """The parsed origin remote of the repo at `cwd`, or None if there's no origin."""
    url = _origin_url(cwd)
    return parse_remote(url) if url else None


# Never prompt: with several directories to sweep, one `git credential fill` that stops to ask on
# /dev/tty would hang the Create Project request instead of failing it. Closing stdin is not enough —
# git asks the terminal, not stdin.
_NO_PROMPT = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": ""}
_FILL_TIMEOUT = 15


def _checkout_dirs(cwd: str | None = None) -> list[str | None]:
    """Directories to ask git from, nearest-to-the-repo first.

    The mounted checkout comes first because that is where Domino authorized the push, and the
    process cwd comes last because /opt/sage/backend is Sage's own baked code, not the user's repo.
    """
    seen: set[str] = set()
    out: list[str | None] = []
    for c in (cwd, os.environ.get("SAGE_WORKSPACE_DIR"), "/mnt/code"):
        if not c or c in seen or not os.path.isdir(c):
            continue
        seen.add(c)
        out.append(c)
    out.append(None)  # the process cwd last — the pre-sweep behaviour, kept as a fallback
    return out


def _fill(host: str, protocol: str, cwd: str | None, path: str | None) -> str | None:
    """One `git credential fill`, run from `cwd`. Returns the token or None."""
    query = f"protocol={protocol}\nhost={host}\n"
    if path:
        # A repo-scoped credential config (`credential.https://host/owner/repo.helper`) only matches
        # when the query carries the path, so ask with it as well as without.
        query += f"path={path}\n"
    try:
        r = subprocess.run(
            ["git", "credential", "fill"],
            input=query + "\n",
            cwd=cwd,
            env={**os.environ, **_NO_PROMPT},
            capture_output=True,
            text=True,
            check=False,
            timeout=_FILL_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    fields = dict(
        line.split("=", 1) for line in r.stdout.splitlines() if "=" in line
    )
    # Domino may place the PAT in either field; the password carries it in practice.
    token = fields.get("password") or fields.get("username")
    return token or None


_URL_CREDS = re.compile(r"^[a-z]+://(?P<user>[^@/]*?)(?::(?P<pw>[^@/]*))?@(?P<host>[^/]+)/")


def _token_in_origin(cwd: str, host: str) -> str | None:
    """The token Domino embedded in the checkout's origin URL, when it did that instead of
    configuring a helper.

    `git credential fill` cannot see this: an URL-embedded credential is not a helper, it is part of
    the remote. A container wired this way pushes fine and still answers "no credential" to every
    question we know how to ask — which is exactly the shape of the failure this covers.
    """
    url = _origin_url(cwd)
    if not url:
        return None
    m = _URL_CREDS.match(url.strip())
    if not m or m.group("host").split(":")[0].lower() != host.lower():
        return None
    from urllib.parse import unquote

    # Domino writes `https://<user>:<PAT>@host/…`; some helpers write the PAT as the user with no
    # password (that is why extract_token below reads either field too).
    secret = m.group("pw") or m.group("user")
    return unquote(secret) if secret else None


def extract_token(host: str, protocol: str = "https", *, cwd: str | None = None) -> str | None:
    """Pull the HTTPS token for `host` from this container's git setup.

    Asks `git credential fill` from each checkout in `_checkout_dirs` (with and without the repo
    path), then falls back to a credential embedded in a checkout's origin URL. `cwd` pins the
    checkout to ask from first.

    In-memory only — the returned value is a live credential; callers MUST NOT log or persist it.
    Returns None when nothing yields a token (e.g. an SSH-key credential, which can't be extracted —
    those apps fall back to the BYO-repo path).
    """
    dirs = _checkout_dirs(cwd)
    for d in dirs:
        remote = remote_for(d) if d else None
        path = None
        if remote is not None and remote.host.lower() == host.lower():
            parsed = _URL_LIKE.match(_origin_url(d) or "") or _SCP_LIKE.match(_origin_url(d) or "")
            path = (parsed.group("path").removesuffix(".git") if parsed else None) or None
        for p in ([path, None] if path else [None]):
            token = _fill(host, protocol, d, p)
            if token:
                return token
    for d in dirs:
        if d and (token := _token_in_origin(d, host)):
            return token
    return None


def credential_probe(host: str, protocol: str = "https") -> dict:
    """Where a credential for `host` was and wasn't found, with no secret in the answer.

    /api/diag serves this. A Builder has no terminal, so without it "no HTTPS git credential" is a
    dead end: this says which checkouts were asked, whether each answered, and how long the answer
    was — enough to tell an SSH-only account from a credential we looked for in the wrong place.
    """
    dirs = _checkout_dirs()
    asked = []
    for d in dirs:
        token = _fill(host, protocol, d, None)
        origin = _token_in_origin(d, host) if d else None
        asked.append({
            "cwd": d or "(process cwd)",
            "is_repo": bool(d and remote_for(d)),
            "fill_len": len(token) if token else 0,
            "origin_url_len": len(origin) if origin else 0,
        })
    return {"host": host, "found": any(a["fill_len"] or a["origin_url_len"] for a in asked),
            "asked": asked}
