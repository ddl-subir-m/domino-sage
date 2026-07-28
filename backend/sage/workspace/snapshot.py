"""Turn-scoped revert for a workspace (stop button support).

Uses git purely as a local content store: the git-dir lives at
`.sage/snapshots/.git` while `--work-tree` points at the workspace root, so no `.git`
is ever created (or touched) at the workspace root itself. A real Domino project's own
git history is therefore never read, committed to, or reset by this.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

# Never snapshotted: heavy/regenerated dirs, our own internal state, and any real repo
# the workspace root itself might already have.
_EXCLUDE = ["node_modules", "dist", ".sage", ".git", ".DS_Store"]


class TurnSnapshot:
    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root
        self._git_dir = workspace_root / ".sage" / "snapshots" / ".git"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", f"--git-dir={self._git_dir}", f"--work-tree={self._root}", *args],
            capture_output=True,
            text=True,
        )

    def _ensure_repo(self) -> None:
        if self._git_dir.exists():
            return
        self._git_dir.parent.mkdir(parents=True, exist_ok=True)
        self._run("init", "-q")
        self._run("config", "user.email", "sage@local")
        self._run("config", "user.name", "sage")
        exclude = self._git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("\n".join(_EXCLUDE) + "\n")

    def commit_before_turn(self) -> str:
        """Snapshot the workspace's current file state before a turn starts."""
        self._ensure_repo()
        self._run("add", "-A")
        self._run("commit", "--allow-empty", "-q", "-m", "pre-turn snapshot")
        return self._run("rev-parse", "HEAD").stdout.strip()

    def discard_changes(self) -> None:
        """Undo everything since the last commit_before_turn(): restore tracked files,
        delete anything new the turn created."""
        self._run("reset", "-q", "--hard", "HEAD")
        self._run("clean", "-fd", "-q")

    def changed_since_pre_turn(self) -> bool:
        """True if any workspace file changed vs the last commit_before_turn() snapshot
        (excluding node_modules/dist/.sage). Ground-truth 'did the agent write anything',
        independent of which write tool the harness happened to name."""
        self._ensure_repo()
        return bool(self._run("status", "--porcelain").stdout.strip())

    def working_tree_hash(self) -> str:
        """A content hash of the whole working tree (a git tree object over everything except
        _EXCLUDE). Capture it at a turn's start and compare at its end to tell whether THAT turn
        changed any file — a per-turn signal, unlike changed_since_pre_turn() which is cumulative
        vs the build-start commit. Stages into the index (add -A) and writes a tree object but never
        commits, so the commit_before_turn() snapshot stays the stop-button revert point. Empty
        string if git is unavailable, so callers degrade to their tool-based edit signal."""
        self._ensure_repo()
        self._run("add", "-A")
        return self._run("write-tree").stdout.strip()
