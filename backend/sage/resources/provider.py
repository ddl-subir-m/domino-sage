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


class ResourceProvider(Protocol):
    def list_llm_aliases(self) -> list[LlmAlias]: ...

    # Takes the project explicitly, as the asset provider's list_datasets(project_id) does: the
    # orchestrator owns which project this builder is bound to, and the provider stays a client.
    def list_model_apis(self, project_id: str | None) -> list[ModelApi]: ...

    # No project argument, unlike the two above, and that asymmetry is the finding: a Data Source is
    # permission-scoped to the person, not the project.
    def list_data_sources(self) -> list[DataSource]: ...


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
            )
        )
    return out


def merge_readiness(sources: list[DataSource], statuses: Any) -> list[DataSource]:
    """Attach `POST /v4/datasource/authentication-status`'s answer to the rows it was asked about.

    The endpoint answers a bare array of booleans with no ids in it, so the only thing tying an answer
    to a source is its POSITION in the list that was sent. That is a contract worth distrusting: if
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
_FAKE_DATA_SOURCES = (
    DataSource("ds-dwh", "Snowflake-Data-Warehouse", "Snowflake", "Shared",
               "The company warehouse. Reads across every schema.", True),
    DataSource("ds-test", "test", "Snowflake", "Individual", None, False),
    DataSource("ds-mssql", "AWS_MSSQL", "SQL Server", "Individual", None, True),
    DataSource("ds-reporting", "reporting-replica", "PostgreSQL", "Shared",
               "Read replica of the reporting database.", None),
)


@dataclass
class FakeResourceProvider:
    """In-memory Resources for local testing/demo (no gateway)."""

    aliases: list[LlmAlias] = field(default_factory=lambda: list(_FAKE_ALIASES))
    model_apis: list[ModelApi] = field(default_factory=lambda: list(_FAKE_MODEL_APIS))
    data_sources: list[DataSource] = field(default_factory=lambda: list(_FAKE_DATA_SOURCES))

    def list_llm_aliases(self) -> list[LlmAlias]:
        return list(self.aliases)

    def list_model_apis(self, project_id: str | None) -> list[ModelApi]:
        """The project is ignored, not validated: this fake stands in for a Domino that answers, and
        a local run has no project ids for a test to be right or wrong about."""
        return list(self.model_apis)

    def list_data_sources(self) -> list[DataSource]:
        return list(self.data_sources)
