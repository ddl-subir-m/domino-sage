"""Turn-snapshot revert tests (stop-button support)."""
from __future__ import annotations

from pathlib import Path

from sage.workspace.manager import WorkspaceManager
from sage.workspace.snapshot import TurnSnapshot


def _fake_template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "node_modules").mkdir()
    (t / "node_modules" / "dep").write_text("x")
    return t


def test_discard_changes_reverts_edits_and_removes_new_files(tmp_path: Path):
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))
    ws = mgr.ensure("p")
    snap = TurnSnapshot(ws.path)

    snap.commit_before_turn()
    (ws.path / "src" / "App.tsx").write_text("edited by agent")
    (ws.path / "src" / "New.tsx").write_text("brand new file")

    snap.discard_changes()

    assert (ws.path / "src" / "App.tsx").read_text() == "placeholder"
    assert not (ws.path / "src" / "New.tsx").exists()


def test_discard_changes_does_not_touch_excluded_dirs(tmp_path: Path):
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))
    ws = mgr.ensure("p")
    snap = TurnSnapshot(ws.path)

    snap.commit_before_turn()
    (ws.path / "node_modules" / "extra.txt").write_text("should survive")

    snap.discard_changes()

    assert (ws.path / "node_modules" / "extra.txt").exists()


def test_changed_since_pre_turn_detects_edits_and_new_files(tmp_path: Path):
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))
    ws = mgr.ensure("p")
    snap = TurnSnapshot(ws.path)

    snap.commit_before_turn()
    assert snap.changed_since_pre_turn() is False  # nothing touched yet

    (ws.path / "src" / "App.tsx").write_text("edited by agent")
    assert snap.changed_since_pre_turn() is True


def test_changed_since_pre_turn_ignores_excluded_dirs(tmp_path: Path):
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))
    ws = mgr.ensure("p")
    snap = TurnSnapshot(ws.path)

    snap.commit_before_turn()
    (ws.path / "node_modules" / "extra.txt").write_text("install artifact, not agent code")

    assert snap.changed_since_pre_turn() is False


def test_history_truncate_drops_entries_after_baseline(tmp_path: Path):
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))
    ws = mgr.ensure("p")

    ws.append_history({"type": "user", "text": "first"})
    baseline = ws.history_len()
    ws.append_history({"type": "user", "text": "second (to be reverted)"})
    ws.append_history({"type": "agent", "kind": "text", "text": "partial reply"})

    ws.truncate_history(baseline)

    assert [h["text"] for h in ws.read_history()] == ["first"]
