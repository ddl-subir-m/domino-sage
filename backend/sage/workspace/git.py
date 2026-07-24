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


def _git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(path), capture_output=True, text=True, check=check
    )


def is_repo(path: Path) -> bool:
    if not Path(path).is_dir():
        return False
    r = _git(path, "rev-parse", "--is-inside-work-tree", check=False)
    return r.returncode == 0 and r.stdout.strip() == "true"


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


def commit_and_push(path: Path, message: str) -> SaveResult:
    """Stage everything, commit, and push. Returns pushed=False (not an error) when there's nothing
    to commit or no remote; raises only on an unexpected git failure (the caller treats that as a
    non-fatal saved:ok=false)."""
    _git(path, "add", "-A")
    if _git(path, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return SaveResult(pushed=False, detail="no changes to commit")
    _git(path, *_identity_args(path), "commit", "-m", message)
    if not has_remote(path):
        return SaveResult(pushed=False, detail="committed (no remote)")
    push = _git(path, "push", check=False)
    if push.returncode != 0:
        return SaveResult(pushed=False, detail=f"push failed: {(push.stderr or push.stdout).strip()[:200]}")
    return SaveResult(pushed=True, detail="pushed")
