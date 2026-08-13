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
    (t / "node_modules" / ".bin").mkdir()
    (t / "node_modules" / ".bin" / "vite").write_text("#!/bin/sh")  # the usable-deps sentinel
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


def test_link_warm_deps_repairs_what_a_failed_npm_install_leaves(tmp_path: Path):
    # The live failure (2026-08-13): `npm install <404 package>` deletes the symlink during reify,
    # then aborts, leaving a real directory with no vite in it. Every later build and the preview
    # fail until this is put back.
    tmpl = _fake_template(tmp_path)
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl)
    ws = mgr.ensure("p")
    nm = ws.path / "node_modules"
    nm.unlink()
    nm.mkdir()
    (nm / ".package-lock.json").write_text("{}")   # npm's leftovers, no .bin/vite

    assert mgr.link_warm_deps() is True
    assert nm.is_symlink() and (nm / "dep").read_text() == "x"
    assert mgr.link_warm_deps() is False           # healthy now — repeat calls are no-ops


def test_link_warm_deps_leaves_a_successful_agent_install_alone(tmp_path: Path):
    # npm CAN rebuild the whole tree from package.json when the install resolves. That directory is
    # real, complete, and may hold a package the agent legitimately added — never clobber it.
    tmpl = _fake_template(tmp_path)
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl)
    ws = mgr.ensure("p")
    nm = ws.path / "node_modules"
    nm.unlink()
    (nm / ".bin").mkdir(parents=True)
    (nm / ".bin" / "vite").write_text("#!/bin/sh")
    (nm / "date-fns").mkdir()

    assert mgr.link_warm_deps() is False
    assert not nm.is_symlink() and (nm / "date-fns").is_dir()


def test_link_warm_deps_relinks_a_dangling_symlink(tmp_path: Path):
    # os.symlink onto an existing-but-dangling link raises FileExistsError: exists() follows the
    # link and reports False while the link itself is still there.
    tmpl = _fake_template(tmp_path)
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl)
    ws = mgr.ensure("p")
    nm = ws.path / "node_modules"
    nm.unlink()
    nm.symlink_to(tmp_path / "gone")

    assert mgr.link_warm_deps() is True
    assert (nm / "dep").read_text() == "x"


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


def test_refresh_entry_script_replaces_a_stale_committed_copy(tmp_path: Path):
    # app.sh is committed to the app's repo at seed time, so an app created from an older image
    # keeps its original copy. Publish refreshes it, or template fixes never reach existing apps.
    tmpl = _fake_template(tmp_path)
    (tmpl / "app.sh").write_text("#!/usr/bin/env bash\nexport PATH=/usr/local/bin:/usr/bin:$PATH\n")
    (tmpl / "app.sh").chmod(0o755)
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl)
    ws = mgr.ensure("proj1")
    (ws.path / "app.sh").write_text("#!/usr/bin/env bash\nexport PATH=/usr/bin:/usr/local/bin:$PATH\n")

    assert mgr.refresh_entry_script() is True
    assert "PATH=/usr/local/bin:/usr/bin" in (ws.path / "app.sh").read_text()
    assert (ws.path / "app.sh").stat().st_mode & 0o111  # still executable for Domino
    assert mgr.refresh_entry_script() is False  # already current — nothing to commit


def test_refresh_entry_script_restores_a_missing_one(tmp_path: Path):
    # Apps seeded before app.sh existed have no entry script at all; Domino fails those opaquely.
    tmpl = _fake_template(tmp_path)
    (tmpl / "app.sh").write_text("echo hi\n")
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl)
    ws = mgr.ensure("proj1")
    (ws.path / "app.sh").unlink()

    assert mgr.refresh_entry_script() is True
    assert (ws.path / "app.sh").read_text() == "echo hi\n"
