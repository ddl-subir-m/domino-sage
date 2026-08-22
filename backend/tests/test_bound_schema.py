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
    SAMPLES_PATH,
    SCHEMA_PATH,
    BoundSource,
    SharedSample,
    agents_block,
    parse_samples,
    parse_schema,
    render_samples,
    render_schema,
)
from sage.resources.builtapp import catalog_problems, serve_module, stranded_levels
from sage.resources.provider import (
    Column,
    FakeResourceProvider,
    ResourceUnavailable,
    SampleRows,
)

# The app template itself, which is what SAGE_TEMPLATE points at and what WorkspaceManager seeds
# from — so this is the same `serve.py` that ends up in every published app.
TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"

SNOWFLAKE = Binding(KIND_DATA_SOURCE, "ds-dwh", "warehouse", "warehouse",
                    "DWH", "MARTS", None, "SnowflakeConfig")
POSTGRES = Binding(KIND_DATA_SOURCE, "ds-pg", "reporting", "reporting",
                   None, "public", None, "PostgreSQLConfig")


def block_for(binding=SNOWFLAKE, columns=None, stranded=(), problems=(), max_rows=5000,
              samples=(False, ())) -> str:
    # `stranded` and `problems` pass through as None when that is what they are: None means "Sage
    # could not check", which renders differently from an empty list, so the helper must not flatten
    # the two into each other.
    source = BoundSource(binding, list(columns or []),
                         None if stranded is None else list(stranded))
    return agents_block([source], None if problems is None else list(problems),
                        max_rows, samples=samples)


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
    written = render_schema([(SNOWFLAKE, columns)])
    assert parse_schema(json.loads(written)) == {SNOWFLAKE.id: columns}


def test_the_record_carries_no_timestamp():
    # It is committed to the creator's own app repo, so a "read at" field would make every re-bind a
    # diff in a file whose content had not changed.
    body = json.loads(render_schema([(SNOWFLAKE, [Column("orders", "id", "int")])]))
    assert set(body) == {"sources"}
    assert set(body["sources"][0]) == {"id", "source", "scope", "connector_type", "tables"}


def test_an_unreadable_record_is_no_schema_rather_than_a_guess():
    assert parse_schema("{not json") == {}
    assert parse_schema(None) == {}


# ---- what the agent reads --------------------------------------------------------------------------


def test_no_data_source_means_no_region_at_all():
    # Machinery for a store that is not there costs context every turn and invites an app built
    # around data it cannot reach.
    assert agents_block([], None, 5000) == ""


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


def test_the_agent_is_told_the_preview_answers_queries_and_a_failure_there_is_real():
    """The inverse of what this asserted before #24 shipped, and the inversion is the point.

    While the preview could not answer, the agent had to be told so — otherwise it read the 404 as a
    broken query and "fixed" working code. Now that Sage runs `serve.py` beside the dev server, the
    old warning is worse than useless: it licensed the agent to design a screen around a failure
    instead of fixing it, which is how a dashboard ends up shipping with an apology where its data
    should be.
    """
    block = block_for()
    assert "answer in the preview too" in block
    assert "a real failure and worth fixing now" in block
    assert "not in the preview" not in block


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
    (template / "src").mkdir(parents=True, exist_ok=True)
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


def orchestrator_on(tmp_path: Path):
    """A second Orchestrator over the same workspace — an orchestrator restart, which is what makes
    an in-memory lock worth re-firing."""
    return orchestrator(tmp_path)


def _entry(orch, binding_id: str) -> dict:
    """One Data Source's entry in the recorded schema. The file holds one per bound source (#33)."""
    body = json.loads((workspace_of(orch) / SCHEMA_PATH).read_text())
    return next(e for e in body["sources"] if e["id"] == binding_id)


def workspace_of(orch) -> Path:
    return orch.project(start_preview=False).workspace.path


def test_binding_a_scope_records_what_its_tables_hold(tmp_path: Path):
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS", "FCT_USAGE_DAILY")
    recorded = _entry(orch, "ds-dwh")
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
    assert [t["name"] for t in _entry(orch, "ds-dwh")["tables"]] == ["V_ARR_WATERFALL",
                                                                    "V_CUSTOMER_HEALTH"]


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
    assert _entry(orch, "ds-dwh")["tables"] == []
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


# ---- sample rows, only ever because someone asked (#16) --------------------------------------------


def test_sample_rows_come_back_cut_and_json_safe():
    provider = FakeResourceProvider()
    sample = provider.sample_rows(source(provider, "ds-dwh"), "DWH", "MARTS", "FCT_USAGE_DAILY")
    assert sample.table == "FCT_USAGE_DAILY"
    assert sample.columns == ["USAGE_DATE", "ACCOUNT_ID", "SEATS_ACTIVE", "COMPUTE_HOURS"]
    assert sample.rows[0] == ["2026-08-18", "ACC-1042", 37, 12.5]


def test_a_wide_value_is_cut_rather_than_dropped():
    # The agent is being shown the SHAPE of the data. A value cut at the limit still says "this is an
    # email address"; a base64 blob in full just spends context.
    from sage.resources.provider import sample_value
    assert sample_value("x" * 200).endswith("…")
    assert len(sample_value("x" * 200)) == 81
    assert sample_value(None) is None      # "this column is often empty" is worth showing


def test_the_row_limit_is_spelled_the_way_each_store_spells_it():
    # SQL Server's family has no LIMIT and takes TOP before the select list. Appending ` LIMIT 5` to
    # one statement for every connector would be a syntax error on two of them.
    from sage.resources.provider import SQL_DIALECTS
    snow = SQL_DIALECTS["SnowflakeConfig"]
    mssql = SQL_DIALECTS["SQLServerConfig"]
    assert snow.statement(snow.sample, database="DWH", schema="MARTS", table="T", limit=5) == (
        'SELECT * FROM "DWH"."MARTS"."T" LIMIT 5')
    assert mssql.statement(mssql.sample, database="u", schema="dbo", table="T", limit=5) == (
        'SELECT TOP 5 * FROM "u"."dbo"."T"')


def test_a_two_level_store_does_not_get_an_empty_database_prefix():
    from sage.resources.provider import SQL_DIALECTS
    pg = SQL_DIALECTS["PostgreSQLConfig"]
    assert pg.statement(pg.sample, schema="public", table="events", limit=5) == (
        'SELECT * FROM "public"."events" LIMIT 5')


def test_the_samples_record_keeps_the_treatment_beside_the_rows():
    # `sensitive` is the creator's judgement about their own data, so it is recorded rather than
    # re-derived — and it is what re-fires the in-memory sovereign lock when a session reopens.
    rows = SampleRows("orders", ["id"], [[1], [2]])
    written = render_samples([SharedSample("ds-dwh", True, rows)])
    sensitive, samples = parse_samples(json.loads(written))
    assert sensitive is True
    assert samples == [SharedSample("ds-dwh", True, rows)]


def test_a_samples_record_written_before_stores_were_named_still_reads(tmp_path: Path):
    """This file is gitignored, so an entry with no Binding only ever reaches a workspace whose
    orchestrator was upgraded under it — but it holds real rows the agent is already being shown, and
    dropping them would un-share data without anyone choosing to."""
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    (workspace_of(orch) / SAMPLES_PATH).write_text(json.dumps({
        "sensitive": True,
        "tables": [{"name": "FCT_USAGE_DAILY", "columns": ["USAGE_DATE"], "rows": [["2026-01-01"]]}],
    }))
    # Attributed to the only store that could have produced it: the first.
    assert orch._shared(orch.project()) == [
        SharedSample("ds-dwh", True, SampleRows("FCT_USAGE_DAILY", ["USAGE_DATE"], [["2026-01-01"]]))]


def test_an_unreadable_samples_record_is_no_samples_and_not_sensitive():
    assert parse_samples("{not json") == (False, [])


def test_nothing_shared_adds_nothing_to_what_the_agent_reads():
    # Criterion 4: working from the schema alone is the default and stays fully supported.
    assert "Sample rows" not in block_for()


def test_shared_rows_are_named_but_never_quoted_into_agents_md():
    # AGENTS.md is committed. The whole reason the samples file is gitignored is that rows must not
    # travel with the repo, so this region can only ever point at them.
    block = block_for(samples=(False, [("warehouse", ["orders", "customers"])]))
    assert "`orders` and `customers`" in block
    assert SAMPLES_PATH in block
    assert "Never copy them anywhere" in block


def test_a_sensitive_share_tells_the_agent_the_conversation_stays_in_domino():
    assert "sovereign models" in block_for(samples=(True, [("warehouse", ["orders"])]))
    assert "sovereign models" not in block_for(samples=(False, [("warehouse", ["orders"])]))


def test_rows_shared_from_two_stores_say_which_store_each_came_from():
    # A table name stops identifying anything once two stores are bound — `events` in the warehouse
    # is not `events` in the app database — and the agent reading these rows is about to write a
    # query that has to name a Binding.
    block = block_for(samples=(False, [("warehouse", ["events"]), ("app-db", ["events", "users"])]))
    assert "`events` in **warehouse**" in block
    assert "`events` and `users` in **app-db**" in block


def test_sharing_reads_the_picked_tables_and_locks_when_marked_sensitive(tmp_path: Path):
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    assert orch.project(start_preview=False).control.snapshot().sensitivity_locked is False
    result = orch.share_sample_rows("ds-dwh", ["FCT_USAGE_DAILY", "DIM_ACCOUNT"], sensitive=True)
    assert result["shared"] == ["FCT_USAGE_DAILY", "DIM_ACCOUNT"]
    assert orch.project(start_preview=False).control.snapshot().sensitivity_locked is True
    sensitive, samples = parse_samples(json.loads((workspace_of(orch) / SAMPLES_PATH).read_text()))
    assert sensitive is True
    assert [s.rows.table for s in samples] == ["FCT_USAGE_DAILY", "DIM_ACCOUNT"]


def test_sharing_without_marking_sensitive_does_not_lock(tmp_path: Path):
    # Criterion 2. Sage does not infer the treatment from the fact that this is warehouse data — the
    # creator knows what is in the table and Sage does not.
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    orch.share_sample_rows("ds-dwh", ["FCT_USAGE_DAILY"], sensitive=False)
    assert orch.project(start_preview=False).control.snapshot().sensitivity_locked is False


def test_the_shared_rows_never_get_committed(tmp_path: Path):
    # The rest of .sage/ rides into the published app's container. Rows must not.
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    orch.share_sample_rows("ds-dwh", ["FCT_USAGE_DAILY"], sensitive=False)
    ignored = (workspace_of(orch) / ".gitignore").read_text().split()
    assert SAMPLES_PATH in ignored


def test_only_the_picked_tables_are_read(tmp_path: Path):
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    asked = []
    real = orch._resources.sample_rows
    orch._resources.sample_rows = lambda *a, **k: (asked.append(a[3]), real(*a, **k))[1]
    orch.share_sample_rows("ds-dwh", ["DIM_ACCOUNT"], sensitive=False)
    assert asked == ["DIM_ACCOUNT"]


def test_sharing_again_replaces_rather_than_accumulates(tmp_path: Path):
    # The picker shows what is currently shared, so the list that comes back IS the choice: a table
    # unticked is one the creator wants the agent to stop seeing.
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    orch.share_sample_rows("ds-dwh", ["FCT_USAGE_DAILY", "DIM_ACCOUNT"], sensitive=False)
    orch.share_sample_rows("ds-dwh", ["DIM_ACCOUNT"], sensitive=False)
    assert orch.sample_candidates()["shared"] == ["DIM_ACCOUNT"]


def test_stopping_takes_the_rows_away_and_leaves_the_lock_on(tmp_path: Path):
    # Sticky, exactly as detaching a sensitive file is: the model has already seen what it has seen,
    # and unlocking is its own deliberate act with its own warning.
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    orch.share_sample_rows("ds-dwh", ["FCT_USAGE_DAILY"], sensitive=True)
    orch.clear_sample_rows()
    assert not (workspace_of(orch) / SAMPLES_PATH).exists()
    assert "Sample rows" not in (workspace_of(orch) / "AGENTS.md").read_text()
    assert orch.project(start_preview=False).control.snapshot().sensitivity_locked is True


def test_sharing_nothing_is_the_opposite_choice_rather_than_an_error(tmp_path: Path):
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    orch.share_sample_rows("ds-dwh", ["FCT_USAGE_DAILY"], sensitive=False)
    assert orch.share_sample_rows("ds-dwh", [], sensitive=False)["shared"] == []
    assert not (workspace_of(orch) / SAMPLES_PATH).exists()


def test_the_picker_offers_the_tables_the_scope_recorded(tmp_path: Path):
    # From the recorded schema, not the store: opening the choice must cost no query and no wait.
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    candidates = orch.sample_candidates()
    assert candidates["bindable"] is True
    assert candidates["tables"] == ["DIM_ACCOUNT", "DIM_DATE", "FCT_USAGE_DAILY",
                                    "FCT_SUBSCRIPTION_REVENUE"]
    assert candidates["shared"] == []


def test_an_app_with_no_data_source_has_nothing_to_offer(tmp_path: Path):
    orch = orchestrator(tmp_path)
    assert orch.sample_candidates() == {"bindable": False, "source": "", "tables": [], "shared": [],
                                        "sensitive": False, "sources": []}


def test_a_reopened_session_relocks_for_rows_that_are_still_there(tmp_path: Path):
    # The lock is in-memory. Without this it would drop on restart while the rows it was protecting
    # sit in the workspace for the agent to read.
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    orch.share_sample_rows("ds-dwh", ["FCT_USAGE_DAILY"], sensitive=True)
    reopened = orchestrator_on(tmp_path)
    assert reopened.project(start_preview=False).control.snapshot().sensitivity_locked is True


def test_a_reopened_session_does_not_lock_for_rows_shared_without_the_mark(tmp_path: Path):
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    orch.share_sample_rows("ds-dwh", ["FCT_USAGE_DAILY"], sensitive=False)
    reopened = orchestrator_on(tmp_path)
    assert reopened.project(start_preview=False).control.snapshot().sensitivity_locked is False


# ---- the routes the panel calls --------------------------------------------------------------------


def client_for(orch, monkeypatch):
    from fastapi.testclient import TestClient

    from sage.orchestrator import app as appmod
    monkeypatch.setattr(appmod, "orchestrator", orch)
    return TestClient(appmod.control_app)


def test_the_panel_can_read_share_and_stop(tmp_path: Path, monkeypatch):
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    client = client_for(orch, monkeypatch)

    offered = client.get("/api/project/samples").json()
    assert "FCT_USAGE_DAILY" in offered["tables"] and offered["shared"] == []

    shared = client.post("/api/project/samples",
                         json={"tables": ["FCT_USAGE_DAILY"], "sensitive": True})
    assert shared.status_code == 200
    assert shared.json() == {"shared": ["FCT_USAGE_DAILY"], "sensitive": True, "rows": 3}

    assert client.delete("/api/project/samples").json()["shared"] == []


def test_a_store_that_will_not_answer_is_a_502_with_its_own_reason(tmp_path: Path, monkeypatch):
    # Unlike a Binding, there is nothing to record when the rows do not arrive — the rows ARE what
    # was asked for, so this fails rather than half-succeeding.
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")

    def refuse(*args, **kwargs):
        raise ResourceUnavailable("Snowflake-Data-Warehouse did not answer: timeout")

    orch._resources.sample_rows = refuse
    response = client_for(orch, monkeypatch).post(
        "/api/project/samples", json={"tables": ["FCT_USAGE_DAILY"], "sensitive": False})
    assert response.status_code == 502
    assert "did not answer" in response.json()["error"]
    assert not (workspace_of(orch) / SAMPLES_PATH).exists()


def test_sharing_from_an_app_with_no_data_source_is_a_404(tmp_path: Path, monkeypatch):
    orch = orchestrator(tmp_path)
    response = client_for(orch, monkeypatch).post(
        "/api/project/samples", json={"tables": ["anything"], "sensitive": False})
    assert response.status_code == 404


# ---- the whole-screen state, distinct from an empty collection (#32) ------------------------------


def test_the_agent_is_told_the_whole_screen_says_so_not_just_the_panel_that_asked():
    """Observed live 2026-08-21: a dashboard whose data pane correctly said the data was not
    available, surrounded on the same screen by three hero pills claiming "Date range ready" and
    "Platform and device filters", a fully live "Refine view" panel whose selects held only `All`,
    and a filled primary "Apply filters" button that did nothing.

    Every part of that was the agent following the States section by analogy: it built the empty
    state for the ONE collection that asked, and left the rest of the screen asserting it worked.
    The state that actually occurs for a Data-Source app is not one empty list, it is every control
    inert at once, and nothing told the agent that was a different thing.
    """
    block = block_for()
    assert "WHOLE SCREEN says so" in block
    assert "different state from an empty list" in block


def test_the_agent_is_told_to_disable_the_controls_and_say_why():
    # The first criterion, and the user's own design rule: a disabled element explains why it is
    # disabled. A select holding one dead option explains nothing.
    block = block_for()
    assert "Disable those controls and say why beside them" in block
    assert 'holding only "All"' in block


def test_the_agent_is_told_not_to_advertise_what_the_screen_cannot_show():
    # The second criterion. Feature badges and headings naming filters or metrics are claims about
    # what the screen does, and a screen that cannot query is not doing any of them.
    block = block_for()
    assert "Drop the headings and feature badges" in block
    assert "they are claims" in block


def test_the_agent_is_told_a_dead_primary_button_is_not_an_option():
    # The third criterion. A filled primary button is the strongest thing on a screen, and one that
    # does nothing when pressed is the single loudest false claim the state can make.
    block = block_for()
    assert "filled primary button that does nothing when pressed" in block
    assert "make that the primary action" in block


def test_the_agent_is_given_the_test_to_apply_rather_than_only_the_rules():
    # The fifth criterion, in the words it is written in, so the agent can check its own screen
    # against the thing a creator will actually read off it.
    block = block_for()
    assert "not yet" in block and "working, but empty" in block


def test_an_app_with_no_data_source_is_told_none_of_this():
    # The region exists only for an app that reads a store. Describing an unreachable store to an app
    # that has none costs context on every turn and invites a screen built around data it cannot
    # reach — the same reason the rest of this block is conditional.
    assert agents_block([], None, 5000) == ""


# ---- several Data Sources bound at once (#33) ---------------------------------------------------


def data_block(workspace: Path) -> str:
    """Sage's managed data region of AGENTS.md, alone. The rest of the file is the template's own
    prose, which carries English words that a bare substring search mistakes for table names."""
    agents = (workspace / "AGENTS.md").read_text()
    begin, end = "<!-- sage:app-data:begin -->", "<!-- sage:app-data:end -->"
    b, e = agents.find(begin), agents.find(end)
    return agents[b:e] if b != -1 and e != -1 else ""


def test_every_bound_data_source_is_described_with_its_own_tables(tmp_path: Path):
    """The point of binding two (#33): "one tab from @snowflake and another from @redshift" needs the
    agent to know both stores. Each entry is keyed by the Binding id a query has to carry, so which
    store a table is in survives into `.sage/queries.json`."""
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    orch.bind_data_source("ds-mssql", "underwriting", "dbo")

    assert [t["name"] for t in _entry(orch, "ds-dwh")["tables"]] == [
        "DIM_ACCOUNT", "DIM_DATE", "FCT_USAGE_DAILY", "FCT_SUBSCRIPTION_REVENUE"]
    assert [t["name"] for t in _entry(orch, "ds-mssql")["tables"]] == ["policies", "claims", "quotes"]

    block = data_block(workspace_of(orch))
    assert '### Snowflake-Data-Warehouse — `"binding": "ds-dwh"`' in block
    assert '### AWS_MSSQL — `"binding": "ds-mssql"`' in block
    # The mistake that replaces "which columns" once there are two stores is "which store".
    assert '`"ds-dwh"` for Snowflake-Data-Warehouse, `"ds-mssql"` for AWS_MSSQL' in block


def test_one_data_source_is_described_without_the_per_store_headings(tmp_path: Path):
    # A heading per store, when there is one store, is structure that says nothing — and this block
    # is re-read whole on every turn.
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    block = data_block(workspace_of(orch))
    assert "### Snowflake-Data-Warehouse" not in block
    assert '- `binding` must be `"ds-dwh"`. That is this app\'s Data Source.' in block


def test_removing_one_source_leaves_the_others_described(tmp_path: Path):
    # Rebuilt from the manifest, so an unbound store cannot leave its tables behind — and the store
    # that stays is not re-read for it, because nothing about that store moved.
    orch = orchestrator(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    orch.bind_data_source("ds-mssql", "underwriting", "dbo")

    orch.unbind("data_source", "ds-dwh")

    body = json.loads((workspace_of(orch) / SCHEMA_PATH).read_text())
    assert [e["id"] for e in body["sources"]] == ["ds-mssql"]
    block = data_block(workspace_of(orch))
    assert "AWS_MSSQL" in block
    assert "`FCT_USAGE_DAILY`" not in block


def test_a_source_bound_while_its_store_was_down_is_asked_again_but_only_once(tmp_path: Path):
    """An entry that came back EMPTY still counts as read. Without that, `_write_app_data` runs at
    the end of every turn and would re-ask a store that is down on each one — turning an unreachable
    warehouse into a tax on every build."""
    orch = orchestrator(tmp_path)
    calls = []
    real = orch._resources.list_columns

    def counted(*args, **kwargs):
        calls.append(args[0].id)
        raise ResourceUnavailable("did not answer: timeout")

    orch._resources.list_columns = counted
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    assert _entry(orch, "ds-dwh")["tables"] == []
    assert len(calls) == 1

    orch._write_app_data(orch.project())      # what the end of every turn does
    orch._write_app_data(orch.project())
    assert len(calls) == 1                     # asked once, not once a turn

    # And a Scope that MOVES is a different question, so it is asked again.
    orch._resources.list_columns = real
    orch.bind_data_source("ds-dwh", "DWH", "REPORTING")
    assert [t["name"] for t in _entry(orch, "ds-dwh")["tables"]] == ["V_ARR_WATERFALL",
                                                                    "V_CUSTOMER_HEALTH"]
