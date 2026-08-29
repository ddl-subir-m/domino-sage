"""Every machine commit in a Built App repo reads `build: `, not `sage: ` (#120).

The prefix is prose a person reads, in a repo the user owns and a partner's customer can read. But
git history is immutable and a pack can change, so it cannot be re-branded the way a UI string is:
it is de-named once instead, for every pack including Domino's (ADR-0014's third arm).

KEPT rather than dropped, because it does real work — it marks a machine commit in a repo a person
also commits to. Nothing parses it: there is no reader anywhere in the tree, which is why renaming
it is safe and why there is no test here pinning a parser that does not exist.

Forward-only. A repo whose history already says `sage: ` keeps it, and the last test is the one that
says so — rewriting a committed record to fix a name is not on the table.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sage.orchestrator.service import Orchestrator
from sage.provision.seed import seed_and_push
from sage.router.models import ModelCatalog


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True,
                          check=True).stdout


def _subject(path: Path, rev: str = "HEAD") -> str:
    return _git(path, "log", "-1", "--format=%s", rev).strip()


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building this app\n")
    return t


def _repo(tmp: Path) -> Path:
    """A Project volume that is the root of its own repo. No remote: the push is another test's
    subject, and `_save_to_git` commits before it pushes."""
    root = tmp / "mnt" / "code"
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "dev@example.com")
    _git(root, "config", "user.name", "Dev")
    (root / "README.md").write_text("project\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    return root


def _orch(tmp: Path, root: Path) -> Orchestrator:
    return Orchestrator(
        workspace_dir=root, template=_template(tmp), gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage")


def _save(tmp: Path, prompt: str) -> str:
    """One build's save, and the subject it left in the log."""
    root = _repo(tmp)
    orch = _orch(tmp, root)
    project = orch.project(start_preview=False)
    (root / "apps" / project.workspace.app_id / "src" / "App.tsx").write_text("// the turn's work\n")
    orch._save_to_git(project, prompt)
    return _subject(root)


def test_a_build_commit_carries_the_prompt_behind_the_new_prefix(tmp_path: Path):
    assert _save(tmp_path, "add a revenue chart") == "build: add a revenue chart"


def test_only_the_first_line_of_the_prompt_reaches_the_subject(tmp_path: Path):
    # A subject line, not the prompt: a multi-line prompt would make `git log --oneline` unreadable,
    # and the whole prompt is in `.sage/history.jsonl` anyway (ADR-0006).
    assert _save(tmp_path, "add a chart\n\nand make it blue") == "build: add a chart"


def test_a_long_prompt_is_still_cut_to_a_subject_length(tmp_path: Path):
    assert _save(tmp_path, "x" * 200) == "build: " + "x" * 72


def test_a_turn_with_nothing_typed_still_marks_itself_as_a_machine_commit(tmp_path: Path):
    # The prefix is kept for exactly this: a person commits to this repo too, and a bare subject
    # would not say which of the two wrote it.
    assert _save(tmp_path, "   ").startswith("build: ")


def test_the_seed_commit_is_named_for_what_it_is(tmp_path: Path):
    template = tmp_path / "seed-template"
    template.mkdir()
    (template / "index.html").write_text("<!doctype html>")
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))

    seed_and_push(str(bare), template, settings={"displayName": "Quarterly Revenue"})

    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", str(bare), str(checkout))
    assert _subject(checkout) == "Initial commit"
    # The settings still ride in that commit — the message is all that moved.
    assert json.loads((checkout / ".sage" / "settings.json").read_text())["displayName"]


def test_a_repo_whose_history_already_says_sage_keeps_it(tmp_path: Path):
    """Forward-only, which is the whole reason this is a rename and not a migration."""
    root = _repo(tmp_path)
    (root / "old.txt").write_text("built before the rename\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "sage: an older build")
    before = _git(root, "rev-parse", "HEAD").strip()

    orch = _orch(tmp_path, root)
    project = orch.project(start_preview=False)
    (root / "apps" / project.workspace.app_id / "src" / "App.tsx").write_text("// newer\n")
    orch._save_to_git(project, "a newer build")

    assert _subject(root) == "build: a newer build"
    assert _subject(root, before) == "sage: an older build"
