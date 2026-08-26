"""ProvisionService — the git-backed Sage Project flow behind the Workbench door (#43, #44).

Ties the pieces together: pick a collision-free repo name, create the private repo (provider API),
seed+push the warm template, create a git-based Domino project pointing at the repo, and launch a
Sage Builder workspace. Also lists the caller's Sage Projects and re-opens an existing one.

This is the seam the Workbench door sits on: a viewer's Default Project, "New project", and
switching Projects are all `create_app` / `open_app` / `list_apps` against a real control plane.
There is no Hub App and no Hub UI — Publish, Stop and Delete stay in the Sage Builder, where the
person doing them already is.

Every collaborator is behind a Protocol so the whole flow runs against fakes in tests with no
network. The one piece that needs live verification on Domino is turning a created workspace into a
browser URL (open_url) — the v4 workspace-create response fields aren't nailed down; we derive
best-effort and mark it so.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import naming
from .domino import BUILDER_WORKSPACE_NAME, BuiltApp, ControlPlane, ProjectRef
from .github import RepoInfo, RepoNameConflict, RepoProvider
from .seed import seed_and_push

log = logging.getLogger("sage.provision.service")

# The seed step: materialize the template into the new repo and push it. Injectable so fake-mode and
# tests can no-op it (a real git push would otherwise need a live remote).
Seeder = Callable[..., None]


@dataclass(frozen=True)
class AppCreated:
    project: ProjectRef
    repo: RepoInfo
    workspace: dict[str, Any]
    open_url: str | None


def workspace_open_url(ws: dict[str, Any], project_name: str | None = None) -> str | None:
    """Host-relative path that opens a running workspace in the browser:
    /{owner}/{project}/notebookSession/{runId}/ (same shape as preview.prefix).

    Returned as a path with no host on purpose: DOMINO_API_HOST is the internal cluster address and
    isn't browser-reachable, so the browser resolves this against the external origin the caller is
    already served from. Returns None if the pieces are missing (the caller then shows a fallback).

    owner + runId come from the v4 WorkspaceDto (ownerName, mostRecentSession.executionId). The DTO's
    `project` field is null on create/open, so the project name — the URL slug — must be passed in by
    the caller, who knows it from the ProjectRef."""
    if not isinstance(ws, dict):
        return None
    owner = ws.get("ownerName")
    project = project_name or (
        (ws.get("project") or {}).get("name") if isinstance(ws.get("project"), dict) else None
    )
    session = ws.get("mostRecentSession") or {}
    run_id = session.get("executionId") or session.get("id") if isinstance(session, dict) else None
    if not (owner and project and run_id):
        return None
    return f"/{quote(str(owner))}/{quote(str(project))}/notebookSession/{run_id}/"


# v4 workspace `state` values that mean "stopped but relaunchable in place" (vs. running, or the
# terminal deleted/failed states that warrant a fresh workspace). Matched case-insensitively.
_STOPPED_STATES = frozenset({"stopped", "stopping"})


def is_builder_workspace(ws: dict[str, Any]) -> bool:
    """True unless the workspace is clearly a non-builder session — a VS Code / Jupyter workspace a
    user opened in the same project. The list DTO carries no tool info, so we discriminate by name:
    Sage names its builders BUILDER_WORKSPACE_NAME. An unnamed workspace is treated as a builder
    (backward-compatible), so only a workspace with a different explicit name is excluded."""
    if not isinstance(ws, dict):
        return False
    name = ws.get("name")
    return not name or name == BUILDER_WORKSPACE_NAME


def is_owned_by(ws: dict[str, Any], owner: str | None) -> bool:
    """True when this workspace belongs to `owner` — or when the caller isn't filtering by owner.

    A Project can hold several people's Sage Builders. Reusing or resuming a collaborator's would
    put two people in one container and hand this viewer someone else's session, so attaching is
    always scoped to the viewer (#47). `ownerName` on the workspace DTO is the Domino username, the
    same value `whoami()` returns. A workspace with no owner is nobody's to claim.
    """
    if owner is None:
        return True
    name = ws.get("ownerName")
    return bool(name) and str(name) == owner


def workspace_is_running(ws: dict[str, Any]) -> bool:
    """True once the workspace's session is actually running — i.e. safe to open in the browser.

    The coarse workspace `state` flips to "Started" while the session is still booting, so prefer
    the session's sessionStatusInfo.isRunning; fall back to `state` only when that's absent."""
    if not isinstance(ws, dict):
        return False
    session = ws.get("mostRecentSession") or {}
    info = session.get("sessionStatusInfo") if isinstance(session, dict) else None
    if isinstance(info, dict) and "isRunning" in info:
        return bool(info.get("isRunning"))
    return str(ws.get("state") or ws.get("status") or "").lower() == "running"


class ProvisionService:
    def __init__(
        self,
        control_plane: ControlPlane,
        repo_provider: RepoProvider,
        template: Path,
        *,
        branch: str = "main",
        name_limit: int = 50,
        seed: Seeder = seed_and_push,
        push_token_provider: Callable[[], str] | None = None,
    ) -> None:
        self._cp = control_plane
        self._repo = repo_provider
        self._template = Path(template)
        self._branch = branch
        self._name_limit = name_limit
        self._seed = seed
        self._push_token_provider = push_token_provider

    def list_apps(self) -> list[ProjectRef]:
        """The caller's Sage Projects — the control plane keeps only git-backed projects whose repo
        name starts with `sage-`, so an ordinary Domino project never shows up as Sage work."""
        return self._cp.list_apps()

    def list_built_apps(self) -> list[BuiltApp]:
        """The Built Apps to show this viewer in the Gallery (#48).

        The control plane's app list is global — every App on the deployment — so it is narrowed by
        the Projects this viewer can list, and that list is already sage-* only. Both halves matter:
        the Project list is Domino answering "may they see it", and the sage-* prefix is what
        "published from a Sage Builder" means. The Workbench App falls out for free — Sage's own
        project is not a sage-* one.

        An App in a sage-* Project the viewer cannot list is left out. That is the conservative
        miss, and the alternative is offering a card that opens on a permission error.
        """
        mine = {p.id for p in self.list_apps()}
        return [a for a in self._cp.list_all_apps() if a.project_id in mine]

    def _create_repo(self, base: str, display_name: str) -> RepoInfo:
        last: Exception | None = None
        for name in naming.candidates(base, self._name_limit):
            try:
                return self._repo.create_repo(name, description=f"Sage app: {display_name}", private=True)
            except RepoNameConflict as e:  # name taken — try the next -N candidate
                last = e
        raise RuntimeError(f"could not find a free repo name under {base!r} after {self._name_limit} tries") from last

    def _rollback_repo(self, repo: RepoInfo) -> None:
        """Best-effort delete of a just-created repo when provisioning fails before the project
        exists. Never masks the original failure — a failed cleanup is only logged."""
        try:
            self._repo.delete_repo(repo.full_name)
            log.info("rolled back orphaned repo %s", repo.full_name)
        except Exception:
            log.warning("couldn't roll back repo %s (delete it manually)", repo.full_name, exc_info=True)

    def create_app(self, display_name: str, *, name: str | None = None) -> AppCreated:
        """Provision a Project: a private `sage-*` repo, a git-based Domino project of that same
        name, and this caller's Sage Builder in it.

        The Domino project is named after the REPO, never after what the person typed (#46). Both
        halves of Sage look a Project up by that name — the door finds a viewer's Default with
        `naming.default_project_name`, and a `-N` collision suffix has to land on both sides — and a
        typed name is neither unique nor stable. The readable name is seeded into the repo instead,
        as the chip overlay the new builder will read, and sent as the project's description so the
        Project is still findable in Domino's own UI.

        `name` is an already-`sage-`-prefixed name to use instead of a slug of `display_name`; the
        door passes the Default's computed name.
        """
        display_name = display_name.strip()
        if not display_name:
            raise ValueError("app name is required")

        repo = self._create_repo(name or naming.repo_base(display_name), display_name)
        # Roll back the repo if we fail before the project exists — otherwise it's an orphan. Once
        # the project is created the app is real, so a later (workspace) failure must NOT delete it.
        try:
            self._seed(
                repo.clone_url, self._template, branch=self._branch,
                token_provider=self._push_token_provider,
                settings={"displayName": display_name},
            )
            # The repo name, not `name`: _create_repo may have taken a -N candidate, and the project
            # has to carry the same suffix.
            repo_name = repo.full_name.split("/", 1)[-1]
            project = self._cp.create_project(
                repo_name, git_url=repo.clone_url, branch=self._branch, description=display_name)
        except Exception:
            self._rollback_repo(repo)
            raise

        ws = self._cp.create_workspace(project.id, branch=self._branch)
        return AppCreated(project=project, repo=repo, workspace=ws, open_url=workspace_open_url(ws, project.name))

    def open_app(self, project_id: str, *, owner: str | None = None) -> dict[str, Any]:
        """Return a runnable workspace in an existing Project: reuse a running one, else restart a
        stopped one in place, else launch a fresh one.

        `owner` scopes reuse and resume to one person's builders (#47) — pass the viewer's Domino
        username and a collaborator's builder in the same Project is left running and untouched.
        A fresh launch is always the caller's own, so it needs no filter.
        """
        # The workspace DTO has no project name (the URL slug), so resolve it from the app list.
        name = next((a.name for a in self._cp.list_apps() if a.id == project_id), None)
        workspaces = [w for w in self._cp.list_workspaces(project_id)
                      if isinstance(w, dict) and is_builder_workspace(w) and is_owned_by(w, owner)]
        for ws in workspaces:
            state = str(ws.get("state") or ws.get("status") or "").lower()
            if state in ("", "running", "started", "active") or ws.get("isRunning"):
                return self._open_result(ws, name, launched=False)
        # Resume the newest stopped workspace in place rather than piling up new ones. The session
        # DTO carries no owner/open-url fields, so return it launched=True and let the caller's
        # status poll surface the running URL (same path as a fresh create).
        # The v4 list DTO (WorkspaceDto) has NO isRestartable field — that lives only on the separate
        # WorkspaceSummary schema — so restartability is derived from `state`: anything stopped and
        # not deleted can be relaunched. (A deleted/failed workspace falls through to a fresh create.)
        restartable = [
            w for w in workspaces
            if w.get("id") and not w.get("deleted")
            and str(w.get("state") or w.get("status") or "").lower() in _STOPPED_STATES
        ]
        if restartable:
            target = max(restartable, key=lambda w: w.get("createdAt") or "")
            self._cp.resume_workspace(project_id, str(target["id"]))
            return self._open_result(target, name, launched=True)
        ws = self._cp.create_workspace(project_id, branch=self._branch)
        return self._open_result(ws, name, launched=True)

    def workspace_status(
        self, project_id: str, workspace_id: str | None = None, *, owner: str | None = None
    ) -> dict[str, Any]:
        """Current running-state + open URL for a Project's workspace — the caller polls this after a
        launch so it only sends the viewer in once the session is actually running.

        `owner` scopes the answer the same way `open_app` scopes reuse: without it, a collaborator's
        newer builder could answer for the viewer's and hand back a URL that is not theirs to open.
        """
        name = next((a.name for a in self._cp.list_apps() if a.id == project_id), None)
        workspaces = [w for w in self._cp.list_workspaces(project_id)
                      if isinstance(w, dict) and is_builder_workspace(w) and is_owned_by(w, owner)]
        ws = None
        if workspace_id:
            ws = next((w for w in workspaces if w.get("id") == workspace_id), None)
        if ws is None:
            # Prefer a running workspace so the card reflects a live builder even when a stopped
            # leftover was created more recently (the earlier relaunch bug piled these up); else newest.
            running = [w for w in workspaces if workspace_is_running(w)]
            pool = running or workspaces
            if pool:
                ws = max(pool, key=lambda w: w.get("createdAt") or "")
        if ws is None:
            return {"running": False, "open_url": None, "state": None, "workspace_id": None}
        return {
            "running": workspace_is_running(ws),
            "open_url": workspace_open_url(ws, name),
            "state": ws.get("state") or ws.get("status"),
            "workspace_id": ws.get("id"),
        }

    @staticmethod
    def _open_result(ws: dict[str, Any], name: str | None, *, launched: bool) -> dict[str, Any]:
        return {
            "workspace": ws,
            "open_url": workspace_open_url(ws, name),
            "running": workspace_is_running(ws),
            "launched": launched,
        }
