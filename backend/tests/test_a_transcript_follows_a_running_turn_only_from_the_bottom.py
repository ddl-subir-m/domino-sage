"""Chat and Build follow a running turn only for a reader who is at the bottom.

Every narration line and tool step of a running turn rewrites the indicator under the last
message. The scroller followed that indicator unconditionally, so a reader who scrolled up to read
something earlier was pulled back down each time the reasoning moved, and could not stay anywhere.
A reader who has scrolled away is offered Jump to latest once something arrives below them.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "transcript_follow_harness.mjs"

pytestmark = [
    pytest.mark.skipif(shutil.which("node") is None,
                       reason="node is not on PATH (it is in the Sage image)"),
    pytest.mark.parametrize("mode", ["chat", "build"]),
]


def _run(mode: str, acts: list[dict]) -> list[dict]:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"mode": mode, "acts": acts}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_reader_at_the_bottom_is_carried_along_by_the_turn(mode):
    steps = _run(mode, [{"act": "narrate", "text": "Reading the schema"},
                        {"act": "answer", "text": "Here is"},
                        {"act": "stream", "text": " the answer"}])
    for step in steps:
        assert step["scrollTop"] == step["bottom"], step
        assert not step["jumpButton"], step


def test_a_reader_who_scrolled_up_stays_where_they_are_while_the_turn_runs(mode):
    steps = _run(mode, [{"act": "scroll", "to": 200},
                        {"act": "narrate", "text": "Reading the schema"},
                        {"act": "narrate", "text": "Joining the two tables"},
                        {"act": "answer", "text": "Here is"},
                        {"act": "stream", "text": " the answer"}])
    for step in steps:
        assert step["scrollTop"] == 200, step


def test_jump_to_latest_appears_only_once_something_arrives_below(mode):
    """Scrolling up with nothing new is reading, not falling behind."""
    steps = _run(mode, [{"act": "scroll", "to": 200},
                        {"act": "narrate", "text": "Reading the schema"}])
    assert not steps[0]["jumpButton"]
    assert steps[1]["jumpButton"]


def test_jump_to_latest_takes_the_reader_down_and_following_resumes(mode):
    steps = _run(mode, [{"act": "scroll", "to": 200},
                        {"act": "narrate", "text": "Reading the schema"},
                        {"act": "jump"},
                        {"act": "narrate", "text": "Joining the two tables"}])
    for step in steps[2:]:
        assert step["scrollTop"] == step["bottom"], step
        assert not step["jumpButton"], step


def test_scrolling_back_to_the_bottom_picks_the_turn_up_again(mode):
    steps = _run(mode, [{"act": "scroll", "to": 200},
                        {"act": "narrate", "text": "Reading the schema"},
                        {"act": "scroll", "to": "bottom"},
                        {"act": "narrate", "text": "Joining the two tables"}])
    assert steps[1]["scrollTop"] == 200
    assert not steps[2]["jumpButton"]
    assert steps[-1]["scrollTop"] == steps[-1]["bottom"]


def test_opening_another_conversation_lands_on_its_newest_turn(mode):
    """Where the last conversation was scrolled to says nothing about the next one."""
    steps = _run(mode, [{"act": "scroll", "to": 200},
                        {"act": "narrate", "text": "Reading the schema"},
                        {"act": "open", "thread": "conv_b"}])
    assert steps[-1]["scrollTop"] == steps[-1]["bottom"]
    assert not steps[-1]["jumpButton"]
