"""#557 live replay: discovery must return names without exposing stored customer rows."""

from __future__ import annotations

import json

import pytest

from sage.liveread import run
from sage.liveread.data_use import DataUse
from sage.resources.provider import DataSource

from .test_a_chat_turn_works_a_number_out_in_sql import FakeAnswer, turn_for


@pytest.mark.parametrize("sql, columns, row", [
    (("SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
      "WHERE TABLE_NAME ILIKE '%CASE%' ORDER BY TABLE_NAME"),
     ["TABLE_SCHEMA", "TABLE_NAME"], ["MARTS", "SFDC__CASE"]),
    ("SELECT TABLE_SCHEMA, TABLE_NAME, ROW_COUNT FROM DWH.INFORMATION_SCHEMA.TABLES",
     ["TABLE_SCHEMA", "TABLE_NAME", "ROW_COUNT"], ["MARTS", "SFDC__CASE", 3]),
    ('SELECT t."TABLE_NAME" AS name FROM "DWH"."INFORMATION_SCHEMA"."TABLES" t',
     ["name"], ["SFDC__CASE"]),
    (("SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, ORDINAL_POSITION, IS_NULLABLE "
      "FROM DWH.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'SFDC__CASE'"),
     ["TABLE_NAME", "COLUMN_NAME", "DATA_TYPE", "ORDINAL_POSITION", "IS_NULLABLE"],
     ["SFDC__CASE", "ACCOUNT_ID", "VARCHAR", 2, "YES"]),
    ("SELECT CATALOG_NAME, SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA",
     ["CATALOG_NAME", "SCHEMA_NAME"], ["DWH", "MARTS"]),
    (("SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME FROM SNOWFLAKE.ACCOUNT_USAGE.TABLES "
      "WHERE DELETED IS NULL"),
     ["TABLE_CATALOG", "TABLE_SCHEMA", "TABLE_NAME"], ["DWH", "MARTS", "SFDC__CASE"]),
    (("SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM SNOWFLAKE.ACCOUNT_USAGE.COLUMNS "
      "WHERE DELETED IS NULL"),
     ["TABLE_NAME", "COLUMN_NAME", "DATA_TYPE"], ["SFDC__CASE", "ACCOUNT_ID", "VARCHAR"]),
])
def test_discovery_names_reach_the_tool_and_the_model_receipt(tmp_path, sql, columns, row):
    data, journal = DataUse(), []
    turn, _ = turn_for(
        tmp_path, answer=FakeAnswer(columns, [row]), keep_rows=True,
        source_for=lambda _: DataSource("dwh", "DWH", "Snowflake", "Shared",
                                         connector_type="SnowflakeConfig"),
        record_data_use=lambda event, reply: data.record(event, reply, journal.append, "turn"),
    )
    said = run.perform("live_read_query", {"source": "DWH", "sql": sql}, turn)
    assert str(row[0]) in said and str(row[-1]) in said, said
    event, reply, _ = next(iter(data.operations.values()))
    assert event["selected_fields"] == columns
    assert reply["selected"] == {"rows": [row]}
    request, used = data.prepare({"messages": [
        {"role": "assistant", "tool_calls": [{"id": "call", "type": "function",
         "function": {"name": "live_read_query", "arguments": json.dumps(
             {"source": "DWH", "sql": sql})}}]},
        {"role": "tool", "tool_call_id": "call", "content": json.dumps(reply)},
    ]})
    assert used == {event["operation_id"]}
    assert json.loads(request["messages"][-1]["content"])["selected"] == {"rows": [row]}
    assert event["role"] == "working"
    assert sql not in json.dumps(journal), "the saved receipt keeps the SQL hash only"


@pytest.mark.parametrize("sql", [
    "SELECT TABLE_NAME FROM CUSTOMER_DATA",
    "SELECT TABLE_NAME FROM CUSTOMER_DATA AS INFORMATION_SCHEMA",
    "SELECT TABLE_NAME FROM CUSTOMER_DATA AS TABLES",
    'SELECT TABLE_NAME FROM "INFORMATION_SCHEMA.TABLES"',
    "SELECT TABLE_NAME FROM DWH.INFORMATION_SCHEMA_COPY.TABLES",
    "SELECT TABLE_NAME FROM DWH.ACCOUNT_USAGE.TABLES",
    "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.CUSTOMER_DATA",
    "SELECT TABLE_NAME FROM CUSTOMER_DATA -- INFORMATION_SCHEMA.TABLES",
    "SELECT TABLE_NAME FROM CUSTOMER_DATA WHERE NOTE = 'INFORMATION_SCHEMA.TABLES'",
    "SELECT c.TABLE_NAME FROM INFORMATION_SCHEMA.TABLES t JOIN CUSTOMER_DATA c ON 1=1",
    "WITH TABLES AS (SELECT TABLE_NAME FROM CUSTOMER_DATA) SELECT TABLE_NAME FROM TABLES",
    ("SELECT (SELECT EMAIL FROM CUSTOMER_DATA LIMIT 1) AS TABLE_NAME "
     "FROM INFORMATION_SCHEMA.TABLES"),
    "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES UNION SELECT EMAIL FROM CUSTOMER_DATA",
    "SELECT * FROM INFORMATION_SCHEMA.TABLES",
    "SELECT COMMENT AS TABLE_NAME FROM INFORMATION_SCHEMA.TABLES",
    "SELECT COLUMN_DEFAULT AS COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS",
    "SELECT LOWER(TABLE_NAME) FROM INFORMATION_SCHEMA.TABLES",
])
def test_metadata_spelling_does_not_disclose_an_ordinary_value(tmp_path, sql):
    turn, recorded = turn_for(tmp_path, answer=FakeAnswer(["TABLE_NAME"], [["private-value"]]),
                              keep_rows=True)
    said = run.perform("live_read_query", {"source": "DWH", "sql": sql}, turn)
    assert "private-value" not in said
    assert recorded[0][1]["selected"] == {}
    assert recorded[0][0]["selected_fields"] == []


def test_catalog_names_keep_the_value_budget(tmp_path):
    row = ["table-" + "x" * 20_000]
    turn, recorded = turn_for(tmp_path, answer=FakeAnswer(["TABLE_NAME"], [row]))
    said = run.perform("live_read_query", {
        "source": "DWH", "sql": "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES"}, turn)
    assert "too large to read here" in said
    assert row[0] not in said
    assert recorded[0][1]["selected"] == {}


def test_catalog_discovery_cannot_open_an_unattached_source(tmp_path):
    called = []
    turn, recorded = turn_for(tmp_path, bound={"datasource": ()},
                              run_statement=lambda *args, **kw: called.append(args))
    said = run.perform("live_read_query", {
        "source": "DWH", "sql": "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES"}, turn)
    assert "isn't in this conversation" in said
    assert not called and not recorded


def test_catalog_discovery_keeps_the_store_row_cap(tmp_path):
    limits = []

    def query(source, sql, *, limit):
        limits.append(limit)
        return FakeAnswer(["TABLE_NAME"], [["SFDC__CASE"]], truncated=True)

    turn, recorded = turn_for(tmp_path, run_statement=query)
    said = run.perform("live_read_query", {
        "source": "DWH", "sql": "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES"}, turn)
    assert limits == [500]
    assert "SFDC__CASE" in said and "there are more" in said
    assert recorded[0][0]["coverage"]["unfinished"] == 1


def test_snowflake_named_user_tables_on_another_connector_are_not_catalogs(tmp_path):
    turn, recorded = turn_for(
        tmp_path, answer=FakeAnswer(["TABLE_NAME"], [["private-value"]]),
        source_for=lambda _: DataSource("dwh", "DWH", "SQL Server", "Shared",
                                         connector_type="SQLServerConfig"),
    )
    said = run.perform("live_read_query", {
        "source": "DWH", "sql": "SELECT TABLE_NAME FROM SNOWFLAKE.ACCOUNT_USAGE.TABLES"}, turn)
    assert "private-value" not in said
    assert recorded[0][1]["selected"] == {}


@pytest.mark.parametrize("connector, sql", [
    ("PostgreSQLConfig", 'SELECT TABLE_NAME FROM "INFORMATION_SCHEMA"."TABLES"'),
    ("PostgreSQLConfig", 'SELECT TABLE_NAME FROM "Information_Schema".tables'),
    ("PostgreSQLConfig", 'SELECT TABLE_NAME FROM information_schema."TABLES"'),
    ("SnowflakeConfig", 'SELECT TABLE_NAME FROM "information_schema".TABLES'),
    ("SnowflakeConfig", 'SELECT TABLE_NAME FROM INFORMATION_SCHEMA."Tables"'),
    ("SnowflakeConfig", 'SELECT TABLE_NAME FROM "snowflake".ACCOUNT_USAGE.TABLES'),
    ("SnowflakeConfig", 'SELECT TABLE_NAME FROM SNOWFLAKE."account_usage".TABLES'),
])
def test_quoted_catalog_lookalikes_do_not_gain_disclosure(tmp_path, connector, sql):
    turn, recorded = turn_for(
        tmp_path, answer=FakeAnswer(["TABLE_NAME"], [["private-value"]]),
        source_for=lambda _: DataSource("dwh", "DWH", connector, "Shared",
                                         connector_type=connector),
    )
    said = run.perform("live_read_query", {"source": "DWH", "sql": sql}, turn)
    assert "private-value" not in said
    assert recorded[0][1]["selected"] == {}
