"""The rail shows the name the server gave a Conversation, while the turn is still running.

A Conversation opened in Build is named off the first thing typed into it, and the server does
that naming at the TOP of the turn, before the build loop is entered. The rail did not find out.
`sendBuildPrompt` re-read the preview and the Bindings when the turn ended and never re-read the
Thread index at all, so the row kept the words "New conversation" for the whole build and for
every build after it -- until something unrelated reloaded the list. `approveBuild` had the read;
the ordinary path never did, which is why the fix that named the Conversation looked like it had
never shipped.

Fixed by telling rather than asking: the turn yields the name it just wrote, and the rail applies
it. No read, so the rail is right within a frame instead of within a build.

The harness answers the one question `build_stream_harness` cannot. That one awaits the whole turn,
so every read the `finally` makes has already happened by the time it looks, and the minutes before
that are exactly what this is about. `/threads` there answers with the placeholder forever, so a
pass cannot be bought by a round trip -- see `js/rail_lag_harness.mjs`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "rail_lag_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


@pytest.fixture(scope="module")
def report() -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input="", check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_rail_row_carries_the_new_name_before_the_turn_ends(report: dict) -> None:
    """The symptom, stated from the person's side: they typed, and the rail still said nothing."""
    assert report["midTurn"]["railRow"] == report["named"], report["midTurn"]


@needs_node
def test_the_open_conversation_carries_it_too(report: dict) -> None:
    """The row and the header answer the same question and must not disagree about it."""
    assert report["midTurn"]["openConversation"] == report["named"], report["midTurn"]


@needs_node
def test_the_name_is_learned_from_the_frame_and_not_fetched(report: dict) -> None:
    """The whole point of carrying the name on the stream.

    `/threads` in the harness answers with the placeholder forever, so a rail that went and asked
    would still be wrong -- but it would also be paying for a read per turn that the frame already
    covers. Asserted on the count rather than the answer so the cost is what fails, not the words.
    """
    assert report["threadReadsBeforeSnapshot"] == 0, report


@needs_node
def test_the_name_survives_the_end_of_the_turn(report: dict) -> None:
    """Applied to the store, not painted over the top of it.

    The `finally` reloads several things and the transcript is rebuilt as the turn unwinds. A name
    that only lasted until then would read as fixed in a demo and broken by the next click.
    """
    assert report["afterTurn"]["railRow"] == report["named"], report["afterTurn"]
