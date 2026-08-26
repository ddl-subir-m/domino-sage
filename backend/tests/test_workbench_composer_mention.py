"""The Chat composer's @mention menu, pinned at the source.

There is no browser in this suite, so these are source assertions in the style of test_fonts.py:
each one names a way the mention menu misbehaved live, and the shape of the fix that stops it. A
behavioural test would need a DOM; what these buy is that the mistakes cannot come back by accident.

The first two moved here from test_builder_composer.py when the single-file builder UI became the
Workbench: the bugs were found there, and the properties that stop them are properties of whatever
composer is on screen.
"""
from pathlib import Path

WB = Path(__file__).resolve().parents[1] / "sage" / "workbench"
UI = (WB / "js" / "components" / "composer.js").read_text()
CSS = (WB / "css" / "chat.css").read_text()


def test_picking_a_mention_inserts_at_name_instead_of_stripping_it():
    assert "function mentionToken(" in UI
    assert "setText(text.slice(0, mention.start) + token + pad + after)" in UI
    assert "The mention resolves into a chip rather than into text" not in UI


def test_mention_menu_includes_thread_artifacts_as_this_thread():
    assert "thread.artifacts" in UI
    assert "In this thread" in UI


def test_moving_the_mention_highlight_does_not_rebuild_the_rows():
    # The arrow keys read as dead. The menu sits over the composer, so the pointer is usually resting
    # on it, and the old builder rebuilt every row on each move — a fresh row inserted under a still
    # pointer fires mouseenter, which handed the highlight straight back to the hovered row. Here the
    # rows are keyed, so a move changes one class and React reuses the DOM the pointer is over.
    assert "key: resource.id," in UI
    assert "className: `sw-mention-item${index === cursor ? ' is-active' : ''}`," in UI
    for arrow in ("ArrowDown", "ArrowUp"):
        assert f"e.key === '{arrow}'" in UI
    # The keys move the cursor and nothing else — no refetch, no re-open, no rebuild.
    assert "setCursor((c) => (c + 1) % suggestions.length);" in UI
    assert "setCursor((c) => (c - 1 + suggestions.length) % suggestions.length);" in UI


def test_nothing_competes_with_the_keyboard_highlight():
    # `.mm-item.active, .mm-item:hover` shared one declaration in the builder, so even a move that
    # DID land looked identical to a hover and read as nothing happening. There is one highlight
    # here, and the pointer drives it through the same cursor the keys do (onMouseEnter -> setCursor)
    # rather than through a second style that can disagree with it.
    assert ".sw-mention-item.is-active" in CSS
    assert ".sw-mention-item:hover" not in CSS
    assert "onMouseEnter: () => setCursor(index)," in UI
