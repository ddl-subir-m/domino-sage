"""A Resource group's add door does not depend on the group being empty (#164).

The working set had two ways to add a Resource and neither answered "add another one of these".
The head's dropdown is kind-blind: it names acts. The per-group "Add from Domino" link sat inside
the `count === 0` branch, so the first Language model somebody added took the door away with it —
the affordance was there for as long as it was not needed, and gone the moment it was.

The fix draws the door on the group head, always. The head then holds two controls, so the head
itself can no longer be one: a `+` nested in a `role="button"` row is invalid markup, and its click
would bubble and collapse the group it just opened a catalog for.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "resource_group_add_harness.mjs"
_CSS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "css" / "shell.css"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)


def _act(act: str) -> dict:
    """The panel drawn with rows in some groups and none in others."""
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


def _heads() -> dict:
    return {head["label"]: head for head in _act("drawn")["heads"]}


def test_a_group_with_rows_in_it_still_offers_a_way_in():
    """The bug. `Language models (1)` had no door: the link was in the empty branch."""
    heads = _heads()
    assert heads["Language models (1)"]["hasAdd"]
    assert heads["Data (2)"]["hasAdd"]
    # The empty group keeps its door too, so this is a door added rather than one moved.
    assert heads["Predictive models (0)"]["hasAdd"]
    assert _act("drawn")["emptyLinks"] == ["Add from Domino"]


def test_a_group_with_no_catalog_behind_it_offers_nothing():
    """Agents, Skills and MCPs are placeholders until OpenCode config wires them.

    A door onto a catalog that cannot answer is worse than no door: it is a dead end with a
    label on it, and the empty-state link already declines to draw one for the same reason.
    """
    heads = _heads()
    for group in ("Agents (0)", "Skills (0)", "MCPs (0)"):
        assert not heads[group]["hasAdd"], group


def test_the_door_is_a_real_button_that_says_what_it_does():
    """Icon-only, so the label lives in a tooltip and an aria-label rather than on screen.

    A `button` rather than a clickable div is what makes it reachable by keyboard at all, and
    the head is no longer a control itself — it is a row holding two.
    """
    head = _heads()["Language models (1)"]
    assert head["addIsButton"]
    assert head["toggleIsButton"]
    assert not head["rowIsButton"]
    assert head["addLabel"] == "Add language models from Domino"
    assert head["addTooltip"] == head["addLabel"]


def test_pressing_the_door_opens_the_catalog_on_that_kind():
    """The point of a per-group door: it arrives pre-filtered, where the head's dropdown cannot."""
    filled = _act("press-filled")
    assert filled["catalogOpen"] and filled["catalogKind"] == "model_llm"
    empty = _act("press-empty")
    assert empty["catalogOpen"] and empty["catalogKind"] == "model_predictive"


def test_a_group_holding_two_kinds_opens_on_everything():
    """`Data` covers Datasets and Data Sources, and the catalog takes one kind or none.

    Picking the first subgroup would silently mean Datasets and hide Data Sources behind a
    filter the caller never chose. `null` is Everything, which reaches both.
    """
    assert _act("press-data")["catalogKind"] is None


def test_pressing_the_door_does_not_collapse_the_group():
    """The door would otherwise shut behind you: the head's click used to be the collapse."""
    for act in ("press-filled", "press-empty", "press-data"):
        assert _act(act)["collapseUnchanged"], act


def test_the_doors_focus_is_visible():
    """Keyboard reach is worth nothing if the focused control is indistinguishable from the rest."""
    css = _CSS.read_text()
    assert ".sw-res-group-add:focus-visible" in css
    assert ".sw-res-group-toggle:focus-visible" in css
