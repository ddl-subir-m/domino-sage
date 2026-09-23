"""The per-process registry of open projects (ONE-APP-PLAN.md §2.2).

Each project is a git clone of a Domino `sage-*` project at `home/projects/<slug>/`. This module
knows the on-disk shape (`.sage/project.json`, found by a directory scan — no index, matching
`WorkspaceManager`'s own "per D9" rule) and lazily builds/caches one `Orchestrator` per opened
project. It does NOT know how to build one itself: `open()` is handed a factory closure at
construction, so every process-wide shared service (gateway, catalog, control plane, asset/resource
providers, token source) is built once by the caller (`orchestrator/app.py`'s bootstrap) and threaded
through — matching the target architecture's "shared, process-wide" services list.

Named `RegistryEntry`, not `ProjectRecord` — `sage.workspace.manager.ProjectRecord` already owns
that name for a different record (a Project's plan/settings/session bookkeeping, kept at the volume
root, ADR-0008). This one is the registry's own small file: which Domino project a local clone
came from.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..orchestrator.service import Orchestrator
    from ..provision.domino import ControlPlane

# Kept beside the project's own `.sage/` bookkeeping rather than at the project root, so a project's
# root directory listing stays exactly what the git repo says it is.
RECORD_REL_PATH = ".sage/project.json"


@dataclass(frozen=True)
class RegistryEntry:
    """The 6-key file ONE-APP-PLAN.md §2.2 names, written at clone/create time (Phase 3) and read
    by every `open()` after."""

    slug: str
    domino_project_id: str
    domino_project_name: str
    owner_name: str
    repo_url: str
    created_at: str

    @staticmethod
    def load(path: Path) -> RegistryEntry:
        data = json.loads(path.read_text())
        return RegistryEntry(
            slug=str(data["slug"]),
            domino_project_id=str(data["dominoProjectId"]),
            domino_project_name=str(data["dominoProjectName"]),
            owner_name=str(data["ownerName"]),
            repo_url=str(data["repoUrl"]),
            created_at=str(data["createdAt"]),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "slug": self.slug,
                    "dominoProjectId": self.domino_project_id,
                    "dominoProjectName": self.domino_project_name,
                    "ownerName": self.owner_name,
                    "repoUrl": self.repo_url,
                    "createdAt": self.created_at,
                },
                indent=2,
            )
            + "\n"
        )


@dataclass(frozen=True)
class ProjectRow:
    """One row of `GET /api/projects` (§2.2's merged listing)."""

    slug: str
    name: str
    local: bool
    current: bool = False


class ProjectRegistry:
    """Lazy, cached, one `Orchestrator` per opened project.

    Thread-safe: `open()`/`close()` may run on different request threads (FastAPI's threadpool
    serves each sync route on its own worker thread).
    """

    def __init__(
        self,
        home: Path,
        build_orchestrator: Callable[[RegistryEntry, Path], Orchestrator],
        control_plane: ControlPlane | None = None,
    ) -> None:
        self._home = Path(home)
        self._projects_dir = self._home / "projects"
        self._build_orchestrator = build_orchestrator
        self._control_plane = control_plane
        self._lock = threading.Lock()
        self._open: dict[str, Orchestrator] = {}

    # -- on-disk scan, no index (ADR-0008) ---------------------------------------------------

    def _record_path(self, slug: str) -> Path:
        return self._projects_dir / slug / RECORD_REL_PATH

    def workspace_dir(self, slug: str) -> Path:
        return self._projects_dir / slug

    def local_slugs(self) -> list[str]:
        """Every project directory on disk holding a registry entry.

        A half-finished `create()` (repo pushed, entry not yet written) is deliberately excluded —
        ONE-APP-PLAN.md §2.2 has the entry written before the first push for exactly this reason, so
        an interrupted create reads as "not a project yet" rather than as a broken one.
        """
        if not self._projects_dir.is_dir():
            return []
        return sorted(
            child.name
            for child in self._projects_dir.iterdir()
            if child.is_dir() and (child / RECORD_REL_PATH).exists()
        )

    def entry(self, slug: str) -> RegistryEntry | None:
        path = self._record_path(slug)
        if not path.exists():
            return None
        return RegistryEntry.load(path)

    # -- listing (merges local clones with what the token can see) --------------------------

    def list(self, current: str | None = None) -> list[ProjectRow]:
        """Local clones first (so a project this process already knows the slug for is never
        shadowed by a remote row guessing at one), then any `sage-*` Domino project the token can
        see that isn't already a local row.
        """
        rows: dict[str, ProjectRow] = {}
        for slug in self.local_slugs():
            e = self.entry(slug)
            name = e.domino_project_name if e else slug
            rows[slug] = ProjectRow(slug=slug, name=name, local=True, current=slug == current)
        if self._control_plane is not None:
            for ref in self._control_plane.list_apps():
                slug = self._slug_for_remote(ref)
                if slug in rows:
                    continue
                rows[slug] = ProjectRow(slug=slug, name=ref.name, local=False, current=slug == current)
        return [rows[k] for k in sorted(rows)]

    @staticmethod
    def _slug_for_remote(ref) -> str:
        """A not-yet-cloned project has no local directory name yet. The repo's own name is the
        closest stable candidate until Phase 3's `clone()` picks the on-disk one for real."""
        uri = (ref.git_url or "").rstrip("/")
        name = uri.rsplit("/", 1)[-1].removesuffix(".git") if uri else ""
        return name or ref.id

    # -- lifecycle ----------------------------------------------------------------------------

    def is_open(self, slug: str) -> bool:
        with self._lock:
            return slug in self._open

    def all_open(self) -> list[Orchestrator]:
        """A snapshot of every currently-cached Orchestrator, for process shutdown (save
        in-progress work, stop supervisors) — the one thing that must reach EVERY open project, not
        just whichever one a request happened to dispatch to last."""
        with self._lock:
            return list(self._open.values())

    def open(self, slug: str) -> Orchestrator:
        """Get-or-build. Raises `KeyError` for a slug with no local project directory — `list()`
        (or Phase 3's `clone()`) is what makes a remote-only row openable."""
        with self._lock:
            existing = self._open.get(slug)
            if existing is not None:
                return existing
            entry = self.entry(slug)
            if entry is None:
                raise KeyError(f"no local project {slug!r}")
            orch = self._build_orchestrator(entry, self.workspace_dir(slug))
            self._open[slug] = orch
            return orch

    def close(self, slug: str) -> None:
        """Drops the cached Orchestrator so the next `open()` rebuilds it.

        Best-effort stops the one active preview supervisor if a turn ever started one (ADR-0040:
        one current app per project, so there is at most one to stop) — but this is not yet the
        real per-project preview lifecycle Phase 4 ("Preview per project") builds. Today's supervisor
        is reached through whatever `Project` the Orchestrator happened to have bound
        (`orch._project.supervisor`), not a first-class handle this class owns, so a project closed
        before it ever ran a turn leaves nothing to stop, and this is silent about that rather than
        raising — recorded as a real, temporary gap, not silently assumed handled.
        """
        with self._lock:
            orch = self._open.pop(slug, None)
        if orch is None:
            return
        project = getattr(orch, "_project", None)
        supervisor = getattr(project, "supervisor", None)
        if supervisor is not None:
            supervisor.stop()
