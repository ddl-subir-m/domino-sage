"""#234: a push git rejects is committed, not pushed — and must not read as a plain save.

`_save_to_git` already told the difference between "nothing to commit" and "pushed" via
`pushed`, but collapsed every `pushed=False` cause into the same `ok: True`. A person reading
"Saved — push failed: <remote>" saw the same styling as a real save, with no sign their work
never left the machine.

`rejected` is additive: `ok` keeps its value (still True — the work IS committed) so callers
that already branch on `ok` for retry-arming and stop-reporting see no change. Only a caller
that reads `rejected` learns anything new.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace import git


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True,
                          check=True).stdout


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    return t


def _repo(tmp: Path) -> Path:
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


def test_a_push_git_rejects_is_reported_as_rejected_not_a_plain_save(tmp_path: Path, monkeypatch):
    root = _repo(tmp_path)
    orch = _orch(tmp_path, root)
    project = orch.project(start_preview=False)
    (root / "apps" / project.workspace.app_id / "src" / "App.tsx").write_text("// the turn's work\n")

    # git.push()'s own non-fast-forward behavior is test_git.py's job; here we're pinning that
    # _save_to_git forwards result.rejected into the event untouched.
    monkeypatch.setattr(git, "push", lambda path: git.SaveResult(
        pushed=False, detail="push failed: rejected non-fast-forward", rejected=True))

    saved = orch._save_to_git(project, "add a chart")

    assert saved["ok"] is True  # the work IS committed — not a failure
    assert saved["pushed"] is False
    assert saved["rejected"] is True
    assert "push failed" in saved["detail"]


def test_no_changes_to_commit_is_still_a_plain_save(tmp_path: Path):
    """Regression pin (#234): the pre-existing "nothing to commit" case is a genuine success and
    must not pick up `rejected` as a side effect of adding it elsewhere."""
    root = _repo(tmp_path)
    orch = _orch(tmp_path, root)
    project = orch.project(start_preview=False)
    orch._save_to_git(project, "seed the app template")  # settle attach's own commit first

    saved = orch._save_to_git(project, "nothing changed")

    assert saved["ok"] is True
    assert saved["pushed"] is False
    assert saved.get("rejected", False) is False
    assert "no changes" in saved["detail"]


def test_no_remote_is_still_a_plain_save(tmp_path: Path):
    """committed-but-no-remote is also pushed=False and must not read as rejected."""
    root = _repo(tmp_path)
    orch = _orch(tmp_path, root)
    project = orch.project(start_preview=False)
    (root / "apps" / project.workspace.app_id / "src" / "App.tsx").write_text("// work\n")

    saved = orch._save_to_git(project, "add a chart")

    assert saved["ok"] is True
    assert saved["pushed"] is False
    assert saved.get("rejected", False) is False
