"""The first chip's one-time note (#137): a chip is per-Conversation.

The note itself needs a browser to be seen, so, as in test_workbench_composer_mention.py, most of
these are source assertions: each pins the shape that keeps an acceptance criterion true. The
dismissal is the one part with behaviour worth running — it goes through the node harness the
viewer-prefs suite already uses, against the real prefs.js and a fake localStorage.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_WB = Path(__file__).resolve().parents[1] / "sage" / "workbench"
_HARNESS = Path(__file__).resolve().parent / "js" / "prefs_harness.mjs"
UI = (_WB / "js" / "components" / "composer.js").read_text()
PREFS = (_WB / "js" / "prefs.js").read_text()
CSS = (_WB / "css" / "chat.css").read_text()

COPY = "Added to this Conversation only — an app you build declares its own Resources."

_needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                 reason="node is not on PATH (it is in the Sage image)")


def _run(steps: list[dict]) -> list:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(steps), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# The note ---------------------------------------------------------------------


def test_the_note_rides_the_chips_and_only_the_chips():
    """Appears on first chip-add only: the same guard that draws the chip row draws the note, so
    it shows exactly when the first chip does — never over an empty composer, and not as a second
    copy per chip."""
    assert "attachments.length > 0 && !chipHintDismissed &&" in UI
    assert UI.count("sw-chip-hint'") == 1
    assert ".sw-chip-hint" in CSS


def test_the_copy_follows_the_writing_rules():
    """Sentence case, no exclamation points, and the glossary's words: Conversation and Resources
    are what a person is taught, `thread` and `attachment` are identifiers."""
    assert COPY in UI
    assert "!" not in COPY
    assert "Don't show this again" in UI


# The dismissal ----------------------------------------------------------------


def test_dismissing_hides_now_and_persists_through_the_viewer_pref():
    # State first, pref second: hiding must not wait on a write the browser may refuse.
    assert "setChipHintDismissed(true);" in UI
    assert "SW.prefs.set('chipScopeHintDismissed', true);" in UI
    # And the render reads the same pref back, so a dismissal from a past visit still holds.
    assert "SW.prefs.get('chipScopeHintDismissed')" in UI


def test_the_composer_never_touches_storage_itself():
    """Guarded storage access has one address: prefs.js. Both touches sit inside a try, and the
    composer goes through SW.prefs rather than growing a second, unguarded reader."""
    assert "chipScopeHintDismissed: { fallback: false, values: [true, false] }" in PREFS
    assert "try {\n      return asRecord(JSON.parse(window.localStorage.getItem(KEY))) || {};" in PREFS
    assert "try {\n        window.localStorage.setItem(KEY, JSON.stringify(all));" in PREFS
    assert PREFS.count("window.localStorage") == 2   # no third, unguarded touch
    assert "localStorage" not in UI


@_needs_node
def test_with_nothing_stored_the_note_is_due():
    """The page renders correctly with no stored value: the pref answers False, so the note shows.
    A first visit and blocked storage look the same, which is the safe direction."""
    assert _run([{"viewer": "u1", "op": "get", "name": "chipScopeHintDismissed"}]) == [False]


@_needs_node
def test_a_dismissal_is_still_there_after_a_reload():
    answers = _run([
        {"viewer": "u1", "op": "set", "name": "chipScopeHintDismissed", "value": True},
        {"op": "reload"},
        {"op": "get", "name": "chipScopeHintDismissed"},
    ])
    assert answers[0] is True
    assert answers[-1] is True


@_needs_node
def test_one_viewers_dismissal_does_not_silence_anothers_note():
    answers = _run([
        {"viewer": "u1", "op": "set", "name": "chipScopeHintDismissed", "value": True},
        {"viewer": "u2", "op": "get", "name": "chipScopeHintDismissed"},
    ])
    assert answers[-1] is False


@_needs_node
def test_broken_storage_reads_as_not_dismissed():
    """Unparseable storage and a hand-edited value both land on the fallback: the note may show
    one extra time, which beats a page that fails to draw."""
    answers = _run([
        {"viewer": "u1", "op": "seed", "key": "sw.prefs", "raw": "not json {"},
        {"op": "get", "name": "chipScopeHintDismissed"},
        {"op": "seed", "key": "sw.prefs", "raw": '{"u1": {"chipScopeHintDismissed": "yes"}}'},
        {"op": "get", "name": "chipScopeHintDismissed"},
    ])
    assert answers[1] is False
    assert answers[3] is False
