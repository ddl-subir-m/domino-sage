"""Provider adapter: create a repo via the provider's REST API (Phase 4.1).

Confirmed live against github.com (repo_provision_probe.sh, DRY_RUN=0):
  POST https://api.github.com/user/repos
    {"name","private":true,"auto_init":false,"description"}  -> 201
    response: {"full_name","clone_url","private", …}
  a name collision returns 422 (caller retries the next `-N` candidate).

  a delete (rollback of a half-provisioned app) is DELETE /repos/{owner}/{name} -> 204.
  an existence check is GET /repos/{owner}/{name} -> 200 / 404 (standard read endpoint, not
  re-probed here). 404 also covers a repo this token cannot see, which is unreachable either
  way — repo_exists answers reachability, not existence in the abstract.

The token comes from credentials.extract_token and is used in-memory only — never logged. It signs
the create/delete API calls and (via a one-shot helper) the seed push; see seed.py.

github-enterprise shares this exact shape at base https://<host>/api/v3 (unverified — same adapter,
different base_url). Other providers (GitLab/Bitbucket) are separate adapters, added as verified.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
# The existence check runs on a door open that has ALREADY failed, and the viewer is waiting on the
# error. The 30s default would be spent twice over on every press of Try again, so this one call
# gets its own short budget: an answer that slow is worth less than showing the failure promptly.
REPO_EXISTS_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class RepoInfo:
    full_name: str  # "owner/repo"
    clone_url: str  # https clone URL — what the Domino project points at
    private: bool


class RepoNameConflict(Exception):
    """The provider rejected the name as already taken (retry the next candidate)."""


class RepoProviderError(Exception):
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"repo provider returned {status}: {body[:300]}")


class RepoProvider(Protocol):
    def create_repo(self, name: str, *, description: str = "", private: bool = True) -> RepoInfo:
        """Create a repo named `name`. Raises RepoNameConflict if the name is taken."""
        ...

    def delete_repo(self, full_name: str) -> None:
        """Delete a repo ("owner/name"). Used to roll back a half-provisioned app."""
        ...

    def repo_exists(self, full_name: str) -> bool | None:
        """Can this token reach the repo "owner/name"? None when the provider could not be asked.

        Three answers, not two: a caller uses this to explain a failure it is already holding, so
        "I could not check" must never read as "it is gone".
        """
        ...


@dataclass
class FakeRepoProvider:
    """In-memory provider for tests/local fake-mode provisioning — no network."""

    host: str = "github.com"
    created: list[RepoInfo] = field(default_factory=list)
    owner: str = "test-owner"
    existence_checks: list[str] = field(default_factory=list)  # full_names repo_exists was asked

    def create_repo(self, name: str, *, description: str = "", private: bool = True) -> RepoInfo:
        full = f"{self.owner}/{name}"
        if any(r.full_name == full for r in self.created):
            raise RepoNameConflict(name)
        info = RepoInfo(full_name=full, clone_url=f"https://{self.host}/{full}.git", private=private)
        self.created.append(info)
        return info

    def delete_repo(self, full_name: str) -> None:
        self.created = [r for r in self.created if r.full_name != full_name]

    def repo_exists(self, full_name: str) -> bool | None:
        # Recorded, because this fake answers False for ANY name it doesn't hold — so a caller
        # that computed the wrong name would look identical to one that computed the right one.
        self.existence_checks.append(full_name)
        return any(r.full_name == full_name for r in self.created)


class GitHubProvider:
    """Real GitHub adapter. `token_provider` returns the HTTPS token (in-memory; never logged)."""

    def __init__(
        self,
        token_provider: Callable[[], str],
        *,
        base_url: str = GITHUB_API_BASE,
        transport: httpx.BaseTransport | None = None,  # test seam (httpx.MockTransport)
        timeout_s: float = 30.0,
    ) -> None:
        self._token_provider = token_provider
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout_s = timeout_s

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token_provider()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }

    def create_repo(self, name: str, *, description: str = "", private: bool = True) -> RepoInfo:
        body: dict[str, Any] = {
            "name": name,
            "private": private,
            "auto_init": False,
            "description": description,
        }
        with httpx.Client(transport=self._transport, timeout=self._timeout_s) as client:
            r = client.post(f"{self._base_url}/user/repos", json=body, headers=self._headers())
        if r.status_code == 422:  # name exists (or other validation) — treat as a collision to retry
            raise RepoNameConflict(name)
        if r.status_code >= 400:
            raise RepoProviderError(r.status_code, r.text)
        data = r.json()
        return RepoInfo(
            full_name=data["full_name"],
            clone_url=data["clone_url"],
            private=bool(data.get("private", private)),
        )

    def delete_repo(self, full_name: str) -> None:
        with httpx.Client(transport=self._transport, timeout=self._timeout_s) as client:
            r = client.delete(f"{self._base_url}/repos/{full_name}", headers=self._headers())
        if r.status_code >= 400 and r.status_code != 404:  # 404 = already gone; treat as success
            raise RepoProviderError(r.status_code, r.text)

    def repo_exists(self, full_name: str) -> bool | None:
        """404 or a redirect -> False, 2xx -> True, anything else -> None (three answers).

        The caller gates this on `ControlPlane.git_host` while the request goes to `_base_url`.
        Those agree only because `_build_provision_service` refuses to build at all for a
        non-github.com host (`detect_provider` -> "github-enterprise"). The day GHE is enabled —
        the base_url swap this module's docstring advertises — that gate has to move onto the
        provider's own host, or a healthy ghe.corp Default gets asked about at api.github.com,
        404s, and its owner is told to archive it.

        Unlike the other two calls this one never raises. It runs only on a path that is already
        failing, and a transport error here would replace the caller's real error with this one.
        """
        try:
            with httpx.Client(transport=self._transport, timeout=REPO_EXISTS_TIMEOUT_S) as client:
                r = client.get(f"{self._base_url}/repos/{full_name}", headers=self._headers())
        except Exception:
            return None
        if r.status_code == 404:
            return False
        if 300 <= r.status_code < 400:
            # GitHub answers 301 for a renamed or transferred repo, and httpx does not follow
            # redirects by default. The URL asked about is the one Domino stored and just failed to
            # clone, so "not at this URL" is the answer that matters — and a stale `mainRepository`
            # is the case this diagnosis is MOST right about, not one to decline.
            return False
        return True if r.status_code < 300 else None


def repo_full_name(clone_url: str | None, *, host: str) -> str | None:
    """"owner/name" from a clone URL on `host`, or None if this provider cannot address it.

    A Domino project can point at any git URI — a BYO repo, an enterprise host, ssh — so both
    halves are checked rather than assumed: the URL must sit on `host`, and its path must be
    exactly two segments.

    The host check is the load-bearing one. Without it a `gitlab.com/owner/repo` Default yields
    "owner/repo", the configured GitHub provider is asked about it, and GitHub's 404 means "not on
    GitHub" rather than "gone" — which would tell someone to archive a Project that is perfectly
    healthy. There is no undo for that, so an unrecognised host answers None and the caller keeps
    whatever error it was already holding.
    """
    parsed = urlsplit(clone_url or "")
    if not host or (parsed.hostname or "").lower() != host.strip().lower():
        return None
    parts = [seg for seg in parsed.path.strip("/").removesuffix(".git").split("/") if seg]
    return "/".join(parts) if len(parts) == 2 else None
