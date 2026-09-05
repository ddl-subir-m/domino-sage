"""What the working set's Plans group does with a plan somebody put away (#167).

Archiving is reversible, so hiding an archived plan with nothing on screen saying so would fail the
empty-state rule: the person who put it away and now wants it back has no answer to "where did it
go". The group head is that answer — it counts what is hidden and offers the way in.

One interaction had to be got right. The panel marks a row `live` when it matches `activePlanId`,
which comes from `_thread_plan_id`, and that read deliberately still sees archived documents so the
Conversation that produced one goes on showing its plan card. So a document really can be archived
and live at once. Archived wins: a hidden-but-highlighted row is the worst of both.

`test_a_resource_group_offers_a_way_in_whether_or_not_it_is_empty` is the prior art for the harness.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "plans_group_archive_harness.mjs"
_CSS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "css" / "shell.css"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)


def _act(act: str) -> dict:
    """The panel drawn over two plans, the newer of which is archived and is also the
    Conversation's own current plan."""
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"act": act}),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_an_archived_plan_is_not_listed_in_the_plans_group():
    drawn = _act("drawn")

    assert [row["name"] for row in drawn["rows"]] == ["A consumption dashboard."]
    # And the head's count names what is on screen rather than what exists.
    assert drawn["head"]["label"] == "Plans (1)"


def test_the_group_head_counts_what_it_hid_and_offers_the_way_back_to_it():
    """The count is on the label rather than in a tooltip: it is the answer to "where did my plan
    go", and a number you have to hover to read is no answer."""
    head = _act("drawn")["head"]

    assert head["archivedLabel"] == "Show archived (1)"
    # A real button, not a clickable span, or it is unreachable by keyboard — the same rule the
    # group's add door is held to.
    assert head["archivedIsButton"] is True
    assert head["archivedPressed"] is False


def test_an_archived_plan_is_never_drawn_as_the_live_one():
    """The archived document IS the Conversation's current plan here, which is the whole hazard.
    Hidden, it cannot be highlighted; revealed, it still must not be, because the highlight means
    "this is what is being built from" and an archived plan is not."""
    drawn = _act("drawn")
    assert [row["live"] for row in drawn["rows"]] == [False]

    pressed = _act("press")
    revealed = next(r for r in pressed["rows"] if r["name"] == "A desk exposure dashboard.")
    assert revealed["live"] is False


def test_pressing_the_toggle_shows_the_archived_plan_and_says_it_is_archived():
    """A revealed row that reads exactly like a live one would make the toggle pointless: the
    person pressed it to find out which plan was put away."""
    pressed = _act("press")

    assert [row["name"] for row in pressed["rowsBefore"]] == ["A consumption dashboard."]
    assert [row["name"] for row in pressed["rows"]] == [
        "A desk exposure dashboard.", "A consumption dashboard."]
    revealed = pressed["rows"][0]
    assert revealed["subtitle"].startswith("Archived")
    # The review outcome is still on it, beside the word: archiving is a flag, not a status.
    assert "Approved" in revealed["subtitle"]


def test_the_toggle_says_it_is_showing_them_once_it_is():
    """A control that reports itself pressed while its own words still say "Show" is a control that
    cannot be pressed back."""
    head = _act("press")["head"]

    assert head["archivedLabel"] == "Hide archived (1)"
    assert head["archivedPressed"] is True
    # And the count is off the whole list, so it does not fall to zero the moment they are shown.
    assert head["label"] == "Plans (2)"


def test_showing_them_into_a_folded_group_unfolds_it():
    """The rows sit under the group's own caret. Pressing the way in on a folded group flipped the
    label to "Hide archived (1)" and bumped the count while nothing appeared — a control reporting
    itself pressed with nothing on screen to show for it."""
    pressed = _act("press-while-collapsed")

    assert pressed["head"]["collapsed"] is False
    assert [row["name"] for row in pressed["rows"]] == [
        "A desk exposure dashboard.", "A consumption dashboard."]


def test_hiding_them_again_leaves_the_group_open():
    """The un-collapse belongs to the way in alone. Folding the group again on the way out would
    take the live plans with it, and nobody asked to hide those."""
    back = _act("press-and-back")

    assert back["head"]["archivedLabel"] == "Show archived (1)"
    assert back["head"]["collapsed"] is False
    assert [row["name"] for row in back["rows"]] == ["A consumption dashboard."]


def test_the_toggles_focus_is_visible():
    """Keyboard reach is worth nothing if the focused control is indistinguishable from the rest —
    the same rule the group's add door is held to."""
    assert ".sw-res-group-archived:focus-visible" in _CSS.read_text()
