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
    """The bug. `Language models (1)` had no door: the link was in the empty branch.

    The empty group is no longer part of this claim, because there is no empty group — a group
    with nothing in it is not drawn (ADR-0035). What that costs is the "and the empty one keeps
    its door" half; what it buys is that the surviving claim is asked of every group on screen,
    which is the case #164 was actually reported from."""
    heads = _heads()
    assert heads["Language models (1)"]["hasAdd"]
    assert heads["Data (2)"]["hasAdd"]
    assert heads["Predictive models (1)"]["hasAdd"]
    # And the branch's own link went with the branch, so the door is not drawn twice.
    assert _act("drawn")["emptyLinks"] == []


def test_a_group_with_no_catalog_behind_it_offers_nothing():
    """Agents, Skills and MCPs are placeholders until OpenCode config wires them, and Files come
    from Upload rather than from the catalog at all.

    A door onto a catalog that cannot answer is worse than no door: it is a dead end with a
    label on it.

    Asked of a group that HOLDS something, which it could not be before: an empty placeholder group
    is now simply absent (ADR-0035), and "no door" would pass on a group that was not drawn. The
    fixture gives Agents a row so the group exists and the missing door is the placeholder rule.
    """
    heads = _heads()
    assert "Agents (1)" in heads, "the placeholder group was not drawn, so nothing was tested"
    assert not heads["Agents (1)"]["hasAdd"]
    # Files is the other kind of doorless group, and a new one: `openCatalog('file')` has nothing
    # to open, because a file arrives by Upload.
    assert not heads["Files (1)"]["hasAdd"]
    # And a group nobody has put anything in is not on screen to be asked.
    assert "Skills (0)" not in heads and "MCPs (0)" not in heads


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
    predictive = _act("press-predictive")
    assert predictive["catalogOpen"] and predictive["catalogKind"] == "model_predictive"


def test_a_group_holding_two_kinds_opens_on_that_group():
    """`Data` covers Datasets and Data Sources, so its door asks the catalog for both.

    It opened on Everything while the catalog took one kind or none, which reached both kinds and
    a fistful of models with them — a door labelled Data landing on a list of everything. Picking
    the first subgroup is not the answer either: that silently means Datasets and hides Data
    Sources behind a filter the caller never chose.
    """
    assert _act("press-data")["catalogKind"] == "data"


def test_pressing_the_door_does_not_collapse_the_group():
    """The door would otherwise shut behind you: the head's click used to be the collapse."""
    for act in ("press-filled", "press-predictive", "press-data"):
        assert _act(act)["collapseUnchanged"], act


def test_the_doors_focus_is_visible():
    """Keyboard reach is worth nothing if the focused control is indistinguishable from the rest."""
    css = _CSS.read_text()
    assert ".sw-res-group-add:focus-visible" in css
    assert ".sw-res-group-toggle:focus-visible" in css
