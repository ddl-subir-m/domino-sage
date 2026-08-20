"""The agent is told what the bound tables hold, and how to ask for them (#15).

Criterion 5 — "schema retrieval is covered by tests with the fake provider" — is why the cascade's
fake grew a `columns` table: the path from picking a Scope to an agent that can name real columns has
to be exercisable on a laptop with no warehouse behind it.

Everything about the rendering is a pure function over an already-read manifest, so the shape of what
the agent reads is asserted here without a workspace, and the two facts Sage cannot decide for itself
— whether the Scope travels, and which queries the app will refuse — are read from the Built App's
own `serve.py` rather than restated.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from sage.resources.bindings import KIND_DATA_SOURCE, Binding
from sage.resources.bound_schema import (
    INLINE_COLUMN_LIMIT,
    SCHEMA_PATH,
    agents_block,
    parse_schema,
    render_schema,
)
from sage.resources.builtapp import catalog_problems, serve_module, stranded_levels
from sage.resources.provider import Column, FakeResourceProvider, ResourceUnavailable

# The app template itself, which is what SAGE_TEMPLATE points at and what WorkspaceManager seeds
# from — so this is the same `serve.py` that ends up in every published app.
TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"

SNOWFLAKE = Binding(KIND_DATA_SOURCE, "ds-dwh", "warehouse", "warehouse",
                    "DWH", "MARTS", None, "SnowflakeConfig")
POSTGRES = Binding(KIND_DATA_SOURCE, "ds-pg", "reporting", "reporting",
                   None, "public", None, "PostgreSQLConfig")


def block_for(binding=SNOWFLAKE, columns=None, stranded=(), problems=(), max_rows=5000) -> str:
    # `stranded` and `problems` pass through as None when that is what they are: None means "Sage
    # could not check", which renders differently from an empty list, so the helper must not flatten
    # the two into each other.
    return agents_block(binding, list(columns or []),
                        None if stranded is None else list(stranded),
                        None if problems is None else list(problems), max_rows)


# ---- reading the columns, through the fake provider ----------------------------------------------


def source(provider: FakeResourceProvider, source_id: str):
    return next(s for s in provider.list_data_sources() if s.id == source_id)


def test_the_columns_of_one_bound_table_come_back_with_their_types():
    provider = FakeResourceProvider()
    columns = provider.list_columns(source(provider, "ds-dwh"), "DWH", "MARTS", "FCT_USAGE_DAILY")
    assert [(c.table, c.name, c.type) for c in columns] == [
        ("FCT_USAGE_DAILY", "USAGE_DATE", "DATE"),
        ("FCT_USAGE_DAILY", "ACCOUNT_ID", "VARCHAR"),
        ("FCT_USAGE_DAILY", "SEATS_ACTIVE", "NUMBER"),
        ("FCT_USAGE_DAILY", "COMPUTE_HOURS", "FLOAT"),
    ]


def test_a_scope_that_stopped_at_a_schema_reads_every_table_in_it():
    # Stopping at a schema is a real choice (#11), so it has to produce a usable answer rather than
    # a demand that the creator go back and pick a table.
    provider = FakeResourceProvider()
    columns = provider.list_columns(source(provider, "ds-dwh"), "DWH", "MARTS")
    assert {c.table for c in columns} == {
        "DIM_ACCOUNT", "DIM_DATE", "FCT_USAGE_DAILY", "FCT_SUBSCRIPTION_REVENUE"}


def test_a_connector_with_no_cascade_at_all_refuses_before_it_gets_to_columns():
    # [] is what an empty schema looks like, and a connector Sage cannot look inside is a different
    # thing. Telling them apart is what keeps the unverified dialects honest.
    provider = FakeResourceProvider()
    with pytest.raises(ResourceUnavailable) as caught:
        provider.list_columns(source(provider, "ds-oracle"), "", "anything")
    assert "cannot list what is inside" in str(caught.value)


def test_a_connector_that_can_list_tables_but_not_columns_says_which_it_is(monkeypatch):
    # Every dialect shipped today has a columns statement, so this state is reachable only through
    # the field's own default — which is what a future connector with a cascade and no
    # `information_schema` would land on.
    from sage.resources import provider as provider_module
    listable = replace(provider_module.SQL_DIALECTS["SnowflakeConfig"], columns=None)
    monkeypatch.setitem(provider_module.SQL_DIALECTS, "SnowflakeConfig", listable)
    fake = FakeResourceProvider()
    with pytest.raises(ResourceUnavailable) as caught:
        fake.list_columns(source(fake, "ds-dwh"), "DWH", "MARTS")
    assert "cannot read the columns" in str(caught.value)


def test_the_columns_statement_asks_one_question_for_a_whole_schema():
    # A query per table would turn binding a 200-table schema into minutes: the cascade already
    # measures ~3s a level against the live warehouse.
    from sage.resources.provider import SQL_DIALECTS
    dialect = SQL_DIALECTS["SnowflakeConfig"]
    every = dialect.statement(dialect.columns, database="DWH", schema="MARTS")
    one = dialect.statement(dialect.columns, database="DWH", schema="MARTS", table="FCT_USAGE_DAILY")
    assert "INFORMATION_SCHEMA.COLUMNS" in every and "TABLE_NAME =" not in every
    assert "AND TABLE_NAME = 'FCT_USAGE_DAILY'" in one


# ---- the record on disk ---------------------------------------------------------------------------


def test_the_recorded_schema_round_trips():
    columns = [Column("orders", "id", "int"), Column("orders", "total", "decimal"),
               Column("customers", "id", "int")]
    written = render_schema(SNOWFLAKE, columns)
    assert parse_schema(json.loads(written)) == columns


def test_the_record_carries_no_timestamp():
    # It is committed to the creator's own app repo, so a "read at" field would make every re-bind a
    # diff in a file whose content had not changed.
    body = json.loads(render_schema(SNOWFLAKE, [Column("orders", "id", "int")]))
    assert set(body) == {"source", "scope", "connector_type", "tables"}


def test_an_unreadable_record_is_no_schema_rather_than_a_guess():
    assert parse_schema("{not json") == []
    assert parse_schema(None) == []


# ---- what the agent reads --------------------------------------------------------------------------


def test_no_data_source_means_no_region_at_all():
    # Machinery for a store that is not there costs context every turn and invites an app built
    # around data it cannot reach.
    assert agents_block(None, [], None, None, 5000) == ""


def test_a_small_schema_is_written_out_in_full():
    columns = [Column("orders", "id", "int"), Column("orders", "placed_on", "date")]
    block = block_for(columns=columns)
    assert "`orders`" in block
    assert "- `placed_on` date" in block
    assert SCHEMA_PATH not in block      # nothing to go and read


def test_a_large_schema_is_named_and_left_in_the_file():
    columns = [Column(f"t{i}", f"c{j}", "int") for i in range(30) for j in range(4)]
    assert len(columns) > INLINE_COLUMN_LIMIT
    block = block_for(columns=columns)
    assert "It has 30 tables" in block
    assert f"columns are in `{SCHEMA_PATH}`" in block
    assert "- `c0` int" not in block      # not the whole thing, or AGENTS.md pays for it every turn


def test_columns_sage_could_not_read_are_said_to_be_missing_rather_than_left_blank():
    block = block_for(columns=[])
    assert "could not read" in block
    assert "Ask the user" in block


def test_the_agent_is_told_the_binding_id_it_must_write():
    # A catalog naming a Binding this app does not record is refused at startup, and the id is not
    # something the agent can work out from anything else it can see.
    assert '"binding": "ds-dwh"' in block_for()
    assert 'must be `"ds-dwh"`' in block_for()


def test_the_agent_is_told_the_row_cap_it_is_writing_against():
    assert "at most 250 rows" in block_for(max_rows=250)


def test_a_scope_that_travels_asks_for_unqualified_sql():
    assert "Write table names unqualified" in block_for(stranded=[])


def test_a_scope_that_cannot_travel_asks_for_the_name_in_the_statement():
    block = block_for(binding=POSTGRES, stranded=[("schema", "public")])
    assert "has to name public itself" in block
    assert "FROM public.usage" in block


def test_when_sage_could_not_ask_the_instruction_is_the_one_that_works_either_way():
    # None is "could not check", not "nothing is stranded". A qualified name runs on every connector;
    # an unqualified one runs only where the Scope travels, so the safe default is the strict one.
    assert "qualified" in block_for(stranded=None)


def test_the_agent_is_told_not_to_read_the_store_itself():
    # Criterion 2. The agent has a shell and could go and look; what stops it is being told, because
    # putting production rows in a model's context is the creator's decision to make (#16).
    block = block_for()
    assert "Do not read the Data Source yourself" in block


def test_the_agent_is_warned_that_the_preview_cannot_answer_queries():
    # Otherwise it sees the preview 404, decides the query is broken, and "fixes" working code (#24).
    assert "not in the preview" in block_for()


def test_the_queries_the_app_will_refuse_are_quoted_in_the_apps_own_words():
    problem = "The query revenue reads the schema MARTS, which a PostgreSQL Data Source cannot carry."
    block = block_for(problems=[problem])
    assert problem in block


def test_a_catalog_with_nothing_wrong_adds_no_section():
    assert "will refuse" not in block_for(problems=[])


# ---- the two answers that come from the Built App's own serve.py ------------------------------------


def test_whether_a_scope_travels_is_answered_by_the_file_that_enforces_it():
    # Not by a second copy of the table in the orchestrator: one that was right when it was written
    # and wrong the day a connector was added to the other would have Sage promise a query the
    # published app then refuses.
    assert stranded_levels(TEMPLATE, SNOWFLAKE) == []
    assert stranded_levels(TEMPLATE, POSTGRES) == [("schema", "public")]


def test_a_template_without_a_built_app_says_it_could_not_check(tmp_path: Path):
    assert stranded_levels(tmp_path, SNOWFLAKE) is None
    assert catalog_problems(tmp_path, tmp_path) is None


def test_serve_py_is_loaded_once_per_template():
    assert serve_module(TEMPLATE) is serve_module(TEMPLATE)


def test_the_catalog_check_reports_what_the_published_app_would(tmp_path: Path):
    (tmp_path / ".sage").mkdir()
    (tmp_path / ".sage" / "bindings.json").write_text(json.dumps([{
        "kind": "data_source", "id": "ds-pg", "name": "reporting", "display_name": "reporting",
        "schema": "public", "connector_type": "PostgreSQLConfig",
    }]))
    (tmp_path / ".sage" / "queries.json").write_text(json.dumps([{
        "name": "recent", "binding": "ds-pg", "sql": "SELECT id FROM events", "params": [],
    }]))
    problems = catalog_problems(TEMPLATE, tmp_path)
    assert len(problems) == 1
    assert "public" in problems[0] and "PostgreSQL" in problems[0]


def test_a_catalog_that_holds_together_reports_nothing(tmp_path: Path):
    (tmp_path / ".sage").mkdir()
    (tmp_path / ".sage" / "bindings.json").write_text(json.dumps([{
        "kind": "data_source", "id": "ds-dwh", "name": "warehouse", "display_name": "warehouse",
        "database": "DWH", "schema": "MARTS", "connector_type": "SnowflakeConfig",
    }]))
    (tmp_path / ".sage" / "queries.json").write_text(json.dumps([{
        "name": "usage", "binding": "ds-dwh",
        "sql": "SELECT ACCOUNT_ID FROM FCT_USAGE_DAILY WHERE USAGE_DATE >= :since",
        "params": [{"name": "since", "type": "date"}],
    }]))
    assert catalog_problems(TEMPLATE, tmp_path) == []


def test_an_app_with_no_catalog_has_no_problems(tmp_path: Path):
    assert catalog_problems(TEMPLATE, tmp_path) == []


# ---- through the orchestrator, from picking a Scope to what the agent reads -------------------------


def orchestrator(tmp_path: Path):
    """A real workspace, seeded from a template that carries the REAL `serve.py`.

    Copied rather than stubbed because the whole point of `builtapp` is that Sage asks that file
    instead of restating it: a stub here would test a second implementation into existence.
    """
    from sage.gateway.client import FakeGatewayClient
    from sage.orchestrator.service import Orchestrator
    from sage.router.models import ModelCatalog

    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("placeholder")
    (template / "package.json").write_text("{}")
    shutil.copy2(TEMPLATE / "serve.py", template / "serve.py")
    shutil.copy2(TEMPLATE / "src" / "sageQuery.ts", template / "src" / "sageQuery.ts")
    shutil.copy2(TEMPLATE / "src" / "sageBase.ts", template / "src" / "sageBase.ts")

    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=template,
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=FakeResourceProvider(),
    )
    orch.project(start_preview=False)
    return orch


def workspace_of(orch) -> Path:
    return orch.project(start_preview=False).workspace.path


def test_binding_a_scope_records_what_its_tables_hold(tmp_path: Path):
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS", "FCT_USAGE_DAILY")
    recorded = json.loads((workspace_of(orch) / SCHEMA_PATH).read_text())
    assert recorded["scope"] == "DWH.MARTS.FCT_USAGE_DAILY"
    assert [t["name"] for t in recorded["tables"]] == ["FCT_USAGE_DAILY"]
    assert {c["name"] for c in recorded["tables"][0]["columns"]} == {
        "USAGE_DATE", "ACCOUNT_ID", "SEATS_ACTIVE", "COMPUTE_HOURS"}


def test_the_agent_is_told_the_columns_and_how_to_query_them(tmp_path: Path):
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS", "FCT_USAGE_DAILY")
    agents = (workspace_of(orch) / "AGENTS.md").read_text()
    assert "## The app's data" in agents
    assert "`SEATS_ACTIVE` NUMBER" in agents
    assert "`.sage/queries.json`" in agents
    # Snowflake carries both levels, so the statement must NOT repeat them.
    assert "Write table names unqualified" in agents


def test_an_app_seeded_before_the_helper_existed_gets_one_when_it_binds(tmp_path: Path):
    # The template ships `src/sageQuery.ts`, so every project seeded after #15 already has it. This is
    # for the ones seeded before: their repo has none, and the block above tells the agent to import
    # from a module that is not there — the same gap `ensure_llm_helper` exists to close.
    orch = orchestrator(tmp_path)
    helper = workspace_of(orch) / "src" / "sageQuery.ts"
    helper.unlink()
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    assert helper.is_file()


def test_rebinding_to_another_schema_replaces_the_recorded_columns(tmp_path: Path):
    # The schema file describes the Binding beside it. Left behind, it would have the agent writing
    # queries against tables the app no longer reads.
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    orch.bind_data_source("ds-dwh", "DWH", "REPORTING")
    recorded = json.loads((workspace_of(orch) / SCHEMA_PATH).read_text())
    assert [t["name"] for t in recorded["tables"]] == ["V_ARR_WATERFALL", "V_CUSTOMER_HEALTH"]


def test_unbinding_takes_the_recorded_columns_with_it(tmp_path: Path):
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    orch.unbind(KIND_DATA_SOURCE, "ds-dwh")
    assert not (workspace_of(orch) / SCHEMA_PATH).exists()
    assert "## The app's data" not in (workspace_of(orch) / "AGENTS.md").read_text()


def test_a_store_that_will_not_answer_still_records_the_binding(tmp_path: Path):
    # The Binding is the creator's decision and it stands. What they lose is the column names, and
    # the agent is told so rather than left to invent them.
    orch = orchestrator(tmp_path)

    def refuse(*args, **kwargs):
        raise ResourceUnavailable("Snowflake-Data-Warehouse did not answer: timeout")

    orch._resources.list_columns = refuse
    entries = orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    assert [e["id"] for e in entries if e["kind"] == KIND_DATA_SOURCE] == ["ds-dwh"]
    assert json.loads((workspace_of(orch) / SCHEMA_PATH).read_text())["tables"] == []
    assert "could not read" in (workspace_of(orch) / "AGENTS.md").read_text()


def test_a_scope_that_stopped_above_a_schema_asks_the_store_nothing(tmp_path: Path):
    # Columns live under a schema. A Scope that names only a database has nothing to read, and
    # asking anyway would be a wasted round trip that fails on every connector.
    orch = orchestrator(tmp_path)
    asked = []
    orch._resources.list_columns = lambda *a, **k: asked.append(a) or []
    orch.bind_data_source("ds-dwh", "DWH")
    assert asked == []


def test_a_broken_query_the_agent_wrote_is_quoted_back_to_it(tmp_path: Path):
    # The published app refuses this at startup, which is after a publish and a cold start. Sage runs
    # the same check with the same code, so the agent reads the app's own sentence next turn.
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-reporting", "", "public", "events")
    (workspace_of(orch) / ".sage" / "queries.json").write_text(json.dumps([{
        "name": "recent", "binding": "ds-reporting", "sql": "SELECT id FROM events", "params": [],
    }]))
    orch._recheck_app_data()
    agents = (workspace_of(orch) / "AGENTS.md").read_text()
    assert "Queries this app will refuse" in agents
    assert "has to name public itself" in agents


def test_a_catalog_that_holds_together_is_not_reported_as_a_problem(tmp_path: Path):
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS", "FCT_USAGE_DAILY")
    (workspace_of(orch) / ".sage" / "queries.json").write_text(json.dumps([{
        "name": "usage", "binding": "ds-dwh",
        "sql": "SELECT ACCOUNT_ID FROM FCT_USAGE_DAILY WHERE USAGE_DATE >= :since",
        "params": [{"name": "since", "type": "date"}],
    }]))
    orch._recheck_app_data()
    assert "will refuse" not in (workspace_of(orch) / "AGENTS.md").read_text()
