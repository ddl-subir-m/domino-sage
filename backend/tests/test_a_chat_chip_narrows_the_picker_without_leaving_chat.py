"""A Conversation's context doors move the sensitivity lock, so they have to re-ask (ADR-0043).

The server half was already right for one of the two chip shapes and already tested:
`_datasets_in_scope` counts the PINNED door beside the bound and attached ones. What the browser did
with that was ask nobody. `refreshSensitivity` ran on a scope load, a Binding change, a mode change
and a Conversation open — and putting something into a Conversation is none of those, even though it
is one of the three doors that arms the lock.

So Chat went on offering every vendor model for a conversation the router would refuse, and the lock
landed on whatever read happened next for some other reason. In practice that was the trip to Build,
whose `refreshBindings` re-asks, which is why the narrowing looked like it needed a tab change. The
removal door had the mirror of it: the lock would not come off until the same trip, which is the
quieter failure — over-restrictive rather than leaky — but it makes the one way out the copy offers
look like it did nothing.

Both doors ask on the same gate — has this deployment opted in — rather than on whether a lock is
currently drawn. The tighter gate is the one that looks right and is not: what is drawn is exactly
what an attach's unawaited read is in the middle of replacing, so an undo taken before that read
lands would ask nobody and let the locked answer arrive over a chip already gone.

The whole-Dataset chip is covered where it can actually be proved —
`test_a_declared_dataset_narrows_by_every_door` drives the real reader. The fake server here applies
the same rule rather than locking on any chip at all, because the first version of this file did
lock on any chip, and it passed green while the real server answered "unlocked" for that shape.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "chat_attach_lock_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)


def _run(act: str) -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"act": act}),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# --- The attach door ------------------------------------------------------------------------------

@needs_node
def test_a_pinned_dataset_file_narrows_the_picker_before_anyone_leaves_chat():
    got = _run("attach")
    assert got["locked"] is True
    assert got["approved"] == ["qwen-2-5"]


@needs_node
def test_pinning_a_whole_dataset_narrows_it_too():
    """The same door at the other grain. The chip carries no `datasetId` — the resource id IS the
    Dataset — so a reader that only looked for the file's field would miss the coarser act. What
    the browser has to get right here is only that it ASKS; the reading is the server's."""
    got = _run("attach-whole-dataset")
    assert got["locked"] is True


@needs_node
def test_an_opted_out_deployment_pays_no_round_trip_for_an_attach():
    """Why the read is gated rather than unconditional. With the gate off there is no answer an
    attach can change, and every deployment would pay a request per chip to hear that."""
    got = _run("attach-gate-off")
    assert got["sensitivityReads"] == 0
    assert got["locked"] is False


@needs_node
def test_the_first_chip_in_a_new_chat_does_not_lose_to_the_read_that_opened_it():
    """Two reads for ONE conversation, which the thread-id guard cannot tell apart.

    `attach` opens a conversation when none is open, and `newThread` fires its own read on the way
    through — asked before the chip exists, so it answers unlocked — while the read after the post
    answers locked. Same thread, no generation, and the locked one is the slower of the two because
    it awaits `loadAppList` first. Whichever landed last won, and losing puts every vendor model
    back in the picker for a conversation the router will refuse.
    """
    got = _run("attach-new-chat")
    assert got["locked"] is True


@needs_node
def test_a_newer_read_that_failed_does_not_take_the_older_answer_with_it():
    """Why the ordering compares against what has been APPLIED rather than what has been ASKED.

    A guard that discarded this answer because a newer read existed was making a promise the
    rejection path does not keep: it writes nothing. A newer read that 5xx'd left both answers on
    the floor and the stale one on screen — and the answer it dropped is the locked one, which makes
    that guard worse than no guard at all.
    """
    got = _run("attach-then-failed-read")
    assert got["locked"] is True


# --- The removal door -----------------------------------------------------------------------------

@needs_node
def test_closing_the_last_declared_chip_lifts_the_lock_without_leaving_chat():
    got = _run("remove")
    assert got["locked"] is False


@needs_node
def test_closing_a_whole_dataset_chip_lifts_it_too():
    """The coarser shape again, on the door that reads it through the other branch. A regression
    that dropped `dataset:` from the removal path alone would leave this lock stuck on."""
    got = _run("remove-whole-dataset")
    assert got["locked"] is False


@needs_node
def test_an_opted_out_deployment_pays_no_round_trip_for_a_removal_either():
    got = _run("remove-gate-off")
    assert got["sensitivityReads"] == 0


@needs_node
def test_an_undo_taken_before_the_attachs_read_lands_does_not_leave_the_lock_on():
    """Mention a declared Dataset, then close the chip again before the attach's read comes back.

    A removal gated on whether a lock is DRAWN reads the screen, and the screen is one read out of
    date here — it says nothing is locked, so nothing is asked, and the attach's locked answer lands
    afterwards to grey the picker for a chip that is already gone. That is the lock-that-will-not-
    come-off this half exists to prevent, reached by the ordinary gesture of undoing a mistake.
    """
    got = _run("remove-after-attach")
    assert got["locked"] is False


@needs_node
def test_a_removal_does_not_lift_a_lock_the_transcript_is_holding():
    """The server decides, never this. Once a turn has run under the lock the transcript carries the
    rows, so closing every chip lifts nothing and the way out is a new chat. A browser that dropped
    the lock locally on a removal would be the stale-unlocked direction — a model somebody picks and
    the router then refuses under them."""
    got = _run("remove-sticky")
    assert got["locked"] is True
