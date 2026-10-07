"""No test runs `npm ci` into the repo's own React+Vite template (#689).

A worktree has no `template/react-vite/node_modules`, and `link_warm_deps` installs one when it is
missing. So a test that opened a workspace on the real template did a ~190 MB network install, and
from then on that worktree ran the tests gated on the template's packages, which a fresh worktree
skips: the skip set depended on which tests had run there before.
"""
from pathlib import Path

import pytest

from sage.workspace.manager import WorkspaceManager
from sage.workspace.stack import REACT_VITE


def _no_subprocess(monkeypatch):
    def fail(*_a, **_k):
        raise AssertionError("a test ran npm against the repo's template")

    monkeypatch.setattr("sage.workspace.manager.subprocess.run", fail)


def test_the_real_template_is_never_npm_installed_by_a_test(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    assert (REACT_VITE.template_dir / "package-lock.json").is_file()
    _no_subprocess(monkeypatch)
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=REACT_VITE.template_dir)
    mgr.ensure("p")
    mgr.install_template_deps()


def test_a_test_template_with_a_lockfile_still_reaches_the_install(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    t = tmp_path / "template"
    (t / "src").mkdir(parents=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "package-lock.json").write_text("{}")
    calls = []
    monkeypatch.setattr("sage.workspace.manager.subprocess.run",
                        lambda cmd, **_k: calls.append(cmd))
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=t)
    mgr.ensure("p")
    calls.clear()
    mgr.install_template_deps()
    assert calls and calls[0][:2] == ["npm", "ci"]
