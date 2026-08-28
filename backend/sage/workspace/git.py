"""Git persistence for the bound workspace.

After a clean build the orchestrator commits + pushes the workspace so the app code AND the
`.sage/` transcript become durable (git-based Domino compute is ephemeral — only committed files
survive a restart). Push relies on Domino's pre-authorized credential helper, so no token handling
lives here; auto-*creating* the remote for brand-new apps is a separate concern (Phase 4).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SaveResult:
    pushed: bool
    detail: str


@dataclass
class SyncResult:
    """Outcome of pulling the remote into the workspace.

    status is one of: "up-to-date" (nothing to pull), "merged" (remote changes integrated),
    "conflict" (merge left markers in `conflicts` for the caller to resolve), "conflict-unresolved"
    (resolution failed and the merge was rolled back), "no-remote", or "error"."""
    status: str
    conflicts: list[str]
    detail: str


@dataclass
class Incoming:
    """What the remote has that this workspace does not, as of the last fetch.

    `head` is the remote commit those changes end at, and "" when there is nothing to pull. It is
    the thing a caller remembers when somebody chooses to build anyway (#78), so the same decision
    isn't asked for again until the remote moves on."""
    head: str
    files: list[str]


def _git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(path), capture_output=True, text=True, check=check
    )


def is_repo_root(path: Path) -> bool:
    """True only when `path` is the ROOT of its own repo — not merely somewhere inside one.

    The distinction is the whole bug in #20. Locally the workspace sits at `backend/workspaces/app`,
    inside Sage's own source tree and gitignored, so it is not its own repo. The old check asked
    `--is-inside-work-tree`, which walks up until it finds a repo and answers `true` — and a save
    from a subdirectory stages the whole enclosing tree, so stopping the local orchestrator committed
    and pushed Sage's uncommitted source to `origin/main`. `--show-toplevel` names the repo it found,
    which lets the caller notice the answer came from somewhere above it.

    Paths are resolved on both sides: on macOS a `/tmp` workspace reports `/private/tmp`, and a
    string compare would call a real repo root not-a-root.
    """
    p = Path(path)
    if not p.is_dir():
        return False
    r = _git(p, "rev-parse", "--show-toplevel", check=False)
    if r.returncode != 0:
        return False
    return Path(r.stdout.strip()).resolve() == p.resolve()


def has_remote(path: Path) -> bool:
    r = _git(path, "remote", check=False)
    return r.returncode == 0 and bool(r.stdout.strip())


def _identity_args(path: Path) -> list[str]:
    """Use the repo's configured identity (Domino sets it) when present; otherwise fall back to a
    sage identity so an unconfigured environment still commits cleanly rather than erroring."""
    args: list[str] = []
    if not _git(path, "config", "user.email", check=False).stdout.strip():
        args += ["-c", "user.email=sage@dominodatalab.com"]
    if not _git(path, "config", "user.name", check=False).stdout.strip():
        args += ["-c", "user.name=sage"]
    return args


def commit_all(path: Path, message: str, exclude: list[str] | None = None) -> bool:
    """Stage everything and commit. Returns False (not an error) when there's nothing to commit.
    `exclude` unstages the given workspace-relative paths after staging, so bytes that must never be
    committed (attached-data copies leaked into src/) are kept out of the commit — they stay on disk,
    just untracked."""
    _git(path, "add", "-A")
    if exclude:
        _git(path, "reset", "-q", "--", *exclude, check=False)
    if _git(path, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    _git(path, *_identity_args(path), "commit", "-m", message)
    return True


def untrack(path: Path, rel: str) -> bool:
    """Drop `rel` from the index, leaving it on disk. True if git was tracking it.

    An ignore rule does nothing to a file git already knows about, so a workspace that committed
    one before it became generated state needs this once. A no-op every turn after that."""
    if _git(path, "ls-files", "--error-unmatch", "--", rel, check=False).returncode != 0:
        return False
    _git(path, "rm", "--cached", "-q", "--", rel)
    return True


def push(path: Path) -> SaveResult:
    """Push HEAD. Returns pushed=False (not an error) when there's no remote or the push is
    rejected (e.g. a non-fast-forward — the caller should pull first)."""
    if not has_remote(path):
        return SaveResult(pushed=False, detail="committed (no remote)")
    r = _git(path, "push", check=False)
    if r.returncode != 0:
        return SaveResult(pushed=False, detail=f"push failed: {(r.stderr or r.stdout).strip()[:200]}")
    return SaveResult(pushed=True, detail="pushed")


def commit_and_push(path: Path, message: str) -> SaveResult:
    """Stage everything, commit, and push. Returns pushed=False (not an error) when there's nothing
    to commit or no remote; raises only on an unexpected git failure (the caller treats that as a
    non-fatal saved:ok=false)."""
    if not commit_all(path, message):
        return SaveResult(pushed=False, detail="no changes to commit")
    return push(path)


def current_branch(path: Path) -> str:
    return _git(path, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip() or "main"


def pull(path: Path) -> SyncResult:
    """Fetch the remote and merge the current branch's upstream into the working tree. On conflict
    the tree is left with markers and SyncResult.conflicts lists the files, for the caller (the
    agent) to resolve and then finalize_merge(). Never pushes. Assumes a clean tree (commit first)."""
    if not has_remote(path):
        return SyncResult("no-remote", [], "no remote to pull from")
    fetch = _git(path, "fetch", "origin", check=False)
    if fetch.returncode != 0:
        raise RuntimeError(f"git fetch failed: {(fetch.stderr or fetch.stdout).strip()[:200]}")
    ref = f"origin/{current_branch(path)}"
    # No upstream branch yet (nothing pushed) -> nothing to pull.
    if _git(path, "rev-parse", "--verify", "--quiet", ref, check=False).returncode != 0:
        return SyncResult("up-to-date", [], "no upstream branch")
    merge = _git(path, *_identity_args(path), "merge", "--no-edit", ref, check=False)
    if merge.returncode == 0:
        if "up to date" in merge.stdout.lower():
            return SyncResult("up-to-date", [], "already up to date")
        return SyncResult("merged", [], merge.stdout.strip()[:200] or "merged remote changes")
    conflicts = unresolved_conflicts(path)
    if conflicts:
        return SyncResult("conflict", conflicts, "merge conflicts need resolution")
    # A non-conflict merge failure (e.g. local changes would be overwritten) — roll back and raise.
    _git(path, "merge", "--abort", check=False)
    raise RuntimeError(f"git merge failed: {(merge.stderr or merge.stdout).strip()[:200]}")


def unresolved_conflicts(path: Path) -> list[str]:
    """Files git considers unmerged (conflicted) in the index."""
    r = _git(path, "diff", "--name-only", "--diff-filter=U", check=False)
    return [f for f in r.stdout.splitlines() if f.strip()]


_CONFLICT_MARKERS = ("<<<<<<< ", ">>>>>>> ")


def files_with_conflict_markers(path: Path, files: list[str]) -> list[str]:
    """Of `files`, those that still contain conflict markers — used to verify the agent actually
    resolved them (the index stays "unmerged" until `git add`, so diff-filter=U can't confirm this)."""
    out: list[str] = []
    for f in files:
        try:
            text = (Path(path) / f).read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if any(m in text for m in _CONFLICT_MARKERS):
            out.append(f)
    return out


def finalize_merge(path: Path, message: str) -> None:
    """Stage the resolved files and commit the in-progress merge."""
    _git(path, "add", "-A")
    _git(path, *_identity_args(path), "commit", "--no-edit", "-m", message)


def abort_merge(path: Path) -> None:
    """Roll back an in-progress merge, restoring the pre-pull state."""
    _git(path, "merge", "--abort", check=False)


def fetch(path: Path) -> bool:
    """Refresh the remote-tracking refs. Best-effort by design, unlike `pull`'s fetch: this one runs
    at the top of a turn and on a timer, and an unreachable remote has to leave both exactly as an
    up-to-date one would rather than stopping the turn with a network error."""
    if not has_remote(path):
        return False
    return _git(path, "fetch", "origin", check=False).returncode == 0


def incoming(path: Path) -> Incoming:
    """Commits on the remote branch this workspace hasn't merged, and the files they change.

    Reads the refs `fetch` left behind and never touches the network itself, so the answer is a
    local read once someone has paid for the fetch. Local commits are not incoming: `HEAD...ref`
    diffs from the merge base, so a workspace that is merely ahead reads as nothing to pull."""
    if not has_remote(path):
        return Incoming("", [])
    ref = f"origin/{current_branch(path)}"
    if _git(path, "rev-parse", "--verify", "--quiet", ref, check=False).returncode != 0:
        return Incoming("", [])
    # Counted, not diffed, to decide: a commit that changes nothing back is still a commit the
    # local branch has to merge before it can push.
    behind = _git(path, "rev-list", "--count", f"HEAD..{ref}", check=False).stdout.strip()
    if not behind or behind == "0":
        return Incoming("", [])
    changed = _git(path, "diff", "--name-only", f"HEAD...{ref}", check=False)
    files = [f for f in changed.stdout.splitlines() if f.strip()]
    return Incoming(_git(path, "rev-parse", ref, check=False).stdout.strip(), files)
