"""`Use in {app}` reaches a Data Source, once the Scope has a source (#129, ADR-0011).

WHAT WAS MISSING. #127 hung the door on the Project row and let one kind through it: a language
model, whose bind takes a single argument. A Data Source's Binding carries a Scope — which database,
which schema, which table — and a Project row has nowhere to get one from. So the rail listed Data
Sources, said nothing about whether the selected app could reach them, and offered no way to make it
so. The only writer was the handoff.

WHERE THE SCOPE COMES FROM, which is the question the door was waiting on. It is the cascade
position the person is standing on. Walking the cascade is already how a Scope gets chosen — the
names in `bind_data_source` "came from the cascade the creator has just walked" — so the door is
hung at each position that HAS one, and the act sends where you are:

    the top of the cascade   no door: nothing is chosen yet, and a Binding here would name
                             the whole source and no part of it
    the schema stage         beside the crumb, Scope {database}
    the table stage          beside the crumb, Scope {database, schema}
    a table leaf row         on the row, Scope {database, schema, table}

THE FLAG THAT DID TWO JOBS. `canBind` both put the act on a row's menu and switched the row's
subtitle to `Not used by {app}`. #129 needs the second for a Data Source and must not have the
first, so the two came apart: `saysAppUse` is the sign and `canBind` is the door. Widening the old
flag would have put a scopeless bind back on the row, which is the one shape this design rules out.

THE DOOR THAT MUST NOT HIDE. An Alias's door disappears once the app holds the Binding — one act,
one place. Copying that here would be a bug: re-binding replaces the record in place, because
`Binding.key` leaves the Scope out, so a second walk through the cascade is the ONLY way to move a
Scope to another schema. Hide the door and the first choice is permanent.

Nothing is mounted — see `js/data_source_scope_harness.mjs`, which unlike its siblings runs REAL
hooks, because the cascade is a state machine and its positions are the whole question.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "data_source_scope_harness.mjs"
_PANEL = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

APP = "Desk dashboard"
DOOR = f"Use in {APP}"


def _run(harness: Path, steps: list[dict]) -> list[dict]:
    out = subprocess.run(
        ["node", str(harness)],
        input=json.dumps(steps),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _at(**step) -> dict:
    """One position in the cascade, reached by walking to it."""
    return _run(_HARNESS, [step])[-1]


def _row(step: dict, section: str, rid: str = "data_source:ds_1") -> dict:
    """The one row for `rid` under `section`. The same Resource appears under two of them — the
    Project's list and the app's — and which one carries which act is half of what #129 asks."""
    found = [r for r in step["rows"] if r["section"] == section and r["id"] == rid]
    assert len(found) == 1, f"{rid} appears {len(found)} times under {section}: {step['rows']}"
    return found[0]


def _source_row(step: dict) -> dict:
    """The Project's own row for the Data Source — the one the cascade hangs under."""
    return _row(step, "Project resources")


# ---- the door, at each of the four positions ----------------------------------------------------


@needs_node
def test_the_top_of_the_cascade_offers_no_door():
    """Nothing is chosen here, so there is no Scope to send. A bind from the top would record that
    the creator picked a part of the source they have not reached yet — and every level below is
    still open, so it would be a claim about a choice nobody made."""
    top = _at(walk=[])
    assert top["doors"] == []
    assert top["crumb"] == []
    # And the top is a real position rather than an empty screen: the databases are listed.
    assert top["steps"] == ["DWH", "SANDBOX"]


@needs_node
def test_the_schema_stage_binds_the_database_it_stands_in():
    """One level down. The crumb says `DWH` and so does the Scope — the door and the breadcrumb are
    the same fact drawn twice, which is why they appear together."""
    step = _at(walk=["DWH"], bind="crumb")
    assert step["crumb"] == ["DWH"]
    assert step["posted"] == [{"kind": "data_source", "id": "ds_1", "database": "DWH", "schema": ""}]


@needs_node
def test_the_table_stage_binds_the_database_and_the_schema():
    """Two levels down, where the tables are on screen and the Scope is everything above them."""
    step = _at(walk=["DWH", "MARTS"], bind="crumb")
    assert step["crumb"] == ["DWH", "MARTS"]
    assert step["posted"] == [
        {"kind": "data_source", "id": "ds_1", "database": "DWH", "schema": "MARTS"}]


@needs_node
def test_a_table_row_binds_the_table_it_names():
    """The deepest position, and the only one whose door is on a row. The leaf carries the Scope
    already — the same object its chip is built from — so the Binding and a mention of that table
    cannot disagree about which one was meant."""
    step = _at(walk=["DWH", "MARTS"], bind="FCT_USAGE_DAILY")
    assert step["posted"] == [{"kind": "data_source", "id": "ds_1", "database": "DWH",
                               "schema": "MARTS", "table": "FCT_USAGE_DAILY"}]


@needs_node
def test_a_source_domino_pins_a_database_for_opens_with_a_scope_and_a_door():
    """"No door at the top" means "no door where nothing is chosen", and for this source something
    already is. `default_database` is a real field, and `DataSourceCascade` seeds its state from it,
    so — as `FakeResourceProvider` puts it — such a cascade "opens on the schema level with the
    database already answered". The first screen therefore HAS a Scope, and a door that keyed off
    "has the person clicked yet" rather than off the crumb would wrongly refuse it."""
    step = _at(walk=[], source="ds_2", bind="crumb")
    assert step["crumb"] == ["underwriting"]
    assert step["doors"] == ["sw-tree-bind"]
    assert step["posted"] == [
        {"kind": "data_source", "id": "ds_2", "database": "underwriting", "schema": ""}]


@needs_node
def test_every_table_row_carries_its_own_door_beside_the_crumbs():
    """Three doors at the table stage: one for the schema the crumb names, and one per table. A
    single door there would make the tables unbindable without a second control somewhere else."""
    step = _at(walk=["DWH", "MARTS"])
    assert step["leaves"] == ["DIM_ACCOUNT", "FCT_USAGE_DAILY"]
    assert step["doorsBefore"] == 3


# ---- the flag that did two jobs -----------------------------------------------------------------


@needs_node
def test_a_data_source_row_says_the_app_does_not_use_it():
    """The sign, which #127 gave to Aliases and this ticket extends. Absence was invisible: a Data
    Source sat in the rail looking exactly as ready as one the app could actually read."""
    row = _source_row(_at(walk=[]))
    assert f"Not used by {APP}" in row["texts"]


@needs_node
def test_a_data_source_row_offers_no_act_of_its_own():
    """The other half of the split, and the trap in it. The row has no Scope — that is the whole
    reason the door moved into the cascade — so an act here could only send a scopeless bind. The
    sign is on the row; the door is one level in."""
    row = _source_row(_at(walk=[]))
    assert not any(i["label"] == DOOR for i in row["items"])
    assert not any(i["key"] == "use-in-app" for i in row["items"])


@needs_node
def test_the_alias_keeps_the_row_level_act_the_split_took_from_the_data_source():
    """The control this ticket must not disturb. `canBind` narrowed; it did not move."""
    alias = next(r for r in _at(walk=[])["rows"] if r["id"] == "llm_alias:al_1")
    assert f"Not used by {APP}" in alias["texts"]
    assert any(i["key"] == "use-in-app" and i["label"] == DOOR for i in alias["items"])


@needs_node
def test_chat_shows_neither_the_sign_nor_the_door():
    """A Binding names exactly one app and Chat shows none, so both would be naming an app that is
    not on screen. The #127 rule, unchanged: what Chat keeps is the conversation's own act."""
    step = _at(walk=[], mode="chat")
    assert step["doors"] == []
    row = _source_row(step)
    assert not any("Not used by" in t for t in row["texts"])
    assert any(i["label"] == "Use in this chat" for i in row["items"])
    # Asked of the MENUS as well as of the cascade, because the two hide the act in different
    # places: `doors` reads the labels controls carry as children, and a Dropdown item's label is
    # data on a prop — so no menu item could ever have shown up in the assertion above.
    assert not any(i["key"] == "use-in-app" for r in step["rows"] for i in r["items"])


# ---- the door that must not hide ----------------------------------------------------------------


@needs_node
def test_the_door_is_still_there_once_the_app_holds_the_binding():
    """Where the Alias's rule would have been exactly wrong. The row now reads `Required by {app}`
    and the door is still in the cascade, because re-binding is how a Scope is edited."""
    step = _at(walk=["DWH"], bind="crumb")
    assert step["doors"] == ["sw-tree-bind"]
    assert f"Required by {APP}" in _source_row(step)["texts"]


@needs_node
def test_a_second_walk_moves_the_scope_instead_of_adding_a_binding():
    """The reason the door stays: two passes, two posts, ONE record — and the record says where the
    second pass ended. `Binding.key` leaves the Scope out, so this is an edit rather than a second
    dependency, which is what makes "change the schema without removing and re-picking" true."""
    step = _at(walk=["DWH", "MARTS"], bind="crumb",
               then={"back": "DWH", "walk": ["SANDBOX"], "bind": "crumb"})
    assert [p.get("database") for p in step["posted"]] == ["DWH", "SANDBOX"]
    assert len(step["bindings"]) == 1
    assert step["bindings"][0]["database"] == "SANDBOX"
    assert "schema" not in step["bindings"][0]


# ---- a store that will not answer ---------------------------------------------------------------


@needs_node
def test_a_listing_that_fails_does_not_take_the_door_with_it():
    """The creator walked here through listings that DID answer, so the position still stands. A
    Binding is a decision about which part of the source the app reads, not a promise that the next
    level down is readable today — the same reason `_write_bound_schema` records the Binding when
    the columns fail to come back."""
    step = _at(walk=["DWH"], fail="schema")
    assert step["crumb"] == ["DWH"]
    assert step["doors"] == ["sw-tree-bind"]
    # And it still says what went wrong, in the platform's own words (#121).
    assert any("couldn’t look inside" in t for t in step["words"])


@needs_node
def test_a_failure_at_the_top_still_offers_nothing_to_bind():
    """The other side of it. No listing answered and nothing was chosen, so there is no Scope to
    fall back on — a door here would be the scopeless bind, arrived at by a different route."""
    step = _at(walk=[], fail="database")
    assert step["crumb"] == []
    assert step["doors"] == []


# ---- the removal, which was already there -------------------------------------------------------


@needs_node
def test_remove_from_the_app_still_reaches_a_data_source():
    """Verified rather than rebuilt. The `In {app}` section owns the removal for every kind it lists
    (ADR-0011), and a Data Source has been in that section since #99 — so the door this ticket hangs
    already had its undo, and the split must not have moved it."""
    step = _at(walk=["DWH"], bind="crumb")
    in_app = _row(step, f"In {APP}")
    assert any(i["key"] == "remove-from-app" and i["label"] == f"Remove from {APP}"
               for i in in_app["items"])
    # And the removal stayed the app section's: the Project row offers the Project's, not the app's.
    assert [i["key"] for i in _source_row(step)["items"] if i["key"]] == ["mention", "remove"]


# ---- the panel harness the rest of the rail is asserted through ---------------------------------


@needs_node
def test_the_sign_reaches_data_source_rows_in_the_rail_the_other_tests_draw():
    """The same claim, through `build_header_harness.mjs` — the fixture every other panel test
    shares. Its Data Source rows carry a `bindingKey` now, the way `SW.api.resources()` has always
    sent one, so a row the selected app does not bind says so there too."""
    step = _run(_PANEL, [{"panel": "thr_many", "select": "app_a"}])[-1]
    loose = [r for r in step["rows"]
             if r["section"] == "Project resources" and "Risk warehouse" in r["texts"]]
    assert len(loose) == 1
    assert "Not used by Desk dashboard" in loose[0]["texts"]
    assert not any(i["label"] == DOOR for i in loose[0]["items"])
