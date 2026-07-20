"""Workspace module (SPEC C1/C9, DESIGN Seam 3 handoff).

A per-project working directory seeded from the warm React+Vite template. Deep module,
narrow interface: callers create/get a workspace and read/write the plan artifact; how the
template is materialized (copy source + symlink the warm node_modules) is hidden.

node_modules is symlinked from the template rather than copied so each workspace is warm
(deps already installed) without paying a multi-hundred-MB copy per project.
"""
from __future__ import annotations

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
