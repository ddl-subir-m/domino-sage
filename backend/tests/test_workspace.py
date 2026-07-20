"""Workspace module tests (Step 3.2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.workspace.manager import WorkspaceManager


def _fake_template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "node_modules").mkdir()
    (t / "node_modules" / "dep").write_text("x")
    return t


def test_create_seeds_from_template_and_symlinks_node_modules(tmp_path: Path):
    tmpl = _fake_template(tmp_path)
    mgr = WorkspaceManager(root=tmp_path / "ws", template=tmpl)

    ws = mgr.create("proj1")

    assert ws.app_entry.read_text() == "placeholder"
    assert (ws.path / "package.json").exists()
    nm = ws.path / "node_modules"
    assert nm.is_symlink() and (nm / "dep").read_text() == "x"  # warm deps, not copied


def test_create_rejects_existing(tmp_path: Path):
    mgr = WorkspaceManager(root=tmp_path / "ws", template=_fake_template(tmp_path))
    mgr.create("p")
    with pytest.raises(FileExistsError):
        mgr.create("p")


def test_plan_artifact_roundtrip(tmp_path: Path):
    mgr = WorkspaceManager(root=tmp_path / "ws", template=_fake_template(tmp_path))
    ws = mgr.create("p")
    assert ws.read_plan() is None
    ws.write_plan("# plan\nstep 1")
    assert ws.read_plan() == "# plan\nstep 1"
    assert ws.plan_path.parent.name == ".sage"


def test_get_returns_none_when_absent(tmp_path: Path):
    mgr = WorkspaceManager(root=tmp_path / "ws", template=_fake_template(tmp_path))
    assert mgr.get("nope") is None
    mgr.create("yes")
    assert mgr.get("yes") is not None
