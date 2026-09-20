"""A pinned leaf starts every new conversation, and the row it was pinned on stays put (#468).

WHAT WAS BROKEN. Pinning was already durable, already project-scoped, already explicitly toggled
and already reversible — and it bought a sort of the `@` menu and nothing else. So a person who had
made that statement about `DWH.MARTS.MIXPANEL__EVENT` still added it to every conversation by hand,
three in a row in one measured workspace, and read Pin as a broken button sitting beside the one
that works. There was no broken control in that row. There was a missing one.

WHAT THIS FILE HOLDS. Two halves, and they are tested in two places because they are two kinds of
claim. The Conversation half is server-side and asked of the orchestrator: what a brand-new Thread's
`context.json` holds, and — the condition a naive fix gets wrong — what it holds after somebody
closes a seeded chip. The tree half is a rendering and is asked of `js/pin_mark_harness.mjs`, which
draws the leaves: where the mark lands, what it says to a hover and to a screen reader, that it is
not a button, and that the order of the rows is the same with the pin and without it.

WHY THE ORDER IS ASSERTED AT ALL. Nothing else in the suite is positioned to notice a tree that
starts sorting itself. The `@` menu is where a pin reorders things, deliberately and in one place
(`groupsFromMembership`); this tree's job is to show what a Data Source CONTAINS, and a row that
jumps when it is clicked breaks the structure the person just drilled through to reach it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

_HARNESS = Path(__file__).resolve().parent / "js" / "pin_mark_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

SOURCE = "data_source:ds-dwh"
TABLE = "MIXPANEL__EVENT"
PIN = {"database": "DWH", "schema": "MARTS", "table": TABLE}
MARK = "Pinned — starts every new conversation"


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="p", implement="i", ask="a")


def _orch(tmp: Path) -> Orchestrator:
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    orch = Orchestrator(workspace_dir=tmp / "mnt" / "code", template=template, gateway=object(),
                        catalog=_catalog(), project_id="Sage")
    orch.project(start_preview=False)
    return orch


def _with_the_source(orch: Orchestrator, pin: dict | None = None) -> None:
    """The Data Source in the working set, pinned on the way in when a pin is named.

    `add_project_resource` takes the pin in the same call the way the tree's own `Pin` does through
    `addToProject`, so a fixture that pinned separately would be taking a route the product does not.
    """
    row = {"id": SOURCE, "kind": "datasource", "name": "Snowflake-Data-Warehouse"}
    if pin is not None:
        row["pin"] = pin
    orch.add_project_resource(row)


def _chips(orch: Orchestrator, thread_id: str) -> list[dict]:
    """This Thread's chips, read back through the door the Conversation itself opens on."""
    return orch.get_thread(thread_id)["context"]["items"]


# ---- the Conversation half ---------------------------------------------------------------------


def test_a_pinned_table_is_already_in_a_conversation_that_has_just_opened(tmp_path: Path):
    """Condition 1. The chip is a TABLE chip, not a bare Data Source one: `scope` names the table
    and `resourceId` is the leaf's own id, which is what makes it the chip `Use here` would have
    written and not a second, thinner kind that happens to have the right name on it."""
    orch = _orch(tmp_path)
    _with_the_source(orch, PIN)

    items = _chips(orch, orch.create_thread()["id"])

    assert len(items) == 1
    chip = items[0]
    assert chip["name"] == TABLE
    assert chip["kind"] == "data_source"
    assert chip["scope"] == {"database": "DWH", "schema": "MARTS", "table": TABLE}
    assert chip["resourceId"] == f"table:ds-dwh:DWH.MARTS.{TABLE}"
    assert chip["parentId"] == SOURCE


def test_a_pinned_dataset_file_is_seeded_the_same_way(tmp_path: Path):
    """The other leaf a membership row can pin. Same act, and the Dataset half has its own shape —
    `datasetId` and `datasetRelPath` are what a file chip is READ by everywhere downstream, and a
    chip carrying a name and neither of them is one the turn cannot open."""
    orch = _orch(tmp_path)
    orch.add_project_resource({"id": "dataset:ds_sales", "kind": "dataset", "name": "sales_2026",
                               "pin": {"path": "train.csv"}})

    items = _chips(orch, orch.create_thread()["id"])

    assert len(items) == 1
    assert items[0]["kind"] == "file"
    assert items[0]["name"] == "train.csv"
    assert items[0]["datasetId"] == "ds_sales"
    assert items[0]["datasetRelPath"] == "train.csv"


def test_seeding_never_takes_the_attachment_path(tmp_path: Path, monkeypatch):
    """The one thing #468 forbids outright. A Dataset file reaches a Built App through `attach_file`
    — bytes copied into the app, a manifest entry that rehydrates them on publish, and the
    sensitivity lock armed (ADR-0048) — and that is a different act from putting a chip on a
    conversation. Seeding takes the Chat route because it sets no `inBuild`, which is the only thing
    `add_thread_context` forks on.

    The call is RECORDED rather than made to raise. Seeding swallows what a pin's own chip costs, so
    a refusal planted in `attach_file` comes back as a missing chip and the failure names the wrong
    thing — and a check on the chip that was written cannot tell the two routes apart either, since
    the attaching branch catches its own failures and writes the same row. A recorder survives both.

    `create_thread` is the door BOTH rails mint a Thread through, and the server cannot tell them
    apart there — Build has no handoff behind a Thread it starts (#74), which is why the browser
    sends `inBuild` on its own POST. So a Build conversation started from scratch carries these chips
    too. What it does not carry, and what this pins, is an Attachment.
    """
    orch = _orch(tmp_path)
    orch.add_project_resource({"id": "dataset:ds_sales", "kind": "dataset", "name": "sales_2026",
                               "pin": {"path": "train.csv"}})

    reached: list[tuple] = []
    monkeypatch.setattr(Orchestrator, "attach_file",
                        lambda self, *a, **k: reached.append(a) or {"path": "public/data/x"})
    items = _chips(orch, orch.create_thread()["id"])

    assert reached == []
    assert len(items) == 1
    assert "attachedApp" not in items[0]


def test_a_seeded_chip_that_was_closed_stays_closed(tmp_path: Path):
    """Condition 2, and the one a fix that seeds on READ gets wrong.

    `context.json` is the record of what this Conversation was decided to hold, and closing a chip
    is a decision. Reopening is asserted through `get_thread`, which is the door the browser comes
    back through — a check that read the file would pass against a `get_thread` that re-seeded on
    its way out, which is exactly the shape being ruled out.
    """
    orch = _orch(tmp_path)
    _with_the_source(orch, PIN)
    tid = orch.create_thread()["id"]

    assert orch.remove_thread_context(tid, _chips(orch, tid)[0]["id"]) is not None

    assert _chips(orch, tid) == []
    assert _chips(orch, tid) == []   # and a second open is not a second chance to put it back


def test_a_project_with_no_pins_opens_a_conversation_with_no_chips(tmp_path: Path):
    """Condition 3. The Data Source is a member and nothing about it is pinned, which is the state
    every project starts in — so this is also the claim that nothing was made to arrive by merely
    being in the working set."""
    orch = _orch(tmp_path)
    _with_the_source(orch)

    assert _chips(orch, orch.create_thread()["id"]) == []


def test_unpinning_stops_the_seeding(tmp_path: Path):
    """Condition 4. Pin is reversible, and the reversal has to reach the thing pinning now does."""
    orch = _orch(tmp_path)
    _with_the_source(orch, PIN)
    assert len(_chips(orch, orch.create_thread()["id"])) == 1

    assert orch.unpin_project_resource(SOURCE, PIN) is True

    assert _chips(orch, orch.create_thread()["id"]) == []


def test_pinning_does_not_reach_into_a_conversation_that_is_already_open(tmp_path: Path):
    """Condition 5, and the promise the tooltip makes out loud: *Your current conversation doesn't
    change.* A pin is a statement about the conversations to come. Reaching backwards into the one
    on screen would be the second control in that row attaching to the open Conversation, which is
    the ambiguity `Use here` already owns and the reason Pin is worth having separately."""
    orch = _orch(tmp_path)
    _with_the_source(orch)
    open_thread = orch.create_thread()["id"]
    assert _chips(orch, open_thread) == []

    orch.pin_project_resource(SOURCE, PIN)

    assert _chips(orch, open_thread) == []
    assert len(_chips(orch, orch.create_thread()["id"])) == 1


def test_every_pin_on_every_parent_is_seeded(tmp_path: Path):
    """Two parents, two leaves, one Conversation. A loop that stopped at the first membership row
    holding pins, or at the first pin on a row, would pass every test above it: each of those names
    one leaf, and one is exactly what a short loop delivers."""
    orch = _orch(tmp_path)
    _with_the_source(orch, PIN)
    orch.pin_project_resource(SOURCE, {"database": "DWH", "schema": "MARTS", "table": "DIM_ACCOUNT"})
    orch.add_project_resource({"id": "dataset:ds_sales", "kind": "dataset", "name": "sales_2026",
                               "pin": {"path": "train.csv"}})

    names = [c["name"] for c in _chips(orch, orch.create_thread()["id"])]

    assert sorted(names) == ["DIM_ACCOUNT", TABLE, "train.csv"]


def test_a_pin_that_cannot_be_seeded_still_opens_the_conversation(tmp_path: Path, monkeypatch):
    """The Conversation outranks the chip. A store pinned last week that answers nothing today, or
    any other failure on the way to the chip, must cost that chip and not the ability to start
    talking — which is the one thing the person was trying to do."""
    orch = _orch(tmp_path)
    _with_the_source(orch, PIN)

    def refuse(self, thread_id, item):
        raise RuntimeError("Snowflake answered 403.")

    monkeypatch.setattr(Orchestrator, "add_thread_context", refuse)
    tid = orch.create_thread()["id"]

    assert orch.get_thread(tid)["id"] == tid


# ---- the tree half -------------------------------------------------------------------------


def _leaves(steps: list[dict]) -> list[dict]:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(steps), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_a_pinned_leaf_wears_the_mark_and_its_neighbours_do_not(tmp_path: Path):
    """Condition 6. The pin is on the MIDDLE row of three, so a mark drawn on every leaf and a mark
    drawn on the first one both fail here."""
    bare, pinned = _leaves([{"pins": []}, {"pins": [dict(PIN, name=TABLE)]}])

    assert [leaf["mark"] for leaf in bare["leaves"]] == [None, None, None]
    marked = [leaf["name"] for leaf in pinned["leaves"] if leaf["mark"]]
    assert marked == [TABLE]


@needs_node
def test_the_mark_says_the_same_thing_to_a_hover_and_to_a_screen_reader(tmp_path: Path):
    """One string reaching both, and it names the EFFECT rather than the state: what nobody could
    see was not that a row was pinned, it was what pinning did.

    `role` and `iconHidden` are what make the label REACH anybody, and the sentence was written
    before either was true. An `aria-label` on a bare `span` sits on a generic element and is not
    exposed; the antd icon inside carries `role="img" aria-label="pushpin"` of its own, and that is
    what a screen reader would have read instead. So hover said why the row was marked and audio
    said "pushpin" — two grains of the same mark disagreeing, which is what this assertion is for.

    `clickable` is the last half: the mark is a status and the row already holds the control that
    changes it, so a mark that could be pressed would be a second door onto one act."""
    (pinned,) = _leaves([{"pins": [dict(PIN, name=TABLE)]}])
    mark = next(leaf["mark"] for leaf in pinned["leaves"] if leaf["mark"])

    assert mark["title"] == MARK
    assert mark["label"] == MARK
    assert mark["role"] == "img"
    assert mark["iconHidden"] is True
    assert mark["clickable"] is False


@needs_node
def test_pinning_moves_no_row(tmp_path: Path):
    """Condition 7. The tree shows what a Data Source contains; that is its whole job, and an order
    that changed under a click would break the structure the person just walked down through. The
    fixture's tables are deliberately unsorted and the pin is on the middle one, so a tree that
    sorted its leaves — by name or by pinned-first — reds here either way. Pinning the FIRST row
    instead passes against a pinned-first sort, because the row it floats to is the one it is
    already on; that was true of this assertion until the plant for it came back green."""
    bare, pinned = _leaves([{"pins": []}, {"pins": [dict(PIN, name=TABLE)]}])

    order = [leaf["name"] for leaf in bare["leaves"]]
    assert order == ["DIM_ACCOUNT", TABLE, "FCT_USAGE_DAILY"]
    assert [leaf["name"] for leaf in pinned["leaves"]] == order


@needs_node
def test_both_arms_of_the_pin_control_say_which_conversations_they_reach(tmp_path: Path):
    """The copy, asserted where it is DRAWN rather than where it is written. Pin's old hover said it
    sent nothing, which stopped being true the moment a pinned leaf started every new conversation;
    Unpin had no hover at all, leaving the person undoing the durable act as the only one not told
    what it reaches. Each names both halves, because the confusion this row keeps producing is about
    WHICH conversation an act lands in."""
    bare, pinned = _leaves([{"pins": []}, {"pins": [dict(PIN, name=TABLE)]}])

    pin = next(a for a in bare["leaves"][0]["acts"] if a["ink"] == "Pin")
    assert pin["tip"] == ("Adds this to every new conversation. "
                          "Your current conversation doesn't change.")

    row = next(leaf for leaf in pinned["leaves"] if leaf["mark"])
    unpin = next(a for a in row["acts"] if a["ink"] == "Unpin")
    assert unpin["tip"] == ("Removes this from new conversations. "
                            "Your current conversation doesn't change.")


@needs_node
def test_the_pin_control_carries_the_leaf_it_sits_beside(tmp_path: Path):
    """Which row a click is about. The mark and the hover are both about the middle leaf; a control
    wired to the row above it would draw identically and pin the wrong table."""
    (clicked,) = _leaves([{"pins": [], "pin": TABLE}])

    # The harness's own Data Source, not this file's: the two fixtures name different sources
    # because they are asking different questions of them.
    assert clicked["posted"] == [{
        "act": "pin", "parent": "data_source:ds_1",
        "pin": {"database": "DWH", "schema": "MARTS", "table": TABLE, "name": TABLE},
    }]
