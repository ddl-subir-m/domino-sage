"""The conversation you asked for last is the one the store settles on.

`openThread` awaits three times before it writes anything, and it had no guard, so the response
that landed LAST won. That is not the same as the one the person asked for last: click B then A and
a slow B settles the store on B while the route and the rail say A. `sendMessage` reads
`state.thread`, so the next message goes to the conversation nobody is looking at.

`selectApp` has a guard for this (`selecting`) and copying it would have been wrong — that one
makes a second asker bail, which is right when you are already going where it asked and wrong when
clicking A after B has to land on A. The store uses a generation counter instead, like `scopeLoad`.

The harness serves one conversation slowly and asks for it first, which is the only way to tell the
two rules apart.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "open_race_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

# Slow enough that the second open finishes first every time, short enough not to pad the suite.
_SLOW = {"conv_slow": 40}


def _run(steps: list[dict]) -> list[dict]:
    """What the store held after each step."""
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(steps), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_second_open_wins_even_when_its_answer_lands_first():
    """Ask for the slow conversation, then the fast one. The fast one is what the person is
    looking at, so it is what the store must hold when both have answered."""
    raced = _run([{"race": {"first": "conv_slow", "second": "conv_fast", "latency": _SLOW}}])[0]
    assert raced["thread"] == "conv_fast"


@needs_node
def test_a_superseded_open_leaves_no_part_of_itself_behind():
    """The whole view moves together or not at all. A store that guarded only the first write
    would pass the test above and still show the losing conversation's turns or context."""
    raced = _run([{"race": {"first": "conv_slow", "second": "conv_fast", "latency": _SLOW}}])[0]
    assert raced["title"] == "Fast"
    assert raced["turns"] == ["fast turn"]
    assert raced["context"] == ["fast.csv"]


@needs_node
def test_the_superseded_open_reports_that_it_lost():
    """It returns None rather than a thread it did not apply. Callers that route on the result
    would otherwise navigate to a conversation the store threw away."""
    raced = _run([{"race": {"first": "conv_slow", "second": "conv_fast", "latency": _SLOW}}])[0]
    assert raced["firstReturned"] is None
    assert raced["secondReturned"] == "conv_fast"


@needs_node
def test_opening_one_at_a_time_still_works():
    """The guard must cost nothing on the ordinary path. Opening the slow conversation on its own
    lands on it, including when a faster one was opened before."""
    steps = [{"open": "conv_fast"}, {"open": "conv_slow", "latency": _SLOW}]
    fast, slow = _run(steps)
    assert fast["thread"] == "conv_fast" and fast["returned"] == "conv_fast"
    assert slow["thread"] == "conv_slow" and slow["returned"] == "conv_slow"
    assert slow["context"] == ["slow.csv"]
