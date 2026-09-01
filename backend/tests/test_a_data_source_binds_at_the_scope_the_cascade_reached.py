"""A Data Source Binding records the cascade position the creator was standing on (#129).

WHAT THE DOOR SENDS. `Use in {app}` reaches a Data Source from three of the cascade's four
positions, and the Scope it posts is wherever the person stood when they opened it: a schema stage
sends `{database}`, a table stage sends `{database, schema}`, and a leaf row sends all three. The
top of the cascade has no Scope and so has no door — a bind from there would name the whole source
and no part of it, which is the one shape #129 rules out.

WHY THIS FILE EXISTS AT ALL, given `POST /api/bindings` already took four arguments before #129 was
opened. The route and `bind_data_source` were written for the handoff's writer and had no caller in
the panel, so every depth below was reachable in principle and untested in practice. The door is
what turns them into three things a person can actually do, and a depth nobody asserts is a depth
the next refactor is free to flatten.

THE DEPTHS ARE NOT INTERCHANGEABLE, which is the whole reason the door carries the position rather
than a fixed level. `_write_bound_schema` gates columns on `if binding.schema:` — columns live under
a schema — so a database-level Scope records a Binding and no columns, and a schema-level one
records both. That is a difference in what the AGENTS.md region can tell the agent, and it has to
survive: a bind that silently wrote a database-level Scope where a schema was chosen would leave the
agent writing queries against table names it was never given.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import sage.orchestrator.app as appmod
from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_DATA_SOURCE
from sage.resources.bound_schema import SCHEMA_PATH, parse_schema
from sage.resources.provider import FakeResourceProvider
from sage.router.models import ModelCatalog


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp_path: Path) -> Orchestrator:
    """A real workspace on disk: both halves under test here are files the bind writes."""
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=FakeResourceProvider(),
    )
    orch.project(start_preview=False)  # memoized, so nothing under test starts a dev server
    return orch


def _source(entries: list[dict]) -> dict:
    """The one Data Source row in a binding list."""
    rows = [e for e in entries if e["kind"] == KIND_DATA_SOURCE]
    assert len(rows) == 1, f"expected exactly one Data Source Binding, got {rows}"
    return rows[0]


def _columns(orch: Orchestrator) -> dict[str, list]:
    """The recorded schema, per Binding id — what the agent is actually handed."""
    path = orch.project(start_preview=False).workspace.path / SCHEMA_PATH
    return parse_schema(json.loads(path.read_text())) if path.exists() else {}


# ---- the three depths, one per cascade position ------------------------------------------------


def test_a_bind_from_the_schema_stage_records_the_database_alone(tmp_path: Path):
    """Standing at the schema stage, the creator has answered one question: which database. That is
    a Scope, and a Binding for it is a record worth keeping — `bind_data_source` says so in as many
    words — even though the levels below it are still open."""
    orch = _orch(tmp_path)
    row = _source(orch.bind_data_source("ds-dwh", "DWH"))
    assert row["database"] == "DWH"
    # Absent rather than empty: the manifest is committed to the creator's own repo, and a level
    # nobody chose is not a level recorded as blank (`test_bindings.py` pins the same rule).
    assert "schema" not in row and "table" not in row


def test_a_bind_from_the_table_stage_records_the_database_and_the_schema(tmp_path: Path):
    """One position deeper: the database and the schema are both answered and the table list is what
    is on screen. The Scope is the pair, and the app reads every table under it."""
    orch = _orch(tmp_path)
    row = _source(orch.bind_data_source("ds-dwh", "DWH", "MARTS"))
    assert (row["database"], row["schema"]) == ("DWH", "MARTS")
    assert "table" not in row


def test_a_bind_from_a_leaf_row_records_all_three(tmp_path: Path):
    """The deepest position, and the only one whose door is on a row rather than beside the crumb."""
    orch = _orch(tmp_path)
    row = _source(orch.bind_data_source("ds-dwh", "DWH", "MARTS", "FCT_USAGE_DAILY"))
    assert (row["database"], row["schema"], row["table"]) == ("DWH", "MARTS", "FCT_USAGE_DAILY")
    # The label the row showed, carried so the panel can name the source with the gateway down.
    assert row["display_name"] == "Snowflake-Data-Warehouse"


# ---- what each depth leaves for the agent -------------------------------------------------------


def test_a_database_level_scope_records_a_binding_and_no_columns(tmp_path: Path):
    """Columns live under a schema, so a Scope that stopped above one has none to read. The Binding
    still stands — this is the "record worth keeping rather than a half-finished one" the docstring
    describes — and the agent is told the columns are unknown rather than handed a guess."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH")
    assert _columns(orch).get("ds-dwh") == []


def test_a_schema_level_scope_writes_the_columns_of_every_table_under_it(tmp_path: Path):
    """The difference the depth makes. One position deeper than the test above and the agent gains
    the real column names in the real tables, which is the whole of what `.sage/schema.json` is for
    (#15)."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    columns = _columns(orch)["ds-dwh"]
    assert {c.table for c in columns} == {
        "DIM_ACCOUNT", "DIM_DATE", "FCT_USAGE_DAILY", "FCT_SUBSCRIPTION_REVENUE"}
    assert ("FCT_USAGE_DAILY", "SEATS_ACTIVE") in {(c.table, c.name) for c in columns}


def test_a_table_level_scope_writes_that_table_and_no_other(tmp_path: Path):
    """The narrowest Scope narrows the schema too. A leaf bind that handed over the whole schema
    would put three tables the app does not read in front of the agent on every turn."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS", "FCT_USAGE_DAILY")
    assert {c.table for c in _columns(orch)["ds-dwh"]} == {"FCT_USAGE_DAILY"}


def test_moving_the_scope_up_a_level_takes_the_old_schemas_columns_with_it(tmp_path: Path):
    """Why the door stays on screen after a bind (#129). Re-binding replaces in place — `Binding.key`
    leaves the Scope out — so a second pass through the cascade is how a Scope moves, and the columns
    have to move with it. Left behind, they would describe a schema the app no longer reads."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    assert _columns(orch)["ds-dwh"]  # the schema's columns are on file
    row = _source(orch.bind_data_source("ds-dwh", "SANDBOX"))
    assert row["database"] == "SANDBOX" and "schema" not in row
    assert _columns(orch)["ds-dwh"] == []


# ---- the route the door actually posts to -------------------------------------------------------


def test_the_route_takes_each_depth_the_door_can_send(tmp_path: Path, monkeypatch):
    """The door is a browser control, so the depths have to survive JSON and a route signature.

    The bodies below are the ones the door REALLY sends, copied from what the JS harness records
    rather than from what reads tidily here. The difference matters: the crumb's door posts the pair
    it stands on, so at the schema stage it sends `schema: ""` — an EMPTY LEVEL, not an absent one —
    and it never sends `table` at all, because the pair is the whole of what that position knows.
    A test that posted only the levels it wanted recorded would be exercising a shape no control
    produces, and `str(body.get(...) or "")` — the line that flattens "" to "not chosen" — would be
    untested on the one input it exists for.
    """
    orch = _orch(tmp_path)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    def post(**scope: str) -> dict:
        res = client.post("/api/bindings", json={"kind": KIND_DATA_SOURCE, "id": "ds-dwh", **scope})
        assert res.status_code == 200, res.text
        return _source(res.json()["bindings"])

    at_schema_stage = post(database="DWH", schema="")
    assert at_schema_stage["database"] == "DWH"
    # The empty level lands as no level, rather than as a schema named "".
    assert "schema" not in at_schema_stage and "table" not in at_schema_stage
    at_table_stage = post(database="DWH", schema="MARTS")
    assert (at_table_stage["database"], at_table_stage["schema"]) == ("DWH", "MARTS")
    assert "table" not in at_table_stage
    at_leaf = post(database="DWH", schema="MARTS", table="DIM_ACCOUNT")
    assert at_leaf["table"] == "DIM_ACCOUNT"
    # Three posts, one record: the door stays open precisely because re-binding replaces.
    assert len([b for b in client.get("/api/bindings").json()["bindings"]
                if b["kind"] == KIND_DATA_SOURCE]) == 1
