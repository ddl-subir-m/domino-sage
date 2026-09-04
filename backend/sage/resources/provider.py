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

The Model API (#8): the unscoped listing 403s (`"not authorized to view access configuration"`) —
that surface is deployment-wide and wants an admin role a normal Sage user does not have. Scoped to
a project it answers 200 (verified live, DOMINO-PRIMITIVES.md). Off-Domino, Sage has no home project,
so it fans out over every project the caller owns or collaborates on instead.

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

import concurrent.futures
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from ..orchestrator import brand
from ..router.models import reasoning_efforts_for as name_reasoning_efforts


def _platform_api() -> str:
    """What a failure message calls the platform's own API. One literal read at four call sites, so
    the sentence a creator gets and the sentence the lint reads are the same one. Resolved per call
    rather than at import, because the pack is read when the string is read."""
    return brand.text("The {platformName} API")


class ResourceUnavailable(RuntimeError):
    """A Resource listing could not be produced. The message reaches the user unchanged, so it says
    what failed and what to do about it — and never carries a token or a response body.

    `status` is the HTTP status when Domino answered and refused, and None when it did not answer at
    all. One place needs to tell those apart (`get_model_api`, #42): a 404 for a model is an ANSWER —
    the creator cannot read it, and a token may still prove they can call it — while a Domino that is
    down is a failure to report as one. The message cannot be asked, so the status travels beside it.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


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
    # Where this alias sends its calls, verbatim. Only a Domino-hosted alias has one — 12 of the 14
    # on cloud-dogfood (2026-08-21) do not, because they are vendor models with nothing on Domino
    # behind them. It is kept because it is the ONLY join back to a Hosted GenAI Endpoint, whose
    # status is what says the model will actually answer (#21): `/v1/models` filters on permission
    # alone, so a granted alias whose endpoint is stopped is still offered.
    endpoint_url: str | None = None
    # OpenAI-style reasoning_effort values this alias accepts. Empty means the picker hides effort.
    reasoning_efforts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Person:
    """Somebody who collaborates on the project — a name to put on a plan comment or an approval.

    Not a Resource: nothing lists these in the rail and nothing binds one. Plan review is the only
    caller, which is why this carries a display name and nothing else about the account.
    """

    id: str
    name: str        # `fullName`, the label a comment or approval is shown under
    title: str = ""  # `userName`, to tell two people with the same full name apart
    avatar: str = ""


@dataclass(frozen=True)
class Collaborator:
    """A Person Domino records as working on this Project, and the role it records them under.

    A Person plus two facts, rather than two more optional fields on Person, because a directory row
    is a Person and has neither: somebody who is not on this Project has no role on it, and a field
    that is empty for most of its uses stops meaning anything.

    `role` is the RAW platform value — `Contributor`, `ProjectImporter` — never a word of Sage's. It
    is also read back in a different case from the one the write takes, so anything comparing it
    folds case. The owner carries no role at all: the read that names people includes them and the
    read that carries roles does not, so `owner` is what says which row they are.
    """

    id: str
    name: str
    title: str = ""
    avatar: str = ""
    role: str = ""
    owner: bool = False


@dataclass(frozen=True)
class HostedEndpoint:
    """A Domino-hosted GenAI Endpoint — the vLLM deployment an LLM Alias can point at.

    Not a Resource a creator picks; nothing lists these in the rail. It exists so preflight can say
    whether the model behind an Alias is serving, which no other listing answers (#21).
    """

    id: str
    name: str
    url: str  # what `LlmAlias.endpoint_url` joins to, once its trailing /v1 is off
    # `currentVersion.status`, verbatim, or None when there is no current version at all — an
    # endpoint that has never been built has no status rather than a bad one. Kept as Domino's own
    # word for the same reason `ModelApi.status` is: "Stopped" and "BuildFailed" lead to different
    # remedies, and reducing them to a boolean would send half the readers to the wrong one.
    status: str | None = None


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
    #: The name of the project that deployed it, and EMPTY for the builder's own project (#42). The
    #: listing spans projects now, so two rows can both read `churn-risk` and a creator would have to
    #: click one to learn which is which. Blank for the home project rather than filled in: that is
    #: the context every other row in the rail is already in, so naming it would be noise on the
    #: majority of rows and would bury the one label that carries information.
    project_name: str = ""


@dataclass(frozen=True)
class ModelApiListing:
    """The Model APIs a caller can compose with, and whether the fan-out that found them reached
    every project it meant to.

    A partial answer here is a *listing that arrived*, which is why the flag has to travel with the
    rows rather than being a raised failure (ADR-0028 covers a provider that cannot read; ADR-0029
    covers one that read partially, and this is the second). Nothing in the rows shows the cut: a
    non-home project whose listing failed is skipped, and `_member_projects` can come back short in
    five ways of its own.

    Two known shortfalls are NOT carried here, both recorded rather than built (#163). `_model_apis_in`
    exhausting `_MAX_PAGES` would truncate one project's rows, but that is 5,000 Model APIs in a
    single project — the same volume argument that left `list_data_sources` alone. And the fan-out is
    scoped to projects the creator is a MEMBER of, so a model reached through the paste door is
    absent from a listing that is complete over its own scope; that one is a real false-death and
    wants the single-record read (`get_model_api`) rather than a flag, which is a change to how this
    kind is preflighted at all.

    `complete` is what lets a caller refuse to read absence as deletion. Without it, a Binding to a
    Model API in a skipped project looks deleted, and the remedy on offer is Remove — a false death
    with a destructive fix (#163). One bool and no reason: neither consumer renders a reason, and
    ADR-0034 already settled that this kind says nothing about why it could not be checked.
    """

    models: list[ModelApi]
    complete: bool = True


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

    `sample` is not a level at all: it reads ROWS, and it runs only when a creator explicitly asks for
    it (#16). Kept beside the rest because it is the same per-connector problem — `LIMIT` is not
    spelled the same everywhere — and because a connector Sage cannot look inside cannot be sampled
    either.

    `columns` is the fourth level, and the only one no picker opens (#15). It is read once, when the
    creator binds a Scope, so the agent writing the app's queries knows what the tables hold instead of
    guessing column names. `None` for a connector whose columns Sage cannot list, which is the same
    set as the rest of this table.

    Statements are formatted with `{db}` and `{schema}` (validated, quoted identifiers),
    `{schema_lit}` (the same name bare, for the string comparisons `information_schema` needs) and
    `{table_clause}` (the whole `AND TABLE_NAME = '…'` phrase, or nothing when every table is wanted —
    a phrase rather than a name so that "all tables" does not need a second statement per dialect).
    """

    databases: str | None
    schemas: str
    tables: str
    verified: bool = False
    quote: str = '"'  # `"` everywhere except the stores where it means a string literal by default
    columns: str | None = None
    sample: str | None = None

    def ident(self, name: str) -> str:
        """One validated identifier, quoted. Quoted only to preserve case — the validation has
        already ruled out everything quoting would otherwise be protecting against."""
        return f"{self.quote}{safe_identifier(name)}{self.quote}"

    def statement(self, template: str, database: str = "", schema: str = "", table: str = "",
                  limit: int = 0) -> str:
        return template.format(
            db=self.ident(database) if database else "",
            schema=self.ident(schema) if schema else "",
            schema_lit=safe_identifier(schema) if schema else "",
            table=self.ident(table) if table else "",
            table_clause=f" AND TABLE_NAME = '{safe_identifier(table)}'" if table else "",
            # An int this side of the boundary, never a caller's text. It reaches the statement
            # through `%d`-style formatting of a value Python has already proved is a whole number.
            limit=int(limit),
        )


# Spelled in upper case, which is the only spelling that works everywhere: SQL Server defines these
# views as INFORMATION_SCHEMA and a case-sensitive collation rejects the lower-case form, while every
# store that folds unquoted identifiers accepts either. The string LITERALS stay lower case, because
# Postgres's own schema names are lower case and those are values, not identifiers.
_ANSI_SCHEMAS = ("SELECT SCHEMA_NAME AS name FROM {db}.INFORMATION_SCHEMA.SCHEMATA "
                 "ORDER BY SCHEMA_NAME")
_ANSI_TABLES = ("SELECT TABLE_NAME AS name FROM {db}.INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA = '{schema_lit}' ORDER BY TABLE_NAME")
# One statement for the whole Scope, not one per table: a schema with 200 tables would otherwise be
# 200 round trips at ~3s each. ORDINAL_POSITION so the agent reads the columns in the order the table
# declares them, which is the order a person describing the table would use.
# Reading rows, which only #16's explicit act ever runs. Three spellings, because the standard one
# is not universal: SQL Server's family has no `LIMIT` and takes `TOP` before the select list, and a
# store with no database level must not have an empty `{db}` prefix left in front of the schema.
_SAMPLE_3 = "SELECT * FROM {db}.{schema}.{table} LIMIT {limit}"
_SAMPLE_2 = "SELECT * FROM {schema}.{table} LIMIT {limit}"
_SAMPLE_TOP = "SELECT TOP {limit} * FROM {db}.{schema}.{table}"

_ANSI_COLUMNS = ("SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, "
                 "DATA_TYPE AS data_type FROM {db}.INFORMATION_SCHEMA.COLUMNS "
                 "WHERE TABLE_SCHEMA = '{schema_lit}'{table_clause} "
                 "ORDER BY TABLE_NAME, ORDINAL_POSITION")

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
        columns=_ANSI_COLUMNS,
        sample=_SAMPLE_3,
    ),
    # Three levels: `sys.databases` is cross-database on one connection, unlike Postgres.
    "SQLServerConfig": SqlDialect("SELECT name FROM sys.databases ORDER BY name",
                                  _ANSI_SCHEMAS, _ANSI_TABLES, columns=_ANSI_COLUMNS,
                                  sample=_SAMPLE_TOP),
    "SynapseConfig": SqlDialect("SELECT name FROM sys.databases ORDER BY name",
                                _ANSI_SCHEMAS, _ANSI_TABLES, columns=_ANSI_COLUMNS,
                                sample=_SAMPLE_TOP),
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
        columns=("SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, "
                 "DATA_TYPE AS data_type FROM INFORMATION_SCHEMA.COLUMNS "
                 "WHERE TABLE_SCHEMA = '{schema_lit}'{table_clause} "
                 "ORDER BY TABLE_NAME, ORDINAL_POSITION"),
        sample=_SAMPLE_2,
    ),
    # Two levels each, and in MySQL's family "database" and "schema" are one thing — so the single
    # namespace level is offered as the schema, which is the level the Binding records.
    "MySQLConfig": SqlDialect(
        None,
        "SELECT SCHEMA_NAME AS name FROM INFORMATION_SCHEMA.SCHEMATA ORDER BY SCHEMA_NAME",
        ("SELECT TABLE_NAME AS name FROM INFORMATION_SCHEMA.TABLES "
         "WHERE TABLE_SCHEMA = '{schema_lit}' ORDER BY TABLE_NAME"),
        quote="`",
        columns=("SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, "
                 "DATA_TYPE AS data_type FROM INFORMATION_SCHEMA.COLUMNS "
                 "WHERE TABLE_SCHEMA = '{schema_lit}'{table_clause} "
                 "ORDER BY TABLE_NAME, ORDINAL_POSITION"),
        sample=_SAMPLE_2,
    ),
    # Catalogs are the outer level on both, and `SHOW` is how each names them.
    "DatabricksConfig": SqlDialect("SHOW CATALOGS", _ANSI_SCHEMAS, _ANSI_TABLES, quote="`",
                                   columns=_ANSI_COLUMNS, sample=_SAMPLE_3),
    "TrinoConfig": SqlDialect("SHOW CATALOGS", _ANSI_SCHEMAS, _ANSI_TABLES, columns=_ANSI_COLUMNS,
                              sample=_SAMPLE_3),
    # Two levels. A BigQuery dataset holds its own `INFORMATION_SCHEMA`, so the tables view is
    # qualified by the schema rather than filtered on it.
    "BigQueryConfig": SqlDialect(
        None,
        "SELECT SCHEMA_NAME AS name FROM INFORMATION_SCHEMA.SCHEMATA ORDER BY SCHEMA_NAME",
        "SELECT TABLE_NAME AS name FROM {schema}.INFORMATION_SCHEMA.TABLES ORDER BY TABLE_NAME",
        quote="`",
        columns=("SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, "
                 "DATA_TYPE AS data_type FROM {schema}.INFORMATION_SCHEMA.COLUMNS "
                 "WHERE TRUE{table_clause} ORDER BY TABLE_NAME, ORDINAL_POSITION"),
        sample=_SAMPLE_2,
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
        raise ValueError(brand.text(
            "{assistantName} will not send {name} to a database as a name. A database, schema or "
            "table name may hold letters, digits, underscores and $ only.",
            name=repr(name[:60]),
        ))
    return name


@dataclass(frozen=True)
class Column:
    """One column of one table inside a Data Source (#15).

    Carries the table as well as its own name, because the Scope a Binding records may be a whole
    schema — the columns of thirty tables come back as one list, and which table each belongs to is
    the half that makes them usable.

    `type` is the store's own word for it (`VARCHAR`, `NUMBER`, `TIMESTAMP_NTZ`), not normalised.
    The agent writing the app's queries reads it to decide what a comparison should look like, and a
    tidied-up name would be Sage guessing on its behalf about a store it cannot see.
    """

    table: str
    name: str
    type: str = ""


@dataclass(frozen=True)
class SampleRows:
    """A handful of real rows out of one table (#16).

    Only ever produced by an explicit act. Rows are production data, and putting them in a model's
    context is a decision that belongs to the person who knows what is in the table — never to a rule
    that inferred it would be helpful.

    Values arrive already reduced to what JSON has words for, and long ones already cut: a store
    answers with decimals, timestamps and blobs, and one wide column would otherwise spend the
    agent's context on a base64 run nobody reads.
    """

    table: str
    columns: list[str]
    rows: list[list]


def dialect_for(source: DataSource) -> SqlDialect:
    """The introspection SQL for one source, or a refusal naming the connector."""
    dialect = SQL_DIALECTS.get(source.connector_type)
    if dialect is None:
        known = source.connector or source.connector_type or "this kind of"
        raise ResourceUnavailable(brand.text(
            "{assistantName} cannot list what is inside a {kind} {dataSource} yet, so it cannot "
            "offer its databases and schemas. You can still record that the app uses this "
            "{dataSource}.",
            kind=known,
        ))
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


# One cell's worth of context. A warehouse column can hold a base64 blob or a whole JSON document,
# and the agent is being shown the SHAPE of the data — a value cut at this length still says "this is
# an email address" or "this is a currency code", which is the entire reason for showing it.
SAMPLE_CELL_LIMIT = 80


def sample_value(value: Any, limit: int = SAMPLE_CELL_LIMIT) -> Any:
    """One store value as something JSON can carry and a model can read.

    Anything without a JSON word for it is stringified rather than dropped, for the reason the Built
    App's executor does the same: a column the agent can read beats a column that silently went
    missing. Nulls stay null, because "this column is often empty" is one of the more useful things a
    sample says.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        text = value
    elif hasattr(value, "isoformat"):
        text = value.isoformat()
    else:
        text = str(value)
    if isinstance(text, str) and len(text) > limit:
        return text[:limit] + "…"
    return text


def frame_rows(frame: Any, limit: int = SAMPLE_CELL_LIMIT) -> tuple[list[str], list[list]]:
    """A result frame as (column names in order, rows by position).

    By position rather than by name, as the Built App's own drain is: a query selecting two columns
    with the same name would otherwise answer with one of them twice.
    """
    columns = [str(c) for c in getattr(frame, "columns", [])]
    if not columns:
        return [], []
    values = [frame[c].tolist() for c in frame.columns]
    return columns, [[sample_value(col[i], limit) for col in values] for i in range(len(values[0]))]


class ResourceProvider(Protocol):
    def list_llm_aliases(self) -> list[LlmAlias]: ...

    # Takes the project explicitly, as the asset provider's list_datasets(project_id) does: the
    # orchestrator owns which project this builder is bound to, and the provider stays a client. The
    # argument is the builder's HOME project since #42, not the only one asked about: the listing
    # spans every project the caller belongs to, and home is the one that sorts first and whose
    # failure is fatal. Returns a listing rather than the rows, because the fan-out can come back
    # partial without failing — see `ModelApiListing`, and ADR-0029 for the precedent.
    def list_model_apis(self, project_id: str | None) -> ModelApiListing: ...

    # By id, and no project at all: reach is per model, not per project (#42). None means this caller
    # cannot read the record — which is not the same as cannot call the model, so a caller holding a
    # verified access token is entitled to ignore it.
    def get_model_api(self, model_api_id: str) -> ModelApi | None: ...

    # No argument at all, and unscoped: the listing is deployment-wide and already filtered to what
    # this caller may see. Not a Resource anyone picks — preflight alone reads it, to say whether the
    # endpoint behind an Alias is serving (#21).
    def list_hosted_endpoints(self) -> list[HostedEndpoint]: ...

    # Who works on this project, with the role Domino records each of them under. Takes the project
    # explicitly for the same reason list_model_apis does. RAISES when it cannot read (ADR-0028):
    # empty is a real answer only for a project genuinely worked on alone, and a caller that cannot
    # tell that from a failed read cannot say anything true about either. What a failure costs is
    # the caller's judgement — the plan page catches and shows ids where it would show names.
    def list_collaborators(self, project_id: str | None) -> list[Collaborator]: ...

    # Everyone on the deployment, for the picker that adds one of them to the project. Unscoped, and
    # deliberately not built from the list above: the whole point of the picker is the people who are
    # NOT on the project yet. One call for the lot — the browser filters it.
    def list_directory(self) -> list[Person]: ...

    # Whoever this adapter's token acts as, in the id space a collaborator is named in. `/api/me`
    # answers in the identity provider's, which does not join. "" when Domino will not say.
    def caller_id(self) -> str: ...

    # Add somebody to the project, in the one role Sage assigns, and take somebody off it. Both are
    # per person rather than bulk: a partial failure has to name who it failed on.
    def add_collaborator(self, project_id: str | None, user_id: str) -> None: ...

    def remove_collaborator(self, project_id: str | None, user_id: str) -> None: ...

    # No project argument, unlike the two above, and that asymmetry is the finding: a Data Source is
    # permission-scoped to the person, not the project.
    def list_data_sources(self) -> list[DataSource]: ...

    # The cascade takes the resolved row, not an id, so a caller cannot hand in a connector type of
    # its own and choose which SQL gets sent. `database` is "" for a store with no database level.
    def list_databases(self, source: DataSource) -> list[str]: ...

    def list_schemas(self, source: DataSource, database: str) -> list[str]: ...

    def list_tables(self, source: DataSource, database: str, schema: str) -> list[str]: ...

    # Not a cascade level — no picker opens it. Read once when a Scope is bound, so the agent that
    # writes the app's queries knows what the tables hold (#15). `table` narrows it to one; "" means
    # every table in the schema, which is what a Scope that stopped at a schema is asking for.
    def list_columns(self, source: DataSource, database: str, schema: str,
                     table: str = "") -> list[Column]: ...

    # Rows, and the only method here that reads any. Runs when a creator asks and never otherwise
    # (#16), which is why it takes one table at a time: what is exposed is picked, not swept up.
    def sample_rows(self, source: DataSource, database: str, schema: str, table: str,
                    limit: int = 5) -> SampleRows: ...


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


_EFFORT_VALUES = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})


def parse_reasoning_efforts(raw: Any) -> list[str]:
    """`inference_params.reasoning_effort` as a list of allowed values, if the alias advertised any."""
    if not isinstance(raw, dict):
        return []
    value = raw.get("reasoning_effort") or raw.get("reasoning")
    options: Any
    if isinstance(value, list):
        options = value
    elif isinstance(value, dict):
        options = value.get("enum") or value.get("options") or value.get("values") or []
    else:
        return []
    if not isinstance(options, list):
        return []
    return [str(v) for v in options if str(v) in _EFFORT_VALUES]


def alias_reasoning_efforts(name: str, inference_params: Any = None) -> list[str]:
    """Gateway enum if present, else the GPT-5 / o-series heuristic."""
    listed = parse_reasoning_efforts(inference_params)
    return listed or list(name_reasoning_efforts(name))


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
                endpoint_url=str(rec["endpoint_url"]) if rec.get("endpoint_url") else None,
                reasoning_efforts=alias_reasoning_efforts(name or rid, rec.get("inference_params")),
            )
        )
    for extra in sorted(accessible - claimed):
        out.append(LlmAlias(
            id=extra, name=extra, display_name=extra,
            reasoning_efforts=alias_reasoning_efforts(extra),
        ))
    return out


def parse_model_apis(payload: Any, project_name: str = "") -> list[ModelApi]:
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
                project_name=project_name,
            )
        )
    return out


def parse_endpoints(payload: Any) -> list[HostedEndpoint]:
    """Hosted GenAI Endpoints out of a `GET /api/gen-ai/beta/endpoints` body.

    `currentVersion` is optional in the schema and absent in practice for an endpoint that never
    built, so a missing status is carried as None rather than invented. A record with no url is
    dropped: the url is the only thing an Alias can be joined on, so a row without one cannot answer
    the only question this listing is fetched for.
    """
    out: list[HostedEndpoint] = []
    for rec in records_of(payload):
        url = str(rec.get("url") or "").rstrip("/")
        if not url:
            continue
        status = ((rec.get("currentVersion") or {}).get("status"))
        out.append(
            HostedEndpoint(
                id=str(rec.get("id") or ""),
                name=str(rec.get("name") or ""),
                url=url,
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


def data_library_ready() -> str:
    """"" when THIS interpreter can read inside a Data Source, otherwise why it cannot.

    The same import `_introspect` makes, deliberately: the question "will the cascade work here" has
    exactly one right way to be answered, and a probe that checked something adjacent would go on
    saying yes after the real import started failing.

    It exists because the builder has no terminal. `domino_data` ships in the Domino base image's
    system python, the orchestrator runs from uv's isolated venv, and for a while those were
    different answers with nothing in between to say so — the cascade reported "the Domino data
    library is not installed here" on a deployment that had it. `/api/diag` is where that is visible
    now, next to `sage_rev`, because both answer the same question: did the rebuild take effect.
    """
    try:
        from domino_data.data_sources import DataSourceClient  # noqa: F401
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    return ""


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
    # Projects the Model API listing fans out over, beyond the builder's own (#42). One HTTP call
    # each, so this is a latency budget: 25 keeps the rail inside a couple of seconds on a slow
    # deployment. The paste door covers anyone who works in more projects than this. Raising it is
    # not the answer to #163 either: `_member_projects` pages the whole membership, so the tail is
    # unbounded and dropping the cap trades a correctness bug for the latency one #160 fixed. What
    # #163 changed is that the listing now SAYS it was cut, which is `ModelApiListing.complete`.
    _MAX_FANOUT_PROJECTS = 25
    # How many of those calls are in the air at once (#160), which is a different budget: every
    # request fetches its own short-lived bearer from the Domino token sidecar first, so this is
    # also the number of simultaneous callers that sidecar sees. 8 keeps the measured win — 16
    # projects at ~100 ms each land in two waves, ~0.2 s against 1.6 s serial — and the wave that
    # 26-at-once would have saved is a tenth of a second the creator cannot perceive.
    _FANOUT_AT_ONCE = 8

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

    def list_hosted_endpoints(self) -> list[HostedEndpoint]:
        """Every Hosted GenAI Endpoint this caller can see, deployment-wide.

        One call, unscoped. `projectId` is an optional query parameter and omitting it answered 200
        for a non-admin caller (verified live 2026-08-21, DOMINO-PRIMITIVES.md), which is what keeps
        preflight at a fixed cost rather than one call per slot or per Binding. It is deliberately
        NOT scoped to Sage's own project: the endpoint an Alias points at usually belongs to another
        team, which is the case that made #21 worth checking at all.
        """
        if not self._api_host:
            raise ResourceUnavailable(brand.text(
                "{assistantName} reads {hostedGenaiEndpoint} status from the {platformName} API, "
                "and it is not running against one, so it cannot tell whether the endpoint behind "
                "a model is running."
            ))
        payload = self._get(
            "/api/gen-ai/beta/endpoints",
            root=self._api_host,
            service=_platform_api(),
            token_provider=self._api_token_provider,
        )
        return parse_endpoints(payload)

    def _domino_get(self, path: str, params: dict | None = None) -> Any:
        """A GET against the Domino API with this adapter's own bearer. The three keyword arguments
        below travel together on every Domino call, and the project fan-out makes several."""
        return self._get(
            path,
            root=self._api_host,
            service=_platform_api(),
            token_provider=self._api_token_provider,
            params=params,
        )

    @staticmethod
    def _is_member(record: dict, user_id: str) -> bool:
        """Whether this caller owns the project or collaborates on it. Membership rather than
        visibility, for the reason in `_member_projects`."""
        if str(record.get("ownerId") or "") == user_id:
            return True
        return any(
            isinstance(c, dict) and str(c.get("id") or "") == user_id
            for c in record.get("collaborators") or []
        )

    def _member_projects(self, home_project_id: str) -> tuple[list[tuple[str, str]], bool]:
        """(id, name) for every project worth asking about Model APIs, the builder's own first, and
        whether this is all of them.

        Membership, not visibility. Domino's listing is "projects visible to user", and on a demo
        deployment that can mean every public project on it — fanning out over those would be a slow
        rail showing models the creator holds no token for. Owner or collaborator is the set they
        actually work in, and it is the set whose models they can plausibly call.

        Degrades rather than fails. If Domino will not say who the caller is, or will not list
        projects, this answers with the builder's own project alone — which is exactly the behaviour
        before #42, and a rail listing one project beats a rail listing an error. Off-Domino there is
        no home project: an empty home collapses to the membership list, or to nothing.

        Capped at `_MAX_FANOUT_PROJECTS` beyond the home project, sorted by name so the cap falls in
        the same place twice running. A member of more projects than that reaches the rest through
        the paste door, which is why that door exists.

        The second return value is False wherever this list might be short, and there are five ways
        it can be (#163): the cap cut the tail, `/api/users/v1/self` refused so membership was never
        enumerated at all, a page of the pager failed, `_MAX_PAGES` ran out on a pager Domino never
        terminated, or a page came back in a shape this code cannot read. Only a page that is
        genuinely empty, or an offset past the reported total, is Domino saying "that is all of
        them". Every other exit produces a member list that looks whole and is not, and
        the caller cannot tell from a row count — a creator who really is a member of one project
        and a creator whose membership read died on page one return the same thing. That flag is the
        only difference between them, and `stale_bindings` needs it to avoid calling a live Model API
        deleted.
        """
        home = (home_project_id, "") if home_project_id else None
        try:
            me = str(((self._domino_get("/api/users/v1/self") or {}).get("user") or {}).get("id") or "")
        except ResourceUnavailable:
            if home:
                return [home], False
            raise
        if not me:
            return ([home] if home else []), False

        mine: list[tuple[str, str]] = []
        offset = 0
        # Set at the one exit that means Domino said "that is all of them" — an empty page, or an
        # offset past the reported total. Every other way out of this loop leaves it False: a page
        # that failed, and `_MAX_PAGES` running out on a pager that never terminated.
        reached_end = False
        for _ in range(self._MAX_PAGES):
            try:
                payload = self._domino_get(
                    "/api/projects/beta/projects", {"offset": offset, "limit": self._PAGE},
                )
            except ResourceUnavailable:
                break
            rows = payload.get("projects") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                # A page this code could not read, which is NOT a page saying "no more". Stopping
                # here is what it always did; what must not happen is calling it the end. Domino
                # renaming this key would otherwise produce an empty membership marked whole, and
                # every out-of-home Binding would be reported gone with Remove as the remedy.
                break
            for rec in rows:
                if not isinstance(rec, dict):
                    continue
                pid = str(rec.get("id") or "")
                if not pid or pid == home_project_id or not self._is_member(rec, me):
                    continue
                mine.append((pid, str(rec.get("name") or pid)))
            meta = payload.get("metadata") if isinstance(payload, dict) else None
            total = ((meta or {}).get("pagination") or {}).get("totalCount")
            offset += self._PAGE
            if not rows or (total is not None and offset >= total):
                reached_end = True
                break
        extras = sorted(mine, key=lambda p: p[1])[: self._MAX_FANOUT_PROJECTS]
        return ([home, *extras] if home else extras), reached_end and len(extras) == len(mine)

    def _model_apis_in(self, project_id: str, project_name: str) -> list[ModelApi]:
        """The Model APIs of one project — the whole of what this call used to be."""
        out: list[ModelApi] = []
        offset = 0
        # This pager has the same `_MAX_PAGES` backstop as `_member_projects` and, unlike that one,
        # does NOT report exhausting it (#163). Deliberate rather than missed: the membership pager
        # runs against a tenant's whole project list, which really does get long, while this one
        # would need 5,000 Model APIs in a single project — the volume argument that left
        # `list_data_sources` alone. Recorded so the asymmetry reads as a decision; if it ever
        # bites, this returns a completeness bit and `list_model_apis` ands it in.
        for _ in range(self._MAX_PAGES):
            payload = self._domino_get(
                "/api/modelServing/v1/modelApis",
                {"projectId": project_id, "offset": offset, "limit": self._PAGE},
            )
            page = records_of(payload)
            out += parse_model_apis(page, project_name)
            meta = payload.get("metadata") if isinstance(payload, dict) else None
            total = ((meta or {}).get("pagination") or {}).get("totalCount")
            offset += self._PAGE
            if not page or (total is not None and offset >= total):
                break
        return out

    def list_model_apis(self, project_id: str | None) -> ModelApiListing:
        """Model APIs this caller can compose with, across the projects they belong to (#42).

        Asked once per project rather than once, because the scope cannot be dropped: unscoped,
        Domino answers `403 "not authorized to view access configuration"` — that listing is
        deployment-wide and wants an admin grant a normal Sage user does not have (verified,
        DOMINO-PRIMITIVES.md). So "what can I use?" is one call per project the creator is a member
        of, unioned by id.

        The builder's own project is first and its failure is fatal, as it has always been: a creator
        whose own project will not list is looking at something broken and should be told. Any OTHER
        project's failure is skipped — one odd grant somewhere on the tenant must not empty a rail
        that would otherwise have answered. Off-Domino there is no home project: membership is the
        whole list, and finding none is unlistable rather than empty.

        A skip here, and any of the four ways `_member_projects` can come back short, leave the
        answer partial — so it carries `complete` rather than being bare rows (#163). The skip is
        still a skip: this returns what it found. The change is only that a caller can now tell a
        short listing from a whole one, which is the difference between "that Resource is gone" and
        "we did not manage to look everywhere".
        """
        if not self._api_host:
            raise ResourceUnavailable(brand.text(
                "{assistantName} lists {modelApiPlural} from the {platformName} project it runs "
                "in, and it is not running in one, so it cannot tell whether this project has any."
            ))
        home_id = project_id or ""
        members, complete = self._member_projects(home_id)
        pairs = [(pid, pname) for pid, pname in members if pid]
        if not pairs:
            raise ResourceUnavailable(brand.text(
                "{assistantName} lists {modelApiPlural} from the projects you belong to, and it "
                "could not find any."
            ))
        out: list[ModelApi] = []
        seen: set[str] = set()
        # Together, not one after another (#160). Measured on a real deployment: 16 projects at
        # ~70-130 ms each, which added up to 1.1-2.1 s of the Resource Browser's wait. No call
        # depends on another's answer, so the wait is now the slowest wave rather than the sum.
        #
        # The answers are read back in membership order rather than completion order, which keeps
        # two things the serial loop gave for free: the rail lists the same way twice running, and
        # the home project is still the first writer of a model bound into two projects, so that
        # row keeps its blank label.
        #
        # A home failure is now held until the other calls finish, since the pool joins on the way
        # out. That wait is what a successful listing costs, which is less than the serial listing
        # this replaced — cheaper than leaking threads out of the path that reports breakage.
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(pairs), self._FANOUT_AT_ONCE),
            thread_name_prefix="sage-model-apis",
        ) as pool:
            answers = [(pid, pool.submit(self._model_apis_in, pid, pname)) for pid, pname in pairs]
            for pid, answer in answers:
                try:
                    rows = answer.result()
                except ResourceUnavailable:
                    if pid == home_id:
                        raise
                    # Recorded, not just survived. This `continue` is what made the whole answer
                    # unusable as evidence of absence, one layer above (#163).
                    complete = False
                    continue
                for m in rows:
                    if m.id not in seen:
                        seen.add(m.id)
                        out.append(m)
        return ModelApiListing(out, complete=complete)

    def get_model_api(self, model_api_id: str) -> ModelApi | None:
        """One Model API by id, or None when this caller cannot read it (#42).

        The single-record route answers 200 wherever the caller has access, whatever project the
        model belongs to (verified 2026-08-20, DOMINO-PRIMITIVES.md). That is the question binding
        actually asks — "can this creator reach this model" — which the project listing only ever
        approximated, and got wrong for every model deployed somewhere else.

        None rather than an exception for every refusal, 403 and 404 alike. The caller has a second
        way to establish reach (a verified access token) and it must be free to try it; a Domino that
        will not describe a model is not a Domino that says the model is out of bounds.
        """
        if not self._api_host or not model_api_id:
            return None
        try:
            record = self._domino_get(f"/api/modelServing/v1/modelApis/{model_api_id}")
        except ResourceUnavailable as e:
            # A refusal is an answer: no such model, or not one this caller may read. A Domino that
            # did not answer at all is not, and is re-raised — reporting "that model is not yours"
            # while the API is down would send the creator to look for a permission problem that is
            # not there.
            if e.status in (401, 403, 404):
                return None
            raise
        rows = parse_model_apis([record] if isinstance(record, dict) else record)
        return rows[0] if rows else None

    def list_collaborators(self, project_id: str | None) -> list[Collaborator]:
        """Who is on this project and under which role — two Domino reads that disagree.

        `GET /v4/projects/{id}/collaborators` answers Person records directly, so there is no second
        lookup to turn ids into names, and it INCLUDES the owner. The role lives on the project
        record instead, whose `collaborators[]` EXCLUDES the owner. Neither read answers the question
        on its own, so both are made and joined on the user id (verified live, 2026-09-03).

        Raises rather than degrading (ADR-0028), including when the role read is the one that failed.
        Names without an ownerId would render a list that is not safe to act on: nothing would say
        whose row must not offer Remove, and the design refuses to learn that from a refusal after
        the click. The plan page catches this and shows ids where it would show names.

        No project id or no host is a different thing and answers empty: there is nothing to read,
        rather than a read that failed.
        """
        if not project_id or not self._api_host:
            return []
        payload = self._domino_get(f"/v4/projects/{project_id}/collaborators")
        record = self._project_record(project_id)
        owner_id = str(record.get("ownerId") or "")
        if not owner_id:
            # A record that answered but named no owner is the same hazard as one that did not
            # answer at all, and it is the quieter of the two: every row would come back
            # `owner=False`, the modal would offer Remove on the Project owner, and the creator
            # would learn better from a refusal after the click — which is the thing this read
            # exists to prevent. Refused here rather than rendered.
            raise ResourceUnavailable(brand.text(
                "{service} did not say who owns this {project}, so {assistantName} cannot show who "
                "may be removed from it.",
                service=_platform_api(),
            ))
        # `collaboratorId`, not `id` — the role-bearing record spells the user id differently from
        # the name-bearing one (verified live, 2026-09-03; a first pass read `id`, matched nothing,
        # and every role came back empty with no error to show for it). `id` is still accepted
        # because it costs one clause and the alternative failure is silent.
        roles = {
            str(c.get("collaboratorId") or c.get("id") or ""): str(c.get("projectRole") or "")
            for c in record.get("collaborators") or []
            if isinstance(c, dict)
        }
        out: list[Collaborator] = []
        for person in payload or []:
            if not isinstance(person, dict):
                continue
            uid = str(person.get("id") or "")
            if not uid:
                continue
            out.append(Collaborator(
                id=uid,
                name=str(person.get("fullName") or person.get("userName") or uid),
                title=str(person.get("userName") or ""),
                avatar=str(person.get("avatarUrl") or ""),
                role=roles.get(uid, ""),
                owner=uid == owner_id,
            ))
        return out

    def _project_record(self, project_id: str) -> dict:
        """This one project's record, which carries `ownerId` and the roles.

        The single-project read, not the listing. A first pass picked the project out of
        `GET /v4/projects`, and that is a paging bug waiting for a builder who belongs to enough
        projects: the listing would answer 200 without their project in it, and a healthy project
        would report as a failed read. This route answers about the project asked for or refuses,
        which is the only two things worth telling apart.

        (`/api/projects/beta/projects/{id}` is NOT the equivalent — it 404s. Verified 2026-09-03,
        along with the fields read below.)
        """
        record = self._domino_get(f"/v4/projects/{project_id}")
        return record if isinstance(record, dict) else {}

    def list_directory(self) -> list[Person]:
        """Everyone on the deployment: `GET /v4/users`, which needs no paging (the `/api/users/v1`
        equivalent caps at 10 unless asked otherwise, and answers the same set).

        Not filtered here. A creator picks from the people who are NOT on the project yet, and that
        subtraction needs both lists at once — so the whole directory travels and the browser does
        it. 397 rows was the live figure, around 60KB.
        """
        if not self._api_host:
            return []
        out: list[Person] = []
        for record in records_of(self._domino_get("/v4/users")):
            if not isinstance(record, dict):
                continue
            uid = str(record.get("id") or "")
            if not uid:
                continue
            out.append(Person(
                id=uid,
                name=str(record.get("fullName") or record.get("userName") or uid),
                title=str(record.get("userName") or ""),
                avatar=str(record.get("avatarUrl") or ""),
            ))
        return out

    def caller_id(self) -> str:
        """Whoever this adapter's token acts as, in Domino's own id space.

        `/api/me` cannot answer this: it reads the viewer JWT, whose subject is the identity
        provider's id, and that does not join against a collaborator row. Not knowing costs two
        Remove buttons that Domino would refuse anyway, so this degrades to "" rather than raising —
        the one read in this feature whose failure is not worth a wall.
        """
        if not self._api_host:
            return ""
        try:
            user = (self._domino_get("/api/users/v1/self") or {}).get("user") or {}
        except ResourceUnavailable:
            return ""
        return str(user.get("id") or "")

    # The public API's collaborator routes. `/api/projects/v1/...` registers only POST and DELETE on
    # this path, so a GET or an OPTIONS against it 404s while the write works — never read a path's
    # existence off an OPTIONS on this platform.
    def _collaborators_path(self, project_id: str) -> str:
        return f"/api/projects/v1/projects/{project_id}/collaborators"

    def add_collaborator(self, project_id: str | None, user_id: str) -> None:
        """Add one person, in the one role Sage assigns.

        Lowercase `contributor` is what the write takes; the read answers `Contributor`. Adding
        somebody who is already on the project is a 400 whose only marker is an English sentence
        ("... is already part of project ..."), and matching that sentence would break silently the
        day Domino rewords it. So ANY 400 is answered by re-reading the list once: if the person is
        on the project, the creator's intent holds however it came about. A 400 the re-read does not
        confirm is still a refusal, and a 403 is never re-read — asking twice cannot turn a no into
        a yes.
        """
        if not project_id or not self._api_host:
            raise ResourceUnavailable(brand.text(
                "{assistantName} is not running against {platformName}, so it cannot add anybody "
                "to this {project}."
            ))
        try:
            self._post(
                self._collaborators_path(project_id),
                {"id": user_id, "role": "contributor"},
                root=self._api_host,
                service=_platform_api(),
                token_provider=self._api_token_provider,
            )
        except ResourceUnavailable as e:
            if e.status == 400 and self._is_on_project(project_id, user_id):
                return
            raise

    def _is_on_project(self, project_id: str, user_id: str) -> bool:
        """Whether this person is on the project, asked once. A read that fails here answers False:
        the add is then reported as the refusal it already was, rather than as a success nobody
        confirmed."""
        try:
            payload = self._domino_get(f"/v4/projects/{project_id}/collaborators")
        except ResourceUnavailable:
            return False
        return any(
            isinstance(p, dict) and str(p.get("id") or "") == user_id
            for p in payload or []
        )

    def remove_collaborator(self, project_id: str | None, user_id: str) -> None:
        """Take one person off the project. Under `GRANT_BASED` visibility this is also what takes
        away their access to any App published from it — one act, which is why the confirm names
        both effects."""
        if not project_id or not self._api_host:
            raise ResourceUnavailable(brand.text(
                "{assistantName} is not running against {platformName}, so it cannot change who is "
                "on this {project}."
            ))
        self._send(
            "DELETE",
            f"{self._collaborators_path(project_id)}/{user_id}",
            root=self._api_host,
            service=_platform_api(),
            token_provider=self._api_token_provider,
        )

    def list_data_sources(self) -> list[DataSource]:
        """Data Sources this caller has permission on, SQL kinds only, with readiness asked for.

        No project scope, deliberately: the live probe that settled this found the project-scoped
        listing answering `200 []` for a user with a working Snowflake source, because attaching a
        source to a project is optional bookkeeping in Domino. Permission is what decides usability,
        and the public listing is keyed on it.
        """
        if not self._api_host:
            raise ResourceUnavailable(brand.text(
                "{assistantName} lists {dataSourcePlural} from the {platformName} API, and it is "
                "not configured to reach one, so it cannot tell which {dataSourcePlural} you have."
            ))
        path = "/api/datasource/v1/datasources"
        rows: list[DataSource] = []
        offset = 0
        for _ in range(self._MAX_PAGES):
            payload = self._get(
                path,
                root=self._api_host,
                service=_platform_api(),
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

    def list_columns(self, source: DataSource, database: str, schema: str,
                     table: str = "") -> list[Column]:
        """What the bound tables hold, in one query (#15).

        One statement for the whole Scope, even when it is a schema of two hundred tables: the
        cascade already measures ~3s a level against the live warehouse, so a query per table would
        turn binding a schema into minutes. `table` narrows it when the creator picked one.

        A connector with a cascade but no columns statement raises rather than answering []. An empty
        list is what a schema with no tables looks like, and telling the two apart is the whole reason
        the cascade's unverified dialects are affordable.
        """
        dialect = dialect_for(source)
        if dialect.columns is None:
            raise ResourceUnavailable(brand.text(
                "{assistantName} cannot read the columns inside a {kind} {dataSource}, so the "
                "agent will have to be told what the tables hold.",
                kind=source.connector or source.connector_type,
            ))
        rows = self._introspect_rows(
            source, dialect.statement(dialect.columns, database=database, schema=schema, table=table))
        return [
            Column(str(r.get("table_name") or ""), str(r.get("column_name") or ""),
                   str(r.get("data_type") or ""))
            for r in rows if str(r.get("column_name") or "")
        ]

    def sample_rows(self, source: DataSource, database: str, schema: str, table: str,
                    limit: int = 5) -> SampleRows:
        """A handful of real rows out of one table, because a creator asked for them (#16).

        `SELECT *` with the store's own row limit, which is not spelled the same everywhere — hence a
        statement per dialect rather than one with `LIMIT` appended. The table name is validated and
        quoted by `statement`, as every other identifier Sage sends is.
        """
        dialect = dialect_for(source)
        if dialect.sample is None:
            raise ResourceUnavailable(brand.text(
                "{assistantName} cannot read rows out of a {kind} {dataSource}, so it cannot show "
                "the agent what this table holds.",
                kind=source.connector or source.connector_type,
            ))
        frame = self._query(source, dialect.statement(
            dialect.sample, database=database, schema=schema, table=table, limit=max(1, int(limit))))
        columns, rows = frame_rows(frame)
        return SampleRows(table, columns, rows)

    def _introspect_rows(self, source: DataSource, sql: str) -> list[dict]:
        """`_introspect`, for a statement whose answer is rows rather than a list of names.

        Column keys are lower-cased, because the same `AS table_name` comes back as `TABLE_NAME` from
        Snowflake and `table_name` from Postgres — a caller keying on either spelling would work on
        one warehouse and silently return nothing on the next.
        """
        frame = self._query(source, sql)
        records = frame.to_dict("records") if hasattr(frame, "to_dict") else []
        return [{str(k).lower(): v for k, v in row.items()} for row in records]

    def _introspect(self, source: DataSource, sql: str) -> list[str]:
        """Run one read-only introspection statement against a Data Source and return the names.

        `domino_data` rather than HTTP, unlike everything else in this class: the SQL path is Arrow
        Flight over gRPC to `datasource-proxy`, which is where the store's credentials live, and there
        is no REST equivalent to reach instead.

        The package ships in the Domino base image, and that was never the question — the
        orchestrator runs under `uv run`, in an isolated venv that cannot see system site-packages,
        so an image full of it still raised the ImportError below in a live builder while listing
        worked fine. It is now declared as the `domino` extra and installed into THAT venv by the
        image build, which asserts the import rather than assuming it.

        The import stays local to this method, as `httpx`'s does, because the extra is deliberately
        not part of `--extra dev`: it pulls pandas and pyarrow, and a laptop running the tests has
        to be able to run everything else without them.

        Resolved by NAME, not by id: `get_datasource` takes the name, verified live, and a source
        resolves with no project attachment. The name arrives from `list_data_sources`, so it is
        Domino's own, not a caller's.
        """
        return name_column(self._query(source, sql))

    def _query(self, source: DataSource, sql: str) -> Any:
        """One read-only statement against a Data Source, as a frame. The import notes above apply."""
        try:
            from domino_data.data_sources import DataSourceClient
        except ImportError as e:
            raise ResourceUnavailable(brand.text(
                "{assistantName} reads a {dataSource}'s contents through the {platformName} data "
                "library, which is not installed here. {dataSourcePlural} will still list, but "
                "{assistantName} cannot look inside one."
            )) from e
        try:
            client = DataSourceClient()
            return client.get_datasource(source.name).query(sql).to_pandas()
        except Exception as e:
            # The store's own words, scrubbed. Naming the source matters because the creator is
            # looking at a list of them, and the connector's own error is the only signal that
            # separates "Sage sent the wrong SQL for this connector" from "this schema is empty".
            raise ResourceUnavailable(f"{source.name} did not answer: {readable_error(e)}") from e

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
                service=_platform_api(),
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

        try:
            token = (token_provider or self._token_provider)()
            r = httpx.request(
                method,
                (self._root if root is None else root) + path,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                json=json_body,
                timeout=self._timeout_s,
            )
        except ResourceUnavailable:
            raise
        except Exception as e:
            # `service` arrives resolved and `path` is a literal API path, so both travel as values:
            # a value is not scanned again, and the sentence stays one literal.
            raise ResourceUnavailable(brand.text(
                "{service} didn't answer at {path} ({error}). "
                "{resourcePlural} will be listed once it responds.",
                service=service, path=path, error=type(e).__name__,
            )) from e
        if r.status_code >= 400:
            raise ResourceUnavailable(
                brand.text("{service} answered {code} at {path}.",
                           service=service, code=r.status_code, path=path),
                r.status_code,
            )
        # An unauthenticated call to the gateway returns 200 carrying a Keycloak LOGIN PAGE, so the
        # status is not proof of an answer (verified — DOMINO-PRIMITIVES.md). Inspect the body.
        try:
            return r.json()
        except ValueError as e:
            raise ResourceUnavailable(brand.text(
                "{service} returned a non-JSON body at {path}. That is what a signed-out "
                "session looks like, so this builder's token for it may have expired.",
                service=service, path=path,
            )) from e


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

# table -> [(column, store type)], for the columns the agent is given (#15). Keyed on table name
# alone: the fake's table names are distinct across the tree, and a Scope is always resolved to one
# schema before columns are asked for.
#
# A table that is in the tree and NOT here answers with no columns, which is deliberate — `STAGING`
# is empty and `SCRATCH_FORECAST` is a table nobody described, so a local run rehearses a Scope whose
# schema comes back thin as well as one that comes back full.
# table -> rows, positionally matching `_FAKE_COLUMNS`. Only a couple of tables carry any: a local
# run has to rehearse BOTH sides of #16 — a table the creator chose to sample and one they did not —
# and inventing plausible rows for every table would make sharing look like the default it must not
# be.
_FAKE_ROWS: dict[str, list[list]] = {
    "FCT_USAGE_DAILY": [
        ["2026-08-18", "ACC-1042", 37, 12.5],
        ["2026-08-18", "ACC-2213", 4, 0.0],
        ["2026-08-19", "ACC-1042", 41, 18.25],
    ],
    "DIM_ACCOUNT": [
        ["ACC-1042", "Northwind Trading", "Enterprise", "2024-03-11T00:00:00"],
        ["ACC-2213", "Bluebird Health", "Mid-Market", "2025-11-02T00:00:00"],
    ],
    "accounts": [
        [1, "ops@northwind.example", "2024-03-11T09:14:00"],
        [2, "admin@bluebird.example", "2025-11-02T16:02:00"],
    ],
}

_FAKE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "DIM_ACCOUNT": [("ACCOUNT_ID", "VARCHAR"), ("ACCOUNT_NAME", "VARCHAR"),
                    ("SEGMENT", "VARCHAR"), ("SIGNED_AT", "TIMESTAMP_NTZ")],
    "DIM_DATE": [("DATE_KEY", "DATE"), ("FISCAL_QUARTER", "VARCHAR"), ("IS_WEEKEND", "BOOLEAN")],
    "FCT_USAGE_DAILY": [("USAGE_DATE", "DATE"), ("ACCOUNT_ID", "VARCHAR"),
                        ("SEATS_ACTIVE", "NUMBER"), ("COMPUTE_HOURS", "FLOAT")],
    "FCT_SUBSCRIPTION_REVENUE": [("MONTH", "DATE"), ("ACCOUNT_ID", "VARCHAR"),
                                 ("ARR_USD", "NUMBER"), ("CURRENCY", "VARCHAR")],
    "V_ARR_WATERFALL": [("MONTH", "DATE"), ("MOVEMENT", "VARCHAR"), ("AMOUNT_USD", "NUMBER")],
    "V_CUSTOMER_HEALTH": [("ACCOUNT_ID", "VARCHAR"), ("HEALTH_SCORE", "NUMBER")],
    "policies": [("policy_id", "int"), ("holder_name", "varchar"), ("premium", "decimal")],
    "claims": [("claim_id", "int"), ("policy_id", "int"), ("amount", "decimal"),
               ("filed_on", "date")],
    "quotes": [("quote_id", "int"), ("policy_id", "int"), ("quoted_at", "datetime")],
    "accounts": [("id", "integer"), ("email", "text"), ("created_at", "timestamp")],
    "events": [("id", "integer"), ("account_id", "integer"), ("kind", "text"),
               ("at", "timestamp")],
    "access_log": [("at", "timestamp"), ("actor", "text"), ("path", "text")],
}


@dataclass
class FakeResourceProvider:
    """In-memory Resources for local testing/demo (no gateway)."""

    aliases: list[LlmAlias] = field(default_factory=lambda: list(_FAKE_ALIASES))
    model_apis: list[ModelApi] = field(default_factory=lambda: list(_FAKE_MODEL_APIS))
    # Empty by default: none of the fake aliases is Domino-hosted, so there is nothing for one to
    # point at, and a local run should not invent an endpoint the alias list does not reference.
    hosted_endpoints: list[HostedEndpoint] = field(default_factory=list)
    # Both empty for the same reason as `hosted_endpoints`, and it is the reason a local run reads
    # as "not connected" rather than "nobody to add": the difference is drawn from whether there is
    # a project id at all, not from these being short. Seed them to walk the People flow locally.
    collaborators: list[Collaborator] = field(default_factory=list)
    directory: list[Person] = field(default_factory=list)
    caller: str = ""
    data_sources: list[DataSource] = field(default_factory=lambda: list(_FAKE_DATA_SOURCES))
    # source id -> database -> schema -> tables, for the cascade (#11).
    tree: dict[str, dict[str, dict[str, list[str]]]] = field(
        default_factory=lambda: dict(_FAKE_TREE))
    # table -> [(column, type)], for the schema the agent is given (#15).
    columns: dict[str, list[tuple[str, str]]] = field(
        default_factory=lambda: dict(_FAKE_COLUMNS))
    # table -> rows, for the samples a creator can choose to share (#16).
    rows: dict[str, list[list]] = field(default_factory=lambda: dict(_FAKE_ROWS))

    def list_llm_aliases(self) -> list[LlmAlias]:
        return list(self.aliases)

    def list_hosted_endpoints(self) -> list[HostedEndpoint]:
        return list(self.hosted_endpoints)

    def list_model_apis(self, project_id: str | None) -> ModelApiListing:
        """The project is ignored, not validated: this fake stands in for a Domino that answers, and
        a local run has no project ids for a test to be right or wrong about.

        Complete, because there is no fan-out here to come back short. A test that wants a partial
        listing subclasses and says so, the way `test_preflight.py` does."""
        return ModelApiListing(list(self.model_apis))

    def get_model_api(self, model_api_id: str) -> ModelApi | None:
        """Answers from the same list the fan-out would have found it in. A fake that answered for
        ids absent from its own listing would let a test pass on a model Domino never had."""
        return next((m for m in self.model_apis if m.id == model_api_id), None)

    def list_collaborators(self, project_id: str | None) -> list[Collaborator]:
        """Empty by default, like `hosted_endpoints`: a local run has no Domino directory behind it,
        and inventing colleagues would put names on a review panel nobody can actually send to."""
        return list(self.collaborators)

    def list_directory(self) -> list[Person]:
        return list(self.directory)

    def caller_id(self) -> str:
        return self.caller

    def add_collaborator(self, project_id: str | None, user_id: str) -> None:
        """Refuses an id the directory does not hold, the way Domino would. A picker offers the
        directory, so an id that is not in it did not come from the picker — a fake that accepted it
        would let a test pass on a person Domino has never heard of."""
        person = next((p for p in self.directory if p.id == user_id), None)
        if person is None:
            raise ResourceUnavailable(brand.text(
                "{platformName} has no such person, so there is nobody to add."))
        if any(c.id == user_id for c in self.collaborators):
            return
        self.collaborators.append(Collaborator(
            id=person.id, name=person.name, title=person.title, avatar=person.avatar,
            role="Contributor",
        ))

    def remove_collaborator(self, project_id: str | None, user_id: str) -> None:
        self.collaborators[:] = [c for c in self.collaborators if c.id != user_id]

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

    def list_columns(self, source: DataSource, database: str, schema: str,
                     table: str = "") -> list[Column]:
        """The columns of the bound tables (#15), from `columns` above.

        Refuses exactly where the real one does — a connector with a cascade but no columns statement
        — so the state where Sage can list a schema and not read it is reachable without a warehouse.
        """
        dialect = dialect_for(source)
        if dialect.columns is None:
            raise ResourceUnavailable(brand.text(
                "{assistantName} cannot read the columns inside a {kind} {dataSource}, so the "
                "agent will have to be told what the tables hold.",
                kind=source.connector or source.connector_type,
            ))
        wanted = self.list_tables(source, database, schema)
        if table:
            wanted = [t for t in wanted if t == table]
        return [Column(t, name, ctype) for t in wanted for name, ctype in self.columns.get(t, [])]

    def sample_rows(self, source: DataSource, database: str, schema: str, table: str,
                    limit: int = 5) -> SampleRows:
        """Rows for one table, from `rows` above. Refuses where the real one does.

        A table with no rows recorded answers with none rather than raising: an empty table is a real
        thing to sample, and it is a different answer from a connector that cannot be sampled at all.
        """
        dialect = dialect_for(source)
        if dialect.sample is None:
            raise ResourceUnavailable(brand.text(
                "{assistantName} cannot read rows out of a {kind} {dataSource}, so it cannot show "
                "the agent what this table holds.",
                kind=source.connector or source.connector_type,
            ))
        names = [name for name, _ in self.columns.get(table, [])]
        rows = [[sample_value(v) for v in row] for row in self.rows.get(table, [])[:max(1, limit)]]
        return SampleRows(table, names, rows)
