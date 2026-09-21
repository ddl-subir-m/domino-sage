"""A picked @mention is a chip at once, and picking it again during the wait is not a second POST.

The chip used to appear when the attach POST answered, and `addToContext`'s only duplicate guard
reads the chip list — empty until then. For a table chip the POST took as long as the warehouse
took to describe the table, so a second pick during the wait was a second POST and two chips
landed (Chat and Build alike; the mode made no difference). `store.attach` now pushes a placeholder
before the POST, replaces it in place with the server's row, and drops it on a refusal; and
`sendMessage` waits on the POSTs still out, because the chip stopped being the cue that the row is
on the Thread.

Driven through `tests/js/mention_pending_chip_harness.mjs` — the real store under a fake `fetch`
that holds the attach POST open, so "during the wait" is a state the harness is actually in.

Plants, one per condition (each measured red before the fix was written back):
- drop the placeholder push in `attach` → `test_a_second_pick_during_the_wait_is_one_post` reds
  on `postsDuringWait` (2, not 1).
- drop the `catch` that removes the placeholder → `test_a_refused_chip_leaves_no_chip_behind`
  reds on `after`.
- drop the `attachInFlight` wait in `sendMessage` → `test_a_turn_waits_for_the_chip_to_land`
  reds on `turnPostsWhileHeld`.
- drop `landedRow` from `removeFromConversation` → `test_removing_a_chip_still_landing_removes_the_row_it_lands_as`
  reds on `after` (the DELETE went for `pending:…`, and the row came back with the POST).
- drop the `conversationId() !== tid` return in `attach` → `test_a_post_that_outlives_its_conversation_stays_out_of_the_next_one`
  reds on `after`.
- draw the placeholder for an unindexed Resource too → `test_an_unindexed_resource_gets_no_placeholder`
  reds on `duringWait`.
- drop the `flight.undone` return in `attach` → `test_a_chip_undone_while_landing_is_not_announced`
  reds on `receipts`.

Not planted: the promote path's `landedRow` (`promoteScratch`), which needs an Upload flow this
harness does not fake; it is the same guard the two `drop` paths carry.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "mention_pending_chip_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)")


def _run(act: str) -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"act": act}), check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_chip_is_on_screen_before_the_post_goes_out():
    got = _run("pick-twice")
    # The list as it stood when the POST was issued — one chip, marked pending.
    assert got["chipsAtRequest"][0] == [{"id": "pending:table:ds-dwh:DWH.MARTS.MIXPANEL__EVENT",
                                         "pending": True}]
    assert got["duringWait"][0]["pending"] is True
    assert got["duringWait"][0]["name"] == "MIXPANEL__EVENT"


@needs_node
def test_a_second_pick_during_the_wait_is_one_post():
    got = _run("pick-twice")
    assert got["postsDuringWait"] == 1, got["requests"]
    assert got["posts"] == 1
    assert got["outcomes"] == ["fulfilled", "fulfilled"]
    # The placeholder was replaced in place by the server's row: one chip, no longer pending.
    assert got["after"] == [{"id": "att_1", "pending": False, "name": "MIXPANEL__EVENT"}]


@needs_node
def test_a_refused_chip_leaves_no_chip_behind():
    got = _run("pick-fails")
    assert got["outcomes"][0] == "rejected"
    assert got["after"] == [], got["after"]


@needs_node
def test_a_turn_waits_for_the_chip_to_land():
    got = _run("send-waits")
    assert got["turnPostsWhileHeld"] == 0, got["requests"]
    # And it did go, once the attach answered — after the attach, not instead of it.
    posts = [r for r in got["requests"] if r.startswith("POST")]
    assert posts[0].endswith("/context") and posts[1].endswith("/chat/stream"), posts
    # The turn's own request saw the server's row, not the placeholder.
    assert got["chipsAtRequest"][1] == [{"id": "att_1", "pending": False}]


@needs_node
def test_removing_a_chip_still_landing_removes_the_row_it_lands_as():
    """The undo during the wait. The chip has no server id yet, so the removal waits for the row
    and deletes THAT — one DELETE, for the real id, after the POST; no chip, no row left."""
    got = _run("remove-pending")
    assert got["duringWait"][0]["pending"] is True
    assert got["deletesWhileHeld"] == 0, got["requests"]
    assert got["deletes"] == ["DELETE ./api/threads/thr_a/context/att_1"], got["requests"]
    assert got["after"] == [], got["after"]
    assert got["serverRows"] == 0


@needs_node
def test_a_post_that_outlives_its_conversation_stays_out_of_the_next_one():
    got = _run("switch-mid-post")
    assert got["after"] == [], got["after"]


@needs_node
def test_an_unindexed_resource_gets_no_placeholder():
    """A row Sage adds on its own is not in `resourceIndex`; a chip reading a raw id for the wait
    is worse than the wait, so that path keeps the old shape — the chip lands with the row."""
    got = _run("pick-unknown")
    assert got["duringWait"] == []
    assert len(got["after"]) == 1 and got["after"][0]["pending"] is False


@needs_node
def test_a_chip_undone_while_landing_is_not_announced():
    """The attach's own continuation runs first when the POST answers. It must not say "X is now
    in the conversation" for a chip the person already undid, nor leave the chip behind."""
    got = _run("undo-not-acked")
    assert got["after"] == [], got["after"]
    assert got["receipts"] == [], got["receipts"]
