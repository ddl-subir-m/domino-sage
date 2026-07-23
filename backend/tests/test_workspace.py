"""Workspace module tests — single bound workspace, idempotent seed-in-place."""
from __future__ import annotations

from pathlib import Path

from sage.workspace.manager import WorkspaceManager


def _fake_template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "node_modules").mkdir()
    (t / "node_modules" / "dep").write_text("x")
    return t


def test_ensure_seeds_from_template_and_symlinks_node_modules(tmp_path: Path):
    tmpl = _fake_template(tmp_path)
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl)

    ws = mgr.ensure("proj1")

    assert ws.app_entry.read_text() == "placeholder"
    assert (ws.path / "package.json").exists()
    nm = ws.path / "node_modules"
    assert nm.is_symlink() and (nm / "dep").read_text() == "x"  # warm deps, not copied


def test_ensure_is_idempotent_and_never_clobbers_existing_app(tmp_path: Path):
    tmpl = _fake_template(tmp_path)
    ws_dir = tmp_path / "ws"
    # Pre-existing app (e.g. a fresh git checkout): its files and .git must survive.
    ws_dir.mkdir()
    (ws_dir / "package.json").write_text('{"name": "mine"}')
    (ws_dir / ".git").mkdir()
    (ws_dir / "src").mkdir()
    (ws_dir / "src" / "App.tsx").write_text("my code")
    mgr = WorkspaceManager(workspace_dir=ws_dir, template=tmpl)

    mgr.ensure("proj1")
    mgr.ensure("proj1")  # second call is a no-op

    assert (ws_dir / "package.json").read_text() == '{"name": "mine"}'
    assert ws_dir / ".git" in list(ws_dir.iterdir())
    assert (ws_dir / "src" / "App.tsx").read_text() == "my code"


def test_ensure_seeds_into_preexisting_empty_dir(tmp_path: Path):
    tmpl = _fake_template(tmp_path)
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()  # exists but has no package.json -> gets seeded
    mgr = WorkspaceManager(workspace_dir=ws_dir, template=tmpl)

    ws = mgr.ensure("proj1")

    assert ws.app_entry.read_text() == "placeholder"


def test_plan_artifact_roundtrip(tmp_path: Path):
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))
    ws = mgr.ensure("p")
    assert ws.read_plan() is None
    ws.write_plan("# plan\nstep 1")
    assert ws.read_plan() == "# plan\nstep 1"
    assert ws.plan_path.parent.name == ".sage"
