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

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import naming
from .domino import ControlPlane, ProjectRef
from .github import RepoInfo, RepoNameConflict, RepoProvider
from .seed import seed_and_push

# The seed step: materialize the template into the new repo and push it. Injectable so fake-mode and
# tests can no-op it (a real git push would otherwise need a live remote).
Seeder = Callable[..., None]


@dataclass(frozen=True)
class AppCreated:
    project: ProjectRef
    repo: RepoInfo
    workspace: dict[str, Any]
    open_url: str | None


def workspace_open_url(ws: dict[str, Any]) -> str | None:
    """Host-relative path that opens a running workspace in the browser, assembled from the Domino
    WorkspaceDto: /{owner}/{project}/notebookSession/{runId}/ (same shape as preview.prefix).

    Returned as a path with no host on purpose: DOMINO_API_HOST is the internal cluster address and
    isn't browser-reachable, so the browser resolves this against the external origin the hub is
    already served from. Returns None if the response lacks the pieces (UI then shows a fallback)."""
    if not isinstance(ws, dict):
        return None
    owner = ws.get("ownerName")
    project = (ws.get("project") or {}).get("name") if isinstance(ws.get("project"), dict) else None
    session = ws.get("mostRecentSession") or {}
    run_id = session.get("executionId") or session.get("id") if isinstance(session, dict) else None
    if not (owner and project and run_id):
        return None
    return f"/{quote(str(owner))}/{quote(str(project))}/notebookSession/{run_id}/"


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

    def create_app(self, display_name: str) -> AppCreated:
        display_name = display_name.strip()
        if not display_name:
            raise ValueError("app name is required")

        repo = self._create_repo(display_name)
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

        ws = self._cp.create_workspace(project.id, branch=self._branch)
        return AppCreated(project=project, repo=repo, workspace=ws, open_url=workspace_open_url(ws))

    def open_app(self, project_id: str) -> dict[str, Any]:
        """Return a runnable workspace for an existing app: reuse a running one, else launch one."""
        for ws in self._cp.list_workspaces(project_id):
            state = str(ws.get("state") or ws.get("status") or "").lower()
            if state in ("", "running", "started", "active") or ws.get("isRunning"):
                return {"workspace": ws, "open_url": workspace_open_url(ws), "launched": False}
        ws = self._cp.create_workspace(project_id, branch=self._branch)
        return {"workspace": ws, "open_url": workspace_open_url(ws), "launched": True}
