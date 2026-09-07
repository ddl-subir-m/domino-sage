"""The database-wide catalog walk (#182).

The cascade opens one level at a time because a creator opens one level at a time. A table SEARCH
asks a different question — "which of everything in here is the one" — and answering it a schema at
a time costs ~3s a schema, which is about 45 seconds on the 15-schema warehouse this was measured
against (ADR-0038). One query answers it in 3.84s.

No live warehouse is reachable from here and none is needed: the statement is asserted as data, and
both providers are exercised on stubs. What a live run already proved is the per-schema statement
this one is derived from — the filter dropped, the schema added to the projection — so the delta is
what these tests pin.
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
    assert walks_whole_database(_source("PostgreSQLConfig")) is False
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
        with pytest.raises(ResourceUnavailable, match="PostgreSQL"):
            provider.list_database_tables(_source("PostgreSQLConfig"), "")


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
