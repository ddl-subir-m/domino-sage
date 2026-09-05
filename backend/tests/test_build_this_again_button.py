"""What the Plan page offers once its plan has already built something (#149, ADR-0024).

The server decides whether a plan may be built again; the page decides what a person sees. Those are
different failures. A page that hides the action on an eligible plan leaves the person back where
they started — describing the edit again in the composer — and a page that offers it without saying
it will clear two sign-offs surprises somebody out of a record they wanted.

So each test drives the real component with the eligibility the server would have sent and reads
what got drawn: whether the button is there, whether it is disabled, what its tooltip says instead,
and what the line under it warns. The click is read as the sequence it asks the store for, because
the route "Build this again" writes is named off the conversation the store has open — opening that
conversation late names the one being left, which is the bug this pins.

`test_plan_back_links` is the prior art for the harness.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "plan_build_again_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

_PLAN = {
    "id": "001", "title": "A desk exposure dashboard.", "version": 2, "status": "approved",
    "author": "u-me", "updatedAt": "2026-09-01T10:00:00Z", "summary": "", "sections": {},
    "comments": [], "reviewers": [], "originThreadId": "thr_1", "appId": "app_a",
    "markdown": "1. Add the table\n2. Sort it by date\n",
}

# What the server sends for a plan whose Built App still stands where this plan left it.
_ELIGIBLE = {"offered": True, "eligible": True, "reason": ""}


def _page(*, build_again: dict, approvals: int = 0, variant: str = "page",
          mode: str = "plan", thread=None) -> dict:
    plan = {**_PLAN, "buildAgain": build_again,
            "approvals": [{"user": f"u-{i}", "at": "2026-09-01T10:00:00Z"}
                          for i in range(approvals)]}
    out = subprocess.run(["node", str(_HARNESS)],
                         input=json.dumps({"plan": plan, "variant": variant, "mode": mode,
                                           "thread": thread}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_a_built_plan_offers_the_rebuild_beside_the_way_into_the_app():
    """Stories 3 and 4: two offers that read differently, and the harmless one stays primary. The
    button carries no `type`, which is this Workbench's secondary."""
    page = _page(build_again=_ELIGIBLE)

    assert page["offered"] is True
    assert page["disabled"] is False
    assert page["tooltip"] is None
    assert page["builder"] is True


@needs_node
def test_a_plan_that_has_never_built_is_offered_nothing():
    """Story 16. The page keeps the Approve flow it has always had, and this action does not apply
    — an unbuilt plan reaching the same code path would be a second door onto the first build."""
    page = _page(build_again={"offered": False, "eligible": False, "reason": "never built"})

    assert page["offered"] is False


@needs_node
def test_a_plan_the_app_has_moved_past_says_why_it_cannot_be_used():
    """Stories 5 and 6: disabled, not absent, because a button that explains itself is the only
    thing on this page that can say where to go instead."""
    page = _page(build_again={"offered": True, "eligible": False, "reason": "moved on"})

    assert page["offered"] is True
    assert page["disabled"] is True
    assert "later plan" in page["tooltip"]
    assert "current plan" in page["tooltip"]


@needs_node
def test_a_superseded_plan_says_that_instead():
    """Story 20 has its own reason and needs its own sentence: "a later plan owns this app" and
    "another conversation replaced this before it was built" are not the same news."""
    page = _page(build_again={"offered": True, "eligible": False, "reason": "superseded"})

    assert page["disabled"] is True
    assert "Another conversation" in page["tooltip"]


@needs_node
def test_an_archived_plan_points_at_the_control_beside_it_rather_than_at_another_plan():
    """#167. Every other disabled reason sends you somewhere else, because the plan you want is
    somewhere else. This one does not: the plan you want is the one on screen, and the way to get
    it back is the Unarchive button a few pixels away."""
    page = _page(build_again={"offered": True, "eligible": False, "reason": "archived"})

    assert page["disabled"] is True
    assert "archived" in page["tooltip"]
    assert "Unarchive" in page["tooltip"]


@needs_node
def test_a_plan_with_no_conversation_on_record_says_there_is_nowhere_to_run_it():
    """A build is a turn and a turn lives in a Conversation. A document written before #54 recorded
    none, and running its rebuild in whichever Conversation happens to be open would file the turn
    under work it has nothing to do with — so it is refused with the reason, like "Build this"."""
    page = _page(build_again={"offered": True, "eligible": False, "reason": "no conversation"})

    assert page["disabled"] is True
    assert "no conversation on record" in page["tooltip"]


@needs_node
def test_the_button_says_how_many_approvals_it_will_clear():
    """Story 8, and the reason story 9 can have its way: the cost is stated on the button, so no
    confirmation dialog has to carry it."""
    assert _page(build_again=_ELIGIBLE, approvals=2)["caption"] == \
        "This will clear 2 existing approvals"
    assert _page(build_again=_ELIGIBLE, approvals=1)["caption"] == \
        "This will clear 1 existing approval"


@needs_node
def test_a_plan_with_nothing_to_clear_says_nothing():
    """The line is a warning, not a status. With no sign-offs on record there is nothing to warn
    about, and a line reading "0 approvals" would be noise on the common path."""
    assert _page(build_again=_ELIGIBLE, approvals=0)["caption"] is None


@needs_node
def test_a_plan_the_app_has_moved_past_does_not_warn_about_approvals():
    """It cannot clear them: the press is refused. Warning about a cost that will not be paid reads
    as the button still working."""
    page = _page(build_again={"offered": True, "eligible": False, "reason": "moved on"},
                 approvals=2)

    assert page["caption"] is None


@needs_node
def test_pressing_it_walks_to_the_app_before_it_starts_the_build():
    """The order is the contract. The server builds whichever app is selected, so selecting has to
    happen first; `SW.appRoute` names the conversation off the store, so opening it has to happen
    before the route is written; and the turn goes last so a person is watching Build when it
    streams (story 21)."""
    page = _page(build_again=_ELIGIBLE, thread={"id": "thr_1"})

    assert page["acted"] == [
        "select app_a",
        "open thr_1",
        "route #/build/thr_1?app=app_a",
        'approve 001 again=true edits="1. Add the table\\n2. Sort it by date\\n"',
    ]


@needs_node
def test_the_sheet_beside_a_build_is_still_a_reader():
    """Out of scope, on purpose: the full page is the one place this action lives, so the sheet and
    the rail's pin stay a way to read a plan rather than a second place to rebuild from it."""
    page = _page(build_again=_ELIGIBLE, variant="side", mode="build", thread={"id": "thr_1"})

    assert page["offered"] is False
