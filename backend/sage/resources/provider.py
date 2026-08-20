"""Resource provider — the Domino things a user can pick in the Resource Browser (#2, #5).

Deliberately NOT an extension of the Asset provider: an `Asset` is shaped around a Domino dataset
mounted into this container — mount path, freeform tags, snapshot ids, a list-files operation — and
none of that means anything for a model registration. Resource kinds with genuinely different
shapes get their own types rather than a widened `Asset`.

The LLM Alias: listing one takes TWO gateway calls, not one:

    GET {gateway}/v1/models    -> the model ids THIS caller may use (already permission-filtered)
    GET {gateway}/api/aliases  -> the metadata a picker needs (display_name, capabilities, costs)

The listing is the intersection, so a registration the caller holds no grant for is never presented
as available. The two sets really do differ: verified live on cloud-dogfood 2026-08-18 (see
DOMINO-PRIMITIVES.md), one gateway reported 12 registered aliases and 6 accessible ones.

The Model API (#8): one call, and it MUST carry a projectId. Unscoped, Domino answers
`403 "not authorized to view access configuration"` — that listing is deployment-wide and wants an
admin role a normal Sage user does not have. Scoped to a project it answers 200 (verified live,
DOMINO-PRIMITIVES.md), which is also the right question: a creator composes from what their own
project has deployed.

That call goes to the Domino API host, not to the gateway, so this one adapter speaks to two Domino
surfaces. It is still one object because the rail asks one question — "what can I use?" — and
splitting it would only move the joining somewhere less obvious.

The Data Source (#10): also two calls, and the FIRST one is a choice with a wrong answer.

    GET  {api}/api/datasource/v1/datasources          -> what this caller has permission on
    POST {api}/v4/datasource/authentication-status    -> whether they can actually open each one

Three listings exist and two of them are traps. `/v4/datasource/projects/{projectId}` answered
`200 []` live on cloud-dogfood for a user who had a working Snowflake source, because attaching a
Data Source to a project is optional bookkeeping in Domino, not what makes it usable — a picker built
on it shows an empty panel to someone who can already query. `/v4/datasource/dataSources/all`
answered `403` (it wants `ManageDataSourceExternalData`, an admin grant). The public listing,
`getAccessibleAndActiveDataSources`, returned the source with no project attachment at all
(verified 2026-08-18, DATA-SOURCES-RESEARCH.md), and its semantics — active, and the caller has
access — are exactly the panel's question.

That listing is pre-filtered, so it cannot answer "would this one work for me". Hence the second
call: a Data Source can be visible and still be unopenable, because an `Individual` credential is
entered per person and this caller may not have entered theirs. Readiness is asked, not inferred.

Auth is the existing Domino control-plane bearer path — a `token_provider` from
`sage.gateway.client` (the workspace sidecar in a container, a dgw_ PAT off-Domino). Nothing new.

Two adapters, as with assets:
  - DominoResourceProvider : real, against the LLM Gateway control plane (v2.0.11) + the Domino API
  - FakeResourceProvider   : in-memory, for local testing with no gateway
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol


class ResourceUnavailable(RuntimeError):
    """A Resource listing could not be produced. The message reaches the user unchanged, so it says
    what failed and what to do about it — and never carries a token or a response body."""


@dataclass(frozen=True)
class LlmAlias:
    id: str
    name: str  # what request["model"] must say — the alias is the only name Sage ever calls a model by
    display_name: str  # human label; the row's primary identifier
    description: str | None = None
    capabilities: list[str] = field(default_factory=list)  # "chat", "tools", "embeddings", …
    # `effective_costs` verbatim: {rate name -> number}. Live on Sage's gateway (2026-08-19) this is
    # a flat {"input": x, "output": y}, and the figures match the vendors' published per-1M-token USD
    # rates for sonnet (3/15), opus (5/25) and gpt-5.4 (2.5/15) — the API sends no unit, but the
    # figures are USD per 1M tokens. Nothing is normalised or relabelled here: six unrelated aliases all report
    # {1.0, 2.0}, which is the gateway falling back rather than a real price, and the gateway's own
    # Usage & cost dashboard stays the authority on what a call actually cost.
    costs: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelApi:
    """A deployed Domino endpoint serving a conventional model — it answers a prediction request,
    not a conversation. Nothing like an LLM Alias, which is why it gets its own type."""

    id: str
    name: str  # the row's primary identifier; a Model API has no separate display name
    description: str | None = None
    # The active version's deployment status, verbatim ("Running", "Stopped", …), or None when the
    # Model API has no active version at all. Kept as Domino's own word rather than reduced to a
    # boolean: "what could I compose?" is a different question from "would it answer right now",
    # and a creator reading "Stopped" knows which of the two they are looking at.
    status: str | None = None


# Connector types the rail offers, keyed on Domino's own `dataSourceType`. An ALLOWLIST rather than
# a denylist so an unfamiliar future connector hides instead of rendering as a broken row: Domino's
# enum already carries 33 values and grows without asking us, and the live deployment returned two
# kinds (`DatasetConfig`, `NetAppVolumeConfig`) that are not in the published enum at all.
#
# What is in: SQL and warehouse kinds. They share one access path (Arrow Flight) and one shape —
# query in, table out — which is the shape everything downstream of picking one assumes.
#
# What is deliberately out, and why:
#   - `DatasetConfig`, `NetAppVolumeConfig`  mount-shaped. Already surfaced as Assets, and 16 of the
#     22 rows live were these — listing them here would show the same thing twice under two mental
#     models, one of which (a mount) has no query at all.
#   - object stores (`S3Config`, `GCSConfig`, `ADLSConfig`, `AzureBlobStorageConfig`,
#     `GenericS3Config`, `TabularS3GlueConfig`) — a different protocol and a different question
#     ("which key?", not "which table?").
#   - vector databases (`PineconeConfig`, `QdrantConfig`) — a similarity search is not a query.
#   - `MongoDBConfig`, `PalantirConfig` — neither speaks SQL.
# Each is one line away from being offered, which is the point of the list being explicit.
SQL_CONNECTORS = frozenset({
    "BigQueryConfig", "ClickHouseConfig", "DB2Config", "DB2NativeConfig", "DatabricksConfig",
    "DruidConfig", "GenericJDBCConfig", "GreenplumConfig", "IgniteConfig", "MariaDBConfig",
    "MySQLConfig", "NetezzaConfig", "OracleConfig", "PostgreSQLConfig", "RedshiftConfig",
    "SAPHanaConfig", "SQLServerConfig", "SingleStoreConfig", "SnowflakeConfig", "SynapseConfig",
    "TeradataConfig", "TrinoConfig", "VerticaConfig",
})


@dataclass(frozen=True)
class DataSource:
    """A Domino connector to an external store the caller has permission on.

    `name` and `connector` are not interchangeable, and getting them the wrong way round is the one
    mistake that makes this panel useless. Live on cloud-dogfood two different Snowflake sources —
    `test` and `Snowflake-Data-Warehouse` — both reported `displayName: "Snowflake"`. So Domino's
    `displayName` is the CONNECTOR TYPE's label, not the instance's; `name` is the only field that
    tells two sources apart, and it is the row's primary identifier.
    """

    id: str
    name: str  # the source's own name — the row's primary identifier
    connector: str  # the connector type's label ("Snowflake"), shown as secondary
    # "Shared" | "Individual", verbatim. NOT a readiness signal and deliberately not folded into
    # `ready`: an `Individual` source is one the creator can query right now, in a build session that
    # runs as them. It only becomes a problem at publish time, where a Built App runs as its publisher
    # and would re-export that one person's access to every viewer — which is where ADR-0001 puts the
    # guard. The rail states the fact early so it is not a surprise later; it does not block on it.
    credential_type: str
    description: str | None = None
    # Whether Domino says this caller can actually open the source. `None` means Domino did not
    # answer the readiness question — not the same as "no", and rendered as its own state, because a
    # source the creator can see in Domino must not vanish from the rail just because one call to a
    # private endpoint failed.
    ready: bool | None = None
    # Domino's raw `dataSourceType` ("SnowflakeConfig"), kept alongside the label rather than instead
    # of it. The label is what a creator reads and Domino writes it for people; this is what decides
    # which introspection SQL the cascade sends, and a label cannot: two deployments could both say
    # "Snowflake" while only one type string is the one the allowlist and the dialect table key on.
    # Appended rather than placed next to `connector` so the positional constructor calls that
    # already exist keep meaning what they meant.
    connector_type: str = ""
    # What the source's own `config` already pins, if anything, so the cascade can open on it instead
    # of asking a question Domino has already answered. Both were absent on the live warehouse — the
    # session opened with `DB=None SCHEMA=None` — so "no default" is the ordinary case, and it is the
    # case the cascade exists for.
    default_database: str | None = None
    default_schema: str | None = None


# ---- What is inside a Data Source: one level at a time (#11) -------------------------------------
# There is no introspection endpoint to call. The Data Source proxy takes SQL in and gives a table
# back — that is the whole interface (Arrow Flight over gRPC, per DATA-SOURCES-RESEARCH.md Q2) — so
# "what is in here" is itself a query, and the query is dialect-specific.


@dataclass(frozen=True)
class SqlDialect:
    """How to enumerate one connector family's databases, schemas and tables.

    `databases` is `None` for a store whose connection is already inside one database — Postgres and
    MySQL among them — where there is no outer level to offer and a picker that opened on one would
    show a list of one, or an error. Such a store cascades schema -> table, and its Bindings record
    no database, which is a fact about the connector rather than a missing answer.

    `verified` says whether these statements have been run against a live store of this kind. Only
    Snowflake's have (DATA-SOURCES-RESEARCH.md Addendum 2, timed at 2.3s / 3.5s / 2.9s). The rest are
    the standard `information_schema` shape and are honest guesses. That is affordable only because
    of how they fail: a wrong statement comes back as the store's own error on that one level, which
    reads as "this connector said <x>", not as an empty schema — the failure mode that would have the
    creator believe an answer.

    Statements are formatted with `{db}` and `{schema}` (validated, quoted identifiers) and
    `{schema_lit}` (the same name bare, for the string comparisons `information_schema` needs).
    """

    databases: str | None
    schemas: str
    tables: str
    verified: bool = False
    quote: str = '"'  # `"` everywhere except the stores where it means a string literal by default

    def ident(self, name: str) -> str:
        """One validated identifier, quoted. Quoted only to preserve case — the validation has
        already ruled out everything quoting would otherwise be protecting against."""
        return f"{self.quote}{safe_identifier(name)}{self.quote}"

    def statement(self, template: str, database: str = "", schema: str = "") -> str:
        return template.format(
            db=self.ident(database) if database else "",
            schema=self.ident(schema) if schema else "",
            schema_lit=safe_identifier(schema) if schema else "",
        )


# Spelled in upper case, which is the only spelling that works everywhere: SQL Server defines these
# views as INFORMATION_SCHEMA and a case-sensitive collation rejects the lower-case form, while every
# store that folds unquoted identifiers accepts either. The string LITERALS stay lower case, because
# Postgres's own schema names are lower case and those are values, not identifiers.
_ANSI_SCHEMAS = ("SELECT SCHEMA_NAME AS name FROM {db}.INFORMATION_SCHEMA.SCHEMATA "
                 "ORDER BY SCHEMA_NAME")
_ANSI_TABLES = ("SELECT TABLE_NAME AS name FROM {db}.INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA = '{schema_lit}' ORDER BY TABLE_NAME")

# Keyed on `dataSourceType`, and a subset of SQL_CONNECTORS on purpose: a connector Sage can list but
# cannot look inside still belongs in the panel, because recording the dependency is worth something
# on its own (#6) and the alternative is hiding a source the creator can see in Domino. What it does
# not get is a cascade that opens on an error.
#
# Left out, each one line from being added: DB2, Druid, GenericJDBC, Ignite, Netezza, Oracle, SAP
# HANA, Teradata and Vertica. Not oversights — none of them serves the ANSI `information_schema`
# views this table leans on, so an entry for them would be a guess with nothing behind it, and
# `dialect_for` says so by name instead.
SQL_DIALECTS: dict[str, SqlDialect] = {
    # VERIFIED live against `Snowflake-Data-Warehouse` on cloud-dogfood. `SHOW` rather than
    # `information_schema` at the top two levels because an unqualified `information_schema` query
    # fails outright on a session with no current database, which is exactly the session Domino opens.
    "SnowflakeConfig": SqlDialect(
        databases="SHOW DATABASES",
        schemas="SHOW SCHEMAS IN DATABASE {db}",
        tables=("SELECT TABLE_NAME AS name FROM {db}.INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA = '{schema_lit}' ORDER BY TABLE_NAME"),
        verified=True,
    ),
    # Three levels: `sys.databases` is cross-database on one connection, unlike Postgres.
    "SQLServerConfig": SqlDialect("SELECT name FROM sys.databases ORDER BY name",
                                  _ANSI_SCHEMAS, _ANSI_TABLES),
    "SynapseConfig": SqlDialect("SELECT name FROM sys.databases ORDER BY name",
                                _ANSI_SCHEMAS, _ANSI_TABLES),
    # Two levels. A Postgres connection is bound to one database and cannot read another's catalog,
    # so listing the others would offer choices that then fail. `pg_%` and `information_schema` are
    # dropped: they are the server's own bookkeeping, never what an app was built to read.
    "PostgreSQLConfig": SqlDialect(
        None,
        ("SELECT SCHEMA_NAME AS name FROM INFORMATION_SCHEMA.SCHEMATA "
         "WHERE SCHEMA_NAME NOT LIKE 'pg_%' AND SCHEMA_NAME <> 'information_schema' "
         "ORDER BY SCHEMA_NAME"),
        ("SELECT TABLE_NAME AS name FROM INFORMATION_SCHEMA.TABLES "
         "WHERE TABLE_SCHEMA = '{schema_lit}' ORDER BY TABLE_NAME"),
    ),
    # Two levels each, and in MySQL's family "database" and "schema" are one thing — so the single
    # namespace level is offered as the schema, which is the level the Binding records.
    "MySQLConfig": SqlDialect(
        None,
        "SELECT SCHEMA_NAME AS name FROM INFORMATION_SCHEMA.SCHEMATA ORDER BY SCHEMA_NAME",
        ("SELECT TABLE_NAME AS name FROM INFORMATION_SCHEMA.TABLES "
         "WHERE TABLE_SCHEMA = '{schema_lit}' ORDER BY TABLE_NAME"),
        quote="`",
    ),
    # Catalogs are the outer level on both, and `SHOW` is how each names them.
    "DatabricksConfig": SqlDialect("SHOW CATALOGS", _ANSI_SCHEMAS, _ANSI_TABLES, quote="`"),
    "TrinoConfig": SqlDialect("SHOW CATALOGS", _ANSI_SCHEMAS, _ANSI_TABLES),
    # Two levels. A BigQuery dataset holds its own `INFORMATION_SCHEMA`, so the tables view is
    # qualified by the schema rather than filtered on it.
    "BigQueryConfig": SqlDialect(
        None,
        "SELECT SCHEMA_NAME AS name FROM INFORMATION_SCHEMA.SCHEMATA ORDER BY SCHEMA_NAME",
        "SELECT TABLE_NAME AS name FROM {schema}.INFORMATION_SCHEMA.TABLES ORDER BY TABLE_NAME",
        quote="`",
    ),
}
# Same statements, same shape, different type strings. Written as aliases rather than repeated so a
# correction to one reaches every connector it was a correction for.
SQL_DIALECTS["RedshiftConfig"] = SQL_DIALECTS["PostgreSQLConfig"]
SQL_DIALECTS["GreenplumConfig"] = SQL_DIALECTS["PostgreSQLConfig"]
SQL_DIALECTS["MariaDBConfig"] = SQL_DIALECTS["MySQLConfig"]
SQL_DIALECTS["SingleStoreConfig"] = SQL_DIALECTS["MySQLConfig"]
SQL_DIALECTS["ClickHouseConfig"] = SQL_DIALECTS["MySQLConfig"]

_IDENTIFIER = re.compile(r"[A-Za-z0-9_$]+")


def safe_identifier(name: str) -> str:
    """One database, schema or table name, or a refusal.

    These names come back from the browser, so they reach SQL from outside Sage, and the credential
    behind that SQL is a service account that reads the entire company warehouse (`DOMINO` /
    `APP_ROLE_DOMINO` over `DWH`, verified). So this is an allowlist of the characters a name may
    hold, not an escape: anything else is refused rather than quoted, because a refusal cannot be got
    subtly wrong and an escape can.

    What that costs is a store using quoted, spaced or non-ASCII identifiers. Every name on the live
    warehouse passes, and one that does not says so rather than being sent.
    """
    if not _IDENTIFIER.fullmatch(name or ""):
        raise ValueError(
            f"Sage will not send {name[:60]!r} to a database as a name. A database, schema or table "
            "name may hold letters, digits, underscores and $ only."
        )
    return name


def dialect_for(source: DataSource) -> SqlDialect:
    """The introspection SQL for one source, or a refusal naming the connector."""
    dialect = SQL_DIALECTS.get(source.connector_type)
    if dialect is None:
        known = source.connector or source.connector_type or "this kind of"
        raise ResourceUnavailable(
            f"Sage cannot list what is inside a {known} Data Source yet, so it cannot offer its "
            "databases and schemas. You can still record that the app uses this Data Source."
        )
    return dialect


def cascade_levels(source: DataSource) -> list[str]:
    """The levels this source can offer, outermost first, or [] when Sage has no dialect for it.

    The panel reads this rather than working it out from a connector name, and it is what tells a
    two-level store apart from a broken one: `["schema", "table"]` means there is nothing above the
    schema, where `[]` means Sage does not know how to look at all.
    """
    dialect = SQL_DIALECTS.get(source.connector_type)
    if dialect is None:
        return []
    return (["database"] if dialect.databases else []) + ["schema", "table"]


def name_column(frame: Any) -> list[str]:
    """The names out of one introspection result.

    Every statement above either selects its column as `name` or is a `SHOW`, whose name column
    Snowflake also calls `name` — so `name` is the rule and the first column is the fallback, which
    is what carries `SHOW CATALOGS` (it answers with `catalog`). Matched case-insensitively because
    `AS name` comes back as `NAME` from the stores that upper-case unquoted identifiers.
    """
    columns = list(getattr(frame, "columns", []))
    if not columns:
        return []
    picked = next((c for c in columns if str(c).lower() == "name"), columns[0])
    return [str(v) for v in frame[picked].tolist() if str(v)]


_SECRET_SHAPED = re.compile(r"[A-Za-z0-9_\-]{32,}")


def readable_error(exc: Exception, limit: int = 300) -> str:
    """A store's own failure, in a form that can be shown.

    The message has to reach the creator — it is the only thing that makes an unverified dialect fail
    honestly instead of looking like an empty schema. But `DataSourceClient.__repr__` prints its
    api_key in plaintext (recorded in `spikes/domino-probes/snowflake_query_probe.py`, which had to
    avoid printing the client for that reason), so an exception holding a client would carry the key
    here. Any run of 32 or more identifier characters is replaced — the injected `DOMINO_USER_API_KEY`
    is 64 — and the text is collapsed and cut, because a driver traceback in a side rail is not a
    message anyone reads.
    """
    text = _SECRET_SHAPED.sub("[redacted]", " ".join(str(exc).split()))
    return f"{type(exc).__name__}: {text[:limit]}" if text else type(exc).__name__


class ResourceProvider(Protocol):
    def list_llm_aliases(self) -> list[LlmAlias]: ...

    # Takes the project explicitly, as the asset provider's list_datasets(project_id) does: the
    # orchestrator owns which project this builder is bound to, and the provider stays a client.
    def list_model_apis(self, project_id: str | None) -> list[ModelApi]: ...

    # No project argument, unlike the two above, and that asymmetry is the finding: a Data Source is
    # permission-scoped to the person, not the project.
    def list_data_sources(self) -> list[DataSource]: ...

    # The cascade takes the resolved row, not an id, so a caller cannot hand in a connector type of
    # its own and choose which SQL gets sent. `database` is "" for a store with no database level.
    def list_databases(self, source: DataSource) -> list[str]: ...

    def list_schemas(self, source: DataSource, database: str) -> list[str]: ...

    def list_tables(self, source: DataSource, database: str, schema: str) -> list[str]: ...


def records_of(payload: Any) -> list[dict]:
    """The record list out of a gateway payload.

    One helper for both calls because they disagree: `/v1/models` follows the OpenAI convention
    (`{"object": "list", "data": [...]}`) while `/api/aliases` returned a bare array. `items` is
    accepted too, as the probe that mapped these routes did (spikes/domino-probes).
    """
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("data") or payload.get("items") or []
    else:
        items = []
    return [r for r in items if isinstance(r, dict)]


def accessible_ids(models_payload: Any) -> set[str]:
    """Model ids from a `/v1/models` body — the set this caller is permitted to call."""
    return {str(r["id"]) for r in records_of(models_payload) if r.get("id")}


def parse_capabilities(raw: Any) -> list[str]:
    """Capability modes as a list of strings. Guarded against a bare string, which would otherwise
    iterate into one chip per character."""
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, str)]


def parse_costs(raw: Any) -> dict[str, float]:
    """`effective_costs` as a flat {rate name -> number} map — the shape seen live.

    Keeps the numeric entries and drops everything else rather than guessing at a nesting: a row
    saying no rate was reported is honest, one showing an invented number is not. `bool` is excluded
    because it is an `int` in Python, so a flag would otherwise price at 1.
    """
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): float(v)
        for k, v in raw.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }


def join_aliases(accessible: set[str], records: list[dict]) -> list[LlmAlias]:
    """Intersect the accessible model ids with the alias metadata records.

    Matched on alias `name` OR `id`: `/v1/models` reports the name a call must use, which is the
    alias name on every gateway we have seen, but the control plane keys on the id and the recipe
    this follows joins on either.

    An accessible id with NO metadata record still gets a row, carrying only its name. Dropping it
    would have the panel deny a model the caller can actually use, and `/v1/models` is the authority
    on availability — a thin row is a smaller lie than a missing one.
    """
    out: list[LlmAlias] = []
    claimed: set[str] = set()
    for rec in records:
        name = str(rec.get("name") or "")
        rid = str(rec.get("id") or "")
        key = name if name in accessible else (rid if rid in accessible else "")
        if not key:
            continue
        claimed.add(key)
        out.append(
            LlmAlias(
                id=rid or name,
                name=name or rid,
                display_name=str(rec.get("display_name") or name or rid),
                description=str(rec["description"]) if rec.get("description") else None,
                capabilities=parse_capabilities(rec.get("capabilities")),
                costs=parse_costs(rec.get("effective_costs")),
            )
        )
    for extra in sorted(accessible - claimed):
        out.append(LlmAlias(id=extra, name=extra, display_name=extra))
    return out


def parse_model_apis(payload: Any) -> list[ModelApi]:
    """Model API rows out of a `/api/modelServing/v1/modelApis` body.

    Archived ones are dropped rather than shown greyed out. Archiving in Domino is how a Model API is
    retired, so it is not an unusable-but-relevant Resource — it is one nobody would choose, and
    padding the list with retired endpoints makes the live ones harder to find.

    A record with no name is dropped too: the name IS the row's identifier here, and a row a creator
    cannot read is not a row they can pick.
    """
    out: list[ModelApi] = []
    for rec in records_of(payload):
        if rec.get("isArchived"):
            continue
        name = str(rec.get("name") or "")
        if not name:
            continue
        deployment = (rec.get("activeVersion") or {}).get("deployment") or {}
        status = deployment.get("status")
        out.append(
            ModelApi(
                id=str(rec.get("id") or ""),
                name=name,
                description=str(rec["description"]) if rec.get("description") else None,
                status=str(status) if status else None,
            )
        )
    return out


def connector_label(record: dict) -> str:
    """The connector type's human label for a Data Source record.

    Domino's own `displayName` is that label (see `DataSource`), so it is used as-is rather than
    mapped through a table this repo would then have to keep in step with 33 connector types. When it
    is missing, the raw type reads well enough with its suffix dropped: `SnowflakeConfig` ->
    `Snowflake`. A row without a type label is not worth refusing.
    """
    label = str(record.get("displayName") or "").strip()
    return label or str(record.get("dataSourceType") or "").removesuffix("Config")


def parse_data_sources(payload: Any) -> list[DataSource]:
    """Data Source rows out of a `/api/datasource/v1/datasources` body, filtered to SQL kinds.

    The envelope names its list `dataSources`, which `records_of` does not know about, so that is
    read here. Rows are returned with `ready` unset — readiness is a second call.

    A record with no name is dropped, as a Model API's is: the name is the row's only identifier, so
    a nameless row is not one a creator could tell apart from another.
    """
    items = payload.get("dataSources") if isinstance(payload, dict) else None
    records = records_of(items if items is not None else payload)
    out: list[DataSource] = []
    for rec in records:
        if str(rec.get("dataSourceType") or "") not in SQL_CONNECTORS:
            continue
        name = str(rec.get("name") or "")
        if not name:
            continue
        out.append(
            DataSource(
                id=str(rec.get("id") or ""),
                name=name,
                connector=connector_label(rec),
                credential_type=str(rec.get("credentialType") or ""),
                description=str(rec["description"]) if rec.get("description") else None,
                connector_type=str(rec.get("dataSourceType") or ""),
                default_database=config_value(rec, "database", "databasename", "catalog"),
                default_schema=config_value(rec, "schema", "schemaname", "dataset"),
            )
        )
    return out


def config_value(record: dict, *keys: str) -> str | None:
    """A default database or schema out of a Data Source's `config` map, if it names one.

    `config` is a free map of string to string in the public spec (its whole example is
    `{"host": "example-host.com"}`) and its keys are not enumerated anywhere, so this looks for the
    ones a SQL connector would plausibly use, accepts that none of them is there, and never treats
    absence as a fault. On the live warehouse both were absent, which is the finding the cascade was
    built for rather than a gap in it.

    Matched case-insensitively because the same map has been seen with camelCase keys, and a default
    that exists but is spelled `databaseName` would otherwise cost the creator a level of clicking
    for no reason.
    """
    config = record.get("config")
    if not isinstance(config, dict):
        return None
    lowered = {str(k).lower(): v for k, v in config.items()}
    for key in keys:
        value = lowered.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def merge_readiness(sources: list[DataSource], statuses: Any) -> list[DataSource]:
    """Attach `POST /v4/datasource/authentication-status`'s answer to the rows it was asked about.

    The endpoint answers a bare array of booleans with no ids in it, so the only thing tying an answer
    to a source is its POSITION in the list that was sent. Positional-in-request-order is verified
    live (2026-08-20, DATA-SOURCES-RESEARCH.md Addendum 3), but it is still a contract worth
    distrusting, because nothing in the response would reveal the day it stops holding: if
    the array is not a list of exactly the right length, every row keeps `ready=None` rather than
    being paired off against a shorter answer. A row labelled unusable because a boolean slid by one
    is worse than a row that admits Domino did not say.
    """
    if not isinstance(statuses, list) or len(statuses) != len(sources):
        return sources
    return [
        replace(src, ready=bool(ok)) if isinstance(ok, bool) else src
        for src, ok in zip(sources, statuses)
    ]


class DominoResourceProvider:
    """Resources from Domino: LLM Aliases from the LLM Gateway, Model APIs from the Domino API.

    `gateway_base_url` is the same OpenAI base Sage already routes model calls to (ending in `/v1`);
    the gateway's control plane sits at its root, so both alias calls come off one URL that is
    already configured and there is nothing new to set up.

    `api_host` is the Domino API (`DOMINO_API_HOST`), which is where Model APIs and Data Sources live
    — a different service from the gateway, so it carries its own bearer, exactly as the asset
    provider does. Left unset, both report as unavailable instead of as an empty list: Sage that
    cannot ask has not learned the project has none.
    """

    _PAGE = 100
    _MAX_PAGES = 50  # backstop against a non-terminating pager, as the asset provider has

    def __init__(
        self,
        gateway_base_url: str,
        token_provider: Callable[[], str],
        timeout_s: float = 20.0,
        api_host: str = "",
        api_token_provider: Callable[[], str] | None = None,
    ) -> None:
        self._root = gateway_base_url.rstrip("/").removesuffix("/v1").rstrip("/")
        self._token_provider = token_provider
        self._timeout_s = timeout_s
        self._api_host = api_host.rstrip("/")
        self._api_token_provider = api_token_provider or token_provider

    def list_llm_aliases(self) -> list[LlmAlias]:
        models = self._get("/v1/models")  # accessible set, already filtered for this caller
        aliases = self._get("/api/aliases")  # display name, capabilities, cost
        return join_aliases(accessible_ids(models), records_of(aliases))

    def list_model_apis(self, project_id: str | None) -> list[ModelApi]:
        """Model APIs deployed in ONE project. The scope is not a filter for convenience — the
        unscoped listing is an admin surface and 403s for a normal user, so a call without a project
        is a call that cannot succeed and is better refused here than sent."""
        if not self._api_host or not project_id:
            raise ResourceUnavailable(
                "Sage lists Model APIs from the Domino project it runs in, and it is not running in "
                "one, so it cannot tell whether this project has any."
            )
        path = "/api/modelServing/v1/modelApis"
        out: list[ModelApi] = []
        offset = 0
        for _ in range(self._MAX_PAGES):
            payload = self._get(
                path,
                root=self._api_host,
                service="The Domino API",
                token_provider=self._api_token_provider,
                params={"projectId": project_id, "offset": offset, "limit": self._PAGE},
            )
            page = records_of(payload)
            out += parse_model_apis(page)
            meta = payload.get("metadata") if isinstance(payload, dict) else None
            total = ((meta or {}).get("pagination") or {}).get("totalCount")
            offset += self._PAGE
            if not page or (total is not None and offset >= total):
                break
        return out

    def list_data_sources(self) -> list[DataSource]:
        """Data Sources this caller has permission on, SQL kinds only, with readiness asked for.

        No project scope, deliberately: the live probe that settled this found the project-scoped
        listing answering `200 []` for a user with a working Snowflake source, because attaching a
        source to a project is optional bookkeeping in Domino. Permission is what decides usability,
        and the public listing is keyed on it.
        """
        if not self._api_host:
            raise ResourceUnavailable(
                "Sage lists Data Sources from the Domino API, and it is not configured to reach one, "
                "so it cannot tell which Data Sources you have."
            )
        path = "/api/datasource/v1/datasources"
        rows: list[DataSource] = []
        offset = 0
        for _ in range(self._MAX_PAGES):
            payload = self._get(
                path,
                root=self._api_host,
                service="The Domino API",
                token_provider=self._api_token_provider,
                params={"offset": offset, "limit": self._PAGE},
            )
            envelope = payload.get("dataSources") if isinstance(payload, dict) else payload
            rows += parse_data_sources(payload)
            meta = payload.get("metadata") if isinstance(payload, dict) else None
            total = ((meta or {}).get("pagination") or {}).get("totalCount")
            offset += self._PAGE
            # Counted on the RAW page, not on the rows that survived the allowlist: a page made
            # entirely of dataset-backed sources filters down to nothing, and stopping there would
            # hide every SQL source that came after it.
            if not records_of(envelope) or (total is not None and offset >= total):
                break
        return merge_readiness(rows, self._authentication_status([r.id for r in rows]))

    # ---- The cascade: three levels, each its own call, each on being opened (#11) ----
    # Not one call returning a tree. Each level costs seconds against the live warehouse (2.3s, 3.5s,
    # 2.9s measured), so a prefetched tree would spend all three before the creator had looked at the
    # first, and a warehouse with 30 schemas would spend thirty more.

    def list_databases(self, source: DataSource) -> list[str]:
        """The databases inside one source, or [] when its connector has no level above the schema.

        [] here is not an empty state to render as "nothing found": `cascade_levels` is what says
        whether this level exists at all, and a two-level store answers [] because there was never a
        question, not because the answer was nothing.
        """
        dialect = dialect_for(source)
        if dialect.databases is None:
            return []
        return self._introspect(source, dialect.databases)

    def list_schemas(self, source: DataSource, database: str) -> list[str]:
        dialect = dialect_for(source)
        return self._introspect(source, dialect.statement(dialect.schemas, database=database))

    def list_tables(self, source: DataSource, database: str, schema: str) -> list[str]:
        dialect = dialect_for(source)
        return self._introspect(
            source, dialect.statement(dialect.tables, database=database, schema=schema))

    def _introspect(self, source: DataSource, sql: str) -> list[str]:
        """Run one read-only introspection statement against a Data Source and return the names.

        `domino_data` rather than HTTP, unlike everything else in this class: the SQL path is Arrow
        Flight over gRPC to `datasource-proxy`, which is where the store's credentials live, and there
        is no REST equivalent to reach instead. The import is local to this method for the reason
        `httpx`'s is — the package is preinstalled in the Sage Environment (verified, 6.7.4) but is
        not a test dependency, so a machine without it has to be able to run everything else.

        Resolved by NAME, not by id: `get_datasource` takes the name, verified live, and a source
        resolves with no project attachment. The name arrives from `list_data_sources`, so it is
        Domino's own, not a caller's.
        """
        try:
            from domino_data.data_sources import DataSourceClient
        except ImportError as e:
            raise ResourceUnavailable(
                "Sage reads a Data Source's contents through the Domino data library, which is not "
                "installed here. Data Sources will still list, but Sage cannot look inside one."
            ) from e
        try:
            client = DataSourceClient()
            frame = client.get_datasource(source.name).query(sql).to_pandas()
        except Exception as e:
            # The store's own words, scrubbed. Naming the source matters because the creator is
            # looking at a list of them, and the connector's own error is the only signal that
            # separates "Sage sent the wrong SQL for this connector" from "this schema is empty".
            raise ResourceUnavailable(f"{source.name} did not answer: {readable_error(e)}") from e
        return name_column(frame)

    def _authentication_status(self, ids: list[str]) -> Any:
        """Whether the caller can open each of `ids`, positionally. `None` when Domino did not say.

        A private endpoint — there is no public equivalent for this question, which is the same
        reason `sage.provision.domino` reaches for `/v4` for workspace lifecycle. Because it is
        private it is also the least certain call in this file, so a failure here degrades the panel
        to "readiness unknown" rather than failing the listing that already succeeded. Losing the
        readiness chip is a small lie by omission; hiding Data Sources the creator can see in Domino
        is the empty-panel dead end this whole listing was chosen to avoid.
        """
        if not ids:
            return None
        try:
            return self._post(
                "/v4/datasource/authentication-status",
                {"dataSourceIds": ids},
                root=self._api_host,
                service="The Domino API",
                token_provider=self._api_token_provider,
            )
        except ResourceUnavailable:
            return None

    def _get(
        self,
        path: str,
        *,
        root: str | None = None,
        service: str = "The LLM Gateway",
        token_provider: Callable[[], str] | None = None,
        params: dict | None = None,
    ) -> Any:
        return self._send("GET", path, root=root, service=service,
                          token_provider=token_provider, params=params)

    def _post(
        self,
        path: str,
        body: dict,
        *,
        root: str | None = None,
        service: str = "The LLM Gateway",
        token_provider: Callable[[], str] | None = None,
    ) -> Any:
        return self._send("POST", path, root=root, service=service,
                          token_provider=token_provider, json_body=body)

    def _send(
        self,
        method: str,
        path: str,
        *,
        root: str | None,
        service: str,
        token_provider: Callable[[], str] | None,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> Any:
        import httpx  # local import so tests never need it on the path they don't take

        token = (token_provider or self._token_provider)()
        try:
            r = httpx.request(
                method,
                (self._root if root is None else root) + path,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                json=json_body,
                timeout=self._timeout_s,
            )
        except Exception as e:
            raise ResourceUnavailable(
                f"{service} didn't answer at {path} ({type(e).__name__}). "
                "Resources will be listed once it responds."
            ) from e
        if r.status_code >= 400:
            raise ResourceUnavailable(f"{service} answered {r.status_code} at {path}.")
        # An unauthenticated call to the gateway returns 200 carrying a Keycloak LOGIN PAGE, so the
        # status is not proof of an answer (verified — DOMINO-PRIMITIVES.md). Inspect the body.
        try:
            return r.json()
        except ValueError as e:
            raise ResourceUnavailable(
                f"{service} returned a non-JSON body at {path}. That is what a signed-out "
                "session looks like, so this builder's token for it may have expired."
            ) from e


# Mirrors what Sage's own gateway actually returns (probed 2026-08-19), the Domino-hosted sovereign
# alias included, so the rail can be exercised locally with no gateway. Kept faithful rather than
# tidy: every real record carries `streaming` and `responses` alongside the capabilities that
# actually tell aliases apart, and several report the gateway's fallback {1.0, 2.0} rate.
_FAKE_ALIASES = (
    LlmAlias("f-gpt54", "gpt-5.4", "gpt-5.4", "gpt-5.4",
             ["chat", "tools", "responses", "streaming", "vision"], {"input": 2.5, "output": 15.0}),
    LlmAlias("f-sonnet", "sonnet", "Claude Sonnet 4.6", None,
             ["chat", "responses", "tools", "streaming"], {"input": 3.0, "output": 15.0}),
    LlmAlias("f-opus", "opus", "Claude Opus 4.6", None,
             ["chat", "streaming", "responses", "tools"], {"input": 5.0, "output": 25.0}),
    LlmAlias("f-qwen3c", "bedrock-qwen3-coder", "bedrock-qwen3-coder", None,
             ["chat", "streaming", "tools", "responses"], {"input": 1.0, "output": 2.0}),
    LlmAlias("f-qwen25", "qwen-2-5", "Qwen 2.5 (Domino-hosted)",
             "Runs inside Domino, so calls never leave the platform.",
             ["chat", "tools"], {"input": 1.0, "output": 2.0}),
    LlmAlias("f-embed", "text-embedding-3-small", "Text Embedding 3 Small",
             "Turns text into vectors. Not a chat model.", ["embeddings"], {}),
)


# Covers every state a row can be in — running, stopped, never deployed, and with the description
# Domino leaves empty more often than not — so the rail's four renderings can all be seen locally.
# Not drawn from a probe: the project this was built in has no Model APIs deployed at all, which is
# also why the empty state below is the case a local run hits first.
_FAKE_MODEL_APIS = (
    ModelApi("f-churn", "churn-risk", "Scores an account's chance of cancelling.", "Running"),
    ModelApi("f-fraud", "fraud-scorer", None, "Running"),
    ModelApi("f-demand", "demand-forecast", "Weekly units by SKU.", "Stopped"),
    ModelApi("f-draft", "price-optimiser-draft", None, None),
)


# Drawn from what cloud-dogfood actually returned (2026-08-18): two Snowflake sources whose
# `displayName` was identical, one SQL Server, and the credential spread that was really there. The
# `ready=False` row is the case the panel exists for — `test` is an `Individual`-credential source, so
# whether it opens depends on whether THIS person entered their own credentials, and here they have
# not. The last row carries `ready=None`, the state a failed readiness call leaves behind.
#
# Since #11 the connector TYPE is on each row as well, because that is what decides whether the
# cascade can open and how many levels it has. The four types here cover the three shapes the panel
# has to draw: three levels (Snowflake), two (Postgres, where a database is not a thing to pick), and
# none at all (`billing-oracle`, added for that state — Oracle is inside the SQL allowlist and
# outside the dialect table, so it lists and records but cannot be looked inside).
_FAKE_DATA_SOURCES = (
    DataSource("ds-dwh", "Snowflake-Data-Warehouse", "Snowflake", "Shared",
               "The company warehouse. Reads across every schema.", True,
               connector_type="SnowflakeConfig"),
    DataSource("ds-test", "test", "Snowflake", "Individual", None, False,
               connector_type="SnowflakeConfig"),
    # The one source that pins a database in its own config, so the cascade opens on the schema level
    # with the database already answered — the shortcut the live warehouse did not offer.
    DataSource("ds-mssql", "AWS_MSSQL", "SQL Server", "Individual", None, True,
               connector_type="SQLServerConfig", default_database="underwriting"),
    DataSource("ds-reporting", "reporting-replica", "PostgreSQL", "Shared",
               "Read replica of the reporting database.", None,
               connector_type="PostgreSQLConfig"),
    DataSource("ds-oracle", "billing-oracle", "Oracle", "Shared", None, True,
               connector_type="OracleConfig"),
)

# source id -> database -> schema -> tables. A store with no database level keys on "", the same empty
# string the cascade passes at that level, so the fake and the real contract agree about what a
# two-level store is rather than the fake inventing a shape of its own.
#
# `ds-test` is present and empty on purpose: a source whose databases genuinely list as nothing is a
# state the panel has to draw, and it is a different sentence from a source Sage cannot look inside.
_FAKE_TREE: dict[str, dict[str, dict[str, list[str]]]] = {
    "ds-dwh": {
        # Named after what the live warehouse actually holds, curated layers first, so a local run
        # rehearses the real choice instead of `db1 / schema1 / table1`.
        "DWH": {
            "MARTS": ["DIM_ACCOUNT", "DIM_DATE", "FCT_USAGE_DAILY", "FCT_SUBSCRIPTION_REVENUE"],
            "REPORTING": ["V_ARR_WATERFALL", "V_CUSTOMER_HEALTH"],
            "STAGING": [],
        },
        "SANDBOX": {"PUBLIC": ["SCRATCH_FORECAST"]},
    },
    "ds-test": {},
    "ds-mssql": {"underwriting": {"dbo": ["policies", "claims", "quotes"]}},
    "ds-reporting": {"": {"public": ["accounts", "events"], "audit": ["access_log"]}},
}


@dataclass
class FakeResourceProvider:
    """In-memory Resources for local testing/demo (no gateway)."""

    aliases: list[LlmAlias] = field(default_factory=lambda: list(_FAKE_ALIASES))
    model_apis: list[ModelApi] = field(default_factory=lambda: list(_FAKE_MODEL_APIS))
    data_sources: list[DataSource] = field(default_factory=lambda: list(_FAKE_DATA_SOURCES))
    # source id -> database -> schema -> tables, for the cascade (#11).
    tree: dict[str, dict[str, dict[str, list[str]]]] = field(
        default_factory=lambda: dict(_FAKE_TREE))

    def list_llm_aliases(self) -> list[LlmAlias]:
        return list(self.aliases)

    def list_model_apis(self, project_id: str | None) -> list[ModelApi]:
        """The project is ignored, not validated: this fake stands in for a Domino that answers, and
        a local run has no project ids for a test to be right or wrong about."""
        return list(self.model_apis)

    def list_data_sources(self) -> list[DataSource]:
        return list(self.data_sources)

    # The cascade, from the tree above. `dialect_for` is called rather than skipped so the fake
    # refuses a connector the real provider would also refuse: a source Sage has no dialect for has to
    # fail the same way locally, or the state never gets drawn until someone is on a real deployment.
    def list_databases(self, source: DataSource) -> list[str]:
        dialect_for(source)
        return sorted(d for d in self.tree.get(source.id, {}) if d)

    def list_schemas(self, source: DataSource, database: str) -> list[str]:
        dialect_for(source)
        return sorted(self.tree.get(source.id, {}).get(database, {}))

    def list_tables(self, source: DataSource, database: str, schema: str) -> list[str]:
        dialect_for(source)
        return list(self.tree.get(source.id, {}).get(database, {}).get(schema, []))
