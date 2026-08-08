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
# The script Domino runs to serve a published App. Sage-owned — see refresh_entry_script.
_ENTRY_SCRIPT = "app.sh"


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
    def architecture_path(self) -> Path:
        """A design document the user asked for ("give me an architecture for…"). Deliberately NOT
        plan.md: a plan is a one-shot handoff that archive_plan() moves aside as soon as a build
        consumes it, and an architecture is a reference the user keeps coming back to."""
        return self.path / ".sage" / "architecture.md"

    def write_architecture(self, text: str) -> None:
        self.architecture_path.parent.mkdir(parents=True, exist_ok=True)
        self.architecture_path.write_text(text)

    def read_architecture(self) -> str | None:
        p = self.architecture_path
        return p.read_text() if p.exists() else None

    def archive_plan(self) -> Path | None:
        """Move the consumed plan out of the agent's live view (SPEC P6). The plan artifact is a
        one-shot handoff, not a living spec: once the Implement turn has built from it, a leftover
        `.sage/plan.md` reads like *current* intent/state and can mislead a later turn — it's the
        one .sage/ file that looks like instructions. Archived copies stay under .sage/plans/ so git
        retains the history. Returns the archive path, or None if there was no live plan."""
        if not self.plan_path.exists():
            return None
        archive_dir = self.path / ".sage" / "plans"
        archive_dir.mkdir(parents=True, exist_ok=True)
        n = len(list(archive_dir.glob("[0-9]*.md"))) + 1
        dest = archive_dir / f"{n:03d}.md"
        while dest.exists():  # never clobber a prior archived plan
            n += 1
            dest = archive_dir / f"{n:03d}.md"
        self.plan_path.rename(dest)
        return dest

    @property
    def settings_path(self) -> Path:
        """Per-project Sage settings (e.g. skip_planning to opt out of the first-build plan gate).
        Same committed-.sage pattern as model_overrides.json."""
        return self.path / ".sage" / "settings.json"

    def read_settings(self) -> dict:
        if not self.settings_path.exists():
            return {}
        try:
            data = json.loads(self.settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def write_settings(self, settings: dict) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(settings, indent=2))

    def has_built(self) -> bool:
        """True once a code-writing build has completed here. Drives the first-BUILD plan gate
        (not first-turn): questions asked before the first build must not consume the gate, and the
        gate must still fire on the first real build request no matter how many questions preceded it."""
        return bool(self.read_settings().get("built"))

    def mark_built(self) -> None:
        """Latch has_built() on after the first successful build. Idempotent; persisted in settings
        so it survives an orchestrator restart (a rebuilt project must not re-gate)."""
        settings = self.read_settings()
        if not settings.get("built"):
            settings["built"] = True
            self.write_settings(settings)

    def read_last_turn_failed(self) -> bool:
        """True when the previous build attempt on this project ended badly (see the failure-replan
        block in orchestrator.service). Drives the cross-turn failure gate: the turn after a failure
        is exactly when stopping to plan is worth the interruption.

        Lives in settings.json next to `built` rather than being derived from history.jsonl: the
        transcript is append-only and replayable, so it can't record that a signal has been CONSUMED,
        and consumption is what keeps this one-shot instead of a permanent approval wall. Fails open
        through read_settings() — missing or corrupt state reads as "didn't fail", i.e. build."""
        return bool(self.read_settings().get("last_turn_failed"))

    def set_last_turn_failed(self, failed: bool) -> None:
        """Record (or clear) the previous-turn failure signal. Best-effort by design: this runs on the
        terminal path of every turn, and a workspace we can't write to must not turn a finished build
        into a raised exception mid-stream. A lost write just means no gate next turn — the same
        behaviour as before this feature existed."""
        try:
            settings = self.read_settings()
            if bool(settings.get("last_turn_failed")) == failed:
                return
            settings["last_turn_failed"] = failed
            self.write_settings(settings)
        except OSError:
            pass

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

    def refresh_entry_script(self) -> bool:
        """Bring the workspace's deploy entry script back in line with the template. True if changed.

        app.sh is Sage infrastructure, not app content: it encodes how a published App installs,
        builds and serves itself, and the agent has no reason to touch it. But it's COMMITTED to the
        app's repo when the project is seeded, so an app keeps whatever app.sh it was born with —
        which meant a fix to the template only ever reached NEW apps, while every existing app went
        on crash-looping on the bug we'd already fixed (the Node-18 PATH order, 2026-08-07). Callers
        refresh at publish time so the fix travels to every app that deploys.
        """
        src = self._template / _ENTRY_SCRIPT
        if not src.is_file():
            return False
        dst = self._dir / _ENTRY_SCRIPT
        if dst.is_file() and dst.read_bytes() == src.read_bytes():
            return False
        shutil.copy2(src, dst)  # copy2 keeps the +x bit Domino needs to run it
        return True
