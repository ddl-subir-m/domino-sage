"""#459 (ADR-0065): a save with nothing new to commit still has something to send.

A refused push answers `ok: True` and leaves the commits sitting on this disk. The next save then
hits `_save_to_git`'s early return — "no changes to commit" — and never reaches `git.push`, so an
**ahead-but-clean** workspace can never catch up: not on the timer, not on the next open, and not at
shutdown, which takes the same return.

The remote that refuses here is a real one. A bare repo with a `pre-receive` hook that exits 1
fetches exactly like a healthy remote and declines every push, which is the shape `rejected=True`
actually describes — a dead URL is a different bug (`git.pull` raises and the save answers
`ok: False`, which already arms a retry today).
"""
from __future__ import annotations

from pathlib import Path

from sage.workspace import git

from .test_incoming_changes import _git, _orch, _project, _repo


def _refuse_pushes(tmp: Path) -> Path:
    """Make `_repo`'s bare remote decline every push while still serving fetches."""
    hook = tmp / "remote.git" / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    return hook


def _remote_head(tmp: Path, branch: str = "HEAD") -> str:
    return _git(tmp / "remote.git", "rev-parse", branch).strip()


def _solo(tmp: Path) -> Path:
    """A repo root with no remote at all."""
    root = tmp / "solo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "f.txt").write_text("hi\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "one")
    return root


# --- the reader ---------------------------------------------------------------------------------

def test_a_refused_push_leaves_the_work_unsent(tmp_path: Path):
    """The whole reason `unsent` can be a local read: a refused push does not move `origin/<branch>`
    and a successful one does, so the refs a push already left behind are an honest record of what
    the remote was actually given."""
    root = _repo(tmp_path)
    assert git.unsent(root) is False            # seeded and pushed — nothing owing

    _refuse_pushes(tmp_path)
    (root / "work.txt").write_text("local work\n")
    git.commit_all(root, "sage: local work")
    assert git.push(root).rejected is True
    assert git.unsent(root) is True

    (tmp_path / "remote.git" / "hooks" / "pre-receive").unlink()
    assert git.push(root).pushed is True
    assert git.unsent(root) is False


def test_a_branch_that_was_never_pushed_is_unsent(tmp_path: Path):
    """Where the mirror stops being a mirror. `incoming()` reads a missing `origin/<branch>` as
    nothing to pull, which is right for it; here the same missing ref means nothing has EVER been
    sent, which is the worst case rather than a quiet one."""
    root = _repo(tmp_path)
    _git(root, "checkout", "-q", "-b", "a-branch-nobody-has-seen")
    (root / "work.txt").write_text("local work\n")
    git.commit_all(root, "sage: local work")

    assert git.incoming(root) == git.Incoming("", [])    # the naive mirror would be silent here
    assert git.unsent(root) is True


def test_a_workspace_with_nobody_to_send_to_has_nothing_unsent(tmp_path: Path):
    """No remote, and not a repo root at all (the local `/tmp` workspace, #20). Neither is saved
    through a push, so neither can be owing one."""
    assert git.unsent(_solo(tmp_path)) is False

    not_a_root = tmp_path / "solo" / "subdir"
    not_a_root.mkdir()
    assert git.unsent(not_a_root) is False


def test_a_repo_with_no_commits_has_nothing_unsent(tmp_path: Path):
    """`origin/<branch>` is missing here too, but for the opposite reason: there is no HEAD to send.
    Answering True would put a `git push` on an empty repo, which fails and reads as a refusal."""
    root = tmp_path / "fresh"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "remote", "add", "origin", str(tmp_path / "somewhere.git"))

    assert git.has_remote(root) is True
    assert git.unsent(root) is False


def test_unsent_makes_no_network_call(tmp_path: Path, monkeypatch):
    """Pinned, not asserted in a comment. `unsent` runs on the save path of six callers, and a
    fetch hidden in it would put a round trip on every one of them."""
    root = _repo(tmp_path)
    _refuse_pushes(tmp_path)
    (root / "work.txt").write_text("local work\n")
    git.commit_all(root, "sage: local work")
    git.push(root)

    seen: list[tuple[str, ...]] = []
    real = git._git
    monkeypatch.setattr(git, "_git", lambda path, *args, **kw: (seen.append(args),
                                                                real(path, *args, **kw))[1])

    assert git.unsent(root) is True
    assert seen, "nothing was recorded — the interception missed"
    assert not [a for a in seen if a[0] in ("fetch", "push", "pull", "ls-remote")]


# --- the save -----------------------------------------------------------------------------------

def test_an_ahead_but_clean_workspace_pushes(tmp_path: Path):
    """The one that matters. Commit, refuse the push, change nothing, save again — the remote must
    receive it. Before ADR-0065 the second save returned "no changes to commit" and the work stayed
    on this disk for the life of the container."""
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    project = _project(orch)
    behind = _remote_head(tmp_path)

    _refuse_pushes(tmp_path)
    (root / "work.txt").write_text("local work\n")
    refused = orch._save_to_git(project, "chat (turn)")
    assert refused["rejected"] is True
    assert _remote_head(tmp_path) == behind          # committed here, never sent

    (tmp_path / "remote.git" / "hooks" / "pre-receive").unlink()
    again = orch._save_to_git(project, "chat (idle)")   # nothing has changed in the tree

    assert again["pushed"] is True
    assert not again.get("rejected")
    assert _remote_head(tmp_path) == _git(root, "rev-parse", "HEAD").strip()


def test_a_clean_workspace_with_nothing_owing_still_skips_the_push(tmp_path: Path):
    """The early return is narrowed, not deleted. Five deliberate acts call this; a workspace with
    nothing committed and nothing ahead must still coalesce rather than pay a round trip."""
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    project = _project(orch)
    pushes: list[Path] = []
    orig = git.push
    git.push = lambda path: (pushes.append(path), orig(path))[1]
    try:
        orch._save_to_git(project, "chat (turn)")       # commits the seeded Project, pushes it
        pushes.clear()
        result = orch._save_to_git(project, "chat (idle)")
    finally:
        git.push = orig

    assert result["detail"] == "no changes to commit"
    assert pushes == []


def test_shutdown_pushes_an_ahead_but_clean_workspace(tmp_path: Path):
    """Shutdown's save took the same early return, which is what made the exposure permanent: the
    graceful stop, the one event that was supposed to catch this, could not."""
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    _project(orch)

    _refuse_pushes(tmp_path)
    (root / "work.txt").write_text("local work\n")
    assert orch._save_to_git(orch._project, "chat (turn)")["rejected"] is True

    (tmp_path / "remote.git" / "hooks" / "pre-receive").unlink()
    orch.shutdown()

    assert _remote_head(tmp_path) == _git(root, "rev-parse", "HEAD").strip()


# --- the flush gate -----------------------------------------------------------------------------

def test_the_flush_gate_asks_git_and_not_only_the_flag(tmp_path: Path):
    """`_chat_dirty` means "Chat has files the local repo has not committed", and after a commit
    with a refused push the files ARE committed — so the flag is correctly False and the gate would
    return before any save. The question is asked of git instead of overloading the flag."""
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    _project(orch)

    _refuse_pushes(tmp_path)
    (root / "work.txt").write_text("local work\n")
    orch._chat_dirty = True
    assert orch._flush_chat_save("turn")["rejected"] is True
    assert orch._chat_dirty is False            # committed, so the flag is right to be clear

    (tmp_path / "remote.git" / "hooks" / "pre-receive").unlink()
    landed = orch._flush_chat_save("idle")

    assert landed is not None, "the gate returned on a clear flag with work still owing"
    assert landed["pushed"] is True
    assert _remote_head(tmp_path) == _git(root, "rev-parse", "HEAD").strip()


def test_no_timer_is_armed_after_a_refusal(tmp_path: Path):
    """A negative, so asserted directly. Re-arming would be a `git pull` and a push every 30s for
    the life of the process, and a retry never fixes the credential case that dominates."""
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    _project(orch)

    _refuse_pushes(tmp_path)
    (root / "work.txt").write_text("local work\n")
    orch._chat_dirty = True
    result = orch._flush_chat_save("turn")

    assert result["rejected"] is True
    assert orch._chat_save_timer is None
    assert orch._chat_save_failed == result      # told instead (ADR-0064)


def test_a_project_with_no_remote_clears_and_arms_nothing(tmp_path: Path):
    """The case that must not be swept up. No remote answers `pushed=False` with `rejected` unset
    and is saved as far as anyone can be; it must clear the flag, arm no timer and stay quiet."""
    root = _solo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    _project(orch)

    (root / "work.txt").write_text("local work\n")
    orch._chat_dirty = True
    result = orch._flush_chat_save("turn")

    assert result["pushed"] is False
    assert not result.get("rejected")
    assert orch._chat_dirty is False
    assert orch._chat_save_timer is None
    assert orch._chat_save_failed is None
    assert orch._flush_chat_save("idle") is None   # and nothing is owing on the next pass


def test_a_workspace_that_is_not_a_repo_root_still_clears(tmp_path: Path):
    """The local `/tmp` workspace. `_save_to_git` answers None before any of this, and None is
    "nothing to save" rather than "the save failed"."""
    root = tmp_path / "mnt" / "code"
    root.mkdir(parents=True)
    orch, _oc = _orch(tmp_path, root)
    _project(orch)

    orch._chat_dirty = True
    assert orch._flush_chat_save("turn") is None
    assert orch._chat_dirty is False
    assert orch._chat_save_timer is None
    assert orch._chat_save_failed is None
    assert orch._flush_chat_save("idle") is None


# --- the branch nobody has seen -----------------------------------------------------------------

def test_a_branch_that_was_never_pushed_is_sent(tmp_path: Path):
    """Reporting it is half the job; the ticket asks that it is SENT.

    A bare `git push` refuses a branch with no upstream — `fatal: ... has no upstream branch`,
    exit 128, which `git.push` reads as `rejected`. So `unsent` answering True here would have
    driven a push that can never succeed: a permanent `saveFailed` banner, plus a `commit_all` and
    a `git pull` on every door, for a workspace whose only sin is being on a branch. The upstream
    is set when there is none, which is what makes the True answer true.
    """
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    project = _project(orch)
    _git(root, "checkout", "-q", "-b", "a-branch-nobody-has-seen")
    (root / "work.txt").write_text("local work\n")
    git.commit_all(root, "sage: local work")
    assert git.unsent(root) is True

    result = orch._save_to_git(project, "chat (idle)")

    assert result["pushed"] is True, result["detail"]
    assert not result.get("rejected")
    assert _remote_head(tmp_path, "refs/heads/a-branch-nobody-has-seen") == \
        _git(root, "rev-parse", "HEAD").strip()
    assert git.unsent(root) is False           # and the gate now closes behind it


def test_a_detached_head_has_nothing_unsent(tmp_path: Path):
    """`current_branch` answers the literal "HEAD" when the head is detached, and `origin/HEAD`
    RESOLVES in a clone — so the naive read counts against the remote's default branch and says
    True about a state `git push` refuses outright. There is no branch to send; the answer is no."""
    root = _repo(tmp_path)
    _git(root, "checkout", "-q", "--detach")
    (root / "work.txt").write_text("local work\n")
    git.commit_all(root, "sage: local work")

    assert git.current_branch(root) == "HEAD"      # the producer, pinned rather than assumed
    assert git.unsent(root) is False


def test_a_ref_read_that_fails_reads_as_unsent(tmp_path: Path, monkeypatch):
    """The deliberate asymmetry, asserted rather than left to the docstring. An unreadable count
    is not evidence that the remote has everything — the one answer this reader must not guess."""
    root = _repo(tmp_path)
    real = git._git

    def fail_rev_list(path, *args, **kw):
        if args[:1] == ("rev-list",):
            return real(path, "rev-list", "--count", "definitely-not-a-ref", check=False)
        return real(path, *args, **kw)

    monkeypatch.setattr(git, "_git", fail_rev_list)
    assert git.unsent(root) is True
