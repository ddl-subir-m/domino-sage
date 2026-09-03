"""A control captioned Hide must hide, whichever tab the panel is open on (#150 follow-up).

`toggleDock(tab)` toggles ONE tab: it closes only when the tab it is given is the tab already open,
and otherwise switches to it. That is the right writer for the collapsed rail's per-tab buttons, and
the wrong one for the two doors that mean "hide this panel" — both of which passed the constant
`resources`. So with the panel open on Activity, the sub bar's chevron captioned "Hide the side
panel" switched to Resources, and the shortcut the help drawer advertises as "Toggle the side panel"
took two presses to close.

#150 hit this on the dock's own fold button and fixed that one by passing `dockTab`, leaving its two
siblings behind. It also made the state reachable from a cold start: `dockTab` is a remembered
preference now, so somebody whose last session ended on Activity lands in the broken case on load
rather than having to click their way into it.

Only running it shows this, because the caption and the effect are written in different files —
`components/shell.js` draws the words, `store.js` decides what the click does — and a control that
says one thing and does another is exactly what falls between two files that each read fine alone.
The harness fires the real handlers, reads the tooltips they are drawn under, and reports the store.
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


def test_the_sub_bars_chevron_hides_a_panel_open_on_any_tab():
    """The bug, on the door it was found on. Both tabs, because passing `resources` looked correct
    for as long as Resources was the only tab anybody tested with."""
    for tab in ("resources", "activity"):
        pressed = _press(tab)["subnav"]

        assert pressed["caption"] == "Hide the side panel"
        assert pressed["dockTab"] is None, f"open on {tab}: the chevron switched instead of hiding"


def test_the_shortcut_the_help_drawer_calls_a_toggle_is_one():
    """Same root cause on the keyboard, and worse, because `dockTab` is remembered: somebody whose
    last session ended on Activity gets a two-press close on the first ⌘/ after a page load."""
    for tab in ("resources", "activity"):
        assert _press(tab)["shortcut"]["dockTab"] is None, f"open on {tab}: ⌘/ switched tab"


def test_the_docks_own_fold_button_still_hides_it():
    """The control. This is the sibling #150 already fixed, and moving all three onto one writer
    must not have moved it."""
    for tab in ("resources", "activity"):
        pressed = _press(tab)["fold"]

        assert pressed["caption"] == "Hide panel"
        assert pressed["dockTab"] is None


def test_hiding_the_panel_drops_the_filter_and_is_remembered():
    """Two things the old wiring lost along with the close, on the two doors that did not close.
    A filter is a question about a list nobody is looking at once the panel shuts, and it would come
    back on the next open as a filter nobody could see they had applied. And the close is recorded,
    or a panel somebody shut is open again on the next load."""
    for door in ("subnav", "fold", "shortcut"):
        pressed = _press("activity")[door]

        assert pressed["panelFilter"] is None, door
        assert pressed["wrote"] == [{"me": {"dockTab": None}}], door


def test_every_door_still_opens_a_closed_panel_on_resources():
    """The other half of a toggle, and the reason `toggleDock(dockTab)` alone was not the fix: from
    closed, `dockTab` is null, and toggling null against null leaves the panel shut."""
    pressed = _press(None)

    assert pressed["subnav"]["dockTab"] == "resources"
    assert "Show resources" in pressed["subnav"]["caption"]
    assert pressed["shortcut"]["dockTab"] == "resources"
    # The fold button lives inside the open panel, so a closed one draws no such control at all.
    assert pressed["fold"] == {"absent": True}
