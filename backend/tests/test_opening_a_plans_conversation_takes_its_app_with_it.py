"""Opening a Conversation from a plan or a Resource chip moves the app with it (#198).

Build shows one Conversation and one Built App side by side, and which app that is comes from the
Conversation. Two doors into a Conversation wrote the route by hand — the plan sheet's "Open
conversation" and a Resource row's "Open <conversation>" — instead of going through
`SW.openConversation`, which is where the app is moved on the click. So the transcript changed and
the preview, the Build header and the panel did not: Build sat reading app A beside a conversation
belonging to app B. Clicking back to app A then wrote `?app=A` beside thread B and pinned the
mismatch into the URL, which is the point at which it stopped being recoverable by looking away.

Each test presses the real control and reads the calls the press made, not the tree it left. That
is deliberate: a mismatched pairing looks exactly like a correct one in rendered markup, and the
only difference is whether `selectApp` was asked anything. `test_plan_back_links` is the prior art
for the harness and owns what the plan page OFFERS; this file owns what pressing one does.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "plan_open_conversation_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

# The conversation both doors open, and the app Build already has in the preview. They belong to
# different apps in every case here — the same app has nothing to move and would pass either way.
_ORIGIN = "thr_origin"
_OPEN_APP = "app_open"
_OTHER_APP = "app_other"


def _press(*, door: str, mode: str = "build", app: str = _OTHER_APP) -> dict:
    """Presses one door and returns what it asked for, before anything could come back.

    `app` is the Built App the PLAN stands in. It is the only thing either door can know about the
    app of the conversation it opens, and the chip door knows even that much of nothing — so the
    empty string is a real shape, not a degenerate one.
    """
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"door": door, "mode": mode, "app": app}),
        check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    drawn = json.loads(out.stdout.strip().splitlines()[-1])
    assert drawn["offered"], (
        f"the {door} door drew nothing to press, so nothing below was tested. Fix the fixture "
        "rather than the assertion — a door that is absent passes every claim about what it calls."
    )
    return drawn["onClick"]


@needs_node
def test_the_plan_sheets_door_switches_the_app_it_knows_about():
    """The plan document carries `appId`, so this door never has to wait to find out."""
    clicked = _press(door="plan")
    assert clicked["selected"] == [_OTHER_APP]
    assert clicked["routed"] == [f"#/build/{_ORIGIN}"]


@needs_node
def test_the_app_moves_on_the_same_click_as_the_transcript():
    """Both, or the pairing on screen is wrong for as long as one of them lags.

    The route was always written. What made this a bug rather than a slow load is that the route
    was written ALONE, so there was no later moment at which the app caught up — Build resolves an
    app from a bare `#/build/<id>` only when the Conversation bound one.
    """
    clicked = _press(door="plan")
    assert clicked["selected"] and clicked["routed"]


@needs_node
def test_a_plan_with_no_app_still_opens_and_names_none():
    """A plan can stand in no Built App, and then there is nothing to pass.

    This is the case Build's async `resolveConversationApp` answers, and it is why that effect is
    not removed. Naming the app already on screen would be worse than naming none: it would assert
    a pairing nobody checked.
    """
    clicked = _press(door="plan", app="")
    assert clicked["selected"] == []
    assert clicked["routed"] == [f"#/build/{_ORIGIN}"]


@needs_node
def test_chat_opens_the_conversation_and_selects_no_app():
    """Chat has no preview and no Built App on screen, so there is no second answer to give."""
    clicked = _press(door="plan", mode="chat")
    assert clicked["selected"] == []
    assert clicked["routed"] == [f"#/chat/{_ORIGIN}"]


@needs_node
def test_the_resource_chips_door_goes_through_the_same_one():
    """A chip knows which Conversation holds the Resource and nothing about that Conversation's app.

    So the proof it now uses `openConversation` cannot be a `selectApp` call — there is none to
    make. It is the rest of what that door does: the rail collapses and the plan sheet closes,
    neither of which a bare `SW.router.go` has ever done.
    """
    clicked = _press(door="chip")
    assert clicked["routed"] == [f"#/build/{_ORIGIN}"]
    assert clicked["railHidden"] is True
    assert clicked["planViewerId"] is None


@needs_node
@pytest.mark.parametrize("door", ["plan", "chip"])
def test_the_plan_sheet_closes_on_the_click_and_not_a_round_trip_later(door):
    """The sheet covers the preview, so leaving it up hides the one surface that shows the switch.

    `openThread` clears it at the end of a server round trip. Until then a person watching the
    sheet sees nothing happen at all, which is why a wrong pairing could sit there unnoticed — the
    report was never "it switched to the wrong app", it was "the button does nothing".
    """
    assert _press(door=door)["planViewerId"] is None
