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
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from ..orchestrator import brand

log = logging.getLogger("sage.provision.domino")

_PROJECTS_PATH = "/api/projects/beta/projects"
_APPS_PATH = "/api/apps/beta/apps"
_APPS_PAGE = 100  # the list API's page size
_APPS_MAX = 1000  # ceiling on the global app list, so a wrong totalCount can't spin forever  # public apps API (create+launch, then republish new versions)
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
# The readiness probe is one round trip to a workspace that may not be answering yet, and the door
# repeats it every few seconds, so it gets a short leash of its own rather than the 30s default.
_READY_TIMEOUT_S = 10.0
# The apps API hands back an /apps-internal/{id} URL that 404s in a browser (verified on
# cloud-dogfood 2026-08-07). Domino's own "Copy URL" for that same app is the /modelproducts one.
_APPS_INTERNAL_RE = re.compile(r"/apps-internal/([^/?#]+)")


def _viewer_url(raw: str, app_id: str) -> str:
    """The user-facing URL of a published App, from whatever the apps API returned.

    Two rewrites, both driven by what Domino actually serves:
      - `/apps-internal/{id}` 404s; the working page is `/modelproducts/{id}?scope=project` (same id).
      - a republish returns no URL at all, so fall back to that route built from the app id.
    Host-RELATIVE on purpose: /modelproducts lives on the main host, while DOMINO_API_HOST is the
    internal cluster address and the app may be served under `apps.<host>`. The UI resolves it
    against the browser's origin (builderUrl/mainHostUrl). Any other absolute URL passes through."""
    m = _APPS_INTERNAL_RE.search(raw)
    if m:
        return f"/modelproducts/{m.group(1)}?scope=project"
    if raw:
        return raw
    return f"/modelproducts/{app_id}?scope=project" if app_id else ""


def app_viewer_url(app_id: str) -> str:
    """The page a browser can open for a published App, from its id alone — "" for no id.

    `_viewer_url`'s fallback under a public name, for the callers holding a recorded id rather than
    a response from the apps API: the Build rail reads what each Built App wrote down, and re-reads
    it on a 30-second tick, so asking the control plane per row would make a list that says
    "published" cost one request per app to say it. Pure, and it stays the one place that knows the
    grammar — that rewrite has already been re-learned from live Domino once.
    """
    return _viewer_url("", app_id)


class NotFound(RuntimeError):
    """Domino answered 404: the thing the call named is not there.

    Split out of the generic RuntimeError for one distinction (#80). A publish has to tell "the
    Domino App was deleted on its settings page" from "Domino is having a bad minute", and only a
    404 is evidence of the first — treating a 502 or a timeout as a deletion would create a second
    deployment, which is the failure #70 exists to prevent arriving by a new road.

    A subclass rather than a sibling, so every caller that already catches RuntimeError (or
    Exception) keeps catching this unchanged; only the callers that care look for it by name.
    """


@dataclass(frozen=True)
class ProjectRef:
    id: str
    name: str
    git_url: str | None = None


@dataclass(frozen=True)
class UserRef:
    """Who the control-plane token acts as. On the Workbench App that is the viewer (Domino's
    extended identity puts them behind the sidecar token); in a builder it is whoever started it."""

    id: str
    name: str


@dataclass(frozen=True)
class PublishedApp:
    id: str
    url: str  # shareable Domino App URL ("" if the response carried none, e.g. a republish)


@dataclass(frozen=True)
class BuiltApp:
    """A published Domino App as the Gallery lists it (#48).

    Wider than PublishedApp, which is Publish's own id+URL pair: a Gallery card has to be readable
    before it is clicked, so it carries the App's name, where it came from, and whether it is up.
    """

    id: str
    name: str
    url: str
    project_id: str
    project_name: str
    status: str  # currentVersion.currentInstance.status: Running / Failed / Preparing / "" unknown


@dataclass(frozen=True)
class CredentialRef:
    """One of the caller's Domino Git credentials, as much of it as a person needs to recognise it.

    Carries no secret. `fingerprint` is deliberately absent: it is not a hash of the HTTPS secret
    (ADR-0033) and it identifies nothing to somebody reading an error.
    """

    id: str
    label: str      # e.g. "work PAT (github.com)", or "my key (github.com) [SSH]"
    domain: str
    protocol: str
    usable: bool    # HTTPS, and for the host this control plane provisions against


class ControlPlane(Protocol):
    def whoami(self) -> UserRef: ...
    @property
    def git_host(self) -> str: ...
    def git_credentials(self) -> list[CredentialRef]: ...
    def create_project(self, name: str, *, git_url: str, git_credential_id: str, branch: str = "main", description: str = "") -> ProjectRef: ...
    def create_workspace(self, project_id: str, *, branch: str = "main") -> dict[str, Any]: ...
    def stop_workspace(self, project_id: str, workspace_id: str) -> dict[str, Any]: ...
    def resume_workspace(self, project_id: str, workspace_id: str) -> dict[str, Any]: ...
    def delete_workspace(self, project_id: str, workspace_id: str) -> dict[str, Any]: ...
    def save_workspace_work(self, open_path: str) -> dict[str, Any]: ...
    def workspace_http_ready(self, open_path: str) -> bool | None: ...
    def archive_project(self, project_id: str) -> dict[str, Any]: ...
    def list_apps(self) -> list[ProjectRef]: ...
    def list_workspaces(self, project_id: str) -> list[dict[str, Any]]: ...
    def publish_app(self, project_id: str, *, name: str, git_ref_type: str = "head",
                    git_ref_value: str | None = None, entry_point: str = "app.sh",
                    visibility: str = "GRANT_BASED") -> PublishedApp: ...
    def republish_app(self, app_id: str, *, git_ref_type: str = "head",
                      git_ref_value: str | None = None) -> PublishedApp: ...
    def list_project_apps(self, project_id: str) -> list[PublishedApp]: ...
    def list_all_apps(self) -> list[BuiltApp]: ...
    def delete_app_deployment(self, app_id: str) -> dict[str, Any]: ...
    def app_manage_url(self, app_id: str, project_name: str) -> str | None: ...
    def app_status(self, app_id: str) -> str: ...
    def app_visibility(self, app_id: str) -> str: ...
    def app_exists(self, app_id: str) -> bool: ...


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
        self._me: UserRef | None = None       # whoami's answer, and the token it answered for —
        self._me_for: str | None = None       # see whoami(): the token CAN change under us
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
        if r.status_code == 404:
            raise NotFound(f"{verb} {path} -> 404: {r.text.strip()[:500]}")
        if r.status_code >= 400:
            raise RuntimeError(f"{verb} {path} -> {r.status_code}: {r.text.strip()[:500]}")
        # A successful DELETE answers 204 with no body, and r.json() on that raises the useless
        # "Expecting value: line 1 column 1 (char 0)" — which then surfaced to the user as a FAILED
        # delete even though the App was gone (hub archive, 2026-08-07). No body is not an error;
        # callers already treat a non-dict result as "succeeded, nothing to read".
        if r.status_code == 204 or not r.content.strip():
            return None
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

    @staticmethod
    def _cred_label(c: dict[str, Any]) -> str:
        """Name a credential the way its owner sees it in Account Settings, so an error can be acted
        on. Carries no secret: the list holds no token, and the fingerprint is left out because it
        identifies nothing to a person reading an error."""
        name = str(c.get("name") or "").strip()
        domain = str(c.get("domain") or "").strip()
        proto = str(c.get("protocol") or "https").strip().lower()
        label = f"{name} ({domain})" if name and domain else (name or domain or str(c.get("id") or "unnamed"))
        return label if proto == "https" else f"{label} [{proto.upper()}]"

    @property
    def git_host(self) -> str:
        """The host this control plane attaches credentials for. The caller needs it to say which
        host it found nothing for."""
        return self._git_host

    def git_credentials(self) -> list[CredentialRef]:
        """The caller's Domino Git credentials, in the order the platform lists them.

        Reports; decides nothing (ADR-0028). Which one to send, and what to do when it is refused,
        is the caller's judgement — Sage cannot tell a live credential from a dead one without
        asking Domino, so only the caller knows what an attempt is worth (ADR-0033).
        """
        uid = (self._get("/api/users/v1/self").get("user") or {}).get("id")
        if not uid:
            raise RuntimeError("could not resolve the calling user from /api/users/v1/self")
        raw = self._get(f"/api/users/beta/credentials/{uid}").get("credentials") or []
        out = []
        for c in raw:
            if not isinstance(c, dict) or not c.get("id"):
                continue
            domain = str(c.get("domain") or "")
            proto = str(c.get("protocol") or "https").lower()
            out.append(CredentialRef(
                id=str(c["id"]), label=self._cred_label(c), domain=domain, protocol=proto,
                usable=domain == self._git_host and proto == "https",
            ))
        return out

    def create_project(
        self, name: str, *, git_url: str, git_credential_id: str, branch: str = "main",
        description: str = "",
    ) -> ProjectRef:
        # NewProjectV1. ownerId omitted -> defaults to the calling user (the hub runs as that user).
        #
        # `git_credential_id` is passed in, never chosen here. Domino checks repo ACCESS inside this
        # call — not that the credential exists — so this is the only place a credential is tested,
        # and a rejection is reported as-is for the caller to judge (ADR-0033).
        body = {
            "name": name,
            "description": description or brand.text("Created by {assistantName}"),
            "visibility": "Private",
            "mainRepository": {
                "uri": git_url,
                "serviceProvider": self._provider,
                "defaultRef": {"refType": "Branch", "value": branch},
                "gitCredentialId": git_credential_id,
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
        # mainRepository.defaultRef, so overrideMainGitRepoRef is unnecessary.
        #
        # No environmentRevisionSpec, deliberately: a builder takes the Environment's ACTIVE
        # revision. This used to pin the revision Domino injected into the caller — which is the
        # revision the *Workbench App* was launched with, not the current one. A rebuilt Environment
        # then never reached a new builder until somebody restarted the App, and the symptom was a
        # builder serving Sage code weeks older than the door that created it. The App is a door
        # that lives for seconds; the builder is where the work happens, so the builder wins.
        # (publish_app still pins — a deployed Built App should keep running the image it was
        # tested on. That is a different question with a different answer.)
        body: dict[str, Any] = {
            "name": BUILDER_WORKSPACE_NAME,
            "environmentId": self._env_id,
            "hardwareTierId": {"value": self._tier_id},
            "tools": [self._tool],
            "externalVolumeMounts": [],
        }
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

    def workspace_http_ready(self, open_path: str) -> bool | None:
        """Does the builder's own web server answer behind the workspace proxy yet?

        Domino calls a session running as soon as its execution is up, which is well before the Sage
        process inside it has bound its port — and for that whole gap Domino's proxy answers 502 Bad
        Gateway. Sending the browser in on the session state alone is what put a 502 on the first
        page a new viewer ever saw. So ask the same proxy the browser is about to ask, over the
        internal host, the way save_workspace_work already reaches a running builder.

        True  — something answered, so the browser gets a page rather than the gateway's error.
        False — the proxy has no upstream yet (the gateway family): keep waiting.
        None  — the probe could not tell (no route, a timeout, an auth wall in front of the proxy).
                The caller then falls back to Domino's own answer, so a probe that can never work
                anywhere leaves the door exactly as it was rather than closing it.
        """
        url = f"{self._host}{open_path.rstrip('/')}/healthz"
        try:
            with self._client() as c:
                r = c.get(url, headers=self._headers(), timeout=_READY_TIMEOUT_S)
        except httpx.HTTPError as e:
            log.info("readiness probe couldn't reach %s (%s) — trusting the session state", url, type(e).__name__)
            return None
        if r.status_code in (502, 503, 504):
            return False
        if r.status_code < 500:
            return True
        log.info("readiness probe: %s -> %s — trusting the session state", url, r.status_code)
        return None

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
        app_id = str(d.get("id") or "")
        return PublishedApp(id=app_id, url=_viewer_url(str(d.get("url") or ""), app_id))

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
        return PublishedApp(id=app_id, url=_viewer_url(str(d.get("url") or ""), app_id))

    def list_project_apps(self, project_id: str) -> list[PublishedApp]:
        """Every published Domino App belonging to this project.

        Live-verified schema of the beta apps API: results are wrapped as {"items": [...], "metadata":
        {...}}, the list is GLOBAL (every app on the deployment), and each item nests its project as
        `project.id` (no top-level projectId). We send ?projectId= AND match on project.id
        client-side — never include another project's app.

        The filter IS honored for beta apps: verified 2026-08-20 on cloud-dogfood, where the same
        request that returns 284 rows unfiltered returned exactly this project's 1. The earlier
        "not reliably honored" reading came from projects holding only classic, non-beta apps, whose
        empty answer was the truth rather than a filter failing. The client-side match stays as the
        belt to that braces: one page of 100 is all this reads, so a filter that silently stopped
        working would otherwise hand a caller another project's App."""
        d = self._get(_APPS_PATH, params={"projectId": project_id, "offset": 0, "limit": 100})
        items = d if isinstance(d, list) else (d.get("items") or [])
        out: list[PublishedApp] = []
        for a in items:
            if not isinstance(a, dict) or not a.get("id"):
                continue
            if (a.get("project") or {}).get("id") != project_id:
                continue
            out.append(PublishedApp(id=str(a["id"]), url=_viewer_url(str(a.get("url") or ""), str(a["id"]))))
        return out

    def list_all_apps(self) -> list[BuiltApp]:
        """Every published App this token can read, across every project.

        The beta list is GLOBAL — one deployment answered with 284 rows — so a single page of 100
        would drop most of it while looking like a complete answer. This pages to metadata's
        totalCount, with a ceiling so a bad count can't spin forever.

        Which of these a viewer should actually be shown is a policy question, and it lives one
        layer up in ProvisionService.list_built_apps.
        """
        out: list[BuiltApp] = []
        offset, total = 0, None
        while total is None or offset < total:
            d = self._get(_APPS_PATH, params={"offset": offset, "limit": _APPS_PAGE})
            d = d if isinstance(d, dict) else {"items": d if isinstance(d, list) else []}
            items = d.get("items") or []
            meta = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
            count = meta.get("totalCount")
            total = count if isinstance(count, int) else offset + len(items)
            for a in items:
                if not isinstance(a, dict) or not a.get("id"):
                    continue
                app_id = str(a["id"])
                proj = a.get("project") if isinstance(a.get("project"), dict) else {}
                version = a.get("currentVersion") if isinstance(a.get("currentVersion"), dict) else {}
                inst = version.get("currentInstance") if isinstance(version.get("currentInstance"), dict) else {}
                out.append(BuiltApp(
                    id=app_id,
                    name=str(a.get("name") or "Untitled app"),
                    url=_viewer_url(str(a.get("url") or ""), app_id),
                    project_id=str(proj.get("id") or ""),
                    project_name=str(proj.get("name") or ""),
                    status=str(inst.get("status") or ""),
                ))
            if not items or len(out) >= _APPS_MAX:
                break
            offset += len(items)
        return out

    def delete_app_deployment(self, app_id: str) -> dict[str, Any]:
        """Delete a published App deployment: DELETE /api/apps/beta/apps/{app_id}. Required before the
        project can be archived — a project that still contains a published App is rejected the same
        way it is while it contains a workspace."""
        data = self._delete(f"{_APPS_PATH}/{app_id}")
        return data if isinstance(data, dict) else {"deleted": True}

    def app_status(self, app_id: str) -> str:
        """Deploy status of the app's current instance (Running / Failed / Preparing / '' if unknown),
        read from currentVersion.currentInstance.status. Used to poll a publish to a terminal state."""
        d = self._get(f"{_APPS_PATH}/{app_id}")
        if not isinstance(d, dict):
            return ""
        inst = (d.get("currentVersion") or {}).get("currentInstance") or {}
        return str(inst.get("status") or "")

    def app_visibility(self, app_id: str) -> str:
        """The app's current sharing setting, or "" when the API did not say.

        Read back rather than assumed, because Sage cannot keep setting it: `publish_app` sends
        GRANT_BASED, but a re-publish posts a *version*, which carries no visibility at all. Between
        two publishes the sharing can therefore be changed on the App's own settings page — the page
        Publish links to as "Manage settings in Domino" — and the publish guard in
        `sage.resources.publish_guard` is what this read exists for.

        UNVERIFIED field name: `visibility` is what the create call sends, and nothing has yet
        confirmed the detail response spells it the same way. "" is returned for anything
        unexpected, and the guard reads "" as not-open on purpose — see `open_visibility`.
        """
        d = self._get(f"{_APPS_PATH}/{app_id}")
        if not isinstance(d, dict):
            return ""
        return str(d.get("visibility") or "")

    def app_exists(self, app_id: str) -> bool:
        """Whether the App this id names is still there (#80).

        False for a 404 and NOTHING else: every other failure raises, because the one thing this
        answer is used for is deciding whether to create a second deployment, and "Domino did not
        answer" is not evidence that anything was deleted. The App's own settings page — the one
        Publish links to as "Manage settings in Domino" — is where it gets deleted from, so the id
        Sage recorded on the first publish can outlive the App it names.
        """
        try:
            self._get(f"{_APPS_PATH}/{app_id}")
        except NotFound:
            return False
        return True

    def app_manage_url(self, app_id: str, project_name: str) -> str | None:
        """Host-relative deep-link to the App's settings/overview page in Domino (tier, autoscaling,
        data, sharing), so 1-click Publish stays frictionless while the full native config is one
        click away. Returns a PATH only — DOMINO_API_HOST is the internal cluster address
        (nucleus-frontend…), not user-facing, so the UI resolves this against the browser's external
        origin via builderUrl().

        Shape: /u/{owner}/{project}/apps/{appId}/{appVersionId}/details/overview. The route is
        appId/appVersionId — NOT projectId/appId: publishing a real app and clicking the link 404'd
        on 2026-07-27, and the working UI URL used the beta app id + its currentVersion.id (the
        project id is not in the path at all). The version id is fetched from the app detail; if it
        can't be resolved we return None so the UI omits the link rather than showing a broken one."""
        if not app_id:
            return None
        version_id = ""
        try:
            d = self._get(f"{_APPS_PATH}/{app_id}")
            if isinstance(d, dict):
                version_id = str((d.get("currentVersion") or {}).get("id") or "")
        except Exception:  # best-effort — a broken settings link is worse than no link
            log.exception("app_manage_url: failed to resolve currentVersion for app %s", app_id)
        if not version_id:
            return None
        owner = self._username()
        proj = quote(project_name, safe="")
        return f"/u/{owner}/{proj}/apps/{app_id}/{version_id}/details/overview"

    def whoami(self) -> UserRef:
        """The identity this client's token acts as (GET /api/users/v1/self), cached per token.

        Attach polls ask who the viewer is every few seconds while a builder boots, so the cache
        earns its place. What it may not do is outlive the token it answered for.

        It used to be cached once and forever, on the reasoning that one client acts as one user
        for its lifetime. That stopped being true: `_headers` calls `self._token_provider()` on
        every request, and this object is a process-wide singleton (`orchestrator/app.py`). On the
        published Workbench App — a door serving many viewers — the first viewer warmed `_me`, and
        every viewer after them was handed that name. `Door.ensure_default` builds the Default
        Project name from it, so viewer B landed in viewer A's Project and A's Sage Builder.

        Keyed on the token now, so a different token is a different answer and the poll still costs
        one request. The key is the token itself rather than its `sub`: this client is the thing
        that would have to trust an unverified claim to read a `sub`, and it has no need to — two
        tokens for the same user cost one extra call, which is the cheap side of that trade.
        """
        token = self._token_provider()
        if self._me is None or self._me_for != token:
            u = self._get("/api/users/v1/self").get("user") or {}
            self._me = UserRef(
                id=str(u.get("id") or ""),
                name=str(u.get("userName") or u.get("loginId") or u.get("id") or ""),
            )
            self._me_for = token
        return self._me

    def _username(self) -> str:
        return self.whoami().name

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
        # A Built App DOES pin the revision Domino injected — unlike a builder, which takes the
        # Environment's active one. A deployed artifact should keep running the image it was
        # tested on; a builder should pick up a rebuilt Environment. Same field, opposite answer.
        if self._env_rev:
            version["environmentRevisionId"] = self._env_rev
        return version


@dataclass
class FakeControlPlane:
    """In-memory control plane for tests/local hub — no network."""

    projects: list[ProjectRef] = field(default_factory=list)
    workspaces: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    published: dict[str, PublishedApp] = field(default_factory=dict)  # app_id -> app
    app_projects: dict[str, str] = field(default_factory=dict)  # app_id -> project_id
    app_names: dict[str, str] = field(default_factory=dict)  # app_id -> the name publish asked for
    app_entry_points: dict[str, str] = field(default_factory=dict)  # app_id -> its entryPoint
    app_statuses: dict[str, str] = field(default_factory=dict)  # app_id -> deploy status (app_status)
    app_visibilities: dict[str, str] = field(default_factory=dict)  # app_id -> sharing setting
    built: list[BuiltApp] = field(default_factory=list)  # what list_all_apps answers (Gallery)
    saved_paths: list[str] = field(default_factory=list)  # open_paths a pre-stop save was driven for
    unready_paths: set[str] = field(default_factory=set)  # open_paths whose proxy still has no upstream
    probed_paths: list[str] = field(default_factory=list)  # open_paths a readiness probe was run for
    deleted_apps: list[str] = field(default_factory=list)  # app_ids a deployment delete was asked for
    user: UserRef = UserRef(id="user-1", name="tester")  # who the fake token acts as (the viewer)
    credentials: list[CredentialRef] = field(default_factory=lambda: [
        CredentialRef(id="cred-1", label="test PAT (github.com)", domain="github.com",
                      protocol="https", usable=True),
    ])
    dead_credentials: set[str] = field(default_factory=set)  # ids create_project refuses, as Domino would
    tried_credentials: list[str] = field(default_factory=list)  # ids create_project was called with, in order
    host: str = "github.com"
    _seq: int = 0

    def whoami(self) -> UserRef:
        return self.user

    @property
    def git_host(self) -> str:
        return self.host

    def git_credentials(self) -> list[CredentialRef]:
        return list(self.credentials)

    def create_project(self, name: str, *, git_url: str, git_credential_id: str = "cred-1",
                       branch: str = "main", description: str = "") -> ProjectRef:
        self.tried_credentials.append(git_credential_id)
        if git_credential_id in self.dead_credentials:
            # The shape Domino answers with: a 500 whose body blames repo access, not the
            # credential's existence (ADR-0033).
            raise RuntimeError(
                f"POST {_PROJECTS_PATH} -> 500: "
                f'{{"requestId":"fake-{len(self.tried_credentials)}","errors":["Cannot access Git '
                f'repository with URI: {git_url}. This may be due to invalid Git credentials."]}}'
            )
        self._seq += 1
        ref = ProjectRef(id=f"proj-{self._seq}", name=name, git_url=git_url)
        self.projects.append(ref)
        return ref

    def create_workspace(self, project_id: str, *, branch: str = "main") -> dict[str, Any]:
        ws = {
            "id": f"ws-{project_id}",
            "projectId": project_id,
            "ownerName": self.user.name,
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

    def workspace_http_ready(self, open_path: str) -> bool | None:
        self.probed_paths.append(open_path)
        return open_path not in self.unready_paths

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
        # Domino fixes the entry point when the App is created and a version cannot change it, so
        # which script an App was created to run is the fact a test has to be able to read back.
        self.app_names[app.id] = name
        self.app_entry_points[app.id] = entry_point
        return app

    def republish_app(self, app_id: str, *, git_ref_type: str = "head",
                      git_ref_value: str | None = None) -> PublishedApp:
        return self.published.get(app_id, PublishedApp(id=app_id, url=""))

    def list_project_apps(self, project_id: str) -> list[PublishedApp]:
        return [self.published[aid] for aid, pid in self.app_projects.items()
                if pid == project_id and aid in self.published]

    def list_all_apps(self) -> list[BuiltApp]:
        return list(self.built)

    def delete_app_deployment(self, app_id: str) -> dict[str, Any]:
        # Recorded as well as applied: "deleting an app that was never published makes no
        # control-plane call" is a claim about the call, and popping nothing looks the same as
        # never being asked.
        self.deleted_apps.append(app_id)
        self.published.pop(app_id, None)
        self.app_projects.pop(app_id, None)
        self.app_names.pop(app_id, None)
        self.app_entry_points.pop(app_id, None)
        return {"deleted": True}

    def app_manage_url(self, app_id: str, project_name: str) -> str | None:
        return f"/u/owner/{project_name}/apps/{app_id}/v-{app_id}/details/overview"

    def app_status(self, app_id: str) -> str:
        return self.app_statuses.get(app_id, "Running")

    def app_visibility(self, app_id: str) -> str:
        # Grant-based unless a test says otherwise: that is what publish_app sets, so it is what an
        # app Sage published and nobody re-shared actually has.
        return self.app_visibilities.get(app_id, "GRANT_BASED")

    def app_exists(self, app_id: str) -> bool:
        # `delete_app_deployment` already pops `published`, so deleting an App here is the same
        # move a person makes on its settings page in Domino — which is the whole of #80.
        return app_id in self.published
