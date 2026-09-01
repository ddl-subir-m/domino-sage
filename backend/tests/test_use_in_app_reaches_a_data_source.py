"""`Use in {app}` reaches a Data Source — from the app's own surface (#129, #142, ADR-0021).

WHAT THIS FILE WAS FOR. #127 hung the door on a Project row and let one kind through it: a language
model, whose bind takes a single argument. A Data Source's Binding also records a Scope, and a
Project row had nowhere to get one from — so the rail listed Data Sources, said nothing about
whether the selected app could reach them, and offered no way to make it so.

WHAT #129 ANSWERED, AND #142 REVERSED. #129 made the Scope the cascade position the person was
standing on and hung the door there: beside the crumb at the schema and table stages, on the row at
a leaf, and nowhere at the top. It worked, and it put a three-level tree walk in front of every Data
Source bind. ADR-0021 split the act in two — bind from the Built App's own surface, then choose the
Scope there as a second, cheaper act on a Binding that already exists — so the door left the
cascade. The kind's door is `test_a_data_source_binds_first_and_is_scoped_afterwards.py`'s now.

WHAT SURVIVED THE REVERSAL, and is what this file still asserts:

THE FLAG THAT DID TWO JOBS. `canBind` both put the act on a row's menu and switched the row's
subtitle to `Not used by {app}`. #129 needed the second for a Data Source and had to refuse the
first, so the two came apart: `saysAppUse` is the sign and `canBind` is the door. The split holds
under #142 for a different reason — the door is not this panel's at all now — and the sign is still
the only thing that tells a Resource the app can reach from one merely sitting in the Project.

THE CASCADE, WHICH NOW ONLY LOOKS. It still descends, still remembers where it is, and still
survives a listing that will not answer. What it no longer does is write.

WHAT THE APP'S OWN LIST SAYS. The Scope is the one thing on that row that moves when the Scope
moves, and a Binding that has none says so in words — "Not scoped yet", a named unfinished state
and not an error (#142).

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

SCOPED = [{"kind": "data_source", "id": "ds_1", "name": "Market data EOD",
           "display_name": "Market data EOD", "database": "DWH", "schema": "MARTS",
           "table": "FCT_USAGE_DAILY"}]
UNSCOPED = [{"kind": "data_source", "id": "ds_1", "name": "Market data EOD",
             "display_name": "Market data EOD"}]


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
    Project's list and the app's — and which one carries which act is half of what this asks."""
    found = [r for r in step["rows"] if r["section"] == section and r["id"] == rid]
    assert len(found) == 1, f"{rid} appears {len(found)} times under {section}: {step['rows']}"
    return found[0]


def _source_row(step: dict) -> dict:
    """The Project's own row for the Data Source — the one the cascade hangs under."""
    return _row(step, "In this project")


# ---- the cascade, which now only looks ----------------------------------------------------------


@needs_node
def test_the_cascade_walks_databases_schemas_and_tables():
    """Browsing is orientation, which is the panel's job (ADR-0020), and it is the whole of what is
    left here. Three positions, walked in order, each one listing what is under the last."""
    top = _at(walk=[])
    assert top["steps"] == ["DWH", "SANDBOX"]
    assert top["crumb"] == []

    schemas = _at(walk=["DWH"])
    assert schemas["crumb"] == ["DWH"]
    assert schemas["steps"] == ["MARTS", "REPORTING"]

    tables = _at(walk=["DWH", "MARTS"])
    assert tables["crumb"] == ["DWH", "MARTS"]
    assert tables["leaves"] == ["DIM_ACCOUNT", "FCT_USAGE_DAILY"]


@needs_node
def test_no_position_in_the_cascade_offers_an_act_that_binds():
    """The reversal, asked at every position that used to have a door: the top, the schema stage,
    the table stage and a leaf row. A Binding is the Built App's, so its door is on the app's own
    surface (ADR-0021) — and a Scope is now a second act there, against a Binding that exists, which
    is why standing somewhere is no longer a thing to send."""
    for walk in ([], ["DWH"], ["DWH", "MARTS"]):
        step = _at(walk=walk)
        assert step["doors"] == [], f"a door survived at {walk or 'the top'}"
    # And nothing was written by the walking, which is what "for looking only" means.
    assert _at(walk=["DWH", "MARTS"])["posted"] == []


@needs_node
def test_a_source_domino_pins_a_database_for_still_opens_one_rung_down():
    """`default_database` is a real field and `DataSourceCascade` seeds its state from it, so — as
    `FakeResourceProvider` puts it — such a cascade "opens on the schema level with the database
    already answered". The seeding is what `SW.util.cascadeStage` has to agree with, and it is
    shared with the Scope door now, so a change on either surface would show up here."""
    step = _at(walk=[], source="ds_2")
    assert step["crumb"] == ["underwriting"]
    assert step["steps"] == ["dbo"]
    assert step["doors"] == []


@needs_node
def test_the_crumb_is_the_way_back_up_a_level():
    """What the crumb is for once nothing is hung beside it: where you are, and how to leave. A walk
    that could only descend would make the panel a place you get stuck in."""
    step = _at(walk=["DWH", "MARTS"], then={"back": "DWH", "walk": ["SANDBOX"]})
    assert step["crumb"] == ["SANDBOX"]
    assert step["steps"] == ["PUBLIC"]


@needs_node
def test_a_listing_that_fails_leaves_the_position_standing():
    """A store that will not answer is not a position the person has lost. They walked here through
    listings that DID answer, and the level below being unreadable today says nothing about the
    levels above it — the same rule `_write_bound_schema` follows when the columns fail to come
    back, and the same one the Scope door follows on the other surface."""
    step = _at(walk=["DWH"], fail="schema")
    assert step["crumb"] == ["DWH"]
    # And it still says what went wrong, in the platform's own words (#121).
    assert any("couldn’t look inside" in t for t in step["words"])


# ---- the flag that did two jobs -----------------------------------------------------------------


@needs_node
def test_a_data_source_row_says_the_app_does_not_use_it():
    """The sign, which #127 gave to Aliases and #129 extended. Absence was invisible: a Data Source
    sat in the rail looking exactly as ready as one the app could actually read."""
    row = _source_row(_at(walk=[]))
    assert f"Not used by {APP}" in row["texts"]


@needs_node
def test_a_data_source_row_offers_no_act_of_its_own():
    """The other half of the split. It refused the act because a row had no Scope to send (#129);
    it refuses it now because the act is not this panel's (ADR-0021). Same row, same absence, and
    the sign is still on it."""
    row = _source_row(_at(walk=[]))
    assert not any(i["label"] == DOOR for i in row["items"])
    assert not any(i["key"] == "use-in-app" for i in row["items"])


@needs_node
def test_the_alias_keeps_the_row_level_act_the_split_took_from_the_data_source():
    """The control this ticket must not disturb. `canBind` narrowed; it did not move. The whole act
    leaves this panel in #144, and until it does there is no window in which an Alias has no door."""
    alias = next(r for r in _at(walk=[])["rows"] if r["id"] == "llm_alias:al_1")
    assert f"Not used by {APP}" in alias["texts"]
    assert any(i["key"] == "use-in-app" and i["label"] == DOOR for i in alias["items"])


@needs_node
def test_chat_shows_neither_the_sign_nor_the_alias_door():
    """A Binding names exactly one app and Chat shows none, so both would be naming an app that is
    not on screen. The #127 rule, unchanged: what Chat keeps is the conversation's own act."""
    step = _at(walk=[], mode="chat")
    assert step["doors"] == []
    row = _source_row(step)
    assert not any("Not used by" in t for t in row["texts"])
    assert any(i["label"] == "Use in this chat" for i in row["items"])
    # Asked of the MENUS as well as of the screen, because the two hide the act in different places:
    # `doors` reads the labels controls carry as children, and a Dropdown item's label is data on a
    # prop — so no menu item could ever have shown up in the assertion above.
    assert not any(i["key"] == "use-in-app" for r in step["rows"] for i in r["items"])


# ---- what the app's own list says the app reads --------------------------------------------------
#
# The record is read here rather than walked to, because the two acts that write it are on the other
# surface now. What this list has to do is say what the record says — including when the record says
# a Scope has not been chosen yet.


@needs_node
def test_the_app_list_names_the_part_of_the_source_it_reads():
    """Dotted, the way `Binding.scope` joins the levels for the agent, so the row and the AGENTS.md
    data block name the same thing the same way."""
    row = _row(_at(walk=[], bound=SCOPED), f"In {APP}")
    assert "DWH.MARTS.FCT_USAGE_DAILY" in row["texts"]


@needs_node
def test_a_scope_is_readable_in_full_where_the_row_cuts_it_off():
    """`.sw-res-sub` ellipsises, and a Scope is the one subtitle here whose TAIL identifies it —
    `DWH.MARTS` and `DWH.MARTS_ARCHIVE` truncate to the same pixels in a narrow rail. Asserted off
    the tooltip PROP rather than the row's words, because a hover is not what the row says."""
    assert _row(_at(walk=[], bound=SCOPED), f"In {APP}")["tips"] == ["DWH.MARTS.FCT_USAGE_DAILY"]


@needs_node
def test_a_binding_with_no_scope_reads_as_an_unfinished_state_and_not_an_error():
    """The ordinary state between the two acts (#142). A Data Source is bound with no Scope now, so
    this is what the app's list says most often about a Binding somebody has just made — and it has
    to name the state rather than describe the kind, which the row's own icon already did.

    Not an error, and drawn as none: the Binding stands, the app depends on the Data Source, and
    what is unanswered is which part of it the app reads."""
    row = _row(_at(walk=[], bound=UNSCOPED), f"In {APP}")
    assert "not scoped yet" in row["texts"]
    assert "data source" not in row["texts"]
    # And no tooltip, because there is nothing a hover could add that the row is not already saying.
    assert row["tips"] == []


@needs_node
def test_a_kind_that_records_no_scope_is_left_exactly_as_it_was():
    """Only one kind records which part of it the app reads. An Alias has no part to name, so its
    row keeps the word it had — this render must not reach a kind it has nothing to say about."""
    alias = [{"kind": "llm_alias", "id": "al_1", "name": "sonnet",
              "display_name": "Claude Sonnet 4"}]
    row = _row(_at(walk=[], bound=alias), f"In {APP}", "llm_alias:al_1")
    assert "llm alias" in row["texts"]
    assert row["tips"] == []


# ---- the removal, which was already there -------------------------------------------------------


@needs_node
def test_remove_from_the_app_still_reaches_a_data_source():
    """Verified rather than rebuilt. The `In {app}` section owns the removal for every kind it lists
    (ADR-0011), and only ADDITION moved onto the app's own surface (ADR-0021) — so the removal has
    to be exactly where it was, including for the kind whose addition moved twice."""
    step = _at(walk=[], bound=SCOPED)
    in_app = _row(step, f"In {APP}")
    assert any(i["key"] == "remove-from-app" and i["label"] == f"Remove from {APP}"
               for i in in_app["items"])
    # And the removal stayed the app section's: the Project row offers the Project's, not the app's.
    assert [i["key"] for i in _source_row(step)["items"] if i["key"]] == ["mention", "remove"]


# ---- the panel harness the rest of the rail is asserted through ---------------------------------


@needs_node
def test_the_sign_reaches_data_source_rows_in_the_rail_the_other_tests_draw():
    """The same claim, through `build_header_harness.mjs` — the fixture every other panel test
    shares. Its Data Source rows carry a `bindingKey`, the way `SW.api.resources()` has always sent
    one, so a row the selected app does not bind says so there too."""
    step = _run(_PANEL, [{"panel": "thr_many", "select": "app_a"}])[-1]
    loose = [r for r in step["rows"]
             if r["section"] == "In this project" and "Risk warehouse" in r["texts"]]
    assert len(loose) == 1
    assert "Not used by Desk dashboard" in loose[0]["texts"]
    assert not any(i["label"] == DOOR for i in loose[0]["items"])
