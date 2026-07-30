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


def test_has_built_latches_on_and_persists(tmp_path: Path):
    # Drives the first-BUILD plan gate: starts false, latches true on the first build, survives a
    # fresh manager (restart) via settings, and mark_built is idempotent.
    tmpl = _fake_template(tmp_path)
    ws = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl).ensure("p")
    assert ws.has_built() is False
    ws.mark_built()
    assert ws.has_built() is True
    ws.mark_built()  # idempotent
    assert ws.has_built() is True
    # Persisted: a new manager/workspace over the same dir still sees it built.
    ws2 = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl).ensure("p")
    assert ws2.has_built() is True


def test_mark_built_preserves_other_settings(tmp_path: Path):
    ws = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path)).ensure("p")
    ws.write_settings({"skip_planning": True})
    ws.mark_built()
    settings = ws.read_settings()
    assert settings["skip_planning"] is True and settings["built"] is True


def test_plan_artifact_roundtrip(tmp_path: Path):
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))
    ws = mgr.ensure("p")
    assert ws.read_plan() is None
    ws.write_plan("# plan\nstep 1")
    assert ws.read_plan() == "# plan\nstep 1"
    assert ws.plan_path.parent.name == ".sage"
