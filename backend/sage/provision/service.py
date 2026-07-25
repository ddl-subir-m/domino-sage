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
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import naming
from .domino import BUILDER_WORKSPACE_NAME, ControlPlane, ProjectRef
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


# v4 workspace `state` values that mean "stopped but relaunchable in place" (vs. running, or the
# terminal deleted/failed states that warrant a fresh workspace). Matched case-insensitively.
_STOPPED_STATES = frozenset({"stopped", "stopping"})

# States a workspace can be deleted from. Domino rejects a delete on a live workspace with 500
# "Workspace cannot be deleted from current state", so delete_app stops it and waits for one of
# these before deleting. Matched case-insensitively.
_REMOVABLE_STATES = frozenset({"stopped", "failed", "error"})

# A delete on a Stopped workspace keeps failing transiently ("Workspace delete wasn't completed
# successfully. Please try again.") for a while after the stop — Domino's delete is async and needs
# time to settle. So retry over a generous window (~75s), treating the workspace having disappeared
# from the project as the real success signal (the async delete completes even when the DELETE call
# reports failure).
_DELETE_RETRIES = 15
_DELETE_RETRY_DELAY = 5.0


def is_builder_workspace(ws: dict[str, Any]) -> bool:
    """True unless the workspace is clearly a non-builder session — a VS Code / Jupyter workspace a
    user opened in the same project. The list DTO carries no tool info, so we discriminate by name:
    the hub names its builders BUILDER_WORKSPACE_NAME. An unnamed workspace is treated as a builder
    (backward-compatible), so only a workspace with a different explicit name is excluded."""
    if not isinstance(ws, dict):
        return False
    name = ws.get("name")
    return not name or name == BUILDER_WORKSPACE_NAME


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
        """Return a runnable workspace for an existing app: reuse a running one, else restart a
        stopped one in place, else launch a fresh one."""
        # The workspace DTO has no project name (the URL slug), so resolve it from the app list.
        name = next((a.name for a in self._cp.list_apps() if a.id == project_id), None)
        workspaces = [w for w in self._cp.list_workspaces(project_id)
                      if isinstance(w, dict) and is_builder_workspace(w)]
        for ws in workspaces:
            state = str(ws.get("state") or ws.get("status") or "").lower()
            if state in ("", "running", "started", "active") or ws.get("isRunning"):
                return self._open_result(ws, name, launched=False)
        # Resume the newest stopped workspace in place rather than piling up new ones. The session
        # DTO carries no owner/open-url fields, so return it launched=True and let the UI's status
        # poll surface the running URL (same path as a fresh create).
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
        workspaces = [w for w in self._cp.list_workspaces(project_id)
                      if isinstance(w, dict) and is_builder_workspace(w)]
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

    def _save_before_stop(self, ws: dict[str, Any], name: str | None) -> None:
        """Stop-safe: before a running builder is stopped, ask it to commit in-progress edits, pull +
        resolve any conflicts, and push, so no uncommitted work is lost. Best-effort — a failed or
        unreachable save must never block the stop; the builder's own shutdown hook is the backstop."""
        if not workspace_is_running(ws):
            return  # a stopped builder has nothing running to save
        open_path = workspace_open_url(ws, name)
        if not open_path:
            return  # can't address the builder (missing owner/session) — nothing to call
        try:
            self._cp.save_workspace_work(open_path)
        except Exception:  # noqa: BLE001 — best-effort; the stop proceeds regardless
            log.warning("pre-stop save failed for workspace %s; stopping anyway", ws.get("id"), exc_info=True)

    def stop_app(self, project_id: str, workspace_id: str | None = None) -> dict[str, Any]:
        """Stop an app's builder so it stops consuming compute. Targets the given workspace, else the
        newest running one (falling back to the newest overall). Returns {stopped, workspace_id}."""
        workspaces = [w for w in self._cp.list_workspaces(project_id)
                      if isinstance(w, dict) and is_builder_workspace(w)]
        ws = None
        if workspace_id:
            ws = next((w for w in workspaces if w.get("id") == workspace_id), None)
        if ws is None:
            running = [w for w in workspaces if workspace_is_running(w)]
            pool = running or workspaces
            if pool:
                ws = max(pool, key=lambda w: w.get("createdAt") or "")
        if ws is None:
            return {"stopped": False, "workspace_id": None, "detail": "no workspace to stop"}
        name = next((a.name for a in self._cp.list_apps() if a.id == project_id), None)
        self._save_before_stop(ws, name)
        wid = ws.get("id")
        self._cp.stop_workspace(project_id, str(wid))
        return {"stopped": True, "workspace_id": wid}

    def publish_app(self, project_id: str) -> dict[str, Any]:
        """Publish an app's built code as a live, shareable Domino App. The first publish creates and
        launches the App; a later publish deploys a NEW version to the same App so its URL stays
        stable. Deploys the latest committed code on the project's default branch (gitRef "head"), so
        it's independent of whether the builder is running. Returns {published, app_id, url,
        republished}."""
        name = next((a.name for a in self._cp.list_apps() if a.id == project_id), None)
        existing = self._cp.find_project_app(project_id)
        if existing and existing.id:  # already published — ship a new version, keep the URL
            app = self._cp.republish_app(existing.id)
            out = {"published": True, "app_id": app.id, "url": app.url or existing.url, "republished": True}
        else:
            app = self._cp.publish_app(project_id, name=name or "Sage app")
            out = {"published": True, "app_id": app.id, "url": app.url, "republished": False}
        # Deep-link to Domino's native App settings (tier, autoscaling, data, sharing) — 1-click
        # publish stays frictionless while the full config is one click away.
        out["manage_url"] = self._cp.app_manage_url(project_id, app.id, name or "")
        return out

    def delete_app(self, project_id: str) -> dict[str, Any]:
        """Delete an app: stop any running builder, then archive its Domino project (soft delete —
        a Domino admin can restore it). The GitHub repo is intentionally kept."""
        name = next((a.name for a in self._cp.list_apps() if a.id == project_id), None)
        # The project can't be archived while it still contains a workspace (even a stopped one), so
        # every workspace is deleted first. If a workspace can't be removed, raise the real reason —
        # otherwise the archive fails downstream with a misleading "contains N workspace" 500.
        failures: list[str] = []
        for ws in self._cp.list_workspaces(project_id):
            if not (isinstance(ws, dict) and ws.get("id")):
                continue
            if ws.get("deleted"):
                continue  # already gone — doesn't count against the archive
            wid = str(ws["id"])
            try:
                self._remove_workspace(project_id, ws, name)
            except Exception as e:  # noqa: BLE001 — collect, so one bad workspace reports clearly
                failures.append(f"{wid}: {e}")
        if failures:
            raise RuntimeError("couldn't remove workspace(s) before archiving — " + " | ".join(failures))
        self._cp.archive_project(project_id)
        return {"deleted": True}

    def _remove_workspace(self, project_id: str, ws: dict[str, Any], name: str | None) -> None:
        """Delete a workspace so its project can be archived. Domino rejects a delete on a live
        workspace ("cannot be deleted from current state"), so if it isn't already in a removable
        state, save + stop the builder and wait for it to reach Stopped before deleting."""
        wid = str(ws["id"])
        state = str(ws.get("state") or ws.get("status") or "").lower()
        if state not in _REMOVABLE_STATES:
            self._save_before_stop(ws, name)  # push in-progress work before it's gone (no-op if not running)
            try:
                self._cp.stop_workspace(project_id, wid)
            except Exception:  # noqa: BLE001 — best-effort; it may already be stopping
                pass
            self._wait_until_removable(project_id, wid)
        # The delete is async and can 500 transiently ("delete wasn't completed, please try again");
        # retry, and treat a workspace that has since disappeared as done.
        last: Exception | None = None
        for attempt in range(_DELETE_RETRIES):
            try:
                self._cp.delete_workspace(project_id, wid)
                return
            except Exception as e:  # noqa: BLE001 — retry the flaky delete
                last = e
                if self._workspace_gone(project_id, wid):
                    return
                if attempt < _DELETE_RETRIES - 1:
                    time.sleep(_DELETE_RETRY_DELAY)
        if self._workspace_gone(project_id, wid):
            return
        raise last if last else RuntimeError(f"couldn't delete workspace {wid}")

    def _workspace_gone(self, project_id: str, workspace_id: str) -> bool:
        """True once the workspace no longer shows up in the project (or is marked deleted) — the
        async delete having actually taken effect even if the DELETE call reported failure."""
        ws = next((w for w in self._cp.list_workspaces(project_id)
                   if isinstance(w, dict) and str(w.get("id")) == workspace_id), None)
        return ws is None or bool(ws.get("deleted"))

    def _wait_until_removable(self, project_id: str, workspace_id: str,
                             tries: int = 20, delay: float = 3.0) -> None:
        """Poll until the workspace reaches a state a delete is accepted from (or disappears). Best-
        effort: on timeout, fall through and let the delete surface Domino's real state error."""
        for attempt in range(tries):
            ws = next((w for w in self._cp.list_workspaces(project_id)
                       if isinstance(w, dict) and str(w.get("id")) == workspace_id), None)
            if ws is None or ws.get("deleted"):
                return
            if str(ws.get("state") or ws.get("status") or "").lower() in _REMOVABLE_STATES:
                return
            if attempt < tries - 1:
                time.sleep(delay)
