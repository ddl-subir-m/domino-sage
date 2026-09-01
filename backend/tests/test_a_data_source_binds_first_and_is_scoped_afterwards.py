"""A Data Source is bound first and scoped afterwards (#142, ADR-0021).

WHAT REVERSED. #129 made a Scope the cascade position the creator happened to be standing on, so
every Data Source bind began with a multi-level tree walk — database, then schema, then table —
and the Binding's Scope was a by-product of where the walk stopped. ADR-0021 moved the bind onto
the Built App's own surface, and the tree did not follow it. Binding and scoping are two acts now:
`bind_data_source` records the dependency and `scope_data_source` says which part of it the app
reads, afterwards, against a Binding that already exists.

WHY THIS WAS CHEAP. A scopeless Binding was already a legal, named state. `CONTEXT.md` on **Scope**:
"A Binding may have none, which means the Resource is recorded but the part of it the app reads is
not." Nothing here widens the record; what changed is which act writes which half of it.

THE DEPTHS ARE STILL NOT INTERCHANGEABLE, which is why the second act takes three arguments rather
than one. `_write_bound_schema` gates columns on `if binding.schema:` — columns live under a schema
— so a database-level Scope records a Binding and no columns, and a schema-level one records both.
That is a difference in what the AGENTS.md region can tell the agent, and a Scope that silently
wrote one level where another was chosen would leave the agent writing queries against table names
it was never given.

WHAT `scope_data_source` ADDS over calling `bind_data_source` with a Scope is the refusal, and the
refusal is the point of splitting them. A Scope is a part of a dependency, so writing one where
there is no Binding would record the dependency as a side effect of narrowing it — a bind arriving
through the door that exists because binding and scoping came apart.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sage.orchestrator.app as appmod
from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator, ResourceNotBound
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
    """A real workspace on disk: both halves under test here are files these acts write."""
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


# ---- the first act: a Binding, and no Scope -----------------------------------------------------


def test_a_data_source_binds_with_no_scope_at_all(tmp_path: Path):
    """The act the header's door makes. A picker row names a Resource and nothing inside it, so this
    is called with three empty strings — and the record that comes back is a whole record, not a
    partial write waiting to be completed."""
    orch = _orch(tmp_path)
    row = _source(orch.bind_data_source("ds-dwh"))
    assert row["id"] == "ds-dwh"
    # Absent rather than empty: the manifest is committed to the creator's own repo, and a level
    # nobody chose is not a level recorded as blank (`test_bindings.py` pins the same rule).
    assert "database" not in row and "schema" not in row and "table" not in row
    # The label the listing showed, carried so the panel can name the source with the gateway down.
    assert row["display_name"] == "Snowflake-Data-Warehouse"


def test_a_scopeless_bind_asks_the_store_for_nothing(tmp_path: Path):
    """Columns live under a schema, and this Binding names none — so the agent is told the columns
    are unknown rather than handed a guess, and the creator waits for no query on the way in. That
    is the whole of what makes the first act the cheap one."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh")
    assert _columns(orch).get("ds-dwh") == []


# ---- the second act: the Scope, against a Binding that already exists ---------------------------


def test_the_scope_is_set_afterwards_against_the_binding_that_exists(tmp_path: Path):
    """Two acts, one record. `Binding.key` is kind and id, so scoping edits the Binding rather than
    adding a second dependency — which is what lets the choice be made later without the Resource
    being unpicked and picked again."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh")
    rows = orch.scope_data_source("ds-dwh", "DWH", "MARTS")
    row = _source(rows)
    assert (row["database"], row["schema"]) == ("DWH", "MARTS")
    assert "table" not in row
    assert len([b for b in rows if b["kind"] == KIND_DATA_SOURCE]) == 1


def test_the_scope_can_be_changed_later_without_re_binding(tmp_path: Path):
    """The criterion the door on the app's surface exists to satisfy: a Scope is a choice, and a
    choice you cannot revisit is a trap. Three acts here — one bind, two scopes — and one record."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh")
    orch.scope_data_source("ds-dwh", "DWH", "MARTS")
    row = _source(orch.scope_data_source("ds-dwh", "DWH", "MARTS", "FCT_USAGE_DAILY"))
    assert (row["database"], row["schema"], row["table"]) == ("DWH", "MARTS", "FCT_USAGE_DAILY")


def test_an_empty_scope_clears_the_one_on_file_and_the_binding_stands(tmp_path: Path):
    """The way back to the state the bind leaves. "Not scoped yet" is a named state, so it has to be
    reachable a second time — otherwise the first choice is a one-way trip out of it, undone only by
    a Remove and a re-bind. The columns go with it, because they described the Scope that went."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh")
    orch.scope_data_source("ds-dwh", "DWH", "MARTS")
    row = _source(orch.scope_data_source("ds-dwh"))
    assert "database" not in row and "schema" not in row and "table" not in row
    assert _columns(orch)["ds-dwh"] == []


def test_scoping_a_data_source_the_app_does_not_bind_is_refused(tmp_path: Path):
    """What this act adds over binding with a Scope. Writing a Scope for a Resource the app does not
    depend on would record the dependency as a side effect of narrowing it — a bind arriving through
    the door that exists because binding and scoping came apart."""
    orch = _orch(tmp_path)
    with pytest.raises(ResourceNotBound):
        orch.scope_data_source("ds-dwh", "DWH", "MARTS")
    assert orch.list_bindings() == []


# ---- what each depth leaves for the agent -------------------------------------------------------


def test_a_database_level_scope_records_a_binding_and_no_columns(tmp_path: Path):
    """Stopping at a database is a real answer — narrower than the whole source and wider than one
    schema — and columns live below it. So the Binding stands and the agent is told the columns are
    unknown rather than handed a guess."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh")
    orch.scope_data_source("ds-dwh", "DWH")
    assert _columns(orch)["ds-dwh"] == []


def test_a_schema_level_scope_writes_the_columns_of_every_table_under_it(tmp_path: Path):
    """The difference the depth makes. One level deeper than the test above and the agent gains the
    real column names in the real tables, which is the whole of what `.sage/schema.json` is for
    (#15)."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh")
    orch.scope_data_source("ds-dwh", "DWH", "MARTS")
    columns = _columns(orch)["ds-dwh"]
    assert {c.table for c in columns} == {
        "DIM_ACCOUNT", "DIM_DATE", "FCT_USAGE_DAILY", "FCT_SUBSCRIPTION_REVENUE"}
    assert ("FCT_USAGE_DAILY", "SEATS_ACTIVE") in {(c.table, c.name) for c in columns}


def test_a_table_level_scope_writes_that_table_and_no_other(tmp_path: Path):
    """The narrowest Scope narrows the schema too. A leaf choice that handed over the whole schema
    would put three tables the app does not read in front of the agent on every turn."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh")
    orch.scope_data_source("ds-dwh", "DWH", "MARTS", "FCT_USAGE_DAILY")
    assert {c.table for c in _columns(orch)["ds-dwh"]} == {"FCT_USAGE_DAILY"}


def test_moving_the_scope_up_a_level_takes_the_old_schemas_columns_with_it(tmp_path: Path):
    """The columns describe the Scope beside them, so they have to move when it moves. Left behind,
    they would describe a schema the app no longer reads."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh")
    orch.scope_data_source("ds-dwh", "DWH", "MARTS")
    assert _columns(orch)["ds-dwh"]  # the schema's columns are on file
    row = _source(orch.scope_data_source("ds-dwh", "SANDBOX"))
    assert row["database"] == "SANDBOX" and "schema" not in row
    assert _columns(orch)["ds-dwh"] == []


# ---- removal takes the Scope with it ------------------------------------------------------------


def test_removing_the_binding_takes_its_scope_with_it(tmp_path: Path):
    """So a later re-bind inherits no old table. The Scope is not stored anywhere the Binding is
    not, and `_write_schema_entries` rebuilds `.sage/schema.json` from the manifest — so an unbound
    Data Source cannot leave a schema behind for the agent to write queries against."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh")
    orch.scope_data_source("ds-dwh", "DWH", "MARTS", "FCT_USAGE_DAILY")
    orch.unbind(KIND_DATA_SOURCE, "ds-dwh")
    assert orch.list_bindings() == []
    assert _columns(orch).get("ds-dwh") in (None, [])

    row = _source(orch.bind_data_source("ds-dwh"))
    assert "database" not in row and "schema" not in row and "table" not in row


# ---- the two routes the two doors post to -------------------------------------------------------


def test_the_route_records_a_binding_with_no_scope_in_the_body(tmp_path: Path, monkeypatch):
    """What the Build header's picker really sends: a kind and an id, the same two fields every
    other kind sends. The three scope keys are not omitted-as-in-tidier — they are absent because
    the control that posted this had no cascade position to take one from."""
    orch = _orch(tmp_path)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    res = client.post("/api/bindings", json={"kind": KIND_DATA_SOURCE, "id": "ds-dwh"})
    assert res.status_code == 200, res.text
    row = _source(res.json()["bindings"])
    assert "database" not in row and "schema" not in row and "table" not in row


def test_the_scope_route_takes_each_depth_the_second_act_can_send(tmp_path: Path, monkeypatch):
    """The second door is a browser control too, so the depths have to survive JSON and a route
    signature.

    An EMPTY LEVEL is the shape that matters here. A creator who chose a database and stopped sends
    `schema: ""`, because the control posts the position it is standing on rather than only the
    levels it wants recorded — and `str(body.get(...) or "")`, the line that flattens "" to "not
    chosen", would be untested on the one input it exists for.
    """
    orch = _orch(tmp_path)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)
    client.post("/api/bindings", json={"kind": KIND_DATA_SOURCE, "id": "ds-dwh"})

    def scope(**levels: str) -> dict:
        res = client.post("/api/bindings/data_source/ds-dwh/scope", json=levels)
        assert res.status_code == 200, res.text
        return _source(res.json()["bindings"])

    a_database = scope(database="DWH", schema="")
    assert a_database["database"] == "DWH"
    # The empty level lands as no level, rather than as a schema named "".
    assert "schema" not in a_database and "table" not in a_database
    a_schema = scope(database="DWH", schema="MARTS")
    assert (a_schema["database"], a_schema["schema"]) == ("DWH", "MARTS")
    assert "table" not in a_schema
    a_table = scope(database="DWH", schema="MARTS", table="DIM_ACCOUNT")
    assert a_table["table"] == "DIM_ACCOUNT"
    # Three posts, one record: scoping edits the Binding rather than adding a second dependency.
    assert len([b for b in client.get("/api/bindings").json()["bindings"]
                if b["kind"] == KIND_DATA_SOURCE]) == 1


def test_the_scope_route_refuses_where_the_app_holds_no_binding(tmp_path: Path, monkeypatch):
    """A 404 with a sentence, and nothing written. The refusal is the whole reason this is its own
    route rather than a second POST to `/api/bindings` — that one turns down a Resource the platform
    does not offer, this one turns down a Resource the app does not depend on."""
    orch = _orch(tmp_path)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    res = client.post("/api/bindings/data_source/ds-dwh/scope", json={"database": "DWH"})
    assert res.status_code == 404
    # Its own sentence, and its own cause. `ResourceNotBound` is a separate type from the
    # `LookupError` a Data Source the platform will not describe raises, precisely so this can name
    # the act that fixes it instead of sending the creator to Domino about a grant.
    assert res.json()["error"] == (
        "This app records no Binding for that Data Source, so there is no Scope to set. Use it in "
        "the app first."
    )
    assert client.get("/api/bindings").json()["bindings"] == []


# ---- the two doors, on the app's own surface ----------------------------------------------------
#
# Both are on the Build header, because that is the surface that owns what they write (ADR-0021):
# the picker records the dependency, and the control beside the record's name chooses the part of it
# the app reads. Nothing is mounted — see `js/build_header_harness.mjs` for why.

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH (it is in the Sage image)"
)

APP = "Desk dashboard"
# `app_a` binds `ds_1` with no Scope in the harness fixture, which is the state the first act leaves
# and the state the second one is for.
AT_APP_A = {"thread": "thr_many", "select": "app_a"}


def _steps(steps: list[dict]) -> list[dict]:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(steps),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _keys(items: list[dict]) -> list[str]:
    return [i["key"] for i in items]


@needs_node
def test_the_header_picker_binds_a_data_source_and_sends_no_scope():
    """The first act, from the door ADR-0021 put on the app's own surface. A picker row names a
    Resource and nothing inside it, so the body is a kind and an id — the same two fields every
    other kind sends. The kind is in this picker at all only because the Scope stopped being a
    cascade position: before that, a row here had none to pass."""
    step = _steps([{
        "addIn": True, "thread": "thr_many", "select": "app_c", "pick": "data_source:ds_9",
    }])[-1]
    assert step["posted"] == [{"kind": "data_source", "id": "ds_9"}]
    assert "data_source:ds_9" in step["bindings"]


@needs_node
def test_the_receipt_names_the_second_act():
    """A Data Source arrives unscoped, and unscoped is a state rather than a finished answer — so
    the act that leaves it there says what the next one is. Every other kind is told nothing of the
    sort, because no other kind has a part to choose."""
    source, alias = _steps([
        {"addIn": True, "thread": "thr_many", "select": "app_c", "pick": "data_source:ds_9"},
        {"addIn": True, "thread": "thr_many", "select": "app_c", "pick": "llm_alias:al_1"},
    ])
    said = " ".join(source["said"])
    assert "Rate curve viewer now uses Risk warehouse" in said
    assert "Choose a Scope beside its name" in said
    # And the way back out is still named, because that is what the act ADDS to (ADR-0021).
    assert "Remove it in Project resources" in said
    assert "Choose a Scope" not in " ".join(alias["said"])


@needs_node
def test_a_binding_with_no_scope_names_its_state_beside_the_record():
    """Criterion two, on the surface a creator is looking at when they make one. The unscoped state
    is the door's own label: the Binding that is unfinished is also the one that says how to finish
    it, which is what keeps it from reading as an error. The tooltip says what the click buys and
    that it is reversible, because a Scope is a choice and not a commitment."""
    step = _steps([{"scopeIn": "Market data EOD", "open": False, **AT_APP_A}])[-1]
    assert step["shut"]["label"] == "not scoped yet"
    assert step["shut"]["open"] is False
    assert "Choose which database, schema or table" in step["shut"]["tooltip"]
    assert "change it later" in step["shut"]["tooltip"]


@needs_node
def test_the_scope_door_walks_one_rung_at_a_time_and_stopping_is_an_answer():
    """The ladder, on the app's surface rather than in front of the bind. Each rung lists what is
    under the last, and from the first one down there is something to commit — a database alone is a
    Scope — beside the way back out of it. Without that second item the first rung would be
    permanent for as long as the door is open."""
    step = _steps([{
        "scopeIn": "Market data EOD", "walk": ["DWH"], "then": ["use"], **AT_APP_A,
    }])[-1]
    top, under_dwh = step["rungs"][0], step["rungs"][1]
    assert _keys(top) == ["at:DWH", "at:SANDBOX"]
    # Nothing to commit at the top: a Binding there would name the whole source, which the record
    # already says without this control's help.
    assert "use" not in _keys(top)
    assert _keys(under_dwh) == ["use", "reset", "", "at:MARTS", "at:REPORTING"]
    assert [i["label"] for i in under_dwh][:2] == ["Use DWH", "Start again"]
    # The empty levels are sent, not omitted: the route flattens "" to "not chosen", and a body that
    # left the key out would be asking it to guess which of the two was meant.
    assert step["scoped"] == [{"id": "ds_1", "database": "DWH", "schema": "", "table": ""}]
    assert step["now"]["label"] == "DWH"


@needs_node
def test_choosing_a_table_is_the_answer_rather_than_another_rung():
    """The bottom of the ladder has nothing below it, so a name chosen there IS the Scope. Which
    level a name answers is the store's question and not the menu's — the ladder is not the same
    height for every connector."""
    step = _steps([{
        "scopeIn": "Market data EOD", "walk": ["DWH", "MARTS", "FCT_USAGE_DAILY"], **AT_APP_A,
    }])[-1]
    assert step["scoped"] == [{"id": "ds_1", "database": "DWH", "schema": "MARTS",
                               "table": "FCT_USAGE_DAILY"}]
    assert step["now"]["label"] == "DWH.MARTS.FCT_USAGE_DAILY"
    assert "Desk dashboard reads DWH.MARTS.FCT_USAGE_DAILY" in step["now"]["tooltip"]


@needs_node
def test_the_second_act_records_no_dependency():
    """The whole reason scoping is its own route. A walk here posts to the Scope route and to
    nothing else — if it went through `/bindings`, narrowing a Scope would record the Binding it was
    narrowing, and a control meant to be the cheap one would be writing what the expensive one
    writes."""
    step = _steps([{
        "scopeIn": "Market data EOD", "walk": ["DWH", "MARTS"], "then": ["use"], **AT_APP_A,
    }])[-1]
    assert step["posted"] == []
    assert "POST /bindings" not in step["calls"]
    assert "POST /bindings/data_source/ds_1/scope" in step["calls"]
    # One Binding, holding the Scope the walk chose.
    assert step["bindings"] == ["llm_alias:al_1", "data_source:ds_1 @DWH.MARTS", "model_api:ma_1"]


@needs_node
def test_the_act_says_what_it_did_and_that_the_same_control_moves_it():
    """The receipt ADR-0021 asks for in place of the confirm it refused. The way back here is the
    control itself, which is what makes a Scope different from a Binding — that one is undone by a
    Remove on another surface, and this one is changed where it was chosen."""
    step = _steps([{
        "scopeIn": "Market data EOD", "walk": ["DWH", "MARTS"], "then": ["use"], **AT_APP_A,
    }])[-1]
    said = " ".join(step["said"])
    assert f"{APP} reads DWH.MARTS in Market data EOD" in said
    assert "Choose again from the same control" in said


@needs_node
def test_the_scope_can_be_moved_afterwards_without_a_second_bind():
    """`Start again` is the whole of it: the walk goes back to the top with the Binding untouched,
    and the second commit replaces the first in place. Two Scopes, two posts, one record — and no
    Remove and re-pick, which is the cost a Scope that could not move would carry."""
    step = _steps([{
        "scopeIn": "Market data EOD",
        "walk": ["DWH", "MARTS"], "then": ["reset"], **AT_APP_A,
    }, {
        # The same door reopened, walked somewhere else, and committed. `bindFirst` is deliberately
        # absent: the Binding was already there, which is the premise.
        "scopeIn": "Market data EOD", "walk": ["SANDBOX"], "then": ["use"], **AT_APP_A,
    }])
    back_at_top = step[0]["rungs"][-1]
    assert _keys(back_at_top) == ["at:DWH", "at:SANDBOX"]
    assert step[0]["scoped"] == []   # a walk that was abandoned wrote nothing

    moved = step[1]
    assert moved["scoped"] == [{"id": "ds_1", "database": "SANDBOX", "schema": "", "table": ""}]
    assert moved["posted"] == []
    assert moved["bindings"] == ["llm_alias:al_1", "data_source:ds_1 @SANDBOX", "model_api:ma_1"]


@needs_node
def test_the_two_acts_run_back_to_back_from_the_one_surface():
    """Bind, then scope, both on the Build header and neither leaving it. This is the flow the
    ticket is about — the tree walk is behind the bind now rather than in front of it — so it is
    asserted as one sequence rather than as two halves that happen to pass alone."""
    step = _steps([{
        "scopeIn": "Risk warehouse", "thread": "thr_many", "select": "app_c",
        "bindFirst": "data_source:ds_9", "walk": ["RISK", "LIMITS"], "then": ["use"],
    }])[-1]
    assert step["posted"] == [{"kind": "data_source", "id": "ds_9"}]
    assert step["scoped"] == [{"id": "ds_9", "database": "RISK", "schema": "LIMITS", "table": ""}]
    assert step["shut"]["label"] == "not scoped yet"
    assert step["now"]["label"] == "RISK.LIMITS"


@needs_node
def test_a_listing_that_will_not_answer_leaves_the_levels_already_chosen_on_offer():
    """A store that refuses is not a Scope anybody has lost. The person walked here through a
    listing that DID answer, so `Use DWH` is still a real answer — the same rule
    `_write_bound_schema` follows when the columns fail to come back. The reason is the label,
    because a disabled item never fires."""
    step = _steps([{
        "scopeIn": "Market data EOD", "walk": ["DWH"], "fail": "schema", **AT_APP_A,
    }])[-1]
    under_dwh = step["rungs"][-1]
    assert _keys(under_dwh) == ["use", "reset", "", "unavailable"]
    assert "couldn’t look inside" in [i["label"] for i in under_dwh][-1]
    assert [i["disabled"] for i in under_dwh][-1] is True


@needs_node
def test_the_scope_can_be_cleared_back_to_the_state_the_bind_leaves():
    """"Not scoped yet" is a state the product names and draws, so the door has to be able to reach
    it. Without this the first choice would be a one-way trip — reachable again only by removing the
    Binding and making it afresh, which is exactly the cost splitting the acts was meant to remove.

    Offered at the top of the ladder and only where a Scope is recorded, because that is the one
    position where "no part in particular" is a different answer from the one already on file. It is
    the same empty body the bind already sends."""
    step = _steps([{
        "scopeIn": "Market data EOD", "walk": ["DWH"],
        # Commit, shut the control, open it again — what a person does between two acts on one
        # record, and the only way to see that what the top offers has changed.
        "then": ["use", "reopen", "clear"], **AT_APP_A,
    }])[-1]
    top_before, top_after = step["rungs"][0], step["rungs"][3]
    # Nothing to clear before a Scope is chosen: the Binding already says the app uses the whole
    # source, so the item would be an act with no effect.
    assert "clear" not in _keys(top_before)
    assert _keys(top_after)[:2] == ["clear", ""]
    assert top_after[0]["label"] == "Read all of it — no Scope"

    assert step["scoped"] == [
        {"id": "ds_1", "database": "DWH", "schema": "", "table": ""},
        {"id": "ds_1", "database": "", "schema": "", "table": ""},
    ]
    assert step["now"]["label"] == "not scoped yet"
    # One record throughout — clearing a Scope is not a Remove, and the Binding stands.
    assert step["bindings"] == ["llm_alias:al_1", "data_source:ds_1", "model_api:ma_1"]
    # And the receipt names the state it left behind, in the words the record is drawn with.
    assert "Market data EOD is not scoped yet in Desk dashboard" in " ".join(step["said"])


@needs_node
def test_a_data_source_sage_cannot_look_inside_says_so_rather_than_asking():
    """A connector Sage has no dialect for sends no levels, so there is no ladder to climb. Asking
    the table listing with nothing above it would put a question to the store that neither this case
    nor a missing Project row has an answer to — and would leave the menu empty with no reason in
    it. The Binding is still a real record: "this app uses this Data Source" was the whole of what
    one meant before Scopes existed."""
    step = _steps([{
        "scopeIn": "Ledger export", "thread": "thr_many", "select": "app_c",
        "bindFirst": "data_source:ds_flat",
    }])[-1]
    assert _keys(step["rungs"][0]) == ["unreadable"]
    assert step["rungs"][0][0]["disabled"] is True
    assert "cannot look inside" in step["rungs"][0][0]["label"]
    # No listing was asked for, which is the half a disabled label cannot prove on its own.
    assert not any("/data-sources/" in c for c in step["calls"])


@needs_node
def test_moving_the_selected_app_mid_walk_takes_the_walk_with_it():
    """The Scope route carries no app id, so the server scopes whichever app is SELECTED — and two
    Built Apps in one Project can bind the same Data Source, which is ordinary. A walk keyed on the
    Binding alone would go on matching after the selection moved, and the commit at the end of it
    would land on the app the creator had just left.

    So the walk goes with the selection. The same rule the removal notice follows, for the same
    reason: it describes one act on one app's list.

    Asked of the walk rather than of the door, because after the switch the door may not be drawn at
    all — `app_c` binds no Data Source — and "no door on screen" is a weaker claim than "the walk is
    gone"."""
    step = _steps([{
        "scopeIn": "Market data EOD", "walk": ["DWH"], "switchTo": "app_c", **AT_APP_A,
    }])[-1]
    assert step["app"] == "Rate curve viewer"
    assert step["walkOpen"] is False
    # And nothing was written on the way out — neither app was scoped by the switch.
    assert step["scoped"] == []
