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


def test_is_repo_root_and_has_remote(tmp_path: Path):
    work = _work_repo(tmp_path)
    assert git.is_repo_root(work)
    assert git.has_remote(work)
    assert not git.is_repo_root(tmp_path / "not-a-repo")


def test_a_directory_inside_a_repo_is_not_a_repo_root(tmp_path: Path):
    """#20: asking "am I inside a repo" gets a truthful yes about the WRONG repo. The local
    workspace sits inside Sage's own source tree, and a save from there staged and pushed it."""
    work = _work_repo(tmp_path)
    nested = work / "workspaces" / "app"
    nested.mkdir(parents=True)
    assert not git.is_repo_root(nested)


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
    assert result.rejected is False  # no remote is not a rejection — #234
    assert "App.tsx" in _run(work, "ls-tree", "--name-only", "HEAD")  # committed locally


def test_no_changes_is_a_noop(tmp_path: Path):
    work = _work_repo(tmp_path)
    result = git.commit_and_push(work, "sage: nothing changed")
    assert result.pushed is False and "no changes" in result.detail
    assert result.rejected is False  # genuinely nothing to do, not a rejection — #234


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
    assert result.rejected is True  # a real rejection, not a benign pushed=False — #234
    assert result.behind is True  # the remote is ahead; a pull is the remedy
    # After a pull, the push goes through.
    assert git.pull(work).status == "merged"
    result = git.push(work)
    assert result.pushed is True and result.rejected is False


def test_a_hook_refusal_keeps_the_remote_error_and_is_not_a_reason_to_pull(tmp_path: Path):
    """GitHub prints the 50 MB warning first. The refusal is the `remote: error` lines after
    it, and a pull does not make GitHub accept the blob."""
    work = _work_repo(tmp_path)
    hook = tmp_path / "remote.git" / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \\\n"
        "  'warning: File .sage/threads/thr_x/history.jsonl is 66.37 MB; "
        "this is larger than GitHub recommended maximum of 50.00 MB' \\\n"
        "  'error: Trace: 25a5832841' \\\n"
        "  'error: GH001: Large files detected.'\n"
        "exit 1\n"
    )
    hook.chmod(0o755)
    (work / "App.tsx").write_text("local")
    git.commit_all(work, "sage: local")

    result = git.push(work)

    assert result.pushed is False
    assert result.rejected is True
    assert result.behind is False
    assert "GH001: Large files detected." in result.detail
    assert "Trace: 25a5832841" in result.detail
    assert "66.37 MB" not in result.detail


def test_a_push_is_not_sent_when_a_new_blob_is_over_the_limit(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(git, "BLOB_REJECT_BYTES", 32)
    work = _work_repo(tmp_path)
    (work / "wide.bin").write_bytes(b"x" * 64)
    git.commit_all(work, "sage: wide")

    result = git.push(work)

    assert result.pushed is False
    assert result.rejected is True
    assert result.behind is False
    assert "wide.bin" in result.detail
    assert "not sent" in result.detail
    names = _run(tmp_path / "remote.git", "ls-tree", "-r", "--name-only", "HEAD")
    assert "wide.bin" not in names


def test_a_push_names_an_oversized_blob_a_later_commit_deleted(tmp_path: Path, monkeypatch):
    """GitHub checks every blob the push introduces, including one the tip no longer has."""
    monkeypatch.setattr(git, "BLOB_REJECT_BYTES", 32)
    work = _work_repo(tmp_path)
    (work / "wide.bin").write_bytes(b"x" * 64)
    git.commit_all(work, "sage: wide")
    (work / "wide.bin").unlink()
    git.commit_all(work, "sage: drop wide")

    result = git.push(work)

    assert result.rejected is True
    assert result.behind is False
    assert "wide.bin" in result.detail
    assert result.pushed is False
