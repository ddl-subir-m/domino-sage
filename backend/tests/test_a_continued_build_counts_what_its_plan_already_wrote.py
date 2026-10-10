"""A continued build counts what its plan's earlier attempt already wrote (#753).

Seen in the demo (#714, prompt 8): an approved build wrote its tab, then stopped on a broken tool
call. Continue with another model re-approved the plan, the new model read the files, said the
Done-when was met and edited nothing. Sage answered "No changes yet. Starting over once.", the
clean retry read everything again, and the build ended at the pre-edit work limit with the tab
unfinished and nothing said about what was missing.

The continued Attempt measured "has this build edited the app" from its own start, so the earlier
Attempt's writes were invisible: the pre-edit guard read the correct "already done" as a stuck
turn, and the Done-when review, which diffs from the same start, had nothing to read. Each test
below is one condition of the fix.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode
from sage.workspace.snapshot import QUERIES
from sage.workspace.stack import STACKS

from .fake_opencode import Turn, execution_plan
from .test_continue_with_another_model_resumes_a_failed_turn import (
    _build_orch,
    _click,
    _done,
    _failed_implementation,
    _no_waiting,
    _of,
    _scripted_polls,
)
from .test_retry_an_approved_plan import PHASED_PLAN, _build, _writes

__all__ = ["_no_waiting", "_scripted_polls"]

_ASK = "Add a usage drift tab."
_TAB = "// the usage drift tab, as the first attempt left it\n"
_DONE_SAYS = "The plan's Done-when is met by the files as they are; no further edits are needed."


def _review_reads(orch: Orchestrator) -> list[list[str]]:
    """The code paths each Done-when review was handed, measured from the baseline it was given."""
    reads: list[list[str]] = []

    def review(project, plan_md, tree_before, no_edit=None):
        reads.append(project.snapshot.changed_paths(
            tree_before, project.snapshot.working_tree_hash()))
        return ""

    orch._plan_review_nudge = review
    return reads


def _failed_after_writing(tmp: Path, stack: str, *after: Turn, plan: str = ""):
    """An approved build that wrote the tab, then sent two read calls that did not validate."""
    entry = STACKS[stack].entry_file
    orch, oc = _build_orch(tmp, [
        Turn(text=plan or execution_plan(files=entry)),
        Turn(writes={entry: _TAB}, invalid_calls=["read"]),
        Turn(invalid_calls=["read"]),
        *after], stack=stack)
    tid = orch.create_thread()["id"]
    assert _done(list(orch.build_stream(_ASK, conversation=tid)))["decision"] == "awaiting approval"
    done = _done(list(orch.approve_stream(conversation=tid)))
    assert done["cause"] == "invalid_tool_call" and done["stage"] == "implementation", done
    app = orch.project(start_preview=False).app_for_turn()
    assert app.read_plan_retry_step() == 1, "the plan still owes a build"
    assert (app.path / entry).read_text() == _TAB, "the first attempt's write stays in the app"
    return orch, oc, tid, app.app_id, done["turnId"]


@pytest.mark.parametrize("stack", ["react-vite", "fastapi-antd"])
def test_a_continuation_that_finds_the_work_done_is_reviewed_not_restarted(
        tmp_path: Path, stack: str):
    """The replay of the demo stop. The second reply is what the clean retry would get."""
    entry = STACKS[stack].entry_file
    orch, oc, tid, app_id, turn_id = _failed_after_writing(
        tmp_path, stack, Turn(text=_DONE_SAYS), Turn(text=_DONE_SAYS))
    reads = _review_reads(orch)

    events = _click(orch, turn_id, tid, app_id)

    assert not _of(events, "build-recovery"), "a continuation that edits nothing is not stuck"
    assert not _of(events, "build-pre-edit-limit")
    assert len(oc.prompts) == 4, "one send for the continuation, no clean retry"
    done = _done(events)
    assert done["ok"] is True, done
    # The Done-when review read the change this build made, the first attempt's included.
    assert reads == [[entry]], reads
    # And the plan is consumed like any finished build's, not left owing a build it got.
    app = orch.project(start_preview=False).app_for_turn()
    assert app.read_plan_retry_step() == 0 and not app.read_plan()


def test_a_continuation_names_the_steps_neither_attempt_built(tmp_path: Path):
    """A continuation that edits nothing ends saying which plan steps are not done."""
    stack = "react-vite"
    entry = STACKS[stack].entry_file
    plan = execution_plan(files=entry, step="Show the drift table") + (
        "\n\n### 2. Period picker\n"
        "- Files — src/Periods.tsx\n"
        "- Do — Add non-overlapping default periods.\n"
        "- Done when — The two default periods do not overlap.")
    orch, oc, tid, app_id, turn_id = _failed_after_writing(
        tmp_path, stack, Turn(text=_DONE_SAYS), Turn(text=_DONE_SAYS), plan=plan)
    _review_reads(orch)

    events = _click(orch, turn_id, tid, app_id)

    assert not _of(events, "build-recovery")
    unbuilt = _of(events, "plan-unbuilt")
    assert [e["steps"] for e in unbuilt] == [[2]], unbuilt
    nudges = [e["reason"] for e in _of(events, "iterate")]
    assert nudges == ["a plan step wrote none of its files — building it"], nudges
    assert "Plan step 2 (Period picker)" in oc.prompts[4]["text"]


def test_a_continuation_whose_plan_wrote_nothing_is_still_guarded(tmp_path: Path):
    """The guard stands down only for a build that landed a change in the app's own files. A
    failed attempt that wrote nothing, and a file Sage itself rewrote meanwhile, leave the
    continuation exactly as guarded as a first attempt."""
    orch, _oc, tid, app_id, turn_id = _failed_implementation(
        tmp_path, "react-vite", Turn(text=_DONE_SAYS), Turn(text=_DONE_SAYS))
    app = orch.project(start_preview=False).app_for_turn()
    (app.path / "AGENTS.md").write_text((app.path / "AGENTS.md").read_text() + "\nrefreshed\n")

    events = _click(orch, turn_id, tid, app_id)

    assert [e["message"] for e in _of(events, "build-recovery")] == [
        "No changes yet. Starting over once."]
    assert _done(events)["decision"] == "pre_edit_limit"


def test_a_first_attempt_that_wrote_only_its_query_catalog_landed(tmp_path: Path):
    """The query catalog is outside the tree hash, so only its digest sees this attempt's work."""
    orch, _oc = _build_orch(tmp_path, [
        Turn(text=execution_plan(files=QUERIES)),
        Turn(writes={QUERIES: '{"drift": {"sql": "select 1"}}\n'}, invalid_calls=["read"]),
        Turn(invalid_calls=["read"]),
        Turn(text=_DONE_SAYS), Turn(text=_DONE_SAYS)], stack="react-vite")
    tid = orch.create_thread()["id"]
    assert _done(list(orch.build_stream(_ASK, conversation=tid)))["decision"] == "awaiting approval"
    failed = _done(list(orch.approve_stream(conversation=tid)))
    assert failed["cause"] == "invalid_tool_call", failed
    app = orch.project(start_preview=False).app_for_turn()

    events = _click(orch, failed["turnId"], tid, app.app_id)

    assert not _of(events, "build-recovery")
    assert not _of(events, "build-pre-edit-limit")
    assert _done(events)["ok"] is True


def test_a_fresh_plan_after_a_landed_one_is_guarded_from_its_own_start(tmp_path: Path):
    """Only a retry or a Continue reads where its build started. A new plan approved after an
    earlier one landed is a new build, and editing nothing is still a stuck turn."""
    stack = "react-vite"
    entry = STACKS[stack].entry_file
    orch, _oc = _build_orch(tmp_path, [
        Turn(text=execution_plan(files=entry)),
        Turn(writes={entry: _TAB}),
        Turn(text=execution_plan(files=entry, step="Add the drift chart")),
        Turn(text=_DONE_SAYS), Turn(text=_DONE_SAYS)], stack=stack)
    tid = orch.create_thread()["id"]
    assert _done(list(orch.build_stream(_ASK, conversation=tid)))["decision"] == "awaiting approval"
    assert _done(list(orch.approve_stream(conversation=tid)))["ok"] is True
    assert _done(list(orch.build_stream("Add a drift chart.", conversation=tid,
                                        mode=Mode.PLAN)))["decision"] == "awaiting approval"

    events = list(orch.approve_stream(conversation=tid))

    assert [e["message"] for e in _of(events, "build-recovery")] == [
        "No changes yet. Starting over once."]
    assert _done(events)["decision"] == "pre_edit_limit"


def test_a_resumed_phase_is_not_held_to_the_pre_edit_limit_its_build_passed(tmp_path: Path):
    """A phased build that died in phase 2 kept phase 1 on disk. The resumed phase belongs to a
    build that has landed a change, as phase 2 of an unbroken build does."""
    orch, _oc = _build(tmp_path, [
        Turn(text=PHASED_PLAN),
        _writes("src/data.ts"),          # phase 1 lands
        _writes("src/Table.tsx"),        # phase 2 dies
        _writes("src/Table.tsx"),        # and its own retry dies
        Turn(text=_DONE_SAYS),           # the resumed phase 2 edits nothing
        Turn(text=_DONE_SAYS),
        Turn(text=_DONE_SAYS),
        Turn(text=_DONE_SAYS),
    ], break_on={3, 4}, phased=True)
    list(orch.build_stream("build me a trades dashboard"))
    list(orch.approve_stream())

    events = list(orch.build_stream("try again"))

    assert [e["n"] for e in events if e["type"] == "step-start"][:1] == [2]
    assert not _of(events, "build-recovery")
    assert not _of(events, "build-pre-edit-limit")
