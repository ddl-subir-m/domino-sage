"""Domino control-plane client (Phase 4.2).

Turns a provisioned git repo into a running app: create a git-based project pointing at the repo,
launch a workspace with the Sage Builder tool, and list existing Sage apps.

Project create/list uses the supported public API (paths + body shapes taken from Domino's generated
`domino_public_api_client`, the source of truth):

  POST /api/projects/beta/projects   -> 200 {project:{id,name,mainRepository:{uri}}, metadata}
       NewProjectV1: {name, description, visibility:"Private",
                      mainRepository:{uri, serviceProvider:"Github", defaultRef:{refType:"Branch",value}}}
       (ownerId omitted -> defaults to the calling user)
  GET  /api/projects/beta/projects?offset&limit -> {projects:[{project:{…}}], metadata}

Workspace launch is not part of that public client; it still rides the internal v4 endpoint
(unverified — the next live seam):

  POST /v4/workspace/project/{id}/workspace, GET .../workspace?offset&limit

The sidecar token is short-lived, so we re-acquire it per call (token_provider())."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

log = logging.getLogger("sage.provision.domino")

_PROJECTS_PATH = "/api/projects/beta/projects"
# Sage apps are identified by their repo name prefix (naming.repo_base -> "sage-<slug>"); the public
# create API has no tag field, so list_apps filters on the project's git repo URI instead.
_SAGE_REPO_PREFIX = "sage-"


@dataclass(frozen=True)
class ProjectRef:
    id: str
    name: str
    git_url: str | None = None


class ControlPlane(Protocol):
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
        git_service_provider: str = "Github",  # GitServiceProviderV1 value (hub is github-only in v1)
        git_host: str = "github.com",  # domain of the Domino git credential to attach to projects
        transport: httpx.BaseTransport | None = None,  # test seam
        timeout_s: float = 30.0,
    ) -> None:
        self._host = api_host.rstrip("/")
        self._token_provider = token_provider
        self._env_id = environment_id
        self._env_rev = environment_revision_id
        self._tier_id = hardware_tier_id
        self._tool = builder_tool
        self._provider = git_service_provider
        self._git_host = git_host
        self._cred_id: str | None = None  # resolved lazily, then cached
        self._transport = transport
        self._timeout_s = timeout_s

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self._transport, timeout=self._timeout_s)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_provider()}", "Accept": "application/json"}

    @staticmethod
    def _check(r: httpx.Response, verb: str, path: str) -> Any:
        # Surface v4's response body on error — that's where the validation detail lives, which
        # raise_for_status() drops. No secrets in a v4 error body.
        if r.status_code >= 400:
            raise RuntimeError(f"{verb} {path} -> {r.status_code}: {r.text.strip()[:500]}")
        return r.json()

    def _get(self, path: str, **kw: Any) -> Any:
        with self._client() as c:
            r = c.get(f"{self._host}{path}", headers=self._headers(), **kw)
        return self._check(r, "GET", path)

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        with self._client() as c:
            r = c.post(f"{self._host}{path}", json=body, headers=self._headers())
        return self._check(r, "POST", path)

    def _git_credential_id(self) -> str:
        """Id of the caller's Domino git credential for `git_host` (cached). Domino validates repo
        access at project-create time, so a git-based project pointing at a private repo needs it."""
        if self._cred_id is None:
            uid = (self._get("/api/users/v1/self").get("user") or {}).get("id")
            if not uid:
                raise RuntimeError("could not resolve the calling user from /api/users/v1/self")
            creds = self._get(f"/api/users/beta/credentials/{uid}").get("credentials") or []
            match = next(
                (c for c in creds if isinstance(c, dict) and c.get("domain") == self._git_host
                 and (c.get("protocol") or "https") == "https"),
                None,
            )
            if not match or not match.get("id"):
                raise RuntimeError(
                    f"no HTTPS Git credential for {self._git_host} in your Domino account — "
                    f"add one under Account Settings > Git Credentials, then try again"
                )
            self._cred_id = str(match["id"])
        return self._cred_id

    def create_project(
        self, name: str, *, git_url: str, branch: str = "main", description: str = ""
    ) -> ProjectRef:
        # NewProjectV1. ownerId omitted -> defaults to the calling user (the hub runs as that user).
        body = {
            "name": name,
            "description": description or "Created by Sage",
            "visibility": "Private",
            "mainRepository": {
                "uri": git_url,
                "serviceProvider": self._provider,
                "defaultRef": {"refType": "Branch", "value": branch},
                "gitCredentialId": self._git_credential_id(),
            },
        }
        data = self._post(_PROJECTS_PATH, body)
        proj = data.get("project") if isinstance(data, dict) else None
        pid = (proj or {}).get("id")
        if not pid:
            raise RuntimeError(f"project create returned no id: {str(data)[:200]}")
        return ProjectRef(id=str(pid), name=name, git_url=git_url)

    def create_workspace(self, project_id: str, *, branch: str = "main") -> dict[str, Any]:
        # CreateWorkspaceRequest (domino_private_spec). Required: name, environmentId,
        # hardwareTierId, tools, externalVolumeMounts. The branch comes from the project's
        # mainRepository.defaultRef, so overrideMainGitRepoRef is unnecessary. Pin the same
        # environment revision as the hub when Domino injected one, else default to active.
        body: dict[str, Any] = {
            "name": "sage",
            "environmentId": self._env_id,
            "hardwareTierId": {"value": self._tier_id},
            "tools": [self._tool],
            "externalVolumeMounts": [],
        }
        if self._env_rev:
            body["environmentRevisionSpec"] = {"revisionId": self._env_rev}
        data = self._post(f"/v4/workspace/project/{project_id}/workspace", body)
        # LIVE-VERIFY seam: which field carries the run/session id we assemble the open URL from
        # (see preview.prefix). Workspace metadata has no secrets, so log the shape to stdout.
        if isinstance(data, dict):
            log.info("workspace-create response keys: %s", sorted(data.keys()))
        return data

    def available_tools(self) -> list[dict[str, Any]]:
        """The pluggable workspace tools Domino resolves for this environment (each has an `id` —
        the tool key). A workspace launch fails to schedule if `tools` names an id not in here."""
        data = self._get(f"/v4/environments/{self._env_id}/availableTools")
        items = data if isinstance(data, list) else (data.get("data") if isinstance(data, dict) else [])
        return [t for t in (items or []) if isinstance(t, dict)]

    def list_workspaces(self, project_id: str) -> list[dict[str, Any]]:
        data = self._get(f"/v4/workspace/project/{project_id}/workspace", params={"offset": 0, "limit": 20})
        items = data.get("workspaces") or data.get("data") or data if isinstance(data, (list, dict)) else []
        return items if isinstance(items, list) else []

    def list_apps(self) -> list[ProjectRef]:
        """The caller's Sage apps: projects whose git repo is a `sage-*` repo (the public create API
        has no tag field, so the repo-name prefix is the marker — see naming.repo_base)."""
        data = self._get(_PROJECTS_PATH, params={"offset": 0, "limit": 200})
        envelopes = data.get("projects") if isinstance(data, dict) else data
        out: list[ProjectRef] = []
        for env in envelopes or []:
            # Each item is a ProjectEnvelopeV1 {project:{…}}; tolerate a bare project too.
            p = env.get("project") if isinstance(env, dict) and "project" in env else env
            if not isinstance(p, dict):
                continue
            uri = (p.get("mainRepository") or {}).get("uri") if isinstance(p.get("mainRepository"), dict) else None
            repo_name = uri.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") if uri else ""
            if not repo_name.startswith(_SAGE_REPO_PREFIX):
                continue
            out.append(ProjectRef(id=str(p.get("id") or ""), name=str(p.get("name") or "unnamed"), git_url=uri))
        return out


@dataclass
class FakeControlPlane:
    """In-memory control plane for tests/local hub — no network."""

    projects: list[ProjectRef] = field(default_factory=list)
    workspaces: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _seq: int = 0

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
