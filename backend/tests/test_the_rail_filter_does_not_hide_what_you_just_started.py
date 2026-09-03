"""The Rail's app filter must not narrow a list nobody chose to narrow (#150 follow-up).

Build's header sets the filter on every app pick — "the rail follows the pick, so the two halves of
the screen name the same app" — and the Rail now starts hidden, so the filter is routinely set while
nothing is on screen. Two rules then collide, each written in one file and depended on in another.

A new Conversation has touched no app, and `conversation-list.js` draws its pending row only when no
filter is set, on the grounds that the row "is not in history" and should not sit above a sentence
saying nothing was found. Correct — and with a standing filter it means the row for the Conversation
somebody just started is absent, and the Rail says no conversation has changed that app yet. That is
the Rail contradicting the button that was pressed a moment earlier. Three doors start a
Conversation; only the expanded head cleared the filter, and it did so by accident, through the
`collapseRail` that happens to follow it.

And `toggleRail` cleared the filter on close, on its own stated grounds that it would otherwise
"come back on the next open as a filter nobody could see they had applied". A filter set WHILE the
Rail was hidden arrives through that same door and was not cleared — the same rule, stated half way.

Only running it shows either, because the store decides the filter and the component decides what a
filter hides. The harness fires the real controls and reports the Rail as somebody would next see
it: the row, the chip, and whatever the Rail says when it has nothing to show.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "rail_filter_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

# The app picked in Build's header, whose id the standing filter names.
FILTERED_APP = "Risk Dashboard"


def _act(act: str) -> dict:
    """One act against a Rail that already carries an app filter, then the Rail as next seen."""
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"act": act}), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_conversation_started_from_the_collapsed_rail_is_drawn():
    """The door the bug was found on. The collapsed head's plus icon is the only way to a new
    Conversation while the Rail is shut, which is where the Rail now starts."""
    out = _act("collapsed-plus")

    assert out["pendingConversation"] is True
    assert out["railAppFilter"] is None
    assert out["pendingRowDrawn"] is True, "the Conversation just started is not in the Rail"
    assert out["emptyText"] is None


def test_a_conversation_started_from_the_command_palette_is_drawn():
    """The third door, and the one nothing was clearing for — the expanded head's accident did not
    reach it. ⌘K then New conversation is the same act and has to mean the same thing."""
    out = _act("palette")

    assert out["railAppFilter"] is None
    assert out["pendingRowDrawn"] is True


def test_the_expanded_rails_own_button_still_works():
    """The control. This is the door that was already right, by way of the `collapseRail` after it,
    and moving the clear into the store must not have taken it away."""
    out = _act("expanded-plus")

    assert out["railAppFilter"] is None
    assert out["pendingRowDrawn"] is True


def test_opening_the_rail_does_not_open_it_already_narrowed():
    """A filter can now be set while the Rail is hidden, so it arrives by the door `toggleRail`
    already guards in the other direction. Without this, ⌘\\ opens the Rail showing only an app
    somebody picked in a different control, possibly long before."""
    out = _act("open-rail")

    assert out["railAppFilter"] is None
    assert out["filterChip"] is False
    assert out["rows"] == ["t-old"], "history the filter had been hiding is back"


def test_the_rail_never_says_nothing_changed_an_app_right_after_you_started_one():
    """The sentence that made this a bug rather than a preference. It is a true sentence about
    history and a false one about the screen, and it was drawn in place of the row it displaced."""
    for act in ("collapsed-plus", "palette", "expanded-plus"):
        assert _act(act)["emptyText"] is None, act
