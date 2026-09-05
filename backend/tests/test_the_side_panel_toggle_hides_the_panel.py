"""A control captioned Hide must hide, whichever state the panel starts in (#150 follow-up).

`toggleDock(tab)` toggled ONE tab: it closed only when the tab it was given was the tab already
open, and otherwise switched to it. That was the right writer for the collapsed rail's per-tab
buttons and the wrong one for the doors that mean "hide this panel", all of which passed the
constant `resources`. So with the panel open on Activity, a control captioned "Hide the side panel"
switched to Resources instead, and the shortcut the help drawer advertises as "Toggle the side
panel" took two presses to close.

#150 hit this on the dock's own fold button and fixed that one by passing `dockTab`, leaving its
siblings behind. It also made the state reachable from a cold start: `dockTab` is a remembered
preference now, so somebody whose last session ended on Activity lands in the broken case on load
rather than having to click their way into it.

WHAT #151 CHANGED. There is one panel, so there is no Activity tab to be open on and no tab bar to
hang a control from — the panel draws its own heading and its own Hide button. The sub bar's chevron
is gone with it: it was a third door, two rows up from the panel's own and drawn with the same
glyph, and two identical controls a thumb's width apart are one control and a bug report. `activity`
survives here as a STORED value, which is the case that still exists — see the migration test at the
bottom.

Only running it shows any of this, because the caption and the effect are written in different files
— `components/resource-panel.js` draws the words, `store.js` decides what the click does — and a
control that says one thing and does another is exactly what falls between two files that each read
fine alone. The harness fires the real handlers, reads the tooltips they are drawn under, and
reports the store.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "dock_toggle_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _press(dock_tab):
    """One press of each door, each from a panel in `dock_tab`."""
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"dockTab": dock_tab}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_panels_own_hide_button_hides_it():
    """The control, and the sibling #150 already fixed. It moved out of the dock's tab bar and into
    the panel's heading when the tab bar went, so this is also the claim that it survived the move
    still wired to the writer that records the close."""
    for tab in ("resources", "activity"):
        pressed = _press(tab)["fold"]

        assert pressed["caption"].startswith("Hide the side panel")
        assert pressed["dockTab"] is None, f"open on {tab}: the button switched instead of hiding"


def test_the_shortcut_the_help_drawer_calls_a_toggle_is_one():
    """Same root cause on the keyboard, and worse, because `dockTab` is remembered: somebody whose
    last session ended on a tab that is no longer drawn gets a two-press close on the first ⌘/ after
    a page load."""
    for tab in ("resources", "activity"):
        assert _press(tab)["shortcut"]["dockTab"] is None, f"open on {tab}: ⌘/ switched tab"


def test_hiding_the_panel_drops_the_filter_and_is_remembered():
    """Two things the old wiring lost along with the close, on the doors that did not close. A
    filter is a question about a list nobody is looking at once the panel shuts, and it would come
    back on the next open as a filter nobody could see they had applied. And the close is recorded,
    or a panel somebody shut is open again on the next load."""
    for door in ("fold", "shortcut"):
        pressed = _press("activity")[door]

        assert pressed["panelFilter"] is None, door
        assert pressed["wrote"] == [{"u1": {"dockTab": None}}], door  # keyed by the viewer


def test_every_door_still_opens_a_closed_panel_on_resources():
    """The other half of a toggle, and the reason `toggleDock(dockTab)` alone was never the fix:
    from closed, `dockTab` is null, and toggling null against null left the panel shut."""
    pressed = _press(None)

    assert pressed["rail"]["dockTab"] == "resources"
    assert "Show resources" in pressed["rail"]["caption"]
    assert pressed["shortcut"]["dockTab"] == "resources"
    # The Hide button lives inside the open panel, so a closed one draws no such control at all.
    assert pressed["fold"] == {"absent": True}


def test_each_state_draws_exactly_one_of_the_two_controls():
    """The duplicate this ticket removed, asserted as a rule rather than as one deletion. Hide is
    drawn only while the panel is open and Show only while it is shut, so at no moment are there two
    controls on screen that mean the same thing — which is the state the sub bar's chevron put the
    screen in permanently."""
    assert _press(None)["fold"] == {"absent": True}
    assert _press("resources")["rail"] == {"absent": True}


def test_a_panel_remembered_on_the_tab_that_no_longer_exists_still_opens():
    """`activity` is written into records that already exist, and `prefs.get` refuses a value it
    does not recognise — so dropping it from the list would read back as the fallback, a CLOSED
    panel, for exactly the people who left theirs open. It is migrated instead: the value stays
    legal and reads back as the one panel there is."""
    assert _press("activity")["fold"]["caption"].startswith("Hide the side panel")
