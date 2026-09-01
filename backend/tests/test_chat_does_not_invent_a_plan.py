"""Chat must not tell someone a plan is waiting in Build when none was ever written.

From a live run, and the last turn of it. The person typed "ok lets move this over to build", and
Sage answered:

    Ready to build! Head over to the Build tab in this project and the plan is already waiting
    there — it covers the full dashboard with KPI cards, time trends, department/model rankings,
    token breakdowns, billing mix, and a detailed records table, all wired to live filters.

None of that had happened. The offer had been declined, so no plan was drafted, no sheet was
confirmed and no app was bound. The Build tab they opened on the strength of that sentence said
"No plan yet. Ask Sage to draft one when the work is worth writing down."

The fix is not a prohibition. "Never claim a plan exists" asks the model to weigh a rule against a
sentence that reads as helpful, about a fact it cannot check from where it stands. The turn is given
the fact instead — every turn, two sentences — so the true answer is also the easy one.
"""
from __future__ import annotations

from pathlib import Path

from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator

from .fake_opencode import Turn
from .test_chat_turn import _no_waiting, _orch  # noqa: F401  (_no_waiting is an autouse fixture)

# A question ABOUT the plan rather than a request to hand off. "ok lets move this over to build",
# which is what the person actually typed, now reaches the sheet instead of a turn — so it is no
# longer a way to test what a turn is told. The invented plan is still reachable from here: asking
# after a plan is exactly the question a model answers by inventing one.
ASK_ABOUT_IT = "is there a plan for this yet?"


# ---- when a plan exists ---------------------------------------------------------------------------


def test_an_offer_nobody_answered_is_not_a_plan():
    assert not handoff.has_plan([{"status": "suggested"}])


def test_a_declined_offer_is_not_a_plan():
    """The live Conversation's exact state. Declining wrote nothing, so there is nothing to point at."""
    assert not handoff.has_plan([{"status": "suppressed", "suppressed": True}])


def test_nothing_at_all_is_not_a_plan():
    assert not handoff.has_plan([])
    assert not handoff.has_plan(None)


def test_a_drafted_plan_counts_before_the_sheet_is_confirmed():
    """`planned` means plan.md and the plan document are written. That is a plan to point at."""
    assert handoff.has_plan([{"status": "planned", "planId": "pl_1"}])


def test_a_bound_handoff_counts():
    assert handoff.has_plan([{"status": "bound", "appId": "app_1"}])


def test_a_fresh_offer_on_top_of_a_built_plan_does_not_un_write_it():
    """A Conversation may hand off more than once (ADR-0008), so this reads every entry."""
    assert handoff.has_plan([{"status": "bound", "appId": "app_1"}, {"status": "suggested"}])


# ---- what the turn is told ------------------------------------------------------------------------


def test_a_conversation_with_no_plan_says_so_and_says_what_to_answer():
    note = Orchestrator._plan_state_note([{"status": "suppressed"}])
    assert "no plan and no app" in note
    assert "nothing has been planned yet" in note
    # The sentence that was actually produced, named so the turn cannot reach for it.
    assert "never that a plan is waiting" in note


def test_a_conversation_with_a_plan_is_told_not_to_restate_it():
    note = Orchestrator._plan_state_note([{"status": "bound", "appId": "app_1"}])
    assert "has a plan and it is in Build" in note
    assert "do not restate" in note


# ---- and that it reaches the agent ----------------------------------------------------------------


def test_the_turn_prompt_carries_the_truth_about_this_conversation(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]

    list(orch.chat_stream(tid, ASK_ABOUT_IT))

    assert oc.prompts, "a question about the plan is not a handoff request, so a turn runs"
    assert "no plan and no app" in oc.prompts[0]["text"]


def test_a_conversation_that_planned_is_told_the_other_thing(tmp_path: Path):
    from sage.workspace.threads import ThreadStore

    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    store = ThreadStore(orch.project(start_preview=False).record.path)
    store.mark_handoff_suggested(tid)
    store.mark_handoff_planned(tid, "pl_1")

    list(orch.chat_stream(tid, "what does that dashboard show?"))

    assert "has a plan and it is in Build" in oc.prompts[0]["text"]
