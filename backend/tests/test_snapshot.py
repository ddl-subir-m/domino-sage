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


def test_working_tree_hash_is_per_turn_and_catches_re_edits(tmp_path: Path):
    # The per-turn wrote_code signal: capture the tree hash at a turn's start, compare at its end.
    # Unlike changed_since_pre_turn() (cumulative vs the build-start commit), this must report change
    # ONLY for edits made within the window it brackets — including re-editing an already-dirty file,
    # which `git status --porcelain` alone can't distinguish.
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))
    ws = mgr.ensure("p")
    snap = TurnSnapshot(ws.path)
    snap.commit_before_turn()

    # Turn 1 writes a file.
    t1_start = snap.working_tree_hash()
    (ws.path / "src" / "App.tsx").write_text("edited by agent")
    assert snap.working_tree_hash() != t1_start  # turn 1 wrote code

    # Turn 2 does nothing: its own start/end hashes match, even though the build is dirty vs baseline.
    t2_start = snap.working_tree_hash()
    assert snap.working_tree_hash() == t2_start  # turn 2 wrote nothing...
    assert snap.changed_since_pre_turn() is True  # ...though the cumulative check still sees turn 1

    # Turn 3 re-edits the same already-dirty file: the per-turn hash must still change.
    t3_start = snap.working_tree_hash()
    (ws.path / "src" / "App.tsx").write_text("edited again by agent")
    assert snap.working_tree_hash() != t3_start


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
