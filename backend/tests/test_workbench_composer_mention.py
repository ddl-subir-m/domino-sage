"""Chat composer @mention keeps the token in the box (source pin)."""
from pathlib import Path

UI = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js" / "components" / "composer.js").read_text()


def test_picking_a_mention_inserts_at_name_instead_of_stripping_it():
    assert "function mentionToken(" in UI
    assert "setText(text.slice(0, mention.start) + token + pad + after)" in UI
    assert "The mention resolves into a chip rather than into text" not in UI


def test_mention_menu_includes_thread_artifacts_as_this_thread():
    assert "thread.artifacts" in UI
    assert "In this thread" in UI
