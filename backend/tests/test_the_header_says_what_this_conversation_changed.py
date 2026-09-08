"""The Build header does not call an app "other" when the named app is not in the list.

`touched` is what a Conversation CHANGED. The header counted everything in it that was not the
app in the preview and called the count "other" — a word that only means anything if the named app
is in the list too. It often is not: a handoff binds an app whose build never ran, another tab
moves the Project-wide selection (ADR-0040), a delete falls back to a survivor and keeps the
Conversation. A reporter read `Gong Activity Hub · 1 other app changed here` in a Conversation that
had only ever built something else, and the header had told them their app was built.

So the list is split before it is counted. Named app in it: unchanged, the count is honest. Named
app not in it: the sentence is about the Conversation, and it names what it did change.

Nothing is mounted — see `js/build_header_harness.mjs` for why. The helpers below are the ones
`test_build_keeps_the_conversation_rail.py` drives the same harness with; the count tests live
there and stay there, because they are about the branch that did not change.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)


def _run(steps: list[dict]) -> list[dict]:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(steps),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _build(thread: str = "thr_many", select: str | None = None, **extra) -> dict:
    return _run([{"build": thread, "select": select, **extra}])[-1]


def _said(step: dict) -> str:
    return " ".join(step["words"])


@needs_node
def test_an_app_this_conversation_never_changed_is_not_told_it_was():
    """The reported defect, in the fixture's words: `thr_one` built `app_a` and nothing else, so
    with `app_b` in the preview there is no "other" — there is one app, and it is not this one."""
    said = _said(_build("thr_one", select="app_b"))
    assert "This conversation changed Desk dashboard" in said
    assert "other app" not in said


@needs_node
def test_it_names_every_app_it_changed_and_not_a_count():
    """A count is only useful next to the one you are looking at. With the named app outside the
    list the names are the whole answer, so all of them are on the screen."""
    said = _said(_build("thr_two", select="app_c"))
    assert "This conversation changed Desk dashboard, P&L report" in said
    assert "other app" not in said


@needs_node
def test_the_two_sentences_are_never_both_on_the_screen():
    """They are two readings of one list, so a header showing both would be contradicting itself.
    Checked from both sides: the app that IS in `touched` gets the count and only the count."""
    counted = _said(_build("thr_many", select="app_a"))
    assert "2 other apps changed here" in counted
    assert "This conversation changed" not in counted

    named = _said(_build("thr_many", select="app_c"))
    assert "This conversation changed" in named
    assert "other app" not in named


@needs_node
def test_a_conversation_that_changed_nothing_still_says_nothing():
    """The empty case is the one the old header got right, and splitting the list must not lose
    it: no work to report is not the same fact as work reported somewhere else."""
    said = _said(_build("thr_none", select="app_a"))
    assert "This conversation changed" not in said
    assert "other app" not in said
