"""Provider adapter: create a repo via the provider's REST API (Phase 4.1).

Confirmed live against github.com (repo_provision_probe.sh, DRY_RUN=0):
  POST https://api.github.com/user/repos
    {"name","private":true,"auto_init":false,"description"}  -> 201
    response: {"full_name","clone_url","private", …}
  a name collision returns 422 (caller retries the next `-N` candidate).

  a delete (rollback of a half-provisioned app) is DELETE /repos/{owner}/{name} -> 204.

The token comes from credentials.extract_token and is used in-memory only — never logged. It signs
the create/delete API calls and (via a one-shot helper) the seed push; see seed.py.

github-enterprise shares this exact shape at base https://<host>/api/v3 (unverified — same adapter,
different base_url). Other providers (GitLab/Bitbucket) are separate adapters, added as verified.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"


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


@dataclass
class FakeRepoProvider:
    """In-memory provider for tests/local fake-mode provisioning — no network."""

    host: str = "github.com"
    created: list[RepoInfo] = field(default_factory=list)
    owner: str = "test-owner"

    def create_repo(self, name: str, *, description: str = "", private: bool = True) -> RepoInfo:
        full = f"{self.owner}/{name}"
        if any(r.full_name == full for r in self.created):
            raise RepoNameConflict(name)
        info = RepoInfo(full_name=full, clone_url=f"https://{self.host}/{full}.git", private=private)
        self.created.append(info)
        return info

    def delete_repo(self, full_name: str) -> None:
        self.created = [r for r in self.created if r.full_name != full_name]


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
