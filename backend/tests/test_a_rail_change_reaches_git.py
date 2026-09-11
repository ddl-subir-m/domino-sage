"""A change to the Project's working set is saved, like any other deliberate act.

`remove_project_resource` and `add_project_resource` wrote `.sage/project-resources.json` to the
working tree and stopped there. The file only ever reached git when something else — a chat turn,
a handoff, a build, a publish, a stop — happened to save afterwards. So a creator who removed a
Resource and then deleted the Workspace lost the removal: the next Workspace clones HEAD, and the
row it thought it had removed is still in the rail.

The act is the one `delete_thread` already models — deliberate, Project-scoped, and worth a commit
of its own — so it saves the same way rather than arming the idle timer. Arming would have left
exactly the window this bug was found in: the creator removes a Resource and deletes the Workspace
inside the idle delay, and the change is gone anyway.
"""
from __future__ import annotations

from pathlib import Path

from .test_chat_turn import _orch, _track_saves

CLAIMS = {"id": "dataset:ds_claims", "kind": "dataset", "name": "claims"}


def _quiet(orch):
    """A clean slate: nothing else is pending, so the save under test is the only one."""
    orch._cancel_chat_idle_save()
    orch._chat_dirty = False
    return _track_saves(orch)


def test_removing_a_resource_saves_it(tmp_path: Path):
    orch, _oc = _orch(tmp_path)
    orch.add_project_resource(dict(CLAIMS))
    calls = _quiet(orch)

    assert orch.remove_project_resource("dataset:ds_claims") is True

    assert calls == ["chat (project resources)"]


def test_adding_a_resource_saves_it(tmp_path: Path):
    """The same loss in the other direction: add, delete the Workspace, and the row never existed."""
    orch, _oc = _orch(tmp_path)
    calls = _quiet(orch)

    assert orch.add_project_resource(dict(CLAIMS))["added"] is True

    assert calls == ["chat (project resources)"]


def test_adding_a_row_that_is_already_there_saves_nothing(tmp_path: Path):
    """Idempotent on id (ADR-0018), so a re-add that changed no file is not a commit."""
    orch, _oc = _orch(tmp_path)
    orch.add_project_resource(dict(CLAIMS))
    calls = _quiet(orch)

    assert orch.add_project_resource(dict(CLAIMS))["added"] is False

    assert calls == []


def test_removing_a_row_that_was_never_there_saves_nothing(tmp_path: Path):
    orch, _oc = _orch(tmp_path)
    calls = _quiet(orch)

    assert orch.remove_project_resource("dataset:never") is False

    assert calls == []


def test_a_rail_change_made_mid_turn_is_deferred_not_dropped(tmp_path: Path):
    """`_flush_chat_save` walks the tree and commits, so it will not run under a live turn.

    It arms the idle timer instead. That is the one case where this act coalesces like chat does,
    and it has to, or a rail change made while a build is running would be silently lost — which is
    the bug, back again by a narrower door.
    """
    orch, _oc = _orch(tmp_path)
    calls = _quiet(orch)

    orch._turn_lock.acquire()
    try:
        assert orch.add_project_resource(dict(CLAIMS))["added"] is True
        assert calls == []
        assert orch._chat_dirty is True
        assert orch._chat_save_timer is not None
    finally:
        orch._turn_lock.release()

    orch._cancel_chat_idle_save()
    orch._on_chat_save_idle()
    assert calls == ["chat (project resources)"]
