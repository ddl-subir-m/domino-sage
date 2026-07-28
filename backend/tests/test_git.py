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


def test_commit_all_exclude_keeps_paths_out_of_the_commit(tmp_path: Path):
    # A leaked data copy must never be staged, but stays on disk (untracked) so the preview still works.
    work = _work_repo(tmp_path, with_remote=False)
    (work / "App.tsx").write_text("built by agent")
    (work / "src").mkdir()
    (work / "src" / "sales.csv").write_text("a,b\n1,2\n")

    committed = git.commit_all(work, "sage: build", exclude=["src/sales.csv"])

    assert committed is True
    tracked = _run(work, "ls-files")
    assert "App.tsx" in tracked and "src/sales.csv" not in tracked   # copy excluded from git
    assert (work / "src" / "sales.csv").is_file()                    # but still on disk


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


def _second_clone(tmp_path: Path, work: Path) -> Path:
    """A second checkout of the same remote, standing in for a teammate."""
    bare = tmp_path / "remote.git"
    other = tmp_path / "other"
    _run(tmp_path, "clone", "-q", str(bare), str(other))
    _run(other, "config", "user.email", "mate@example.com")
    _run(other, "config", "user.name", "Mate")
    return other


def test_pull_up_to_date_when_remote_unchanged(tmp_path: Path):
    work = _work_repo(tmp_path)
    result = git.pull(work)
    assert result.status == "up-to-date"
    assert result.conflicts == []


def test_pull_merges_remote_changes(tmp_path: Path):
    work = _work_repo(tmp_path)
    other = _second_clone(tmp_path, work)
    # Teammate adds a new file and pushes it.
    (other / "mate.txt").write_text("from teammate")
    _run(other, "add", "-A")
    _run(other, "commit", "-q", "-m", "mate: add file")
    _run(other, "push", "-q")

    result = git.pull(work)
    assert result.status == "merged"
    assert (work / "mate.txt").read_text() == "from teammate"  # integrated into the working tree


def test_pull_leaves_conflict_markers_for_resolution(tmp_path: Path):
    work = _work_repo(tmp_path)
    other = _second_clone(tmp_path, work)
    # Both sides change the same line of the same file -> a real conflict.
    (other / "seed.txt").write_text("teammate version")
    _run(other, "add", "-A")
    _run(other, "commit", "-q", "-m", "mate: edit seed")
    _run(other, "push", "-q")
    (work / "seed.txt").write_text("builder version")
    assert git.commit_all(work, "sage: edit seed") is True

    result = git.pull(work)
    assert result.status == "conflict"
    assert result.conflicts == ["seed.txt"]
    # The tree is left mid-merge with markers for the agent to resolve.
    assert git.files_with_conflict_markers(work, ["seed.txt"]) == ["seed.txt"]

    # Resolve + finalize, mirroring what the orchestrator does after the agent edits.
    (work / "seed.txt").write_text("reconciled")
    assert git.files_with_conflict_markers(work, ["seed.txt"]) == []
    git.finalize_merge(work, "sage: merge remote changes")
    assert git.push(work).pushed is True
    assert (work / "seed.txt").read_text() == "reconciled"


def test_abort_merge_restores_pre_pull_state(tmp_path: Path):
    work = _work_repo(tmp_path)
    other = _second_clone(tmp_path, work)
    (other / "seed.txt").write_text("teammate version")
    _run(other, "add", "-A")
    _run(other, "commit", "-q", "-m", "mate: edit seed")
    _run(other, "push", "-q")
    (work / "seed.txt").write_text("builder version")
    git.commit_all(work, "sage: edit seed")

    assert git.pull(work).status == "conflict"
    git.abort_merge(work)
    assert git.files_with_conflict_markers(work, ["seed.txt"]) == []
    assert (work / "seed.txt").read_text() == "builder version"  # our commit intact


def test_pull_without_remote_is_noop(tmp_path: Path):
    work = _work_repo(tmp_path, with_remote=False)
    assert git.pull(work).status == "no-remote"


def test_push_rejected_on_non_fast_forward(tmp_path: Path):
    work = _work_repo(tmp_path)
    other = _second_clone(tmp_path, work)
    (other / "mate.txt").write_text("x")
    _run(other, "add", "-A")
    _run(other, "commit", "-q", "-m", "mate")
    _run(other, "push", "-q")
    # Local commits without pulling -> the remote is ahead, so the push is rejected (not an error).
    (work / "App.tsx").write_text("local")
    git.commit_all(work, "sage: local")
    result = git.push(work)
    assert result.pushed is False and "push failed" in result.detail
    # After a pull, the push goes through.
    assert git.pull(work).status == "merged"
    assert git.push(work).pushed is True
