"""Seed a freshly created (empty) repo with the warm template and push it (Phase 4.1).

Runs in the Workbench App container (the door). It clones the template into a temp dir, makes the
initial commit on `main`, and pushes to the new repo's HTTPS URL. Unlike the per-build save in
workspace/git.py (which pushes from /mnt/code, where Domino's credential helper lives), this pushes
from a throwaway temp repo that inherits no credential helper — so it authenticates with the SAME
token the provider adapter already extracted, injected via a one-shot in-memory credential helper
(the token travels only through the child git process's env; never argv, disk, or logs).

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

# De-branded once — see `workspace.git._identity_args`, which makes the same call for the same
# reason. Kept in step with it by `test_the_commit_author_names_nobody`.
_AGENT_IDENTITY = ["-c", "user.email=agent@localhost", "-c", "user.name=agent"]

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
    message: str = "Initial commit",
    token_provider: Callable[[], str] | None = None,
    settings: dict | None = None,
    dest: Path | None = None,
) -> None:
    """Materialize the template into a repo and push it to `clone_url` on `branch`.

    `token_provider`, when given, supplies the HTTPS token for the push (see module docstring):
    it's injected via a one-shot credential helper and the child git process's env only. Raises
    RuntimeError (carrying git's message, never the token) if git fails.

    `settings` is written to `.sage/settings.json` in the initial commit. That is how the name a
    person typed reaches the chip of a builder that does not exist yet (#46): the Domino project is
    named `sage-<slug>` so the door can find it, and the readable name rides in the repo, which is
    the only thing the new container will have. `.sage/settings.json` is a committed file — the
    template's .gitignore excludes only credentials, samples and scratch.

    `dest`, when given, is materialized in place and KEPT — it becomes the caller's own working
    directory rather than a throwaway clone (ONE-APP-PLAN.md §2.2's `registry.create`: "the seeded
    dir becomes the project dir; no second clone"). Left out (the door's own `create_app`), behavior
    is unchanged: a temp dir that is discarded once pushed.
    """
    import json

    push_prefix: list[str] = []
    push_env: dict[str, str] | None = None
    token = token_provider() if token_provider is not None else None
    if token:
        # Clear any inherited helper, then set ours, so auth is deterministic and never prompts.
        push_prefix = ["-c", "credential.helper=", "-c", f"credential.helper={_ONESHOT_HELPER}"]
        push_env = {**os.environ, _PUSH_TOKEN_ENV: token}

    def _seed_into(repo: Path) -> None:
        _copy_template(Path(template), repo)
        if settings:
            sage_dir = repo / ".sage"
            sage_dir.mkdir(parents=True, exist_ok=True)
            merged = {}
            existing = sage_dir / "settings.json"
            if existing.exists():
                try:
                    loaded = json.loads(existing.read_text())
                    merged = loaded if isinstance(loaded, dict) else {}
                except (OSError, ValueError):
                    merged = {}
            merged.update(settings)
            existing.write_text(json.dumps(merged, indent=2))
        _git(repo, "init", "-q")
        _git(repo, "checkout", "-q", "-b", branch)
        _git(repo, "add", "-A")
        _git(repo, *_AGENT_IDENTITY, "commit", "-q", "-m", message)
        _git(repo, "remote", "add", "origin", clone_url)
        _git(repo, *push_prefix, "push", "-q", "-u", "origin", branch, env=push_env)

    if dest is not None:
        _seed_into(Path(dest))
        return

    import tempfile

    with tempfile.TemporaryDirectory(prefix="sage-seed-") as tmp:
        _seed_into(Path(tmp) / "repo")


def clone(
    clone_url: str,
    dest: Path,
    *,
    branch: str = "main",
    token_provider: Callable[[], str] | None = None,
) -> None:
    """Clone an EXISTING repo into `dest` (`registry.clone`, ONE-APP-PLAN.md §2.2/Phase 3 step 2) —
    the mirror of `seed_and_push` for a project this machine has never opened, rather than one Sage
    just created. Same one-shot credential-helper mechanism as the push above (see module
    docstring): the token travels only through the child git process's env, never argv, disk, or
    logs. Raises RuntimeError (carrying git's message, never the token) if git fails."""
    clone_prefix: list[str] = []
    clone_env: dict[str, str] | None = None
    token = token_provider() if token_provider is not None else None
    if token:
        clone_prefix = ["-c", "credential.helper=", "-c", f"credential.helper={_ONESHOT_HELPER}"]
        clone_env = {**os.environ, _PUSH_TOKEN_ENV: token}
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(dest.parent, *clone_prefix, "clone", "-q", "--branch", branch, clone_url, str(dest), env=clone_env)
