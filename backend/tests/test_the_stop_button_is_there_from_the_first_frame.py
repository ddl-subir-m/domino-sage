"""Stop is offered while the turn this tab started is still running (#126, #371).

Reported from a real session: "when I start build, the stop button is not visible. When I move to
chat and then come back to build, I can see the stop button."

Both halves of that sentence come from one gap. The Stop bar renders when `buildRunning` says the
Project is busy, and it renders a BUTTON only when `runningTurn` — which turn holds the lock — is
the turn on this screen. `buildRunning` is set optimistically by the send. `runningTurn` was written
only by a `/build/state` poll, and nothing polls while a send is holding its own SSE open: the
watcher that polls is started by `loadBuild`, which is exactly what a mode switch runs. So the one
turn a person could not stop was the one they had just started, and walking to Chat and back was the
way to fix it.

A streaming turn does not need to be told: it knows its kind, its conversation and its app, which is
every field the bar compares. It claims that name on the first frame that is neither the queue's
`pending` nor one of the ways a turn ends without running, and gives it back as it unwinds.

The harness holds the stream open mid-turn, because that pause is the whole state under test.

"On the first frame" was still too late, which is #371 and the second half of this file. The
server's first frame is the `user` one — the person's own question — and Chat's handler skips it,
so what the claim actually waited on was the gate and the model's first token. The name is taken at
SEND time now, and handed back by the `pending` frame that says the turn is waiting in line rather
than running. The tests below the #126 four are that window, one per send and one per way out of it.

One corner of it is NOT closed here, and no test below claims it is: a BUILD that waits in line
hands its name back at `pending` and has no frame to take it back on, because build never streams
the `user` frame Chat does. It comes out of the queue into the same silence, and closing that needs
the server to say when a turn is granted. Chat's queue path is covered — `requeued` below.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_stop_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(mode: str) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"mode": mode}), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("mode", ["build", "approve", "chat"])
def test_a_turn_this_tab_started_can_be_stopped_while_it_streams(mode: str):
    """The reported bug, in all three places it lives. `approve` is the one from the screenshot —
    the person approved a plan and watched it build with no way to stop it."""
    out = _run(mode)

    assert out["midTurn"]["running"] is True
    assert out["midTurn"]["stopOffered"] is True
    # And so the bar shows a button rather than the caption it falls back to. That caption was the
    # honest answer to "some turn is running and I cannot name it", which is what made this quiet:
    # nothing looked broken, there was just nothing to press.
    assert out["midTurn"]["elsewhere"] is None


@pytest.mark.parametrize("mode", ["build", "approve", "chat"])
def test_the_turn_is_named_no_wider_than_it_is(mode: str):
    """Naming a turn is what makes Stop safe, so it must not name more than one screen. A build in
    another Built App, and the other mode's composer, are both somebody else's turn to stop."""
    out = _run(mode)

    assert out["midTurn"]["stopOfferedInTheOtherMode"] is False
    if mode != "chat":
        assert out["midTurn"]["stopOfferedOnAnotherApp"] is False


def test_a_turn_waiting_in_line_does_not_claim_to_be_running():
    """A queued turn holds nothing (#79). Claiming it would put a Stop over somebody else's work —
    which is the mistake the named turn exists to prevent, made from the other direction."""
    out = _run("queued")

    assert out["midTurn"]["stopOffered"] is False
    # The workspace IS busy, so the bar still says so; it just has no button to offer for it.
    assert out["midTurn"]["elsewhere"] is not None


@pytest.mark.parametrize("mode", ["build", "approve", "chat"])
def test_the_bar_comes_down_when_the_turn_ends(mode: str):
    """The other end of it. Nothing polls this tab back to the truth, so a name left standing would
    leave a Stop button over a finished turn until the next reload."""
    out = _run(mode)

    assert out["runningTurnAfter"] is None
    assert out["runningAfter"] is False


@pytest.mark.parametrize("mode", ["opening", "openingBuild", "openingApprove"])
def test_stop_is_there_before_the_turn_has_anything_to_show_for_itself(mode: str):
    """The window #126 left behind, and the whole of #371.

    #126 claimed the turn on the first frame, which is right for every frame after it and says
    nothing about the ones before. In Chat the server's first frame is the `user` one — the
    person's own question, painted the instant the lock is taken — and the handler skips it, so
    the claim waits on the gate and on the model's first token. Seconds, tens of seconds on a
    routed alias, with the question on screen and nothing under it to press.

    Which is worse than nothing to press: `runningTurnElsewhere` has no turn to name, so the bar
    reads "The workspace is busy." A flat, wrong sentence about somebody else's work, over the
    question the person just asked.

    Build and approve pause on no frame at all, because they have none to pause on. Each build
    generator writes its user row with `append_history(ev, ...)` under `if ev["type"] != "user"`,
    so that row reaches the transcript and never the stream: the next thing down the wire after
    the POST is the first frame of real work. Their window is therefore the same one Chat's is —
    the gate and the first token — with nothing arriving inside it at all.
    """
    out = _run(mode)

    assert out["midTurn"]["running"] is True
    assert out["midTurn"]["stopOffered"] is True
    assert out["midTurn"]["elsewhere"] is None
    # And claiming this early does not leave the name standing afterwards.
    assert out["runningTurnAfter"] is None


def test_a_turn_let_out_of_the_queue_takes_its_name_back():
    """The other end of the queue guard. A send names its turn before it knows whether it will run,
    so a `pending` frame has to hand that name back — and then the turn waits, is granted, and
    starts streaming into exactly the same silent window this ticket is about. The `user` frame
    behind the `pending` one is the queue letting go, and it is where the name goes back on."""
    out = _run("requeued")

    assert out["midTurn"]["stopOffered"] is True
    assert out["midTurn"]["elsewhere"] is None
    assert out["runningTurnAfter"] is None


def test_a_second_question_does_not_take_the_name_off_the_one_that_is_running():
    """The cost of naming a turn before the server has said anything, paid in the one case where
    the send is wrong: a second question goes out while the first is still streaming, and it is
    going to wait in line. Were it to name itself on the way out, the `pending` frame handing that
    name back would take the Stop button off the turn that IS running — #126 again, from the
    direction #371's fix opens. So a send only names its turn when nothing else is named."""
    out = _run("secondInLine")

    assert out["midTurn"]["stopOffered"] is True
    assert out["midTurn"]["elsewhere"] is None
    assert out["runningTurnAfter"] is None


@pytest.mark.parametrize("mode", ["queuedChat", "queuedApprove"])
def test_the_other_two_sends_do_not_claim_a_turn_waiting_in_line_either(mode: str):
    """The queue guard above has only ever been asked of `sendBuildPrompt`. A send names its turn
    before it knows whether it will run (#371), so all three sends now have a name to hand back —
    one guard per send, and Chat is the one the symptom was reported in."""
    out = _run(mode)

    assert out["midTurn"]["stopOffered"] is False
    assert out["midTurn"]["elsewhere"] is not None
