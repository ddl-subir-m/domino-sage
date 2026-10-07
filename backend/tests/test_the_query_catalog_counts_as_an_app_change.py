"""A write to `.sage/queries.json` is a change to the app (#671, #672).

The turn snapshot excludes `.sage/`, Sage's own state, so the one agent-owned file in it — the query
catalog — was invisible to every tree comparison. Live (2026-10-06): a follow-up whose step built the
drift queries was told "Not built from the plan: step 1 (Drift queries)", and follow-ups whose fix
was a repointed query stopped at the pre-edit work limit saying the app had not changed while the
reply said it was fixed. Both were reading the same blind tree.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.pre_edit_guard import PreEditAction, PreEditGuard

from .fake_opencode import Turn, execution_plan
from .test_a_dead_alias_stops_the_turn_before_it_starts import _no_waiting, _orch  # noqa: F401
from .test_turn_path import TABLE_PLAN, _build, _done, _get_built, _run

QUERIES = ".sage/queries.json"

DRIFT_PLAN = Turn(text=execution_plan(
    "Usage Drift", "A drift tab.", "Drift queries", files=QUERIES,
    work="Add the drift queries.") + (
    "\n\n### 2. Drift tab\n- Files — src/App.tsx\n- Do — Add the drift tab.\n"
    "- Done when — The tab shows drift."))


def _built(tmp_path: Path, then: list[Turn]):
    orch, _oc, _gw = _build(tmp_path, [
        Turn(text=TABLE_PLAN), Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        *then,
    ], verdict="BUILD")
    _get_built(orch)
    return orch


def test_a_step_that_wrote_the_query_catalog_reads_as_built(tmp_path: Path):
    orch, _ = _orch(tmp_path, turns=[DRIFT_PLAN, Turn(writes={
        QUERIES: '{"drift": {"sql": "select 1"}}\n', "src/App.tsx": "// drift tab\n"})])
    list(orch.build_stream("add a usage drift tab", conversation="c1"))
    list(orch.approve_stream(conversation="c1"))
    history = orch.project(start_preview=False).app_for_turn().read_history("c1")

    assert "plan-unbuilt" not in [r["type"] for r in history]


def test_a_follow_up_that_only_repoints_a_query_is_a_finished_edit(tmp_path: Path):
    orch = _built(tmp_path, [Turn(text="Repointed the query.", writes={
        QUERIES: '{"drift": {"sql": "select 2"}}\n'})])

    events = _run(orch, "point the drift query at the usage binding")

    assert "build-pre-edit-limit" not in [e["type"] for e in events]
    assert _done(events)["ok"] is True


@pytest.mark.parametrize("rel", ["src/App.tsx", QUERIES])
def test_a_stop_that_leaves_a_change_does_not_say_the_app_is_unchanged(
        tmp_path: Path, monkeypatch, rel: str):
    """A write landing after the guard's last look is still on disk when the stop is written."""
    orch = _built(tmp_path, [Turn(text="Looking."), Turn(text="Still looking.")])
    late = orch.project(start_preview=False).app_for_turn().path / rel
    decide = PreEditGuard.no_edit_completion

    def then_a_late_write(self):
        decision = decide(self)
        if decision.action is PreEditAction.STOP:
            late.write_text("// landed after the decision\n")
        return decision

    monkeypatch.setattr(PreEditGuard, "no_edit_completion", then_a_late_write)

    events = _run(orch, "point the drift query at the usage binding")
    stop = next(e for e in events if e["type"] == "build-pre-edit-limit")

    assert stop["kept"] is True
    assert "before changing the app" not in stop["message"]
