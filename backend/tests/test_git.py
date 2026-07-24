"""Workspace git persistence — commit + push after a clean build."""
from __future__ import annotations

import subprocess
from pathlib import Path

from sage.workspace import git


def _run(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True, check=True).stdout


def _work_repo(tmp_path: Path, with_remote: bool = True) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    _run(work, "init", "-q")
    _run(work, "config", "user.email", "dev@example.com")
    _run(work, "config", "user.name", "Dev")
    (work / "seed.txt").write_text("seed")
    _run(work, "add", "-A")
    _run(work, "commit", "-q", "-m", "seed")
    if with_remote:
        bare = tmp_path / "remote.git"
        _run(tmp_path, "init", "-q", "--bare", str(bare))
        _run(work, "remote", "add", "origin", str(bare))
        _run(work, "push", "-q", "-u", "origin", "HEAD")
    return work


def test_is_repo_and_has_remote(tmp_path: Path):
    work = _work_repo(tmp_path)
    assert git.is_repo(work)
    assert git.has_remote(work)
    assert not git.is_repo(tmp_path / "not-a-repo")


def test_commit_and_push_pushes_to_remote(tmp_path: Path):
    work = _work_repo(tmp_path)
    (work / "App.tsx").write_text("built by agent")

    result = git.commit_and_push(work, "sage: build a thing")

    assert result.pushed is True
    # The new file is on the remote's HEAD tree.
    files = _run(tmp_path / "remote.git", "ls-tree", "--name-only", "HEAD")
    assert "App.tsx" in files


def test_commit_without_remote_is_not_an_error(tmp_path: Path):
    work = _work_repo(tmp_path, with_remote=False)
    (work / "App.tsx").write_text("built by agent")

    result = git.commit_and_push(work, "sage: build")

    assert result.pushed is False and "no remote" in result.detail
    assert "App.tsx" in _run(work, "ls-tree", "--name-only", "HEAD")  # committed locally


def test_no_changes_is_a_noop(tmp_path: Path):
    work = _work_repo(tmp_path)
    result = git.commit_and_push(work, "sage: nothing changed")
    assert result.pushed is False and "no changes" in result.detail


def test_commits_when_identity_unset(tmp_path: Path):
    # An environment with no configured git identity still commits (sage fallback identity).
    work = tmp_path / "work"
    work.mkdir()
    _run(work, "init", "-q")
    (work / "App.tsx").write_text("x")
    result = git.commit_and_push(work, "sage: first")
    assert result.pushed is False  # no remote
    assert _run(work, "log", "--oneline").strip()  # a commit exists
