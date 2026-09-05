"""What Build's empty state says about an app that exists and a plan that is waiting (#172).

The screen had one fact where there are two. "Is there an app?" was read off the PLAN's status —
`built` means plan.md has been archived — so the two notes were mutually exclusive by construction
and the pair (app built, plan live) fell to the plan note alone. A person came back to a rendering
app and was told to build it, under a greeting offering to write the app they were looking at.

So each test drives the real mode with a rail row and a pin, the two things the server sends, and
reads the sentences that got drawn. The greeting is read apart from the notes because it is the line
that was wrong on EVERY row where the app is built, not only on the broken one.

`test_build_this_again_button` is the prior art for the harness.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_empty_state_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

_APP = {"id": "app_a", "name": "Desk dashboard", "built": False, "published": False, "url": ""}
# What `/api/project/plan` sends while plan.md is still live. `retryStep` 0 is a plan nobody has
# built from yet, which is the ordinary case.
_LIVE = {"title": "A desk exposure dashboard.", "markdown": "1. Add the table\n",
         "status": "awaiting", "steps": 1, "planId": "001", "retryStep": 0}


def _screen(*, built: bool, plan: dict | None, running: bool = False, turn: dict | None = None,
            wedged: bool = False) -> dict:
    payload = {"app": {**_APP, "built": built}, "plan": plan, "running": running, "turn": turn,
               "wedged": wedged}
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(payload),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _titles(screen: dict) -> list[str]:
    return [n["title"] for n in screen["notes"]]


# ---- the greeting --------------------------------------------------------------------------------


@needs_node
def test_an_app_that_does_not_exist_yet_is_offered_the_plan_route():
    """The screen Build has always drawn, unchanged: nothing is built, so writing it is the act."""
    screen = _screen(built=False, plan=None)

    assert screen["shown"] is True
    assert screen["greeting"]["title"] == "Build the app from a plan"
    assert "Approve a plan to write this app" in screen["greeting"]["detail"]
    assert _titles(screen) == []


@needs_node
def test_a_built_app_is_never_offered_to_be_written():
    """The line that was wrong on every built row. An app in the preview cannot be written — it is
    there — so the greeting asks for the next change instead."""
    screen = _screen(built=True, plan=None)

    assert "write this app" not in screen["greeting"]["detail"]
    assert screen["greeting"]["title"] == "Keep building this app"


# ---- the two facts, told apart -------------------------------------------------------------------


@needs_node
def test_a_built_app_with_no_plan_says_whose_app_the_preview_is():
    """A new conversation clears the transcript and not the app, and unlabelled the preview reads as
    this conversation's work."""
    screen = _screen(built=True, plan=None)

    assert _titles(screen) == ["The preview is an app you already built"]


@needs_node
def test_an_unbuilt_app_with_a_plan_waiting_says_only_that():
    """Nothing has been built, so there is no app to introduce — only the plan the rail is pinning,
    which without a note reads the same as a plan that was never written."""
    screen = _screen(built=False, plan=_LIVE)

    assert _titles(screen) == ["There is already a plan waiting"]


@needs_node
def test_a_built_app_with_a_plan_waiting_says_both():
    """The pair that had no branch. Both facts are true and neither implies the other: the app is
    rendering AND a plan nobody has built from is live, and the person needs telling both."""
    screen = _screen(built=True, plan=_LIVE)

    assert _titles(screen) == ["The preview is an app you already built",
                              "There is already a plan waiting"]


@needs_node
def test_a_plan_the_app_was_built_from_is_not_a_plan_waiting():
    """`status: built` is plan.md archived — the build consumed it. Nothing is owed, so the pin is a
    record of what the app came from rather than a thing to act on."""
    screen = _screen(built=True, plan={**_LIVE, "status": "built", "retryStep": 0})

    assert _titles(screen) == ["The preview is an app you already built"]


# ---- why the plan is still there -----------------------------------------------------------------


def _plan_note(screen: dict) -> str:
    return next(n["detail"] for n in screen["notes"] if n["title"] == "There is already a plan waiting")


@needs_node
def test_a_plan_nobody_has_built_from_says_it_is_waiting_to_be_read():
    screen = _screen(built=False, plan={**_LIVE, "retryStep": 0})

    assert "clears the transcript, not the plan" in _plan_note(screen)


@needs_node
def test_a_plan_whose_build_did_not_finish_says_so():
    """Retry step 1 is "from the top", which is the whole of an unphased build. The plan is live
    because the build that consumed it never finished, not because nobody has approved it — and the
    two want opposite answers to "try again".

    "Did not finish", never "wrote nothing": a phased build records the resume point BEFORE the
    phase runs, so step 1 owed can be a step 1 that started and failed with its files on disk."""
    screen = _screen(built=False, plan={**_LIVE, "retryStep": 1})

    detail = _plan_note(screen)
    assert "did not finish" in detail
    assert "try again" in detail
    assert "wrote" not in detail


@needs_node
def test_a_phased_build_that_stopped_partway_names_the_step_it_owes():
    """Trap 1: no total. The pin's `steps` counts numbered lines under the Plan heading and the
    phased parser counts briefs, so "step 4 of 6" would be two different questions answered as one.
    The step alone is the fact the resume point actually holds."""
    screen = _screen(built=True, plan={**_LIVE, "retryStep": 4})

    detail = _plan_note(screen)
    assert "step 4" in detail
    assert "of 1" not in detail and "of 4" not in detail


@needs_node
def test_a_first_build_that_died_partway_is_not_offered_to_be_written():
    """`built` is set only by a build that finished every phase, so a FIRST phased build that died
    at step 4 leaves three phases on disk under a row that still says false. Those files are what
    the preview is serving, so the greeting must not offer to write the app — the note beside it is
    saying the earlier steps are already there."""
    screen = _screen(built=False, plan={**_LIVE, "retryStep": 4})

    assert "write this app" not in screen["greeting"]["detail"]
    # And it still is not an app anybody built: that note is a claim about a finished build.
    assert "The preview is an app you already built" not in _titles(screen)


@needs_node
def test_a_build_in_flight_is_not_reported_as_a_build_that_stopped():
    """The resume point is written before each phase runs, so during a build it names the phase
    EXECUTING. `buildTyping` is only this tab's stream — a second tab, or a new conversation opened
    beside a running build, has an empty transcript and would announce a stop that never happened."""
    screen = _screen(built=True, plan={**_LIVE, "retryStep": 3}, running=True)

    detail = _plan_note(screen)
    assert "stopped" not in detail
    assert "step 3" not in detail


@needs_node
def test_a_chat_question_does_not_make_a_dead_build_look_untried():
    """The lock is the Project's and Chat holds it too, so the broad "something is running" would
    hide the step behind an unrelated question. The fallback is not silence — it says a plan whose
    build died is one nobody has built from, and drops "try again", the only words that resume a
    build rather than propose a second plan for a request already approved."""
    screen = _screen(built=True, plan={**_LIVE, "retryStep": 4}, running=True,
                     turn={"kind": "chat", "conversation": "thr_2", "app": ""})

    assert "step 4" in _plan_note(screen)


@needs_node
def test_a_build_in_another_app_does_not_hide_this_apps_resume_point():
    """The resume point lives in the app's own workspace, so the turn that can be moving it is a
    build in THIS app. One in another app is as unrelated to this plan as a Chat question."""
    screen = _screen(built=True, plan={**_LIVE, "retryStep": 4}, running=True,
                     turn={"kind": "build", "conversation": "thr_2", "app": "app_other"})

    assert "step 4" in _plan_note(screen)


@needs_node
def test_a_lock_held_by_nobody_nameable_is_treated_as_a_build():
    """`runningTurn` is null for the instant between two queued turns, and a poll landing there must
    not read as "nothing is building" — the number would name the phase executing."""
    screen = _screen(built=True, plan={**_LIVE, "retryStep": 4}, running=True, turn=None)

    assert "step 4" not in _plan_note(screen)


@needs_node
def test_a_wedged_build_is_not_a_build_that_stopped():
    """The server reports a wedged turn as NOT running — `running` is "locked and not wedged" — so
    every tab but the one streaming it sees an idle Project holding a lock. The step still names the
    phase that was executing, and the "try again" this would offer queues behind the wedge."""
    screen = _screen(built=True, plan={**_LIVE, "retryStep": 4}, running=False, wedged=True)

    assert "step 4" not in _plan_note(screen)


@needs_node
def test_step_one_holds_the_offer_to_write_because_it_says_nothing_about_files():
    """The one number that cannot be read as "there is code here": a build owing step 1 may have
    written half of it or may have failed at the gateway before touching anything, and nothing the
    client reads tells them apart. So the greeting keeps the honest offer — approving that plan IS
    what writes the app — and the note beside it names the shorter route."""
    screen = _screen(built=False, plan={**_LIVE, "retryStep": 1})

    assert screen["greeting"]["title"] == "Build the app from a plan"
    assert "try again" in _plan_note(screen)


@needs_node
def test_a_turn_running_elsewhere_does_not_unwrite_what_is_already_on_disk():
    """The step is silenced while the Project is busy, and that silence must stop at the note. A
    turn running in another conversation does not remove the phases the last attempt left behind,
    so the greeting still has an app in front of it — reading the silenced number here would put
    the offer to write it back on screen for the one app that cannot be written."""
    screen = _screen(built=False, plan={**_LIVE, "retryStep": 4}, running=True)

    assert "write this app" not in screen["greeting"]["detail"]
    assert screen["greeting"]["title"] == "Keep building this app"


@needs_node
def test_the_two_notes_do_not_say_the_same_half_twice():
    """The pair is the point of this screen, so it is the ordinary reading rather than a rare one.
    Both notes opened "A new conversation clears the transcript, not the …", which reads as a
    stutter once they are drawn together."""
    screen = _screen(built=True, plan=_LIVE)

    stems = [n["detail"].startswith("A new conversation clears the transcript")
             for n in screen["notes"]]
    assert stems.count(True) == 1
    # And the plan note still says what to do about the plan, which is the half that was carrying
    # the information.
    assert "Open it in the rail to review it" in _plan_note(screen)
