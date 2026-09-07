"""The database-wide catalog walk (#182), on every connector that can be walked (#187).

The cascade opens one level at a time because a creator opens one level at a time. A table SEARCH
asks a different question — "which of everything in here is the one" — and answering it a schema at
a time costs ~3s a schema, which is about 45 seconds on the 15-schema warehouse this was measured
against (ADR-0038). One query answers it in 3.84s.

No live warehouse is reachable from here and none is needed: the statement is asserted as data, and
both providers are exercised on stubs. What a live run already proved is the per-schema statement
this one is derived from — the filter dropped, the schema added to the projection — so the delta is
what these tests pin.

#187 widens the walk from Snowflake to the rest, and the widening is four statements rather than
seven: the ANSI database-prefixed form serves SQL Server, Synapse, Databricks and Trino, while
PostgreSQL's family and MySQL's each have a two-level form of their own. BigQuery is the one
connector left asking for a schema, because its only project-wide TABLES view is region-qualified
and `BigQueryConfig` carries no region to qualify it with.

`sqlglot` was weighed as a dev-only dependency for #187 and declined. It would catch a typo, bad
quoting or a stray placeholder, but the risk these statements actually carry is a wrong view or
column name — BigQuery's region, ClickHouse's `system` — which parsing cannot see. So the statements
stay asserted as data, which is what the rest of this file already does.
"""

from __future__ import annotations

import pytest

from sage.resources.provider import (
    SQL_DIALECTS,
    DataSource,
    DominoResourceProvider,
    FakeResourceProvider,
    ResourceUnavailable,
    Table,
    walkable_databases,
    walks_whole_database,
)


def _source(connector_type: str, **kw) -> DataSource:
    return DataSource("ds-dwh", "src", connector_type.removesuffix("Config"), "Shared", None, True,
                      connector_type=connector_type, **kw)


class _Frame:
    """The one method `_introspect_rows` touches. Keys come back UPPER-CASED, as Snowflake sends
    them, so the lower-casing on the read path is exercised rather than assumed."""

    def __init__(self, records: list[dict]):
        self._records = records

    def to_dict(self, _orient: str) -> list[dict]:
        return list(self._records)


class _Warehouse(DominoResourceProvider):
    """The real provider with the Arrow Flight call replaced, so the SQL it sends is readable."""

    def __init__(self, records: list[dict]):
        super().__init__("http://gw/v1", lambda: "t")
        self.sent: list[str] = []
        self._records = records

    def _query(self, source: DataSource, sql: str):
        self.sent.append(sql)
        return _Frame(self._records)


def test_the_database_wide_statement_is_the_per_schema_one_without_its_filter():
    # Asserted as data, and asserted against the statement it was derived from: this is not new SQL,
    # it is the live-verified per-schema statement with `WHERE TABLE_SCHEMA` dropped and the schema
    # moved into the projection. Anything else here is a guess against a warehouse nobody can reach
    # from a test run.
    d = SQL_DIALECTS["SnowflakeConfig"]
    assert d.statement(d.database_tables, database="DWH") == (
        'SELECT TABLE_SCHEMA AS table_schema, TABLE_NAME AS table_name '
        'FROM "DWH".INFORMATION_SCHEMA.TABLES '
        "WHERE TABLE_SCHEMA <> 'INFORMATION_SCHEMA' ORDER BY TABLE_SCHEMA, TABLE_NAME")
    # Same view, same database qualification, same read-only shape as the statement that was run
    # live — narrowing to one schema is the whole difference.
    per_schema = d.statement(d.tables, database="DWH", schema="MARTS")
    assert '"DWH".INFORMATION_SCHEMA.TABLES' in per_schema
    assert "TABLE_SCHEMA = " not in d.statement(d.database_tables, database="DWH")


def test_the_walk_does_not_offer_the_stores_own_bookkeeping_as_a_candidate():
    # A creator opening a schema picked one, so the per-schema statement never had to say this. A
    # search picks none, and `INFORMATION_SCHEMA.COLUMNS` and `USAGE_PRIVILEGES` are a plausible
    # name match for a question about usage — ranked beside the marts, they are noise a person has
    # to read past, and the whole feature is about not handing people work.
    d = SQL_DIALECTS["SnowflakeConfig"]
    assert "TABLE_SCHEMA <> 'INFORMATION_SCHEMA'" in d.statement(d.database_tables, database="DWH")


def test_the_database_wide_statement_only_reads():
    # The same bar every other statement in the table is held to: Sage holds a shared credential that
    # can read the whole warehouse, so a write verb must not be reachable from this table at all.
    banned = ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE", "GRANT", "MERGE")
    for name, dialect in SQL_DIALECTS.items():
        if dialect.database_tables is None:
            continue
        rendered = dialect.statement(dialect.database_tables, database="DB").upper()
        assert rendered.startswith(("SELECT", "SHOW")), f"{name}: {rendered}"
        for verb in banned:
            assert verb not in rendered, f"{name} sends {verb}"


def test_the_walk_sends_one_query_and_answers_tables_with_the_schema_that_holds_them():
    # The point of the ticket: one query, and every row carries its schema. Without the schema the
    # answer cannot be grouped, and `MARTS.GONG__CALLS` and `STAGING.STG_GONG__CALLS` become the
    # same row.
    warehouse = _Warehouse([
        {"TABLE_SCHEMA": "MARTS", "TABLE_NAME": "GONG__CALLS"},
        {"TABLE_SCHEMA": "STAGING", "TABLE_NAME": "STG_GONG__CALLS"},
    ])
    assert warehouse.list_database_tables(_source("SnowflakeConfig"), "DWH") == [
        Table("MARTS", "GONG__CALLS"),
        Table("STAGING", "STG_GONG__CALLS"),
    ]
    assert len(warehouse.sent) == 1
    assert '"DWH".INFORMATION_SCHEMA.TABLES' in warehouse.sent[0]


def test_a_row_with_no_table_name_is_dropped_rather_than_offered_as_a_blank_candidate():
    warehouse = _Warehouse([{"TABLE_SCHEMA": "MARTS", "TABLE_NAME": ""},
                            {"TABLE_SCHEMA": "MARTS", "TABLE_NAME": "DIM_DATE"}])
    assert warehouse.list_database_tables(_source("SnowflakeConfig"), "DWH") == [
        Table("MARTS", "DIM_DATE")]


def test_a_connector_with_no_database_wide_statement_is_detectable_before_it_is_asked():
    # A caller reads this to decide whether to walk the database or ask the person for a schema. It
    # has to answer BEFORE the query, because the fallback is a different question to the person,
    # not a retry.
    assert walks_whole_database(_source("SnowflakeConfig")) is True
    assert walks_whole_database(_source("PostgreSQLConfig")) is True
    # BigQuery is the one connector with a cascade and no database-wide statement (#187).
    assert walks_whole_database(_source("BigQueryConfig")) is False
    # No dialect at all is also False, and for the same practical reason: there is nothing to walk.
    # What that caller gets when it falls back is the refusal below, which names the connector.
    assert walks_whole_database(_source("OracleConfig")) is False


def test_a_connector_with_no_dialect_at_all_keeps_the_refusal_that_names_it():
    with pytest.raises(ResourceUnavailable, match="Oracle"):
        _Warehouse([]).list_database_tables(_source("OracleConfig"), "DWH")
    with pytest.raises(ResourceUnavailable, match="Oracle"):
        FakeResourceProvider().list_database_tables(_source("OracleConfig"), "DWH")


def test_a_connector_that_can_be_walked_a_schema_at_a_time_and_not_whole_says_which():
    # Not [] — an empty list is what a database with no tables looks like, and a caller that cannot
    # tell those apart would report an empty warehouse instead of falling back to the cascade.
    for provider in (_Warehouse([]), FakeResourceProvider()):
        with pytest.raises(ResourceUnavailable, match="BigQuery"):
            provider.list_database_tables(_source("BigQueryConfig"), "")


def test_the_fake_walks_the_same_tree_its_cascade_walks():
    # The fake is how every caller above this seam is tested, so its walk has to agree with its own
    # cascade rather than hold a second, hand-written catalog that can drift out of step with it.
    fake = FakeResourceProvider()
    source = next(s for s in fake.list_data_sources() if s.id == "ds-dwh")
    walked = fake.list_database_tables(source, "DWH")
    assert Table("MARTS", "FCT_USAGE_DAILY") in walked
    assert Table("REPORTING", "V_ARR_WATERFALL") in walked
    per_schema = [Table(schema, name)
                  for schema in fake.list_schemas(source, "DWH")
                  for name in fake.list_tables(source, "DWH", schema)]
    ordered = sorted((t.schema, t.name) for t in per_schema)
    # In the order the real statement asks for — schema, then table — which the fake's per-schema
    # listing is not in: a caller that pins this order must not pass on the warehouse and fail on a
    # local run.
    assert [(t.schema, t.name) for t in walked] == ordered
    # `STAGING` is empty in the fixture, and an empty schema contributes rows to neither.
    assert not [t for t in walked if t.schema == "STAGING"]


# ---- Widened to the other seven connectors (#187) ------------------------------------------------

_WALKS = {name: d for name, d in SQL_DIALECTS.items() if d.database_tables}


def test_every_connector_sage_can_look_inside_either_walks_a_database_or_says_which_one_cannot():
    # The whole of #187 in one line: no connector with a cascade is left silently unable to answer a
    # search. The one that is named here is named because its form needs a region nothing supplies.
    declined = sorted(n for n, d in SQL_DIALECTS.items() if d.database_tables is None)
    assert declined == ["BigQueryConfig"]


def test_bigquery_is_declined_rather_than_assumed_to_match_the_ansi_form():
    # Its per-schema statement already says why the ANSI form cannot be borrowed: a BigQuery dataset
    # holds its OWN `INFORMATION_SCHEMA`, so there is no database-level view to sweep. The one
    # project-wide view is `region-us`.INFORMATION_SCHEMA.TABLES, and Domino's `BigQueryConfig`
    # carries only `project` — a guessed region is wrong for every project outside the US, which is
    # a predictable failure rather than the unverified-but-fair guess the other six statements are.
    bigquery = SQL_DIALECTS["BigQueryConfig"]
    assert "{schema}.INFORMATION_SCHEMA.TABLES" in bigquery.tables
    assert bigquery.database_tables is None
    for provider in (_Warehouse([]), FakeResourceProvider()):
        with pytest.raises(ResourceUnavailable, match="BigQuery"):
            provider.list_database_tables(_source("BigQueryConfig"), "")


def test_the_widening_is_four_statements_and_not_seven():
    # Written as three shared strings and Snowflake's own, so a correction to one reaches every
    # connector it was a correction for — the reason the aliases below the table are aliases.
    ansi = {SQL_DIALECTS[n].database_tables
            for n in ("SQLServerConfig", "SynapseConfig", "DatabricksConfig", "TrinoConfig")}
    postgres = {SQL_DIALECTS[n].database_tables
                for n in ("PostgreSQLConfig", "RedshiftConfig", "GreenplumConfig")}
    mysql = {SQL_DIALECTS[n].database_tables
             for n in ("MySQLConfig", "MariaDBConfig", "SingleStoreConfig", "ClickHouseConfig")}
    assert len(ansi) == len(postgres) == len(mysql) == 1
    assert len(ansi | postgres | mysql | {SQL_DIALECTS["SnowflakeConfig"].database_tables}) == 4


def test_every_walk_names_the_two_columns_the_reader_reads_and_the_order_it_groups_by():
    # `list_database_tables` reads `table_schema` and `table_name` off each row. A dialect that
    # projected either under another name would answer rows with a blank schema, which #182 keeps
    # rather than drops — so every table in the warehouse would group under one blank heading.
    for name, dialect in _WALKS.items():
        rendered = dialect.statement(dialect.database_tables, database="DWH")
        assert "AS table_schema" in rendered, name
        assert "AS table_name" in rendered, name
        # Schema first, because every reader of this groups by schema (ADR-0038).
        assert rendered.endswith("ORDER BY TABLE_SCHEMA, TABLE_NAME"), name


def test_no_walk_offers_the_stores_own_bookkeeping_as_a_candidate():
    # #182's ground, now held by all seven: a creator opening a schema picked it, and a search picks
    # none — so `INFORMATION_SCHEMA.USAGE_PRIVILEGES` would rank beside the marts on a question
    # about usage.
    for name, dialect in _WALKS.items():
        rendered = dialect.statement(dialect.database_tables, database="DWH")
        _, _, where = rendered.partition(" WHERE ")
        assert "information_schema" in where.lower(), name


def test_the_shared_ansi_walk_drops_both_spellings_of_the_bookkeeping_schema():
    # One statement serves four connectors that disagree about the case: SQL Server and Synapse
    # define the views as INFORMATION_SCHEMA, Databricks and Trino as information_schema. A
    # case-sensitive collation on either side lets the other spelling through, so both are named.
    for name in ("SQLServerConfig", "SynapseConfig", "DatabricksConfig", "TrinoConfig"):
        dialect = SQL_DIALECTS[name]
        rendered = dialect.statement(dialect.database_tables, database="DWH")
        assert "'INFORMATION_SCHEMA'" in rendered, name
        assert "'information_schema'" in rendered, name


def test_a_store_with_a_database_level_reads_the_view_inside_the_database_it_was_given():
    # Three levels: the view is per-database, so the walk has to be told which one — and it quotes
    # the name the way that store quotes identifiers, not the way Snowflake does.
    for name, dialect in _WALKS.items():
        if dialect.databases is None:
            continue
        rendered = dialect.statement(dialect.database_tables, database="DWH")
        assert f"FROM {dialect.ident('DWH')}.INFORMATION_SCHEMA.TABLES" in rendered, name


def test_a_store_with_no_database_level_leaves_no_empty_prefix_where_a_database_would_go():
    # The finding #182 left standing for this ticket: `{db}` renders to "" for a two-level store,
    # which would have left `FROM .INFORMATION_SCHEMA.TABLES` — a syntax error, and one no test
    # could reach while Snowflake was the only dialect with a statement.
    for name, dialect in _WALKS.items():
        if dialect.databases is not None:
            continue
        assert "{db}" not in dialect.database_tables, name
        rendered = dialect.statement(dialect.database_tables, database="")
        assert "FROM INFORMATION_SCHEMA.TABLES" in rendered, name


def test_the_two_level_families_drop_the_server_databases_that_are_not_anybodys_data():
    # Postgres keeps its `pg_%` catalogs out, the same way its own `schemas` statement does. The
    # MySQL family's one statement covers MariaDB, SingleStore and ClickHouse too, so it names
    # every one of their bookkeeping databases rather than only MySQL's.
    postgres = SQL_DIALECTS["PostgreSQLConfig"]
    assert "NOT LIKE 'pg_%'" in postgres.database_tables
    mysql = SQL_DIALECTS["MySQLConfig"].database_tables
    for bookkeeping in ("mysql", "performance_schema", "sys", "system", "cluster", "memsql"):
        assert f"'{bookkeeping}'" in mysql, bookkeeping


def test_a_two_level_store_is_walked_in_one_query_with_no_database_to_name():
    # The path the search takes for Postgres and MySQL: `list_databases` answers [], the caller
    # passes "", and the walk is still one query rather than a sweep of the schemas.
    warehouse = _Warehouse([{"table_schema": "public", "table_name": "orders"},
                            {"table_schema": "analytics", "table_name": "daily_revenue"}])
    assert warehouse.list_database_tables(_source("PostgreSQLConfig"), "") == [
        Table("public", "orders"), Table("analytics", "daily_revenue")]
    assert len(warehouse.sent) == 1
    assert "FROM INFORMATION_SCHEMA.TABLES" in warehouse.sent[0]


def test_a_three_level_store_is_walked_in_one_query_naming_the_database_it_was_given():
    # The same for the ANSI four, and with the upper-cased keys those stores answer with, so the
    # read path is exercised on a dialect that was never the one live-verified.
    warehouse = _Warehouse([{"TABLE_SCHEMA": "dbo", "TABLE_NAME": "policies"}])
    assert warehouse.list_database_tables(_source("SQLServerConfig"), "underwriting") == [
        Table("dbo", "policies")]
    assert len(warehouse.sent) == 1
    assert '"underwriting".INFORMATION_SCHEMA.TABLES' in warehouse.sent[0]


def test_a_store_with_a_database_level_that_lists_none_is_not_walked_on_an_empty_name():
    # The other half of the finding above, and the half only the widening reaches: `{db}` renders to
    # "" for a THREE-level store too, so a catalog list that comes back empty — a proxy user granted
    # nothing, a permissions-trimmed `sys.databases` — would send `FROM .INFORMATION_SCHEMA.TABLES`.
    # Nothing to walk is [], which produces no candidates and no card, not a syntax error.
    assert walkable_databases(_source("SQLServerConfig"), []) == []
    assert walkable_databases(_source("TrinoConfig"), []) == []
    # A store with no database level is the opposite case: [] is the only answer it can give, and
    # the walk still runs once, on the empty string the cascade passes at that level.
    assert walkable_databases(_source("PostgreSQLConfig"), []) == [""]
    assert walkable_databases(_source("MySQLConfig"), []) == [""]


def test_the_walk_does_not_spend_a_query_on_the_engines_own_catalogs():
    # One level up from the `INFORMATION_SCHEMA` filter, and reachable only because #187 gave these
    # two a walk: `SHOW CATALOGS` always answers `system` on Trino, so a search would offer
    # `system.runtime.queries` beside the marts and spend part of its database budget getting there.
    assert walkable_databases(_source("TrinoConfig"), ["system", "jmx", "hive"]) == ["hive"]
    assert walkable_databases(_source("DatabricksConfig"),
                              ["system", "samples", "main"]) == ["main"]
    # `hive_metastore` stays. It has no `information_schema` and will fail the walk, but a workspace
    # migrated to Unity Catalog keeps its legacy tables there — skipping it would answer a search by
    # hiding data, and a failed walk is the honest outcome of the two.
    assert "hive_metastore" in walkable_databases(_source("DatabricksConfig"),
                                                  ["hive_metastore", "main"])
    # `sys.databases` always answers with four of the engine's own, so a SQL Server holding ONE user
    # database lists five — over `_DATABASES_SEARCHED`, which would have refused the walk outright
    # and left the widening unable to fire on this connector at all.
    assert walkable_databases(_source("SQLServerConfig"),
                              ["master", "model", "msdb", "tempdb", "underwriting"]) == [
        "underwriting"]
    # Snowflake's own two, on the connector #182 verified: `SNOWFLAKE_SAMPLE_DATA` holds TPC-H, so a
    # question about customer data would otherwise rank its `CUSTOMER` against the marts.
    assert walkable_databases(_source("SnowflakeConfig"),
                              ["SNOWFLAKE", "SNOWFLAKE_SAMPLE_DATA", "DWH"]) == ["DWH"]
    # Trino's `tpch` answers `information_schema` perfectly well, which is what makes it worse than
    # `jmx` rather than better: its synthetic tables come back looking like real ones.
    assert walkable_databases(_source("TrinoConfig"), ["tpch", "tpcds", "hive"]) == ["hive"]


def test_every_connector_that_walks_names_the_databases_its_engine_ships_with():
    # The rule, rather than five spot checks: a store that lists its own bookkeeping at the outer
    # level and drops none of it spends the database budget on tables nobody asked about. Postgres
    # and MySQL are exempt because they have no outer level for the engine to put anything in.
    for name, dialect in _WALKS.items():
        if dialect.databases is None:
            continue
        assert dialect.bookkeeping_databases, name
