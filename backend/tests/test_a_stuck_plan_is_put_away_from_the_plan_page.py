"""A plan whose build already ran can be retired from the plan page (#174).

The dead end: a phased build dies at step 4 of 6. The plan stays live on purpose, so "try again"
can resume it. Later the person wants to retire it without building, presses Archive on the plan
page, and gets a 409 telling them to approve or cancel the plan in the Conversation it came from.
The card is there — and it has no Approve and no Cancel, because a failed build persists a `done`
event whose decision is not a gate decision, which clears the card's `pending`. There is no other
Cancel anywhere.

Two guards met in the middle, and the guard is the wrong one. It refuses whenever the origin
Conversation still answers, but "the Conversation is alive" is not the question it means to ask.
The refusal exists so Archive cannot hide a document out from under an OPEN Approve card, and a
plan whose build already ran has no Approve card to strand: `read_plan_retry_step() > 0` is the
durable fact that tells those apart, and it is the same fact #173 reads to name the archive.

What these tests pin: the plan a build got partway through can be put away from the plan page while
its Conversation is alive, it is named as what the app was built from rather than cancelled (#173),
and the plan still genuinely waiting for its first approval — the only one whose card really does
carry the two buttons the refusal names — still refuses.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator, PlanArchiveRefused
from sage.workspace.manager import Workspace
from sage.workspace.threads import ThreadStore

from .fake_opencode import Turn
from .test_retry_an_approved_plan import (  # noqa: F401 — _no_waiting is an autouse fixture
    PLAN,
    _build,
    _done,
    _no_waiting,
    _phased_run_that_dies_in_phase_two,
    _workspace,
)


def _conversation(orch: Orchestrator) -> str:
    """A Conversation the rail actually holds, so `_origin_live` says True.

    The other tests here drive builds from outside the rail, where the origin id has no Thread
    record behind it. The refusal only fires while the origin answers, so a stuck plan can only be
    reached through one the rail really minted.
    """
    return ThreadStore(orch.project(start_preview=False).record.path).create("Desk exposure")["id"]


def _stuck_in_a_live_conversation(tmp_path: Path):
    """A phased build dead at phase 2 of 3, proposed in a Conversation that still answers."""
    orch, _oc = _phased_run_that_dies_in_phase_two(tmp_path)
    thread_id = _conversation(orch)
    list(orch.build_stream("build me a trades dashboard", conversation=thread_id))
    events = list(orch.approve_stream(conversation=thread_id))
    assert _done(events)["decision"].startswith("phase 2 of 3 failed")
    return orch, thread_id


def test_a_plan_a_phased_build_died_partway_through_can_be_put_away(tmp_path: Path):
    """The dead end itself. The plan page's Archive is the only door left: the card the refusal
    named lost its Approve and its Cancel the moment the build failed."""
    orch, _thread_id = _stuck_in_a_live_conversation(tmp_path)
    plan_id = _workspace(orch).live_plan_doc_id()
    doc = orch.read_plan_doc(plan_id)
    assert doc["originLive"] is True            # the guard's old condition still holds
    assert _workspace(orch).read_plan_retry_step() == 2

    orch.archive_plan_doc(plan_id, True)

    assert orch.read_plan_doc(plan_id)["archived"] is True


def test_putting_it_away_retires_the_copy_it_left_behind(tmp_path: Path):
    """Same act, both halves. Leaving `.sage/plan.md` live would leave the app pointing at a
    document the panel has just hidden, and the next turn reading a plan nobody can open as live
    intent (ADR-0007)."""
    orch, _thread_id = _stuck_in_a_live_conversation(tmp_path)
    ws = _workspace(orch)

    orch.archive_plan_doc(ws.live_plan_doc_id(), True)

    assert ws.read_plan() is None
    assert ws.live_plan_doc_id() == ""
    assert ws.read_plan_retry_step() == 0


def test_it_is_named_as_what_the_app_was_built_from_not_as_cancelled(tmp_path: Path):
    """Phase 1's code is on disk, so the app IS partly this plan. Archived as cancelled, the pin
    would skip it and name an EARLIER plan (#173). The split lives in `archive_plan`, and this new
    Cancel door reaches it like the other two."""
    orch, _thread_id = _stuck_in_a_live_conversation(tmp_path)
    ws = _workspace(orch)

    orch.archive_plan_doc(ws.live_plan_doc_id(), True)

    assert "Trades table" in (ws.read_archived_plan() or "")
    assert orch.read_plan_pin()["status"] == "built"


def test_a_whole_build_that_gave_up_can_be_put_away_too(tmp_path: Path):
    """Not only the phased case. An unphased build that gave up owes a build from step 1, and it
    is just as much a plan nobody is waiting to approve."""
    orch, _oc = _build(tmp_path, [PLAN, Turn(writes={"src/App.tsx": "// half a table\n"})],
                       break_on={2})
    thread_id = _conversation(orch)
    list(orch.build_stream("build me a consumption dashboard", conversation=thread_id))
    list(orch.approve_stream(conversation=thread_id))
    ws = _workspace(orch)
    assert ws.read_plan_retry_step() == 1

    orch.archive_plan_doc(ws.live_plan_doc_id(), True)

    assert ws.read_plan() is None
    # Nothing was ever built from it, so this one really is a cancel: the pin must not go on to
    # describe the app as built from a plan whose build wrote nothing.
    assert ws.read_archived_plan() is None


def test_the_plan_still_waiting_for_its_first_approval_still_refuses(tmp_path: Path):
    """The half of the guard worth keeping. This plan owes no build, so its card is still `pending`
    and really does carry the Approve and the Cancel the refusal sends people to."""
    orch, _oc = _build(tmp_path, [PLAN])
    thread_id = _conversation(orch)
    list(orch.build_stream("build me a consumption dashboard", conversation=thread_id))
    plan_id = _workspace(orch).live_plan_doc_id()
    assert _workspace(orch).read_plan_retry_step() == 0

    with pytest.raises(PlanArchiveRefused) as refused:
        orch.archive_plan_doc(plan_id, True)

    assert refused.value.reason == "awaiting approval"
    assert orch.read_plan_doc(plan_id)["archived"] is False


def test_archive_is_refused_while_another_turn_holds_the_tree(tmp_path: Path):
    """The hole the relaxed guard would otherwise open. `_phased_approve` writes the resume step
    BEFORE each phase runs, so from phase 1 onward a live build is indistinguishable from a build
    that already ended. Archive from a second tab would rename `.sage/plan.md` out from under a
    turn still streaming, and the next phase would write a resume point for a document that no
    longer exists."""
    orch, _thread_id = _stuck_in_a_live_conversation(tmp_path)
    ws = _workspace(orch)
    plan_id = ws.live_plan_doc_id()
    orch._turn_lock.acquire()                   # a turn is in flight
    try:
        with pytest.raises(PlanArchiveRefused) as refused:
            orch.archive_plan_doc(plan_id, True)
    finally:
        orch._turn_lock.release()

    assert refused.value.reason == "busy"
    # Refused all the way down: the document is still live, so the running turn's resume point
    # still points at something.
    assert orch.read_plan_doc(plan_id)["archived"] is False
    assert ws.read_plan() is not None
    assert ws.live_plan_doc_id() == plan_id


def test_a_wedged_workspace_still_puts_the_plan_away(tmp_path: Path):
    """The running check must not become a second dead end. A wedged workspace holds `_turn_lock`
    for good and has no turn inside it (#39), so a plan there would refuse forever. `turn_busy`
    already draws that line, which is why the guard reads it rather than the lock."""
    orch, _thread_id = _stuck_in_a_live_conversation(tmp_path)
    ws = _workspace(orch)
    orch._turn_lock.acquire()
    orch._turn_wedged = True
    try:
        orch.archive_plan_doc(ws.live_plan_doc_id(), True)
    finally:
        orch._turn_wedged = False
        orch._turn_lock.release()

    assert ws.read_plan() is None


def test_the_archive_holds_the_tree_while_it_runs(tmp_path: Path, monkeypatch):
    """Not check-then-act. Asking `turn_busy()` and then archiving leaves a window in which a
    queued turn is admitted between the two, and it gets its plan pulled out from under it anyway.
    The archive is a few local file operations, so it takes `_turn_lock` for its whole length —
    the pattern every other door onto the working tree already uses (#39)."""
    orch, _thread_id = _stuck_in_a_live_conversation(tmp_path)
    ws = _workspace(orch)
    held: list[bool] = []
    real = Workspace.archive_plan
    # The lock has to be held at the moment the tree is touched, not merely before it.
    def watched(self, **kw):
        held.append(orch._turn_lock.locked())
        return real(self, **kw)
    monkeypatch.setattr(Workspace, "archive_plan", watched)

    orch.archive_plan_doc(ws.live_plan_doc_id(), True)

    assert held == [True]
    assert orch._turn_lock.locked() is False    # and handed back afterwards
