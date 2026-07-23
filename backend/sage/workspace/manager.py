"""Workspace module (SPEC C1/C9, DESIGN Seam 3 handoff).

A per-project working directory seeded from the warm React+Vite template. Deep module,
narrow interface: callers create/get a workspace and read/write the plan artifact; how the
template is materialized (copy source + symlink the warm node_modules) is hidden.

node_modules is symlinked from the template rather than copied so each workspace is warm
(deps already installed) without paying a multi-hundred-MB copy per project.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

# Source dirs never copied into a workspace (heavy / regenerated / linked separately).
_IGNORE = shutil.ignore_patterns("node_modules", "dist", ".git", ".DS_Store")


@dataclass(frozen=True)
class Workspace:
    project_id: str
    path: Path

    @property
    def app_entry(self) -> Path:
        return self.path / "src" / "App.tsx"

    @property
    def plan_path(self) -> Path:
        """The plan→implement handoff artifact (auto mode). Lives in the workspace so the
        implement session and IDE mode can both see it."""
        return self.path / ".sage" / "plan.md"

    def write_plan(self, text: str) -> None:
        self.plan_path.parent.mkdir(parents=True, exist_ok=True)
        self.plan_path.write_text(text)

    def read_plan(self) -> str | None:
        return self.plan_path.read_text() if self.plan_path.exists() else None

    @property
    def session_path(self) -> Path:
        """Persisted OpenCode session id, so a project re-attached after an orchestrator restart
        (see Orchestrator.open_project) can resume the same conversation instead of starting a
        fresh session with no memory of prior turns."""
        return self.path / ".sage" / "session.json"

    def read_session_id(self) -> str | None:
        if not self.session_path.exists():
            return None
        return json.loads(self.session_path.read_text()).get("session_id")

    def write_session_id(self, session_id: str) -> None:
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_path.write_text(json.dumps({"session_id": session_id}))

    @property
    def history_path(self) -> Path:
        """Append-only transcript of chat-visible build events, so the UI can replay a project's
        history after a page reload or an orchestrator restart (neither of which the in-memory
        registry survives)."""
        return self.path / ".sage" / "history.jsonl"

    def append_history(self, entry: dict) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def read_history(self) -> list[dict]:
        if not self.history_path.exists():
            return []
        return [json.loads(line) for line in self.history_path.read_text().splitlines() if line.strip()]

    def history_len(self) -> int:
        return len(self.read_history())

    def truncate_history(self, n: int) -> None:
        """Drop everything appended after the first `n` entries (stop-button revert:
        removes the in-progress turn's user prompt and any partial response)."""
        if not self.history_path.exists():
            return
        lines = self.history_path.read_text().splitlines()[:n]
        self.history_path.write_text("".join(line + "\n" for line in lines))

    @property
    def catalog_overrides_path(self) -> Path:
        """Per-project overrides of the plan/implement/sovereign/default model ids, layered on
        top of the deployment-wide ModelCatalog so a project can retarget which model Auto uses
        per phase without changing every other project."""
        return self.path / ".sage" / "model_overrides.json"

    def read_catalog_overrides(self) -> dict:
        if not self.catalog_overrides_path.exists():
            return {}
        return json.loads(self.catalog_overrides_path.read_text())

    def write_catalog_overrides(self, overrides: dict) -> None:
        self.catalog_overrides_path.parent.mkdir(parents=True, exist_ok=True)
        self.catalog_overrides_path.write_text(json.dumps(overrides))


class WorkspaceManager:
    def __init__(self, root: Path, template: Path) -> None:
        self._root = Path(root)
        self._template = Path(template)
        self._root.mkdir(parents=True, exist_ok=True)

    def _dir(self, project_id: str) -> Path:
        return self._root / project_id

    def get(self, project_id: str) -> Workspace | None:
        d = self._dir(project_id)
        return Workspace(project_id, d) if d.exists() else None

    def create(self, project_id: str) -> Workspace:
        """Materialize a fresh workspace from the warm template. Idempotent-safe: raises if it
        already exists so we never clobber in-progress work."""
        dest = self._dir(project_id)
        if dest.exists():
            raise FileExistsError(f"workspace already exists: {dest}")
        shutil.copytree(self._template, dest, ignore=_IGNORE)

        # Warm deps: symlink the template's node_modules instead of copying.
        tmpl_modules = self._template / "node_modules"
        if tmpl_modules.exists():
            os.symlink(tmpl_modules, dest / "node_modules")

        return Workspace(project_id, dest)

    def list_ids(self) -> list[str]:
        """Every workspace materialized on disk, whether or not it's currently registered in the
        orchestrator's in-memory Project map (e.g. survives a process restart)."""
        return sorted(p.name for p in self._root.iterdir() if p.is_dir())

    def delete(self, project_id: str) -> None:
        """Remove a workspace's directory from disk. No-op if it was never created."""
        dest = self._dir(project_id)
        if dest.exists():
            shutil.rmtree(dest)
