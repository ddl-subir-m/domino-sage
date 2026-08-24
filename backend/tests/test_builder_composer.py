"""The builder composer's @mention menu and attachment pills, pinned at the source.

There is no browser in this suite, so these are source assertions in the style of test_fonts.py: each
one names a specific way the composer misbehaved live on 2026-08-24, and the shape of the fix that
stops it. A behavioural test would need a DOM; what these buy is that the three mistakes cannot come
back by accident, since every one of them is a single line reverting to what it used to say.
"""
from __future__ import annotations

import re
from pathlib import Path

UI = (Path(__file__).resolve().parents[1] / "sage" / "ui" / "index.html").read_text()


def _body(name: str) -> str:
    """The source of one top-level function, up to the next one."""
    start = UI.index(f"function {name}(")
    end = UI.index("\nfunction ", start + 1)
    return UI[start:end]


def test_moving_the_mention_highlight_does_not_rebuild_the_rows():
    # The arrow keys read as dead. The menu sits over the composer, so the pointer is usually resting
    # on it, and renderMentionMenu() destroyed and re-created every row on each move — a fresh row
    # inserted under a still pointer fires mouseenter, which handed `active` straight back to the
    # hovered row. Moving the highlight must only move the class.
    move = _body("moveMention")
    assert "paintMentionActive()" in move
    assert "renderMentionMenu()" not in move


def test_the_keyboard_highlight_is_not_styled_like_the_pointer_highlight():
    # `.mm-item.active, .mm-item:hover` shared one declaration, so even a move that DID land looked
    # identical to a hover. The two must be separate rules.
    assert ".mm-item.active, .mm-item:hover" not in UI
    assert re.search(r"\.mm-item\.active\s*\{", UI)


def test_a_deletion_never_opens_a_closed_mention_menu():
    # Backspacing over a finished mention re-opened the picker on every keystroke: the caret lands
    # just after "@BigQuery_Dem", which still matches the token regex. The user was deleting and got
    # a menu — and the open menu then took the next Enter for row selection instead of sending.
    update = _body("updateMentionMenu")
    assert "inputType" in update and "startsWith('delete')" in update
    assert "if (deleting && !mentionState) return;" in update
    # A deletion may still NARROW a menu that is already open, so the guard is conditional on state.
    assert "updateMentionMenu(event)" in UI


def test_bound_resources_get_a_pill_like_attached_files_do():
    # @ offered Data Sources, Model APIs and Aliases (mentionCandidates reads both lists) but the
    # pill row only ever walked attachedFiles — so an app with two Data Sources bound showed an empty
    # row and looked like it had no context attached at all.
    pills = _body("renderAttachPills")
    assert "bindingsCache" in pills
    assert "pill resource" in pills
    # Both pills carry a "×". A Resource pill used to have none, on the reasoning that unbinding
    # belongs in the rail where the cost can be stated first — but two pills that look the same and
    # take different gestures is the worse failure, and the file × already answers that risk the
    # other way round, by removing and then reporting what the app still needs.
    assert pills.count("class=\"px\"") == 2
    assert "unbindResource(b.kind, b.id)" in pills
    # Bind and unbind have to move the composer row too, not just the rail.
    assert "renderAttachPills()" in _body("renderBindings")


def test_removing_a_resource_says_what_the_app_still_needs():
    # The pill × and the rail's Remove are one call, so the warning lives there and both get it.
    # Without it, unbinding an Alias leaves every button that called it in place and failing, and
    # nothing on screen connects the dead button to the removal that caused it.
    unbind = _body("unbindResource")
    assert "warnResourceStillUsed" in unbind
    warn = _body("warnResourceStillUsed")
    assert "body.refs" in warn
    assert "Remove from app" in warn
    # Offered, never automatic: stripping a model call out of a screen is a real edit to the app's
    # logic, and the creator may be about to bind a different Resource to the same code.
    assert "removeResourceFromApp" in warn
    # A Data Source is not named in app code — its queries are — so it cannot share the sentence.
    assert "data_source" in warn
