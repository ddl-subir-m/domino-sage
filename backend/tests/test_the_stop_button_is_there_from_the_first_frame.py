"""Stop is offered while the build this tab started is still running (#126).

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
