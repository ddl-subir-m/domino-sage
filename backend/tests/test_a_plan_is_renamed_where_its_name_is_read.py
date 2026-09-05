"""Renaming a plan from the row that draws its name.

The rename itself has existed since the plan got a name somebody chose: the plan page draws a
pencil beside the heading, and `PATCH /api/plans/{id}` with a title alone patches metadata rather
than writing a version. What did not exist was the reach. A bad name is noticed in the rail's Plans
group, and that row was drawn menu-less — the one list row in the Workbench you could not rename
from the list, while a Conversation and a Built App both could.

So the row carries its own overflow now. Only Rename: archiving stays on the plan page for the
reason written beside that button, because it hides a document that may hold other people's
comments and approvals and the walk that shows you the document first is the point of it. A rename
writes no version and costs a reviewer nothing.

`plans_group_archive_harness` is the prior art and the harness this reuses — the panel drawn with
`createElement` stubbed to plain objects, so calling the component returns tree data. What the row
hands its Dropdown cannot be grepped out of the source: the menu is a value reaching a component,
and only drawing the row shows it arrived.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "plans_group_archive_harness.mjs"
_PANEL = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js" / "components"
          / "resource-panel.js")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)


def _act(act: str) -> dict:
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


# ---- the row offers it ---------------------------------------------------------------------

@_needs_node
def test_every_plan_row_offers_the_rename():
    """Including the one with no name of its own — that row is the likeliest to want renaming, so a
    menu built off the title would withhold it from exactly the plan that needs it."""
    menus = _act("menu")["menus"]

    assert [m["name"] for m in menus] == ["A consumption dashboard.", "Untitled plan"]
    assert [m["items"] for m in menus] == [["rename"], ["rename"]]


@_needs_node
def test_the_row_offers_the_rename_and_nothing_else():
    """Archive is deliberately not here. It is a judgement call about a document that may carry
    other people's comments and approvals, and the plan page is where it stays until tidying stale
    plans is shown to hurt — the condition that button's own comment names."""
    for menu in _act("menu")["menus"]:
        assert menu["items"] == ["rename"]


@_needs_node
def test_the_rename_box_opens_on_the_name_the_document_has():
    """`Untitled plan` is the label this list falls back to drawing, not a name anybody gave the
    document. Prefilling it would put the rail's own placeholder in the box as though somebody had
    chosen it, and the first press of Rename would make it the plan's real name."""
    opened = _act("menu")["opened"]

    assert opened["title"] == "Rename plan"
    assert opened["okText"] == "Rename"
    assert opened["defaultValue"] == ""


# ---- and it patches the title alone --------------------------------------------------------

def test_the_row_patches_the_title_alone():
    """A body in the same call would make the rename a new draft, and a version is what reviewers
    commented on. Same rule the plan page's pencil follows."""
    panel = _PANEL.read_text()

    assert "SW.api.patchPlan(row.id, { title: next })" in panel


def test_the_row_tells_the_pin_the_document_moved():
    """The panel's pin names this document too and is read once per load rather than polled, so a
    rename that only redrew the row would leave the pin saying the old name until a reload."""
    assert "SW.store.reloadProjectPlan()" in _PANEL.read_text()


def test_an_empty_name_is_refused_rather_than_written():
    """Matching the plan page. The rename has to keep the box open on the empty name — closing on a
    warning would leave the plan called what it was called, with a dialog saying otherwise."""
    panel = _PANEL.read_text()

    assert "Give the plan a name." in panel
    assert "throw new Error('no name')" in panel
