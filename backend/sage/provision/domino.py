"""Domino control-plane client (Phase 4.2).

The v4 platform calls the hub makes to turn a provisioned git repo into a running app: resolve the
caller's ObjectId, create a git-based project pointing at the repo, launch a workspace with the Sage
Builder tool, and list existing Sage apps. Contract confirmed live in Phase 0 (§0.3):

  GET  /v4/users/self                                   -> {id: <ObjectId>, …}
  POST /v4/projects                                     -> 200 {id, …}
       {name, ownerId, visibility:"Private", description, collaborators:[], tags:{tagNames:[…]}}
  POST /v4/workspace/project/{id}/workspace             -> {id, …}
       {name, environmentId, environmentRevisionId, hardwareTierId:{value}, tools:[…],
        mainGitRepoRef:{type:"branches",value}, externalVolumeMounts:[]}
  GET  /v4/workspace/project/{id}/workspace?offset&limit

The sidecar token is short-lived, so we re-acquire it per call (token_provider()).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

log = logging.getLogger("sage.provision.domino")

# Tag stamped on every project the hub creates, so list_apps can find "my Sage apps" by filter.
SAGE_TAG = "sage"


@dataclass(frozen=True)
class ProjectRef:
    id: str
    name: str
    git_url: str | None = None


class ControlPlane(Protocol):
    def owner_id(self) -> str: ...
    def create_project(self, name: str, *, git_url: str, branch: str = "main", description: str = "") -> ProjectRef: ...
    def create_workspace(self, project_id: str, *, branch: str = "main") -> dict[str, Any]: ...
    def list_apps(self) -> list[ProjectRef]: ...
    def list_workspaces(self, project_id: str) -> list[dict[str, Any]]: ...


class DominoControlPlane:
    """Real v4 client. Needs api_host + a (re-acquired) sidecar token, plus the Environment/hardware
    ids Domino injects into the hub's own workspace (reused for the child apps)."""

    def __init__(
        self,
        api_host: str,
        token_provider: Callable[[], str],
        *,
        environment_id: str,
        hardware_tier_id: str,
        builder_tool: str = "sageBuilder",
        environment_revision_id: str | None = None,
        transport: httpx.BaseTransport | None = None,  # test seam
        timeout_s: float = 30.0,
    ) -> None:
        self._api = api_host.rstrip("/") + "/v4"
        self._token_provider = token_provider
        self._env_id = environment_id
        self._env_rev = environment_revision_id
        self._tier_id = hardware_tier_id
        self._tool = builder_tool
        self._transport = transport
        self._timeout_s = timeout_s

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self._transport, timeout=self._timeout_s)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_provider()}", "Accept": "application/json"}

    def _get(self, path: str, **kw: Any) -> Any:
        with self._client() as c:
            r = c.get(f"{self._api}{path}", headers=self._headers(), **kw)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        with self._client() as c:
            r = c.post(f"{self._api}{path}", json=body, headers=self._headers())
        r.raise_for_status()
        return r.json()

    def owner_id(self) -> str:
        data = self._get("/users/self")
        oid = data.get("id") or data.get("userId")
        if not oid:
            raise RuntimeError("could not resolve caller ObjectId from /v4/users/self")
        return str(oid)

    def create_project(
        self, name: str, *, git_url: str, branch: str = "main", description: str = ""
    ) -> ProjectRef:
        body = {
            "name": name,
            "ownerId": self.owner_id(),
            "visibility": "Private",
            "description": description or "Created by Sage",
            "collaborators": [],
            "tags": {"tagNames": [SAGE_TAG]},
            # git-based project pointing at the freshly provisioned repo.
            "mainGitRepoRef": {"type": "branches", "value": branch},
            "mainRepository": {"uri": git_url, "defaultRef": {"type": "branches", "value": branch}},
        }
        data = self._post("/projects", body)
        pid = data.get("id") or data.get("projectId")
        if not pid:
            raise RuntimeError(f"project create returned no id: {str(data)[:200]}")
        return ProjectRef(id=str(pid), name=name, git_url=git_url)

    def create_workspace(self, project_id: str, *, branch: str = "main") -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": "sage",
            "environmentId": self._env_id,
            "hardwareTierId": {"value": self._tier_id},
            "tools": [self._tool],
            "mainGitRepoRef": {"type": "branches", "value": branch},
            "externalVolumeMounts": [],
        }
        if self._env_rev:
            body["environmentRevisionId"] = self._env_rev
        data = self._post(f"/workspace/project/{project_id}/workspace", body)
        # LIVE-VERIFY seam: which field carries the run/session id we assemble the open URL from
        # (see preview.prefix). Workspace metadata has no secrets, so log the shape to stdout.
        if isinstance(data, dict):
            log.info("workspace-create response keys: %s", sorted(data.keys()))
        return data

    def list_workspaces(self, project_id: str) -> list[dict[str, Any]]:
        data = self._get(f"/workspace/project/{project_id}/workspace", params={"offset": 0, "limit": 20})
        items = data.get("workspaces") or data.get("data") or data if isinstance(data, (list, dict)) else []
        return items if isinstance(items, list) else []

    def list_apps(self) -> list[ProjectRef]:
        """Projects tagged `sage` owned by the caller (best-effort field parsing — v4 project list
        shapes vary by version)."""
        data = self._get("/projects", params={"limit": 100})
        projects = data.get("data") if isinstance(data, dict) else data
        out: list[ProjectRef] = []
        for p in projects or []:
            if not isinstance(p, dict):
                continue
            tags = p.get("tags") or {}
            names = tags.get("tagNames") if isinstance(tags, dict) else tags
            flat = [t if isinstance(t, str) else (t or {}).get("name") for t in (names or [])]
            if SAGE_TAG not in [str(t).lower() for t in flat if t]:
                continue
            out.append(
                ProjectRef(
                    id=str(p.get("id") or p.get("projectId") or ""),
                    name=str(p.get("name") or "unnamed"),
                    git_url=(p.get("mainRepository") or {}).get("uri") if isinstance(p.get("mainRepository"), dict) else None,
                )
            )
        return out


@dataclass
class FakeControlPlane:
    """In-memory control plane for tests/local hub — no network."""

    owner: str = "owner-oid"
    projects: list[ProjectRef] = field(default_factory=list)
    workspaces: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _seq: int = 0

    def owner_id(self) -> str:
        return self.owner

    def create_project(self, name: str, *, git_url: str, branch: str = "main", description: str = "") -> ProjectRef:
        self._seq += 1
        ref = ProjectRef(id=f"proj-{self._seq}", name=name, git_url=git_url)
        self.projects.append(ref)
        return ref

    def create_workspace(self, project_id: str, *, branch: str = "main") -> dict[str, Any]:
        ws = {"id": f"ws-{project_id}", "projectId": project_id}
        self.workspaces.setdefault(project_id, []).append(ws)
        return ws

    def list_workspaces(self, project_id: str) -> list[dict[str, Any]]:
        return list(self.workspaces.get(project_id, []))

    def list_apps(self) -> list[ProjectRef]:
        return list(self.projects)
