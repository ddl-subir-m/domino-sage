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

The corner that was left open is the third part: a BUILD that waits in line hands its name back at
`pending` and had no frame to take it back on, because build never streams the `user` frame Chat
does. #377 closed it by having the server say when a turn is granted — one `running` row from
`_acquire_turn`, yielded at the fall-through past every refusal, which both modes read the same way.
The `requeued*` modes below are that row, one per send.

It also took a special case out rather than putting one in. Chat used to retake the name on its
`user` frame, guarded on `ticket` because that frame is replayed on paths with no turn running. With
one row for both modes there is nothing for that guard to do, and the retake is gone.
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


@pytest.mark.parametrize("mode", ["droppedBuild", "droppedApprove", "droppedReadFailure"])
def test_a_dropped_stream_keeps_stop_refreshes_and_then_leaves_building_after_cancel(mode: str):
    """The live failure from #512: the browser lost SSE while the backend kept running."""
    out = _run(mode)

    assert out["afterDrop"] == {
        "running": True,
        "stopOffered": True,
        "typing": "Connection lost — build is still running.",
        "watcher": True,
    }
    assert out["afterRefresh"] == {"running": True, "stopOffered": True}
    assert out["afterCancel"] == {
        "running": False, "stopOffered": False, "requestedTurnId": "turn_abc"}
    assert out["afterRelease"] == {"running": False, "stopOffered": False}


@pytest.mark.parametrize(
    "mode",
    ["build", "approve", "chat", "opening", "openingBuild", "openingApprove",
     "requeued", "requeuedBuild", "requeuedApprove"],
)
def test_a_live_turn_claim_keeps_the_exact_backend_ticket(mode: str):
    assert _run(mode)["midTurn"]["turnId"] == "turn_abc"


def test_a_preframe_stop_does_not_adopt_a_same_scope_successor_from_state():
    """The response header binds A before any frame; a later state answer may belong to B."""
    assert _run("preframeStopRace") == {
        "buildStateReads": 1, "buildStateReadsAtStop": 0,
        "stopPosts": 1, "requestedTurnId": "turn_abc"}


def test_a_successor_keeps_its_header_identity_until_it_can_own_the_claim():
    """B is sent while A owns the claim. A ends before B's response arrives, and B receives no
    queue frames. Its saved response ticket must still be the exact ticket Stop sends."""
    assert _run("successorHeaderRace") == {
        "turnId": "turn_b", "stopPosts": 1, "requestedTurnId": "turn_b"}


def test_out_of_order_response_headers_obey_server_admission_state():
    """C, B, then A headers arrive. Only A says running, so response order cannot make B or C the
    visible Stop target while their pending frames are held."""
    assert _run("authoritativeHeaders") == {
        "turnId": "turn_a", "sequence": 1, "queued": 2,
        "stopPosts": 1, "requestedTurnId": "turn_a"}


def test_a_delayed_older_running_header_cannot_replace_the_newer_turn():
    """B's sequence wins before A's delayed response callback. A cannot replace or clear B."""
    assert _run("lateRunningHeader") == {
        "turnId": "turn_b", "sequence": 2,
        "stopPosts": 1, "requestedTurnId": "turn_b"}


def test_an_idless_legacy_header_cannot_replace_a_newer_exact_turn():
    """Missing sequence and ID keep compatibility without letting a stale callback replace B."""
    assert _run("legacyIdlessLateHeader") == {
        "turnId": "turn_b", "sequence": 2,
        "stopPosts": 1, "requestedTurnId": "turn_b"}


def test_legacy_backend_state_reconstructs_a_new_running_turn_after_refresh():
    """With no local request, authoritative state may replace completed A with B without a sequence."""
    assert _run("legacyStateReconstruction") == {
        "turnId": "turn_b", "sequence": 0, "stopOffered": True}


def test_a_backend_restart_replaces_old_sequence_and_rejects_the_delayed_old_header():
    """Sequence one in a new process is newer than sequence nine in the old process."""
    assert _run("restartEpochRace") == {
        "turnId": "turn_b", "epoch": "boot_new", "sequence": 1,
        "stopPosts": 1, "requestedTurnId": "turn_b"}


def test_an_old_backend_without_any_exact_identity_offers_only_a_safe_message():
    """No header and no queue event means no correlated Stop can be sent safely."""
    assert _run("legacyNoIdentity") == {
        "stopOffered": False,
        "message": "Stop is unavailable for this turn. Refresh Sage to update it.",
        "stopPosts": 0, "buildStateReads": 0}


def test_overlapping_chat_state_reads_settle_in_request_order():
    """Slow A cannot overwrite B after B's newer state response has settled."""
    assert _run("chatStateReverse") == {
        "turnId": "turn_b", "epoch": "boot_new", "sequence": 1}


def test_a_failed_state_read_after_stream_loss_keeps_the_exact_stop_claim():
    assert _run("droppedStateFailure") == {
        "running": True, "stopOffered": True,
        "typing": "Connection lost — build is still running.", "watcher": True,
        "turnId": "turn_abc"}


def test_a_failed_state_read_during_stop_unwind_keeps_the_accepted_stop_latch():
    out = _run("stopStateFailure")
    assert out["afterCancel"] == {
        "running": False, "stopOffered": False, "requestedTurnId": "turn_abc"}
    assert out["afterUnwind"] == {"running": False, "stopOffered": False}
    assert out["afterRelease"] == {"running": False, "stopOffered": False}


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


@pytest.mark.parametrize("mode", ["requeued", "requeuedBuild", "requeuedApprove"])
def test_a_turn_let_out_of_the_queue_takes_its_name_back(mode: str):
    """The other end of the queue guard. A send names its turn before it knows whether it will run,
    so a `pending` frame has to hand that name back — and then the turn waits, is granted, and
    starts streaming into exactly the same silent window this file is about.

    The `running` row behind the `pending` one is the queue letting go, and it is where the name
    goes back on (#377). The two build modes are why that row had to come from the server at all:
    each pauses on the grant with nothing behind it, because on a build nothing IS behind it — the
    next frame down the wire is the first frame of real work, the far side of the gate and the
    model's first token. Before #377 the readout here was a busy bar with no button, over the
    person's own turn, for that whole stretch."""
    out = _run(mode)

    assert out["midTurn"]["running"] is True
    assert out["midTurn"]["stopOffered"] is True
    assert out["midTurn"]["elsewhere"] is None
    assert out["runningTurnAfter"] is None
    # And the composer stops saying the opposite. The `pending` frame drew a "waiting in line" row
    # with a Cancel on it; from the grant on, that row is a second sentence about this turn that
    # contradicts the Stop bar, and its Cancel is a button the server can no longer act on — the
    # ticket is off the deque, so the click reaches `cancel_pending_turn` and nothing happens.
    assert out["midTurn"]["queued"] == 0


@pytest.mark.parametrize("mode", ["requeuedBuild", "requeuedApprove"])
def test_the_grant_is_a_state_row_and_not_a_receipt(mode: str):
    """What the `running` branch buys beyond the name, and the only thing a plant on the name alone
    would not catch: it RETURNS. Let a `running` frame reach `applyBuildEvent` and it matches none
    of the branches there and falls through to `appendBuildRow`, which writes it into the transcript
    the person reads — a blank row in the middle of their build, for a frame that is about the lock
    and not about the work.

    The optimistic `user` bubble the send drew is the whole of what the transcript should hold at
    this point: the turn has been granted and has done nothing yet."""
    out = _run(mode)

    assert out["midTurn"]["rows"] == ["user"]


@pytest.mark.parametrize("mode", ["requeuedBuild", "requeuedApprove"])
def test_the_granted_turn_is_named_no_wider_than_it_is(mode: str):
    """The grant names one turn, not the mode. Read off the same row #126's rule is read off, and
    for the same reason: a Stop that followed the person to another Built App or to the other
    composer would be a Stop over somebody else's work."""
    out = _run(mode)

    assert out["midTurn"]["stopOfferedInTheOtherMode"] is False
    assert out["midTurn"]["stopOfferedOnAnotherApp"] is False


def test_a_second_question_does_not_take_the_name_off_the_one_that_is_running():
    """The cost of naming a turn before the server has said anything, paid in the one case where
    the send is wrong: a second question goes out while the first is still streaming, and it is
    going to wait in line. Were it to name itself on the way out, the `pending` frame handing that
    name back would take the Stop button off the turn that IS running — #126 again, from the
    direction #371's fix opens. So a send only names its turn when nothing else is named."""
    out = _run("secondInLine")

    assert out["midTurn"]["stopOffered"] is True
    assert out["midTurn"]["elsewhere"] is None
    assert out["midTurn"]["turnId"] == "turn_abc", "the queued turn's header renamed the live turn"
    assert out["runningTurnAfter"] is None


@pytest.mark.parametrize("mode", ["queuedChat", "queuedApprove"])
def test_the_other_two_sends_do_not_claim_a_turn_waiting_in_line_either(mode: str):
    """The queue guard above has only ever been asked of `sendBuildPrompt`. A send names its turn
    before it knows whether it will run (#371), so all three sends now have a name to hand back —
    one guard per send, and Chat is the one the symptom was reported in."""
    out = _run(mode)

    assert out["midTurn"]["stopOffered"] is False
    assert out["midTurn"]["elsewhere"] is not None
    # The other half of the pair the grant is read against: while it really is waiting, the
    # composer's row is the truth and stays up.
    assert out["midTurn"]["queued"] == 1
