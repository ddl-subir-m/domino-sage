"""The Project resources dock can be dragged wider, and a click on the handle is not a resize.

Table names in the Data Source tree sat beside a twenty-four-character act in a pane whose width
was a stylesheet token. Widening the pane is the other half of shortening that act: the names are
long on purpose, and a width nobody can change will always lose them.

Source assertions, in the style of `test_the_side_panel_toggle_hides_the_panel.py`: there is no
browser in this suite, and a handle that is not on the open dock — or a click that writes a width
without moving — is exactly what would come back by accident.
"""
from __future__ import annotations

from pathlib import Path

_WB = Path(__file__).resolve().parents[1] / "sage" / "workbench"
_JS = _WB / "js"
SHELL = (_JS / "components" / "shell.js").read_text()
STORE = (_JS / "store.js").read_text()
PREFS = (_JS / "prefs.js").read_text()
CSS = (_WB / "css" / "shell.css").read_text()

DOCK = SHELL[SHELL.index("function Dock("): SHELL.index("SW.Shell")]


def _method(source: str, name: str) -> str:
    start = source.index(f"\n    {name}(")
    return source[start:source.index("\n    },", start)]


def test_the_open_dock_has_a_resize_handle():
    """The collapsed strip is 44px and has nothing to size. The handle lives on the open pane,
    on its left edge, because that is the boundary with the transcript."""
    assert "sw-dock-resize" in DOCK
    assert "Resize project resources" in DOCK
    assert "is-collapsed" in DOCK
    collapsed = DOCK.split("if (!dockTab)")[1].split("const stored")[0]
    assert "sw-dock-resize" not in collapsed


def test_a_click_that_does_not_move_the_edge_does_not_write_a_width():
    """null means the stylesheet still owns the width, including the laptop media queries. A
    pointerdown/pointerup on the handle with no movement used to be indistinguishable from a
    drag, and would pin the current pixel width forever — so a laptop that later grew would
    keep a dock sized for the small screen."""
    assert "if (moved) SW.store.setDockWidth(next)" in DOCK


def test_the_store_writes_only_a_clamped_width():
    """The preference refuses numbers outside 240–800. The writer has to clamp to that same
    range, or a drag that overshot would be a choice the next load silently dropped."""
    body = _method(STORE, "setDockWidth")
    assert "SW.prefs.set('dockWidth'" in body
    assert "SW.prefs.range('dockWidth')" in body
    assert "SW.prefs.set('dockWidth'" not in _method(STORE, "openDock")
    assert "SW.prefs.set('dockWidth'" not in _method(STORE, "toggleDockOpen")


def test_the_remembered_width_is_seeded_with_the_open_state():
    """Both answers are keyed by viewer, so both have to be read before the Shell paints or the
    dock would open at the stylesheet width and then jump."""
    init = STORE[STORE.index("state.railHidden = SW.prefs.get('railHidden')"):]
    init = init[: init.index("if (brand)")]
    assert "state.dockWidth = SW.prefs.get('dockWidth')" in init
    assert "dockWidth: { fallback: null, values: [null], min: 240, max: 800 }" in PREFS


def test_the_handle_is_the_column_resize_cursor_and_does_not_animate_while_dragged():
    """The dock already transitions width for collapse. That transition fighting a drag is a
    pane that lags the pointer and then eases into place after it."""
    assert "cursor: col-resize" in CSS
    assert ".sw-dock.is-resizing { transition: none; user-select: none; }" in CSS
    assert ".sw-dock-resize" in CSS
