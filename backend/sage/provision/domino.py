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
from urllib.parse import quote
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

log = logging.getLogger("sage.provision.domino")

_PROJECTS_PATH = "/api/projects/beta/projects"
_APPS_PATH = "/api/apps/beta/apps"  # public apps API (create+launch, then republish new versions)
# Sage apps are identified by their repo name prefix (naming.repo_base -> "sage-<slug>"); the public
# create API has no tag field, so list_apps filters on the project's git repo URI instead.
_SAGE_REPO_PREFIX = "sage-"
# The name every hub-created builder workspace is given (WorkspaceDto.name). The list DTO carries no
# tool info, so the hub uses this to tell its own builder workspaces apart from a VS Code/Jupyter
# session a user may have opened in the same project — Start/Stop/status act only on builders.
BUILDER_WORKSPACE_NAME = "sage"
# A pre-stop save drives the builder's commit → pull → agent-resolve → push, which can run a model
# turn to resolve conflicts, so it needs a far longer ceiling than a plain control-plane REST call.
_SAVE_TIMEOUT_S = 180.0


@dataclass(frozen=True)
class ProjectRef:
    id: str
    name: str
    git_url: str | None = None


@dataclass(frozen=True)
class PublishedApp:
    id: str
    url: str  # shareable Domino App URL ("" if the response carried none, e.g. a republish)


class ControlPlane(Protocol):
    def create_project(self, name: str, *, git_url: str, branch: str = "main", description: str = "") -> ProjectRef: ...
    def create_workspace(self, project_id: str, *, branch: str = "main") -> dict[str, Any]: ...
    def stop_workspace(self, project_id: str, workspace_id: str) -> dict[str, Any]: ...
    def resume_workspace(self, project_id: str, workspace_id: str) -> dict[str, Any]: ...
    def delete_workspace(self, project_id: str, workspace_id: str) -> dict[str, Any]: ...
    def save_workspace_work(self, open_path: str) -> dict[str, Any]: ...
    def archive_project(self, project_id: str) -> dict[str, Any]: ...
    def list_apps(self) -> list[ProjectRef]: ...
    def list_workspaces(self, project_id: str) -> list[dict[str, Any]]: ...
    def publish_app(self, project_id: str, *, name: str, git_ref_type: str = "head",
                    git_ref_value: str | None = None, entry_point: str = "app.sh",
                    visibility: str = "GRANT_BASED") -> PublishedApp: ...
    def republish_app(self, app_id: str, *, git_ref_type: str = "head",
                      git_ref_value: str | None = None) -> PublishedApp: ...
    def find_project_app(self, project_id: str) -> PublishedApp | None: ...
    def app_manage_url(self, project_id: str, app_id: str, project_name: str) -> str: ...


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

    def _post(self, path: str, body: dict[str, Any] | None = None, *, params: dict[str, Any] | None = None) -> Any:
        with self._client() as c:
            r = c.post(f"{self._host}{path}", json=body, headers=self._headers(), params=params)
        return self._check(r, "POST", path)

    def _delete(self, path: str) -> Any:
        with self._client() as c:
            r = c.delete(f"{self._host}{path}", headers=self._headers())
        return self._check(r, "DELETE", path)

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
        # Use Domino's stored name — it's the URL slug the open link is built from, and Domino may
        # normalize what we sent.
        return ProjectRef(id=str(pid), name=str(proj.get("name") or name), git_url=git_url)

    def create_workspace(self, project_id: str, *, branch: str = "main") -> dict[str, Any]:
        # CreateWorkspaceRequest (domino_private_spec). Required: name, environmentId,
        # hardwareTierId, tools, externalVolumeMounts. The branch comes from the project's
        # mainRepository.defaultRef, so overrideMainGitRepoRef is unnecessary. Pin the same
        # environment revision as the hub when Domino injected one, else default to active.
        body: dict[str, Any] = {
            "name": BUILDER_WORKSPACE_NAME,
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

    def stop_workspace(self, project_id: str, workspace_id: str) -> dict[str, Any]:
        # Stop a running builder so it stops consuming a hardware tier. Path + verb confirmed against
        # Domino's domino_private_spec (basePath /v4): POST .../workspace/{workspaceId}/stop, no request
        # body, path params only. A stop can return 200 with an empty body, so tolerate a non-JSON body.
        path = f"/v4/workspace/project/{project_id}/workspace/{workspace_id}/stop"
        with self._client() as c:
            r = c.post(f"{self._host}{path}", headers=self._headers())
        if r.status_code >= 400:
            raise RuntimeError(f"POST {path} -> {r.status_code}: {r.text.strip()[:500]}")
        try:
            data = r.json()
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {"stopped": True}

    def resume_workspace(self, project_id: str, workspace_id: str) -> dict[str, Any]:
        # Resume a stopped builder in place by starting a NEW session on the existing workspace — the
        # exact inverse of stop_workspace — instead of creating a fresh workspace each time (which
        # piles up stopped records). We used to POST /v4/workspaces/relaunch, but that 404s the
        # workspace id on the dogfood build; the session endpoint is what the Domino UI itself uses.
        # Spec: POST /v4/workspace/project/{projectId}/workspace/{workspaceId}/sessions
        #   ?externalVolumeMounts=... (required array; the builder mounts none). The server rejects an
        # absent key ("Missing parameter"), so send the key present-but-empty (?externalVolumeMounts=)
        # — an empty STRING, which httpx keeps, not an empty list, which it drops. No request body; the
        # new session checks out the branch's LATEST commit. The WorkspaceSessionDto returned carries no
        # owner/open-url fields, so the caller derives the open URL by polling list_workspaces.
        path = f"/v4/workspace/project/{project_id}/workspace/{workspace_id}/sessions"
        data = self._post(path, params={"externalVolumeMounts": ""})
        return data if isinstance(data, dict) else {"resumed": True}

    def delete_workspace(self, project_id: str, workspace_id: str) -> dict[str, Any]:
        # Remove a workspace entirely (not just stop it). Required before archive_project: a project
        # that still CONTAINS a workspace — even a stopped one — is rejected with 500 "cannot be
        # archived. It contains N workspace(s)". Spec: DELETE /v4/workspace/project/{projectId}/
        # workspace/{workspaceId} (deleteWorkspace), path params only, no body.
        data = self._delete(f"/v4/workspace/project/{project_id}/workspace/{workspace_id}")
        return data if isinstance(data, dict) else {"deleted": True}

    def save_workspace_work(self, open_path: str) -> dict[str, Any]:
        # Pre-stop save: reach the running builder through its own notebookSession proxy (the same
        # host-relative `open_path` the hub opens in the browser) and drive its POST /api/project/sync
        # — commit in-progress edits, pull + agent-resolve any conflicts, push — so stopping the
        # workspace never drops uncommitted work. Runs on the internal DOMINO_API_HOST, which proxies
        # the notebookSession path just like the external origin does.
        url = f"{self._host}{open_path.rstrip('/')}/api/project/sync"
        with self._client() as c:
            r = c.post(url, headers=self._headers(), timeout=_SAVE_TIMEOUT_S)
        return self._check(r, "POST", url)

    def archive_project(self, project_id: str) -> dict[str, Any]:
        # "Delete" a Sage app = archive its Domino project (soft delete; a Domino admin can restore
        # it). Public API, same family as create_project: DELETE /api/projects/beta/projects/{id}.
        # A project that still CONTAINS any workspace is rejected, so the caller deletes the
        # workspaces (after saving + stopping a running builder) BEFORE calling this. The GitHub repo
        # is intentionally NOT touched.
        data = self._delete(f"{_PROJECTS_PATH}/{project_id}")
        return data if isinstance(data, dict) else {"archived": True}

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

    def publish_app(
        self,
        project_id: str,
        *,
        name: str,
        git_ref_type: str = "head",
        git_ref_value: str | None = None,
        entry_point: str = "app.sh",
        visibility: str = "GRANT_BASED",
    ) -> PublishedApp:
        """Publish a git-based project as a Domino App and launch its first version.

        Public apps API (AppCreationRequest): creating an app WITH a `version` also launches it, so
        publish is one call. The App runs entry_point (app.sh) on :8888 behind Domino's app proxy,
        on the same environment + hardware tier the hub itself runs on (self._env_id/_tier_id).
        """
        body = {
            "name": name,
            "projectId": project_id,
            "visibility": visibility,
            "entryPoint": entry_point,
            "configurationType": "STANDARD",
            "version": self._app_version(git_ref_type, git_ref_value),
        }
        d = self._post(_APPS_PATH, body)
        d = d if isinstance(d, dict) else {}
        return PublishedApp(id=str(d.get("id") or ""), url=str(d.get("url") or ""))

    def republish_app(
        self,
        app_id: str,
        *,
        git_ref_type: str = "head",
        git_ref_value: str | None = None,
    ) -> PublishedApp:
        """Deploy a new version of an existing app (the URL is stable across versions) — used to
        re-publish after further edits. The version response carries the version id, not the app id,
        so we keep the caller's app_id."""
        d = self._post(f"{_APPS_PATH}/{app_id}/versions", self._app_version(git_ref_type, git_ref_value))
        d = d if isinstance(d, dict) else {}
        return PublishedApp(id=app_id, url=str(d.get("url") or ""))

    def find_project_app(self, project_id: str) -> PublishedApp | None:
        """The published Domino App for this project, if one already exists — so a re-publish targets
        it (new version, stable URL) instead of creating a duplicate App. The public create API has
        no tag field, so we list by projectId and take the first non-archived app."""
        d = self._get(_APPS_PATH, params={"projectId": project_id, "offset": 0, "limit": 50})
        items = d if isinstance(d, list) else (d.get("apps") or d.get("data") or [])
        for a in items:
            if not isinstance(a, dict) or not a.get("id"):
                continue
            status = str(a.get("status") or a.get("state") or "").lower()
            if "archiv" in status or "delet" in status:  # skip a soft-deleted App — republish would fail
                continue
            return PublishedApp(id=str(a["id"]), url=str(a.get("url") or ""))
        return None

    def app_manage_url(self, project_id: str, app_id: str, project_name: str) -> str:
        """Deep-link to the App's settings/overview page in Domino (tier, autoscaling, data, sharing),
        so 1-click Publish stays frictionless while the full native config is one click away. Shape:
        {host}/u/{owner}/{project}/apps/{projectId}/{appId}/details/overview."""
        owner = self._username()
        proj = quote(project_name, safe="")
        return f"{self._host}/u/{owner}/{proj}/apps/{project_id}/{app_id}/details/overview"

    def _username(self) -> str:
        u = self._get("/api/users/v1/self").get("user") or {}
        return str(u.get("userName") or u.get("loginId") or u.get("id") or "")

    def _app_version(self, git_ref_type: str, git_ref_value: str | None) -> dict[str, Any]:
        """AppVersionCreationRequest: the env + tier the app runs on and the git ref it deploys.
        gitRef.type is head|branches|commitId|tags; value is omitted for "head" (latest on the
        project's default branch)."""
        ref: dict[str, Any] = {"type": git_ref_type}
        if git_ref_value:
            ref["value"] = git_ref_value
        version: dict[str, Any] = {
            "environmentId": self._env_id,
            "hardwareTierId": self._tier_id,
            "gitRef": ref,
        }
        if self._env_rev:  # pin the revision Domino injected, else the app uses the env's active one
            version["environmentRevisionId"] = self._env_rev
        return version


@dataclass
class FakeControlPlane:
    """In-memory control plane for tests/local hub — no network."""

    projects: list[ProjectRef] = field(default_factory=list)
    workspaces: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    published: dict[str, PublishedApp] = field(default_factory=dict)  # app_id -> app
    app_projects: dict[str, str] = field(default_factory=dict)  # app_id -> project_id (find_project_app)
    saved_paths: list[str] = field(default_factory=list)  # open_paths a pre-stop save was driven for
    _seq: int = 0

    def create_project(self, name: str, *, git_url: str, branch: str = "main", description: str = "") -> ProjectRef:
        self._seq += 1
        ref = ProjectRef(id=f"proj-{self._seq}", name=name, git_url=git_url)
        self.projects.append(ref)
        return ref

    def create_workspace(self, project_id: str, *, branch: str = "main") -> dict[str, Any]:
        ws = {
            "id": f"ws-{project_id}",
            "projectId": project_id,
            "ownerName": "owner",
            "project": {"name": project_id},
            "mostRecentSession": {"executionId": f"run-{project_id}"},
        }
        self.workspaces.setdefault(project_id, []).append(ws)
        return ws

    def stop_workspace(self, project_id: str, workspace_id: str) -> dict[str, Any]:
        for ws in self.workspaces.get(project_id, []):
            if ws.get("id") == workspace_id:
                ws["state"] = "Stopped"
                session = ws.get("mostRecentSession")
                if isinstance(session, dict) and isinstance(session.get("sessionStatusInfo"), dict):
                    session["sessionStatusInfo"]["isRunning"] = False
                return {"id": workspace_id, "state": "Stopped"}
        return {"id": workspace_id, "state": "Unknown"}

    def resume_workspace(self, project_id: str, workspace_id: str) -> dict[str, Any]:
        for ws in self.workspaces.get(project_id, []):
            if ws.get("id") == workspace_id:
                ws["state"] = "running"
                session = ws.setdefault("mostRecentSession", {})
                if isinstance(session, dict):
                    session.setdefault("sessionStatusInfo", {})["isRunning"] = True
                return ws
        return {"id": workspace_id, "state": "Unknown"}

    def delete_workspace(self, project_id: str, workspace_id: str) -> dict[str, Any]:
        kept = [w for w in self.workspaces.get(project_id, []) if w.get("id") != workspace_id]
        self.workspaces[project_id] = kept
        return {"deleted": True}

    def save_workspace_work(self, open_path: str) -> dict[str, Any]:
        self.saved_paths.append(open_path)
        return {"saved": True}

    def list_workspaces(self, project_id: str) -> list[dict[str, Any]]:
        return list(self.workspaces.get(project_id, []))

    def archive_project(self, project_id: str) -> dict[str, Any]:
        self.projects = [p for p in self.projects if p.id != project_id]
        self.workspaces.pop(project_id, None)
        return {"archived": True}

    def list_apps(self) -> list[ProjectRef]:
        return list(self.projects)

    def publish_app(self, project_id: str, *, name: str, git_ref_type: str = "head",
                    git_ref_value: str | None = None, entry_point: str = "app.sh",
                    visibility: str = "GRANT_BASED") -> PublishedApp:
        self._seq += 1
        app = PublishedApp(id=f"app-{self._seq}", url=f"https://fake.domino/app/app-{self._seq}")
        self.published[app.id] = app
        self.app_projects[app.id] = project_id
        return app

    def republish_app(self, app_id: str, *, git_ref_type: str = "head",
                      git_ref_value: str | None = None) -> PublishedApp:
        return self.published.get(app_id, PublishedApp(id=app_id, url=""))

    def find_project_app(self, project_id: str) -> PublishedApp | None:
        app_id = next((aid for aid, pid in self.app_projects.items() if pid == project_id), None)
        return self.published.get(app_id) if app_id else None

    def app_manage_url(self, project_id: str, app_id: str, project_name: str) -> str:
        return f"https://fake.domino/u/owner/{project_name}/apps/{project_id}/{app_id}/details/overview"
