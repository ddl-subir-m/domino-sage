"""The rendered history stops being committed (#65).

`.sage/history.md` is regenerated whole from the log beside it on every turn, so two Sage Builders
in one Project conflicted on it every turn over data either one could rebuild. It leaves git. The
log and the Artifacts stay, for the reasons ADR-0006 measured; only this derived file goes.

The catch is the reason the old code gave for committing it: the agent finds this file by grepping,
OpenCode's grep is ripgrep, and ripgrep honours `.gitignore`. A `.ignore` file — which ripgrep reads
ahead of `.gitignore` and git never reads at all — is what keeps the archive greppable once it is
ignored.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from sage.assets.provider import FakeAssetProvider
from sage.orchestrator.service import Orchestrator
from sage.workspace import git

from .test_attach_upload import _catalog, _template

HISTORY_MD = ".sage/history.md"

# What OpenCode's grep tool runs (its ripgrep args, verbatim): hidden files are searched, ignore
# rules are not disabled. So `.sage/` is reachable today only because nothing ignores it.
RG_GREP_ARGS = ["--no-config", "--json", "--hidden", "--no-messages", "--glob=!**/.git/**", "--"]


def _run(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True,
                          check=True).stdout


def _orch(tmp: Path) -> Orchestrator:
    return Orchestrator(workspace_dir=tmp / "mnt" / "code", template=_template(tmp),
                        gateway=object(), catalog=_catalog(), project_id="Sage",
                        assets=FakeAssetProvider())


def _repo(path: Path) -> None:
    """Make an existing directory the root of its own repo, with everything in it committed."""
    _run(path, "init", "-q")
    _run(path, "config", "user.email", "dev@example.com")
    _run(path, "config", "user.name", "Dev")
    _run(path, "add", "-A")
    _run(path, "commit", "-q", "-m", "seed")


def _tracked(path: Path) -> set[str]:
    return set(_run(path, "ls-files").split())


def _turn(orch: Orchestrator, project, prompt: str, reply: str) -> None:
    project.workspace.append_history({"type": "user", "text": prompt})
    project.workspace.append_history({"type": "agent", "kind": "text", "text": reply})
    orch._refresh_history_archive(project)


def test_the_archive_is_written_but_never_tracked(tmp_path: Path):
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    ws = project.workspace.path
    _repo(ws)

    _turn(orch, project, "keep the date filter", "Added the filter.")
    artifact = ws / "examples" / "thr_a" / "revenue.png"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"\x89PNG chart")

    assert "keep the date filter" in project.workspace.history_md_path.read_text()
    git.commit_all(ws, "sage: a turn")
    tracked = _tracked(ws)
    assert HISTORY_MD not in tracked
    # ADR-0006 stands for the two halves that are not derived: the log and the Artifacts.
    assert ".sage/history.jsonl" in tracked
    assert "examples/thr_a/revenue.png" in tracked


def test_a_workspace_that_already_committed_the_archive_stops_tracking_it(tmp_path: Path):
    """A .gitignore line does not untrack a file git already knows about, and every project built
    before this change committed one."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    ws = project.workspace.path
    project.workspace.append_history({"type": "user", "text": "first ask"})
    project.workspace.render_history_md()
    _repo(ws)
    assert HISTORY_MD in _tracked(ws)

    _turn(orch, project, "second ask", "second reply")
    git.commit_all(ws, "sage: a turn")

    assert HISTORY_MD not in _tracked(ws)
    # Untracked, not deleted: the agent reads it on the very next turn.
    assert "second ask" in project.workspace.history_md_path.read_text()


def test_untrack_is_a_no_op_on_a_file_git_never_had(tmp_path: Path):
    ws = tmp_path / "work"
    ws.mkdir()
    (ws / "seed.txt").write_text("seed")
    _repo(ws)
    assert git.untrack(ws, HISTORY_MD) is False


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_the_agent_can_still_grep_the_archive_once_it_is_ignored(tmp_path: Path):
    """The whole reason this file used to be committed. Without the `.ignore` rule a project-wide
    grep returns nothing and the model concludes the user never asked, so this asserts both
    directions: the rule is not decoration, it is the only thing holding the archive in view."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    ws = project.workspace.path
    _repo(ws)
    _turn(orch, project, "keep the date filter", "Added the filter.")

    assert HISTORY_MD in _grep(ws, "date filter")
    (ws / ".ignore").unlink()
    assert HISTORY_MD not in _grep(ws, "date filter")


def _grep(ws: Path, pattern: str) -> str:
    return subprocess.run(["rg", *RG_GREP_ARGS, pattern, "."], cwd=str(ws),
                          capture_output=True, text=True, check=False).stdout


def test_two_builders_take_a_turn_each_and_the_archive_never_conflicts(tmp_path: Path):
    """The point of the change: one Project, two Sage Builders, a turn each, and the file both of
    them regenerate is not one they can collide on.

    `.sage/history.jsonl` still collides — two appends land at the same offset — and that is the
    separate half of #62 that moves the log under `apps/<appId>/`. This test pins the boundary: the
    log is the only conflict, and the archive is not in the list."""
    bare = tmp_path / "remote.git"
    _run(tmp_path, "init", "-q", "--bare", str(bare))

    builders = []
    for name in ("a", "b"):
        orch = _orch(tmp_path / name)
        project = orch.project(start_preview=False)
        ws = project.workspace.path
        if not builders:  # the first builder seeds the shared remote
            _repo(ws)
            _run(ws, "remote", "add", "origin", str(bare))
            _run(ws, "push", "-q", "-u", "origin", "HEAD")
        else:
            shutil.rmtree(ws)
            _run(tmp_path, "clone", "-q", str(bare), str(ws))
            _run(ws, "config", "user.email", "dev@example.com")
            _run(ws, "config", "user.name", "Dev")
        builders.append((orch, project))

    for i, (orch, project) in enumerate(builders):
        ws = project.workspace.path
        _turn(orch, project, f"builder {i} asks", f"builder {i} replies")
        (ws / "src" / f"Panel{i}.tsx").write_text(f"export const P{i} = {i};\n")
        assert git.commit_all(ws, f"sage: turn {i}")
        synced = git.pull(ws)
        assert HISTORY_MD not in synced.conflicts
        if synced.status == "conflict":
            assert synced.conflicts == [".sage/history.jsonl"]
            _resolve_log_conflict(ws)
            git.finalize_merge(ws, "sage: merge remote changes")
        assert git.push(ws).pushed

    remote_files = set(_run(tmp_path, "-C", str(bare), "ls-tree", "-r", "--name-only", "HEAD").split())
    assert HISTORY_MD not in remote_files
    assert {"src/Panel0.tsx", "src/Panel1.tsx"} <= remote_files
    # Each builder still has an archive to read, rendered from its own copy of the log.
    for _, project in builders:
        assert project.workspace.history_md_path.exists()


def _resolve_log_conflict(ws: Path) -> None:
    """Stands in for the agent that resolves a merge in production: keep every line of the
    append-only log, drop the markers. Not what this test is about — it just has to get past it."""
    log = ws / ".sage" / "history.jsonl"
    kept = [ln for ln in log.read_text().splitlines()
            if ln.strip() and not ln.startswith(("<<<<<<<", "=======", ">>>>>>>"))]
    log.write_text("".join(ln + "\n" for ln in kept))


def test_the_template_ships_both_rules(tmp_path: Path):
    """A project seeded after this change never tracks the archive in the first place."""
    template = Path(__file__).resolve().parents[2] / "template" / "react-vite"
    assert HISTORY_MD in _rules(template / ".gitignore")
    assert f"!{HISTORY_MD}" in _rules(template / ".ignore")


def _rules(ignore_file: Path) -> set[str]:
    """The lines git and ripgrep act on — a path named in a comment is not a rule."""
    return {ln.strip() for ln in ignore_file.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}
