"""Git provider detection + credential extraction (Phase 4.1).

Two things the hub needs from the workspace's git setup:
  - which provider/host the user's Domino git credential targets (picks the adapter), and
  - the HTTPS token behind that credential (the one thing `git push` can't do for us: call the
    provider's REST API to *create* a repo).

The token is obtained via `git credential fill` — the mechanism confirmed by git_discovery.sh. It is
returned to the caller and used in-memory only; NOTHING here logs or persists it. Seeding/pushing a
new repo does NOT go through here — that rides Domino's ambient credential helper (`git push` is
pre-authorized for the host), so token handling stays confined to the provider API adapter.
"""
from __future__ import annotations

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


def extract_token(host: str, protocol: str = "https") -> str | None:
    """Pull the HTTPS token for `host` from Domino's git credential helper via `git credential fill`.

    In-memory only — the returned value is a live credential; callers MUST NOT log or persist it.
    Returns None when the helper yields nothing (e.g. an SSH-key credential, which can't be
    extracted — those apps fall back to the BYO-repo path).
    """
    query = f"protocol={protocol}\nhost={host}\n\n"
    r = subprocess.run(
        ["git", "credential", "fill"], input=query, capture_output=True, text=True, check=False
    )
    if r.returncode != 0:
        return None
    fields = dict(
        line.split("=", 1) for line in r.stdout.splitlines() if "=" in line
    )
    # Domino may place the PAT in either field; the password carries it in practice.
    token = fields.get("password") or fields.get("username")
    return token or None
