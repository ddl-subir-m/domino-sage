"""The model on the control is the one the NEWEST answer named, not the one that landed last.

`loadBuild` issues `/project` and then writes the whole model block off the result — eight fields,
between the await and the `applyAppScope` guard three lines under it. The guard covers the
attachments; the model block had none at all. So a read that went out before a model save could
land after it and put the picker back on the model the server held when the read started, and
`chatConfirmed`/`buildConfirmed` go back with it, which is the pair a later refusal restores (#306,
#323).

The fix is the rule this file already lives by and had not applied here: READS carry a generation,
ACTS do not. The read hands the `appScopeTicket()` the line above it already took; the five POST
callers hand nothing and claim the head of the queue where their route answers, because the server
has just written the record the route hands back.

Three conditions, because they pull against each other. Gating the POST sites too would pass the
stale-read tests and revert every save made while a poll was running — the same defect seen from
the other side, which `test_a_save_lands_even_when_a_read_finished_under_it` catches. Letting a read
that FAILED claim the queue would pass both — `loadBuild` catches a dead `/project` into `{}` and
hands it over anyway — and throw away a good read still in flight, which is
`test_a_read_that_failed_does_not_take_the_queue_from_one_that_answered`.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "stale_project_read_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

# The two answers the harness tells apart, on every field of the block a person can move.
_OLD = {"model": "coder", "effort": None, "buildModel": "coder", "buildEffort": None,
        "mode": "auto"}
_NEW = {"model": "gpt-5.4", "effort": "high", "buildModel": "gpt-5.4", "buildEffort": "high",
        "mode": "implement"}
# What a Chat save moves and, just as much to the point, what it leaves alone: the Build pair is
# still whatever the last read said.
_CHAT_SAVED = {"model": "gpt-5.4", "effort": "high", "buildModel": "coder", "buildEffort": None,
               "mode": "auto"}


def _run(steps: list[dict]) -> list[dict]:
    """What the store held after each step."""
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(steps), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_an_ordinary_read_still_writes_the_model_block():
    """The guard must cost nothing on the path everything takes. A `loadBuild` with nothing racing
    it installs the block it read, all five fields."""
    assert _run([{"plainRead": True}])[0] == _NEW


@needs_node
def test_a_read_that_went_out_before_a_save_does_not_undo_it():
    """The read is held open, the save lands inside its window, and only then does the read answer
    with the pre-save block. The person picked that model and the server took it, so it is what the
    control has to keep."""
    assert _run([{"staleReadDuringSave": True}])[0] == _CHAT_SAVED


@needs_node
def test_the_newer_of_two_reads_wins_however_they_resolve():
    """Two `loadBuild` calls, the first held so it answers last. Without a generation the store
    settles on whichever answer arrives last, which is not the same as the newest one."""
    assert _run([{"raceReads": True}])[0] == _NEW


@needs_node
def test_a_save_lands_even_when_a_read_finished_under_it():
    """The other half of the rule. The POST is held, a whole `loadBuild` completes under it serving
    the pre-save block — correctly, since that is the newest answer at that moment — and then the
    save answers. An act is newer than any read in flight, so its answer is the one standing."""
    assert _run([{"saveAnswersAfterRead": True}])[0] == _CHAT_SAVED


@needs_node
def test_a_read_that_failed_does_not_take_the_queue_from_one_that_answered():
    """`loadBuild` catches a dead `/project` into `{}`, which is truthy and so reaches
    `applyModelStatus` deliberately — and writes not one field. An answer with nothing in it must
    not claim the watermark either, or one 500 discards a good read that is still in flight."""
    assert _run([{"failedReadUnderRead": True}])[0] == _NEW


@needs_node
def test_a_refusal_after_a_stale_read_puts_back_the_pair_the_server_took():
    """The field the ticket is really about. `applyModelStatus` also writes `chatConfirmed`, which
    is what a refused save restores (#306, #323) and which nothing on `state` shows — so a stale
    read that re-confirmed a superseded pair would sit there until the next refusal named the wrong
    model, and every test above would still pass."""
    assert _run([{"refusedAfterStaleRead": True}])[0] == _CHAT_SAVED


@needs_node
def test_the_conditions_do_not_interfere():
    """Run in one process, in order. `modelStatusApplied` is module state, so a watermark left too
    high by an earlier step would silently refuse a later one's write — and each step asserted on
    its own would never see it."""
    got = _run([{"plainRead": True}, {"staleReadDuringSave": True},
                {"raceReads": True}, {"saveAnswersAfterRead": True},
                {"failedReadUnderRead": True}, {"refusedAfterStaleRead": True}])
    assert got == [_NEW, _CHAT_SAVED, _NEW, _CHAT_SAVED, _NEW, _CHAT_SAVED]
