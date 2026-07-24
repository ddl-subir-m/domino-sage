"""Seed a freshly created (empty) repo with the warm template and push it (Phase 4.1).

Runs in the hub's workspace. It clones the template into a temp dir, makes the initial commit on
`main`, and pushes to the new repo's HTTPS URL. Unlike the per-build save in workspace/git.py (which
pushes from /mnt/code, where Domino's credential helper lives), this pushes from a throwaway temp
repo that inherits no credential helper — so it authenticates with the SAME token the provider
adapter already extracted, injected via a one-shot in-memory credential helper (the token travels
only through the child git process's env; never argv, disk, or logs).

Pushing an initial `main` before creating the Domino project matters: a git-based project points at
`mainGitRepoRef=main`, which must exist. This also means the builder opens straight to a working
preview instead of an empty checkout.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from ..workspace.manager import _IGNORE, _SEED_SKIP

_SAGE_IDENTITY = ["-c", "user.email=sage@dominodatalab.com", "-c", "user.name=sage"]

# One-shot credential helper: on a `get`, prints creds from $SAGE_PUSH_TOKEN. The token itself never
# appears here — only the env var name does — so it stays out of argv and any process listing.
_PUSH_TOKEN_ENV = "SAGE_PUSH_TOKEN"
_ONESHOT_HELPER = (
    f'!f() {{ test "$1" = get && '
    f'printf "username=x-access-token\\npassword=%s\\n" "${_PUSH_TOKEN_ENV}"; }}; f'
)


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> None:
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env, check=False)
    if r.returncode != 0:
        # Surface git's own message (never the token) so provisioning errors are diagnosable.
        detail = (r.stderr or r.stdout).strip()
        raise RuntimeError(f"git {args[0]} failed (exit {r.returncode}): {detail}")


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


def seed_and_push(
    clone_url: str,
    template: Path,
    *,
    branch: str = "main",
    message: str = "Initial commit from Sage",
    token_provider: Callable[[], str] | None = None,
) -> None:
    """Materialize the template into a temp repo and push it to `clone_url` on `branch`.

    `token_provider`, when given, supplies the HTTPS token for the push (see module docstring):
    it's injected via a one-shot credential helper and the child git process's env only. Raises
    RuntimeError (carrying git's message, never the token) if git fails."""
    import tempfile

    push_prefix: list[str] = []
    push_env: dict[str, str] | None = None
    token = token_provider() if token_provider is not None else None
    if token:
        # Clear any inherited helper, then set ours, so auth is deterministic and never prompts.
        push_prefix = ["-c", "credential.helper=", "-c", f"credential.helper={_ONESHOT_HELPER}"]
        push_env = {**os.environ, _PUSH_TOKEN_ENV: token}

    with tempfile.TemporaryDirectory(prefix="sage-seed-") as tmp:
        repo = Path(tmp) / "repo"
        _copy_template(Path(template), repo)
        _git(repo, "init", "-q")
        _git(repo, "checkout", "-q", "-b", branch)
        _git(repo, "add", "-A")
        _git(repo, *_SAGE_IDENTITY, "commit", "-q", "-m", message)
        _git(repo, "remote", "add", "origin", clone_url)
        _git(repo, *push_prefix, "push", "-q", "-u", "origin", branch, env=push_env)
