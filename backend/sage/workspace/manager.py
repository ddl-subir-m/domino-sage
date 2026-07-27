"""Workspace module (SPEC C1/C9, DESIGN Seam 3 handoff).

The single working directory (the Domino project's mounted volume) seeded from the warm
React+Vite template. Deep module, narrow interface: callers ensure the workspace and read/write
the plan artifact; how the template is materialized (seed source + symlink the warm
node_modules) is hidden.

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
# Top-level template entries skipped when seeding (linked or repo-owned, not template content).
_SEED_SKIP = {"node_modules", "dist", ".git", ".DS_Store"}


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
        """Persisted OpenCode session id, so the project re-attached after an orchestrator restart
        (see Orchestrator.project) can resume the same conversation instead of starting a fresh
        session with no memory of prior turns."""
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

    @property
    def attachments_path(self) -> Path:
        """Committed manifest of attached/uploaded data files. `public/data/` itself is gitignored
        (data never enters git), so this manifest is the source of truth that lets the PUBLISHED app
        rebuild public/data/ from the project's dataset mounts at startup — see the template's
        scripts/rehydrate-data.mjs. Lives under committed .sage/ (like plan.md / history.jsonl)."""
        return self.path / ".sage" / "attachments.json"

    def read_attachments(self) -> list[dict]:
        if not self.attachments_path.exists():
            return []
        try:
            data = json.loads(self.attachments_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        return data if isinstance(data, list) else []

    def write_attachments(self, entries: list[dict]) -> None:
        self.attachments_path.parent.mkdir(parents=True, exist_ok=True)
        self.attachments_path.write_text(json.dumps(entries, indent=2))


class WorkspaceManager:
    """Manages the single workspace bound to this builder's Domino project volume.

    Per D9 one container hosts one project, so the workspace IS the project's mounted directory
    (git-based: /mnt/code), not a per-id copy under some root. `ensure` idempotently seeds the warm
    React+Vite template into that volume the first time (when it carries no app yet) and guarantees
    the warm node_modules symlink; a volume that already holds an app is left untouched.
    """

    def __init__(self, workspace_dir: Path, template: Path) -> None:
        self._dir = Path(workspace_dir)
        self._template = Path(template)

    @property
    def path(self) -> Path:
        return self._dir

    def ensure(self, project_id: str) -> Workspace:
        """Get-or-seed the bound workspace. Idempotent: seeds the template in place only when the
        volume has no app yet (no package.json), never clobbering a pre-existing app or its .git."""
        self._dir.mkdir(parents=True, exist_ok=True)
        if not (self._dir / "package.json").exists():
            # Seed the template INTO the (possibly pre-existing, e.g. a fresh git checkout)
            # directory entry by entry, so an existing .git / dotfiles are preserved.
            for item in self._template.iterdir():
                if item.name in _SEED_SKIP:
                    continue
                dest = self._dir / item.name
                if dest.exists():
                    continue
                if item.is_dir():
                    shutil.copytree(item, dest, ignore=_IGNORE)
                else:
                    shutil.copy2(item, dest)

        # Warm deps: symlink the template's node_modules unless the volume brought its own.
        node_modules = self._dir / "node_modules"
        tmpl_modules = self._template / "node_modules"
        if not node_modules.exists() and tmpl_modules.exists():
            os.symlink(tmpl_modules, node_modules)

        return Workspace(project_id, self._dir)
