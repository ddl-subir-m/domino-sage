"""An approved build that leaves plan steps unbuilt says which ones (#662).

Live (2026-10-06): a cheaper model built part of an approved plan — no charts, no news, no deal desk
— and its closing message said the plan was delivered. Nothing Sage said disagreed. The plan's steps
name the files each one is for, so a step whose files this build neither created nor changed was
not built, and that is said under the turn rather than left to the model's own account of itself.
"""
from __future__ import annotations

from pathlib import Path

from .fake_opencode import Turn, execution_plan
from .test_a_dead_alias_stops_the_turn_before_it_starts import _no_waiting, _orch  # noqa: F401
from .test_a_failed_build_turn_stays_on_screen import UNREADABLE
from .test_build_conversation_return import run as render

THREE_STEPS = Turn(text=execution_plan(
    "Pipeline Room", "A pipeline dashboard.", "Overview page", files="src/App.tsx",
    work="Draw the pipeline overview.") + (
    "\n\n### 2. Charts\n- Files — src/Charts.tsx\n- Do — Add the charts.\n"
    "- Done when — The charts show.\n\n"
    "### 3. Deal desk\n- Files — src/DealDesk.tsx, src/dealDesk/\n- Do — Add the deal desk.\n"
    "- Done when — The deal desk shows."))


def _approve(tmp_path: Path, build: Turn) -> list[dict]:
    orch, _ = _orch(tmp_path, turns=[THREE_STEPS, build])
    list(orch.build_stream("build me a pipeline dashboard", conversation="c1"))
    list(orch.approve_stream(conversation="c1"))
    return orch.project(start_preview=False).app_for_turn().read_history("c1")


def test_a_build_that_skipped_steps_names_them(tmp_path: Path):
    history = _approve(tmp_path, Turn(writes={"src/App.tsx": "// overview\n"}))
    rows = [r for r in history if r["type"] == "plan-unbuilt"]

    assert [r["steps"] for r in rows] == [[2, 3]]
    assert rows[0]["message"] == (
        "Not built from the plan: step 2 (Charts) and step 3 (Deal desk). "
        "This build wrote none of the files those steps name.")


def test_a_build_that_touched_every_step_says_nothing_more(tmp_path: Path):
    history = _approve(tmp_path, Turn(writes={
        "src/App.tsx": "// overview\n", "src/Charts.tsx": "// charts\n",
        "src/dealDesk/Terms.tsx": "// deal desk\n"}))

    assert "plan-unbuilt" not in [r["type"] for r in history]


def test_a_build_that_failed_is_not_also_measured_against_the_plan(tmp_path: Path):
    history = _approve(tmp_path, Turn(writes={"src/App.tsx": "// half\n"}, error=UNREADABLE))

    assert "plan-unbuilt" not in [r["type"] for r in history]


def test_the_transcript_draws_it_as_a_warning():
    rows = render({"savedHistory": [
        {"type": "user", "text": "Approved the plan."},
        {"type": "done", "ok": True, "decision": "clean"},
        {"type": "plan-unbuilt", "steps": [2], "message": "Not built from the plan: step 2 (Charts)."},
    ]})
    assert rows[-1] == {"type": "status", "ok": None, "warn": True,
                        "value": "Not built from the plan: step 2 (Charts)."}
