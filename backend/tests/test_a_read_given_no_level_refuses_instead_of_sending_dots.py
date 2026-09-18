"""A read that was told no database or schema must refuse, not build `..TABLE` (#404).

`statement` fills a level it was given nothing for with the empty string. So a dialect whose sample
spells `{db}.{schema}.{table}` and is handed neither builds `SELECT * FROM .."SALES"`, the store
rejects a name nobody can read back to a cause, and — before #399 — that rejection reached the
person as "could not reach ... Try again in a moment".

The repo named this defect twice before it was fixed, in `run.py` and beside `scope_for` in
`service.py`, and it stayed live both times. A described residual reads as handled, so this file
holds the reproduction instead of a sentence.

The levels are asked PER DIALECT on purpose. Snowflake's sample needs a database and Postgres's
does not, so a blanket "both levels required" would refuse reads that work today.
"""

from __future__ import annotations

import pytest

from sage.liveread.run import Turn, _scoped
from sage.resources.provider import (
    SQL_DIALECTS,
    DataSource,
    DominoResourceProvider,
    ScopeIncomplete,
    levels_missing,
)


def _snowflake() -> DataSource:
    return DataSource(id="ds1", name="Snowflake-Data-Warehouse", connector="Snowflake",
                      credential_type="Individual", connector_type="SnowflakeConfig")


def _postgres() -> DataSource:
    return DataSource(id="ds2", name="fpoblete-postgres-service-account", connector="PostgreSQL",
                      credential_type="Individual", connector_type="PostgreSQLConfig")


def _turn(scope: dict | None = None) -> Turn:
    return Turn(thread_id="t1", examples_dir="/tmp", scope_for=scope or {})


def test_a_bare_table_with_no_recorded_scope_resolves_to_nothing():
    """The reproduction. This is the state that produced the live 19:45:06 failure on #399."""
    _, database, schema, table, _ = _scoped({"source": "SNOW", "table": "SALES"}, _turn())

    assert (database, schema) == ("", ""), "the fallback that builds `..TABLE`"
    assert table == "SALES"


def test_the_dotted_name_the_model_usually_sends_is_unaffected():
    """Why this was intermittent rather than constant: the model normally spells the whole path,
    because every screen teaches that spelling, and the name then wins over the fallback."""
    _, database, schema, _, _ = _scoped(
        {"source": "SNOW", "table": "DWH.MARTS.SALES"}, _turn())

    assert (database, schema) == ("DWH", "MARTS")


def test_a_recorded_scope_also_fills_the_levels():
    _, database, schema, _, _ = _scoped(
        {"source": "SNOW", "table": "SALES"}, _turn({("SNOW", ""): ("DWH", "MARTS")}))

    assert (database, schema) == ("DWH", "MARTS")


@pytest.mark.parametrize("connector_type,expected", [
    ("SnowflakeConfig", ["database", "schema"]),
    ("SQLServerConfig", ["database", "schema"]),
    ("DatabricksConfig", ["database", "schema"]),
    # Two-level stores. A database is not a level these have, so demanding one would refuse a read
    # that works.
    ("PostgreSQLConfig", ["schema"]),
    ("RedshiftConfig", ["schema"]),
    ("BigQueryConfig", ["schema"]),
])
def test_each_dialect_is_asked_for_the_levels_its_own_statement_spells(connector_type, expected):
    assert levels_missing(SQL_DIALECTS[connector_type].sample, "", "") == expected


def test_a_level_that_was_supplied_is_not_asked_for_again():
    snow = SQL_DIALECTS["SnowflakeConfig"].sample
    assert levels_missing(snow, "", "MARTS") == ["database"]
    assert levels_missing(snow, "DWH", "MARTS") == []


def _sample(source, database, schema, monkeypatch):
    """`sample_rows` with the store hop replaced, so a statement that got built is visible."""
    sent = []
    provider = DominoResourceProvider.__new__(DominoResourceProvider)
    monkeypatch.setattr(DominoResourceProvider, "_query",
                        lambda self, src, sql: sent.append(sql), raising=False)
    return provider, sent


def test_a_read_missing_both_levels_refuses_and_names_them(monkeypatch):
    provider, sent = _sample(_snowflake(), "", "", monkeypatch)

    with pytest.raises(ScopeIncomplete) as caught:
        provider.sample_rows(_snowflake(), "", "", "SALES", 5)

    said = str(caught.value)
    assert "database and schema" in said, "say which levels, not just that one is missing"
    assert "SALES" in said
    assert "database.schema.table" in said, "the model can repair this itself"
    assert sent == [], "nothing may be sent to the store"


def test_the_refusal_names_only_the_level_that_is_missing(monkeypatch):
    provider, _ = _sample(_snowflake(), "", "MARTS", monkeypatch)

    with pytest.raises(ScopeIncomplete) as caught:
        provider.sample_rows(_snowflake(), "", "MARTS", "SALES", 5)

    assert "which database SALES is in" in str(caught.value)


def test_a_two_level_store_reads_with_no_database(monkeypatch):
    """The guard must not refuse Postgres, whose sample statement has no database level at all."""
    provider, sent = _sample(_postgres(), "", "public", monkeypatch)
    provider.sample_rows(_postgres(), "", "public", "SALES", 5)

    assert len(sent) == 1 and ".." not in sent[0]


def test_the_listing_route_refuses_rather_than_building_a_statement(monkeypatch):
    """Door two. `_cascade` turns a `ValueError` into a 400, which is what an empty level is:
    the panel only ever sends names the level above it returned."""
    provider, sent = _sample(_snowflake(), "", "", monkeypatch)

    with pytest.raises(ScopeIncomplete):
        provider.list_tables(_snowflake(), "", "")

    assert sent == []


def test_scope_incomplete_is_a_value_error_so_the_cascade_answers_400():
    assert issubclass(ScopeIncomplete, ValueError)


def test_the_refusal_reaches_the_model_as_a_sentence_not_an_exception():
    """Acceptance criterion 1, at the door the person actually hits.

    `_table_rows` catches `ScopeIncomplete` by its own type and hands back a refusal, so the model
    reads a sentence it can act on rather than the turn dying. Caught narrowly on purpose: a bare
    `except ValueError` here would also swallow a driver's own words, which is the rule
    `live_read_again` already states for `NoSuchRead`.
    """
    from sage.liveread.run import _table_rows

    def refusing_sample_rows(source, database, schema, table, limit):
        raise ScopeIncomplete("Sage does not know which database and schema SALES is in.")

    turn = Turn(thread_id="t1", examples_dir="/tmp",
                bound={"datasource": ("Snowflake-Data-Warehouse",)},
                source_for=lambda name: _snowflake(),
                sample_rows=refusing_sample_rows)
    read = _table_rows(turn, "Snowflake-Data-Warehouse", "", "", "SALES", 5)

    assert read.refused == "Sage does not know which database and schema SALES is in."
    assert not read.rows


def test_a_driver_raising_its_own_value_error_is_not_swallowed_as_a_refusal():
    """The other half of catching narrowly. A `ValueError` that is not ours must still travel, or
    this catch becomes the quiet failure the refusal was meant to replace."""
    from sage.liveread.run import _table_rows

    def exploding_sample_rows(source, database, schema, table, limit):
        raise ValueError("numpy: cannot convert this column")

    turn = Turn(thread_id="t1", examples_dir="/tmp",
                bound={"datasource": ("Snowflake-Data-Warehouse",)},
                source_for=lambda name: _snowflake(),
                sample_rows=exploding_sample_rows)

    with pytest.raises(ValueError, match="numpy"):
        _table_rows(turn, "Snowflake-Data-Warehouse", "", "", "SALES", 5)
