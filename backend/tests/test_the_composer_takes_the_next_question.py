"""What the screen does while a question waits its turn (#79).

The backend half of the queue is `test_a_conversation_queues_the_next_turn.py`. This is the half
that only shows up by running the store: the interesting states are the ones BETWEEN two SSE frames,
where a question is on screen as an intention and nowhere else — not in the transcript, not on the
server's disk, and cancellable. Reading store.js cannot tell you whether the bubble it drew
optimistically comes back off again, and that is the whole difference between a queue and a lie.

The harness holds the stream open after the `pending` frame, which is the pause the feature is made
of, and reports the store three times: while waiting, after the turn, and what was POSTed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "turn_queue_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(mode: str) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"mode": mode}), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_question_asked_while_a_turn_runs_is_taken_and_shown_as_waiting():
    """The composer used to drop it: `if (state.chatRunning) return`. It was dropped because the
    server would only have refused it and said so in the transcript, which read as Sage answering a
    question about data with a complaint about a build. The second question was never the thing
    worth refusing — the dead composer was."""
    out = _run("queued")

    queued = out["whileWaiting"]["queued"]
    assert [q["text"] for q in queued] == ["how many rows?"]
    # A pending turn is an intention, not a commitment, and the row says so where it is accepted.
    assert "Nothing has run yet" in queued[0]["message"]
    # The project is busy while it waits — something holds the lock it is queued for — so the turn
    # bar and its Stop stay on screen with the composer open behind them.
    assert out["whileWaiting"]["chatRunning"] is True
    # And it runs, unchanged, when its turn comes.
    assert "Six million rows." in out["answer"]
    assert out["queuedAfter"] == 0


def test_a_waiting_question_is_not_in_the_transcript():
    """The transcript is the receipt. A turn that has not run has nothing to give a receipt for, so
    the queued row lives above the composer and the assistant side of the exchange does not exist
    yet — a "Sage is thinking" bubble for a turn nobody has started is the lie this avoids."""
    out = _run("queued")

    assert out["whileWaiting"]["roles"] == ["user"]      # the question, and no answer forming
    assert out["rolesAfter"] == ["user", "assistant"]


def test_cancelling_a_waiting_question_takes_it_back_off_the_screen():
    """Cancel is its own control and its own endpoint: it drops the pending turn and leaves whatever
    is running alone. What comes back is a cancelled `done`, and the send that has been awaiting it
    all along clears its own row and re-reads the Thread — which the server never wrote, so the
    question the send drew optimistically goes with it."""
    out = _run("cancelled")

    assert "./api/project/turn/cancel" in out["posted"]
    assert out["queuedAfter"] == 0
    assert out["rolesAfter"] == []                       # nothing of it was ever recorded
    assert out["composerSeed"] is None                   # they changed their mind; do not re-offer it


def test_a_second_question_asked_while_the_first_runs_reaches_the_server():
    """`if (state.chatRunning) return` was the composer's whole answer to a running turn, and it is
    gone. Two streams opened means the second question was sent rather than swallowed — which the
    scenario above cannot show, because nothing was running when its one question was asked."""
    assert _run("two-in-flight")["streamsOpened"] == 2


def test_one_turn_finishing_does_not_report_a_project_that_still_has_another():
    """A tab can have several turns open at once now, which it never could before. "A turn is
    running" is a fact about the project, so the first one to unwind must not answer it for the ones
    still here — clearing the flag outright turned the Stop button off over a live turn."""
    out = _run("two-in-flight")

    assert out["busyWhileTheSecondIsOpen"] is True
    assert out["busyAfterBoth"] is False


def test_a_question_refused_because_context_moved_goes_back_to_the_composer():
    """The other way a queued turn ends without running. Sage will not answer it against context it
    was not written against, and will not pretend it did — so the text returns to the box it was
    typed in, ready to send again against what is there now."""
    out = _run("context-changed")

    assert out["composerSeed"] == "how many rows?"
    assert out["rolesAfter"] == []                       # and the bubble for it is gone
    assert out["queuedAfter"] == 0
    assert "./api/project/turn/cancel" not in out["posted"]
