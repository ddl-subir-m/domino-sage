"""HubService — the "New app" flow (Phase 4.1–4.2).

Ties the pieces together: pick a collision-free repo name, create the private repo (provider API),
seed+push the warm template, create a git-based Domino project pointing at the repo, and launch a
builder workspace. Also lists the caller's Sage apps and re-opens an existing one.

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
from .domino import ControlPlane, ProjectRef
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
    isn't browser-reachable, so the browser resolves this against the external origin the hub is
    already served from. Returns None if the pieces are missing (UI then shows a fallback).

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


class HubService:
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
        return self._cp.list_apps()

    def _create_repo(self, display_name: str) -> RepoInfo:
        base = naming.repo_base(display_name)
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

    def create_app(self, display_name: str) -> AppCreated:
        display_name = display_name.strip()
        if not display_name:
            raise ValueError("app name is required")

        repo = self._create_repo(display_name)
        # Roll back the repo if we fail before the project exists — otherwise it's an orphan. Once
        # the project is created the app is real, so a later (workspace) failure must NOT delete it.
        try:
            self._seed(
                repo.clone_url, self._template, branch=self._branch,
                token_provider=self._push_token_provider,
            )
            # Project keeps the human name; fall back to the (unique) repo name if Domino rejects it
            # (e.g. a duplicate project name).
            try:
                project = self._cp.create_project(display_name, git_url=repo.clone_url, branch=self._branch)
            except Exception:  # noqa: BLE001 — v4 create-error shape unconfirmed; retry with a unique name
                fallback = repo.full_name.split("/", 1)[-1]
                project = self._cp.create_project(fallback, git_url=repo.clone_url, branch=self._branch)
        except Exception:
            self._rollback_repo(repo)
            raise

        ws = self._cp.create_workspace(project.id, branch=self._branch)
        return AppCreated(project=project, repo=repo, workspace=ws, open_url=workspace_open_url(ws, project.name))

    def open_app(self, project_id: str) -> dict[str, Any]:
        """Return a runnable workspace for an existing app: reuse a running one, else launch one."""
        # The workspace DTO has no project name (the URL slug), so resolve it from the app list.
        name = next((a.name for a in self._cp.list_apps() if a.id == project_id), None)
        for ws in self._cp.list_workspaces(project_id):
            state = str(ws.get("state") or ws.get("status") or "").lower()
            if state in ("", "running", "started", "active") or ws.get("isRunning"):
                return self._open_result(ws, name, launched=False)
        ws = self._cp.create_workspace(project_id, branch=self._branch)
        return self._open_result(ws, name, launched=True)

    @staticmethod
    def _open_result(ws: dict[str, Any], name: str | None, *, launched: bool) -> dict[str, Any]:
        return {
            "workspace": ws,
            "open_url": workspace_open_url(ws, name),
            "running": workspace_is_running(ws),
            "launched": launched,
        }

    def workspace_status(self, project_id: str, workspace_id: str | None = None) -> dict[str, Any]:
        """Current running-state + open URL for an app's workspace — the UI polls this after a launch
        so it only opens the builder once the session is actually running."""
        name = next((a.name for a in self._cp.list_apps() if a.id == project_id), None)
        workspaces = [w for w in self._cp.list_workspaces(project_id) if isinstance(w, dict)]
        ws = None
        if workspace_id:
            ws = next((w for w in workspaces if w.get("id") == workspace_id), None)
        if ws is None and workspaces:  # newest by creation time
            ws = max(workspaces, key=lambda w: w.get("createdAt") or "")
        if ws is None:
            return {"running": False, "open_url": None, "state": None}
        return {
            "running": workspace_is_running(ws),
            "open_url": workspace_open_url(ws, name),
            "state": ws.get("state") or ws.get("status"),
        }
