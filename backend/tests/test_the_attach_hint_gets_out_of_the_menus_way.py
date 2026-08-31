"""The attach button's hint must not cover the menu the click just opened.

The "+" in the composer bar carries a Tooltip and the attach Dropdown on the same button. Ant
closes a Tooltip on mouseleave, and clicking the button produces no mouseleave — the pointer is
still sitting on it — so an uncontrolled Tooltip stays up over the menu it was pointing at.

The Send button in the same file already carries the controlled-`open` form of this fix, for the
same reason. This is the other button with the same shape, and nothing read it.
"""

import json
import subprocess
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent / "js" / "composer_attach_tooltip_harness.mjs"


def _drawn() -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input="",
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_hovering_the_attach_button_still_shows_the_hint():
    """Half the ask. A fix that closed the tooltip for good would pass the other half and take
    away the only place the button says what it does."""
    drawn = _drawn()
    assert drawn["resting"] is False  # nothing hovered, nothing shown
    assert drawn["hovered"] is True


def test_opening_the_attach_menu_closes_the_hint():
    """The incident. The tooltip and the menu render at the same corner of the same button, so a
    hint left open is a hint drawn on top of the list of things to attach — and the person cannot
    move the pointer off to dismiss it without leaving the menu."""
    drawn = _drawn()
    assert drawn["menuOpenDropdown"] is True  # the menu really is up
    assert drawn["menuOpen"] is False


def test_closing_the_menu_does_not_flash_the_hint_back_up():
    """Dismissing the menu — Escape, or a second click on the button — leaves the pointer where it
    was, so no mouseleave ever arrives to put the hint away. Masking the tooltip without disarming
    the hover just moves the stray hint from during the menu to right after it."""
    assert _drawn()["afterClose"] is False


def test_the_hint_is_suppressed_by_a_menu_not_killed_by_one():
    """The pointer leaves and comes back. A fix that stopped opening the tooltip at all would pass
    every test above and take away the only place the button says what it does."""
    assert _drawn()["rehovered"] is True
