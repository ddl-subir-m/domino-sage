"""Turn-scoped revert for a workspace (stop button support).

Uses git purely as a local content store: the git-dir lives at
`.sage/snapshots/.git` while `--work-tree` points at the workspace root, so no `.git`
is ever created (or touched) at the workspace root itself. A real Domino project's own
git history is therefore never read, committed to, or reset by this.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

# Never snapshotted: heavy/regenerated dirs, our own internal state, and any real repo
# the workspace root itself might already have.
_EXCLUDE = ["node_modules", "dist", ".sage", ".git", ".DS_Store", "__pycache__"]

# The one file under `.sage` the agent writes. _EXCLUDE hides it from every tree identity here, and
# un-excluding `.sage` would let Stop's reset and clean reach Sage's own state (#671, #672).
QUERIES = ".sage/queries.json"

_PRE_TURN = "pre-turn snapshot"


class TurnSnapshot:
    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root
        self._git_dir = workspace_root / ".sage" / "snapshots" / ".git"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", f"--git-dir={self._git_dir}", f"--work-tree={self._root}", *args],
            capture_output=True,
            text=True,
            check=False,  # callers inspect returncode themselves; a failed git call is data here
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

    def commit_before_turn(self, turn_id: str = "") -> str:
        """Snapshot the workspace's current file state before a turn starts.

        `turn_id` goes into the message so turn_changed_app() can find this turn's snapshots later."""
        self._ensure_repo()
        self._run("add", "-A")
        self._run("commit", "--allow-empty", "-q", "-m", f"{_PRE_TURN} {turn_id}".strip())
        return self._run("rev-parse", "HEAD").stdout.strip()

    def turn_changed_app(self, turn_id: str, ignore: frozenset[str] = frozenset()) -> bool:
        """True if the turn `turn_id` changed any workspace file outside `ignore`: the tree of its
        first snapshot against the tree of the first snapshot taken after it, which is that turn's end
        state. A phased build commits once per phase under the same id, so every one of those is
        skipped. False when the turn is not in the log, or no later snapshot has been taken yet.

        `ignore` is for the files Sage writes itself, which land between the same two snapshots."""
        if not turn_id:
            return False
        tag = f"{_PRE_TURN} {turn_id}"
        result = self._run("log", "--reverse", "--format=%T %s")
        if result.returncode != 0:
            return False
        before = ""
        for line in result.stdout.splitlines():
            tree, _, subject = line.partition(" ")
            if not before:
                before = tree if subject == tag else ""
            elif subject != tag:
                diff = self._run("diff", "--name-only", "--no-renames", before, tree, "--")
                return diff.returncode == 0 and any(
                    path not in ignore for path in diff.stdout.splitlines() if path)
        return False

    def discard_changes(self) -> None:
        """Undo everything since the last commit_before_turn(): restore tracked files,
        delete anything new the turn created."""
        self._run("reset", "-q", "--hard", "HEAD")
        self._run("clean", "-fd", "-q")

    def discard_to(self, ref: str) -> None:
        """Undo everything since `ref` (a sha an earlier commit_before_turn() returned).

        A phased build takes a checkpoint per phase, so HEAD is the start of the CURRENT phase and
        discard_changes() would only undo that one. Stop means the user rejected the whole build, so
        it needs to reach back past every intermediate checkpoint — which reset-to-HEAD can't express.
        """
        self._run("reset", "-q", "--hard", ref)
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

    def queries_digest(self) -> str:
        """A content hash of QUERIES, or "" when there is none. Pair it with working_tree_hash()
        wherever the question is whether the agent changed the app."""
        try:
            return hashlib.sha256((self._root / QUERIES).read_bytes()).hexdigest()
        except OSError:
            return ""

    def tree_files(self, tree: str) -> dict[str, str]:
        """Path -> blob id for every file in a tree identity working_tree_hash() returned, or {}."""
        if not tree:
            return {}
        result = self._run("ls-tree", "-r", "-z", tree)
        if result.returncode != 0:
            return {}
        files: dict[str, str] = {}
        for entry in result.stdout.split("\0"):
            meta, _, path = entry.partition("\t")
            if path:
                files[path] = meta.split()[-1]
        return files

    def changed_paths(self, before: str, after: str, *, limit: int = 60) -> list[str]:
        """Return bounded app-relative paths changed between two tree identities."""
        if not before or not after or before == after:
            return []
        result = self._run("diff", "--name-only", "--no-renames", before, after, "--")
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line and not line.startswith("../")][
            :max(0, limit)
        ]
