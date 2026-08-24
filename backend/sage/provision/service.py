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

import json
import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from ..resources.bindings import KIND_DATA_SOURCE, KIND_LLM_ALIAS, Binding, parse_bindings
from ..resources.builtapp import catalog_problems
from ..resources.provider import DataSource, FakeResourceProvider, LlmAlias, ResourceProvider
from ..resources.publish_guard import (
    PublishRefused,
    data_source_bindings,
    publish_problems,
    vendor_model_problems,
    vendor_model_warning,
)
from . import naming
from .domino import BUILDER_WORKSPACE_NAME, ControlPlane, ProjectRef, PublishedApp
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

# Published-app deploy status -> terminal phase (see publish_status). Anything else is still deploying.
_RUNNING_STATES = frozenset({"running"})
_FAILED_STATES = frozenset({"failed", "error"})

# A delete on a Stopped workspace keeps failing transiently ("Workspace delete wasn't completed
# successfully. Please try again.") for a while after the stop — Domino's delete is async and needs
# time to settle. So retry over a generous window (~75s), treating the workspace having disappeared
# from the project as the real success signal (the async delete completes even when the DELETE call
# reports failure).
_DELETE_RETRIES = 15
_DELETE_RETRY_DELAY = 5.0


_ENTRY_POINT = "app.sh"  # the entry script Domino runs to serve a published app (repo root)
_BINDINGS_PATH = ".sage/bindings.json"  # the app's committed Resource list, as the workspace writes it
_QUERIES_PATH = ".sage/queries.json"    # the query catalog the creator's agent wrote (#15)


def _repo_full_name(git_url: str | None) -> str | None:
    """owner/name from an https clone URL (https://github.com/owner/name.git -> owner/name)."""
    if not git_url:
        return None
    path = urlparse(git_url).path.strip("/")
    return path[:-4] if path.endswith(".git") else (path or None)


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
        resources: ResourceProvider | None = None,
    ) -> None:
        self._cp = control_plane
        self._repo = repo_provider
        self._template = Path(template)
        self._branch = branch
        self._name_limit = name_limit
        self._seed = seed
        self._push_token_provider = push_token_provider
        # Only ever asked for Data Sources, and only when an app being published reads one (#12).
        # A fake by default, like the orchestrator's: a hub with no Domino behind it has no listing
        # to check against, and the fake answers about apps that have picked nothing anyway.
        self._resources = resources or FakeResourceProvider()

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
            except Exception:
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
        except Exception:
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
        ref = next((a for a in self._cp.list_apps() if a.id == project_id), None)
        name = ref.name if ref else None
        # Fail fast if the deploy entry script isn't on the branch. Apps seeded before app.sh was
        # added to the template have no entry script, so Domino fails the deploy opaquely ("entry
        # script './app.sh' not found") — surface a clear, actionable message instead.
        full = _repo_full_name(ref.git_url) if ref else None
        if full:
            try:
                has_entry = self._repo.file_exists(full, _ENTRY_POINT, self._branch)
            except Exception:  # provider hiccup — don't block publish on a best-effort pre-check
                log.exception("publish: entry-script pre-check failed; proceeding")
                has_entry = True
            if not has_entry:
                raise RuntimeError(
                    f"'{_ENTRY_POINT}' is missing from the {self._branch} branch, so Domino has no entry "
                    "script to run. This app was created before Sage added the publish entry script — "
                    f"recreate the app, or add {_ENTRY_POINT} to the repo root and rebuild."
                )
        existing = self._cp.find_project_app(project_id)
        self._refuse_unsafe_publish(full, existing)
        if existing and existing.id:  # already published — ship a new version, keep the URL
            app = self._cp.republish_app(existing.id)
            out = {"published": True, "app_id": app.id, "url": app.url or existing.url, "republished": True}
        else:
            app = self._cp.publish_app(project_id, name=name or "Sage app")
            out = {"published": True, "app_id": app.id, "url": app.url, "republished": False}
        # Deep-link to Domino's native App settings (tier, autoscaling, data, sharing) — 1-click
        # publish stays frictionless while the full config is one click away.
        out["manage_url"] = self._cp.app_manage_url(app.id, name or "")
        return out

    def _refuse_unsafe_publish(self, full_name: str | None, existing: PublishedApp | None) -> None:
        """Refuse a publish that would re-export a Data Source (#12), the same two questions the
        builder asks in `Orchestrator._refuse_unsafe_publish`.

        The hub publishes without a builder, so the app's Resource list is read from the committed
        manifest on the default branch rather than from a workspace. That read fails OPEN — an app
        with no manifest is the ordinary case (every app built before #11), and a repo the provider
        could not answer for is not evidence that the app reads anything. Once the manifest DOES
        name a Data Source, the guard is the builder's: an unanswerable credential refuses.
        """
        recorded = parse_bindings(self._read_bindings(full_name))
        bindings = data_source_bindings(recorded)
        if not bindings:
            return
        try:
            sources: list[DataSource] | None = self._resources.list_data_sources()
        except Exception:
            log.exception("publish: couldn't list Data Sources to check the credential guard")
            sources = None
        visibility: str | None = ""   # "" = nothing published yet, so nothing to read
        if existing and existing.id:
            try:
                visibility = self._cp.app_visibility(existing.id)
            except Exception:
                log.exception("publish: couldn't read the app's visibility")
                visibility = None
        problems = publish_problems(bindings, sources, visibility) + \
            vendor_model_problems(recorded, self._aliases_for_guard(recorded))
        if problems:
            raise PublishRefused(problems)

    def publish_check(self, project_id: str) -> dict[str, Any]:
        """What the published app is going to refuse to answer, asked BEFORE the hub publishes (#27).

        The same question `Orchestrator.publish_check` asks, answered for the creator who never
        opened the builder — which is precisely the person #26 was written for and the one route that
        did not tell them. It informs and does not block: a broken query is one screen of an app that
        may be fine everywhere else, and the creator can publish straight past it, exactly as they can
        from the rail.

        The hub has no workspace, so the two manifests come off the default branch rather than off
        disk, and are written to a temp dir for the checker to read. That is deliberate. The
        alternative — a sibling of `catalog_problems` taking raw payloads — would need `load_queries`
        to grow a seam that does not read from disk, and it does not have one on purpose: it ships in
        the creator's app repo and answers to the app, not to Sage. A temp dir per hub publish is the
        cheaper thing to spend, and it keeps ONE implementation and one call signature.

        Everything here fails open, and `checked` says which kind of silence it is. Measured against
        real GitHub on 2026-08-22, because the distinction is finer than it first looks:

          - a project with no git_url, or a template with no `serve.py`  -> `checked: false`
          - a token that is bad or lacks scope (401/403, which `read_file` RAISES) -> `checked: false`
          - an app with no catalog, and a repo that 404s                 -> `checked: true`

        That last row merges two things on purpose. GitHub answers 404 for a missing file, a missing
        ref and a missing repo alike, so `read_file` cannot tell them apart and neither can this. It
        costs nothing: a repo that is not there has no catalog to check either, and the publish it
        precedes fails loudly on its own entry-script pre-check rather than quietly. Buying the
        distinction would mean a second round trip on every hub publish for a field no user sees.
        """
        ref = next((a for a in self._cp.list_apps() if a.id == project_id), None)
        full = _repo_full_name(ref.git_url) if ref else None
        if not full:
            return {"checked": False, "queries": [], "models": None}
        try:
            queries = self._repo.read_file(full, _QUERIES_PATH, self._branch)
            # `read_file` answers None for a file that is not there and raises for a repo it could
            # not reach, so the two stay tellable apart here even though they look the same later.
            if queries is None:
                return {"checked": True, "queries": [], "models": self._vendor_model_warning(full)}
            bindings = self._repo.read_file(full, _BINDINGS_PATH, self._branch)
        except Exception:
            log.exception("publish-check: couldn't read the manifests from %s", full)
            return {"checked": False, "queries": [], "models": None}
        with tempfile.TemporaryDirectory(prefix="sage-publish-check-") as tmp:
            root = Path(tmp)
            (root / _QUERIES_PATH).parent.mkdir(parents=True, exist_ok=True)
            (root / _QUERIES_PATH).write_text(queries, encoding="utf-8")
            # A missing bindings manifest is written as missing rather than as an empty one: the
            # published app would read it the same way, and the point of this check is to say what
            # THAT app will say.
            if bindings is not None:
                (root / _BINDINGS_PATH).write_text(bindings, encoding="utf-8")
            problems = catalog_problems(self._template, root)
        return {"checked": problems is not None, "queries": problems or [],
                "models": self._vendor_model_warning(full)}

    def _aliases_for_guard(self, recorded: list[Binding]) -> list[LlmAlias] | None:
        """The Alias listing the vendor-model guard needs, or None when it could not be fetched.

        The builder's `_aliases_for_guard`, asked from the hub. Same rule, same reason: fetched only
        when a bound store's rows are marked sensitive, so an ordinary publish costs no gateway call.

        The hub can ask this question at all only because the judgement rides in the committed
        manifest — `.sage/samples.json`, which holds the rows, is gitignored and never reaches a
        repo. An app whose manifest predates that stamp reads as not sensitive, which is the same
        answer the builder gave it before this shipped.
        """
        if not any(b.kind == KIND_DATA_SOURCE and b.sensitive for b in recorded):
            return []
        try:
            return self._resources.list_llm_aliases()
        except Exception:
            log.exception("publish: couldn't list LLM Aliases to check where sensitive rows would go")
            return None

    def _vendor_model_warning(self, full_name: str | None) -> str | None:
        """Where this app's rows go, for a creator who never opened the builder (#35). None when silent.

        The same sentence `Orchestrator._vendor_model_warning` produces, from the repo rather than
        from a workspace — the #27 rule that both routes say the same words, not a paraphrase.
        Fails open at every step, as everything on this route does.
        """
        try:
            recorded = parse_bindings(self._read_bindings(full_name))
            if not any(b.kind == KIND_LLM_ALIAS for b in recorded):
                return None
            return vendor_model_warning(recorded, self._resources.list_llm_aliases())
        except Exception:
            log.exception("publish check: couldn't work out where this app's rows would go")
            return None

    def _read_bindings(self, full_name: str | None) -> list[dict]:
        """The app's committed Resource list, or [] when there isn't one to read."""
        if not full_name:
            return []
        try:
            raw = self._repo.read_file(full_name, _BINDINGS_PATH, self._branch)
        except Exception:
            log.exception("publish: couldn't read %s from %s", _BINDINGS_PATH, full_name)
            return []
        if not raw:
            return []
        try:
            return json.loads(raw)
        except ValueError:
            log.warning("publish: %s in %s is not valid JSON", _BINDINGS_PATH, full_name)
            return []

    def publish_status(self, app_id: str) -> dict[str, Any]:
        """Deploy status of a published app, so the hub can poll after Publish and show whether it
        went live or failed (the deploy is async — npm ci + build + serve takes minutes). Maps the
        raw instance status to a phase: running (live) / failed / pending (still deploying)."""
        raw = self._cp.app_status(app_id)
        s = raw.lower()
        if s in _RUNNING_STATES:
            phase = "running"
        elif s in _FAILED_STATES:
            phase = "failed"
        else:
            phase = "pending"
        return {"app_id": app_id, "status": raw, "phase": phase}

    def delete_app(self, project_id: str) -> dict[str, Any]:
        """Delete an app: stop any running builder, then archive its Domino project (soft delete —
        a Domino admin can restore it). The GitHub repo is intentionally kept."""
        name = next((a.name for a in self._cp.list_apps() if a.id == project_id), None)
        # A project that still contains a published Domino App can't be archived (same failure mode as
        # a lingering workspace), so every published App is deleted first. Collect failures and raise
        # the real per-app reason rather than letting archive fail with a misleading message.
        app_failures: list[str] = []
        for pub in self._cp.list_project_apps(project_id):
            try:
                self._cp.delete_app_deployment(pub.id)
            except Exception as e:
                app_failures.append(f"{pub.id}: {e}")
        if app_failures:
            raise RuntimeError("couldn't delete published App(s) before archiving — " + " | ".join(app_failures))
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
            except Exception as e:
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
            except Exception:
                pass
            self._wait_until_removable(project_id, wid)
        # The delete is async and can 500 transiently ("delete wasn't completed, please try again");
        # retry, and treat a workspace that has since disappeared as done.
        last: Exception | None = None
        for attempt in range(_DELETE_RETRIES):
            try:
                self._cp.delete_workspace(project_id, wid)
                return
            except Exception as e:
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
