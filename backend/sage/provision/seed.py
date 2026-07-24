"""Seed a freshly created (empty) repo with the warm template and push it (Phase 4.1).

Runs in the hub's workspace, where Domino's credential helper already authorizes `git push` to the
provider host (proven by git_discovery.sh). So this handles NO token: it clones the template into a
temp dir, makes the initial commit on `main`, and pushes to the new repo's HTTPS URL — the ambient
helper supplies auth, exactly like the per-build save in workspace/git.py.

Pushing an initial `main` before creating the Domino project matters: a git-based project points at
`mainGitRepoRef=main`, which must exist. This also means the builder opens straight to a working
preview instead of an empty checkout.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..workspace.manager import _IGNORE, _SEED_SKIP

_SAGE_IDENTITY = ["-c", "user.email=sage@dominodatalab.com", "-c", "user.name=sage"]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _copy_template(template: Path, dest: Path) -> None:
    """Copy template contents into dest, skipping the same heavy/linked entries the workspace
    seeder skips (node_modules, dist, .git). node_modules is intentionally NOT shipped — the app's
    workspace symlinks the warm template deps at runtime."""
    dest.mkdir(parents=True, exist_ok=True)
    for item in template.iterdir():
        if item.name in _SEED_SKIP:
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=_IGNORE)
        else:
            shutil.copy2(item, target)


def seed_and_push(clone_url: str, template: Path, *, branch: str = "main", message: str = "Initial commit from Sage") -> None:
    """Materialize the template into a temp repo and push it to `clone_url` on `branch`.

    Auth is ambient (Domino credential helper); this never sees or handles a token. Raises
    subprocess.CalledProcessError if git fails (the caller surfaces it as a provisioning error)."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="sage-seed-") as tmp:
        repo = Path(tmp) / "repo"
        _copy_template(Path(template), repo)
        _git(repo, "init", "-q")
        _git(repo, "checkout", "-q", "-b", branch)
        _git(repo, "add", "-A")
        _git(repo, *_SAGE_IDENTITY, "commit", "-q", "-m", message)
        _git(repo, "remote", "add", "origin", clone_url)
        _git(repo, "push", "-q", "-u", "origin", branch)
