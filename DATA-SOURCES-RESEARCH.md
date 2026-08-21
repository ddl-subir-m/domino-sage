# Domino Data Sources — research

Research to decide the smallest shippable slice for letting Sage users pick an **already-provisioned Domino Data Source** (Snowflake today, BigQuery next) instead of uploading a file.

Takeaways for a reader in a hurry:

- **Discovery is a one-call job — but pick the right call.** Use the *user-scoped public* endpoint. A live probe showed the *project-scoped* endpoint returns `200 []` for a user who **does** have a working Snowflake source, because attaching a data source to a project is optional bookkeeping. Building the picker on it would show an empty panel to a user who is fully able to query.
- **Reading data needs no new dependency.** `dominodatalab-data` **6.7.4 is already installed** in the Sage workspace image, and the discovery call needs no library at all.
- **A published Domino App is never "just static".** Domino always runs `app.sh`, and Sage's own published apps already run a Node process on `0.0.0.0:8888`. A data endpoint is an edit to that entrypoint, not a from-scratch backend.
- **The real constraint is identity, not plumbing.** A published App's container holds only the **publisher's** credentials, so any query a viewer triggers runs as the publisher. For an `Individual`-credential source that re-exports one person's database access to everyone with the URL. Fixing it needs a SysAdmin-only, irreversible platform feature.

---

## Bottom line for scoping

**The scope fork is not "can we query data" — it is "whose credentials".**

Two facts collide:

1. Domino's own docs state that for **Domino Apps** the identity used for data source access is *"The user who published the app regardless of who is accessing the app."* ([share-data-sources-securely](https://docs.dominodatalab.com/en/latest/user_guide/33ea62/share-data-sources-securely/))
2. Sage publishes apps that anyone with the URL can be granted access to.

So a naive "app queries Snowflake live" feature means **every viewer reads through the publisher's Snowflake credentials**. For a `Shared` (service-account) data source that is arguably intended. For an `Individual`-credential data source that is a credential-leak-shaped problem, and it is the default behaviour, not an opt-in.

Making the app act as the *viewer* requires Domino's **extended identity propagation**, which per the docs is disabled by default, requires the `SecureIdentityPropagationToAppsEnabled` feature flag plus the `com.cerebro.domino.apps.extendedIdentityPropagationToAppsEnabled` setting, can only be published by **SysAdmins or CloudAdmins**, needs viewer consent, and **cannot be disabled once enabled on an app** ([app-security-and-identity](https://docs.dominodatalab.com/en/latest/user_guide/cb9195/app-security-and-identity/)). That is not a slice.

**Therefore the smallest shippable slice is build-time, not runtime:**

> Let the user **browse and pick** a Domino Data Source in the Sage UI; let the **agent** query it during the build session (where the identity is correctly the builder's own, exactly like a Workspace); and **bake the result into the static bundle** — reusing the `.sage/attachments.json` + `scripts/rehydrate-data.mjs` path that already exists for uploads.

This slice:
- inherits the correct credential semantics for free (the agent runs as the builder, and per the identity table a Workspace/Job uses *"The user who started the execution"*),
- needs **no** Domino feature flags, no admin involvement, and no backend in the published app,
- needs **no new Python dependency** — `dominodatalab-data` 6.7.4 is already in the workspace image (live-verified), and discovery needs no library,
- changes only the discovery surface (a sibling to `AssetProvider`) and the build-time data path,
- and defers the whole per-viewer identity question until there is demand for live data.

The one piece of governance work it should still carry: **warn at publish time when the chosen source is `credentialType: Individual`**, reusing the sensitivity/sovereign-lock path that already exists. See Q3's runtime verdict.

Live per-viewer querying is a **separate, later** project gated on a platform feature and an admin. Do not let it into v1's scope. See [`sage-l2-l3-scope-sizing`] reasoning precedent: the published app being static means a backend is the unlisted prerequisite.

---

## Q1 — Discovery: which endpoint lists usable Data Sources?

### VERIFIED

There are **two API generations**, and I confirmed both by fetching the specs **unauthenticated (HTTP 200)** from cloud-dogfood. The two spec URLs are named by a first-party Domino script:

`automl-service/scripts/download_api_specs.sh` ([source](https://github.com/dominodatalab/AutoML_Extension/blob/main/automl-service/scripts/download_api_specs.sh)):
```bash
PUBLIC_URL="$BASE_URL/assets/public-api.json"
PRIVATE_URL="$BASE_URL/assets/swagger.json"
VERSION_URL="$BASE_URL/version"
```
So `swagger.json` — the spec behind the Swagger-UI link — is the **PRIVATE** spec. The **PUBLIC** spec is a different file.

#### ✅ Recommended: the PUBLIC endpoint

From `https://cloud-dogfood.domino.tech/assets/public-api.json` (`openapi: 3.0.3`, `info.title: "Domino Public API"`, `info.version: "6.4.0"`, 220 paths):

```
GET /api/datasource/v1/datasources
    operationId: getAccessibleAndActiveDataSources
    tag: DataSource
    summary: "Get all active Data Source the user has access to"
    query params (all optional): dataSourceNames, offset, limit
    200 -> PaginatedDataSourceEnvelopeV1
```

This is **exactly** the semantic Sage needs: *active* + *the user has access to*. No project id required — permission-scoped to the caller.

Response shape (`PaginatedDataSourceEnvelopeV1`):
- `dataSources`: array of `DataSourceEnvelopeV1`
- `metadata`: `PaginatedMetadataV1` (`pagination`, `requestId`, `notices`)

`DataSourceEnvelopeV1` — required: `id`, `name`, `ownerId`, `ownerUsername`, `dataSourceType`, `authType`, `credentialType`, `config`, `permissions`, `lastUpdated`, `displayName`; optional `description`.
- `dataSourceType` → `DataSourceTypeV1`: **free-form `string`** in the public spec, described as *"The configuration type of the Data Source"*, `example: "ADLSConfig"`. It is **not** enumerated publicly.
- `authType` → `DataSourceAuthTypeV1`: also a free-form `string`, `example: "AzureBasic"`.
- **`credentialType` → `DataSourceCredentialTypeV1`, enum: `["Individual", "Shared"]`** — this answers Q3's static half directly, in the list response.
- `permissions` → `DataSourcePermissionsV1`: `isEveryone` (bool), `userAndOrganizationIds` (string[]).
- `config` → `DataSourceConfigV1`: map of string→string, `example: {"host": "example-host.com"}`.

Note there is **no `status` field** on the public envelope — the endpoint already filters to active (per its operationId and summary).

#### The PRIVATE (internal) endpoints

From `https://cloud-dogfood.domino.tech/assets/swagger.json` (735 paths, 41 of them `/datasource*`). Independently corroborated by the authenticated copy the user saved at `spikes/domino-probes/dogfood-swagger.json`.

```
GET /v4/datasource/projects/{projectId}     operationId: getDataSourcesByProject  -> array<DataSourceDto>
GET /v4/datasource/dataSources/all          operationId: getAllDataSources        -> array<DataSourceDto>
GET /v4/datasource/name/{name}              -> DataSourceDto
GET /v4/datasource/{dataSourceId}/authentication-status  -> boolean
POST /v4/datasource/authentication-status   body {dataSourceIds:[...]} -> array<boolean>
```

The private `DataSourceDto` is **richer** than the public envelope. Required: `id`, `name`, `ownerId`, `ownerInfo`, `addedBy`, `dataSourceType`, `config`, `dataSourcePermissions`, `lastUpdated`, `lastUpdatedBy`, `lastAccessed`, `addedToProjectTimeMap`, `projectIds`, `adminInfo`, `status`. Plus optional `displayName`, `description`, `dataPlanes`, `engineInfo`, `useAllDataPlanes`, `authType`.

Things the private spec gives you that the public one does not:
- **`status` enum: `Pending` | `Active` | `Deleted`** — a genuine readiness signal.
- **`dataSourceType` IS enumerated** (~35 values) and includes **`SnowflakeConfig`** and **`BigQueryConfig`**, the user's two connectors.
- **`authType` enumerated** (~18): `AzureBasic`, `Basic`, `GCPBasic`, `AWSIAMBasic`, `AWSIAMBasicNoOverride`, `AWSIAMRole`, `AWSIAMRoleWithUsername`, `OAuth`, `PersonalToken`, `UserOnly`, `BasicOptional`, `NoAuth`, `ClientIdSecret`, `OAuthToken`, `APIKey`, `CertAuth`, `KeyPair`, `CustomDSN`.
- `dataSourcePermissions` → `DataSourcePermissionsDto`: `isEveryone`, `userIds`, and **`credentialType` enum `["Individual","Shared"]`**.
- `engineInfo` → `EngineInfoDto`: `engineType` enum `["Domino","Starburst"]`, `catalogEntryName`.

**Gotcha worth recording:** `GET /v4/datasource` is **not** a list. Its operationId is `isAllowedToAccessDataSourceAdminPage` and it returns a bare `boolean`. Do not reach for the obvious-looking path.

#### The `/v4` prefix — VERIFIED (upgraded from "assume")

Three independent confirmations:
1. The dogfood `swagger.json` I fetched declares `"servers": [{"url": "/v4"}]` at the top level.
2. First-party working code: `resolve_domino_v4_api_base_url()` returns `f"{resolve_domino_api_host()}/v4"` — [AutoML_Extension `app/core/domino_http.py`:116-118](https://github.com/dominodatalab/AutoML_Extension/blob/main/automl-service/app/core/domino_http.py).
3. This repo already does it: `/Users/subirmansukhani/Desktop/domino-sage/backend/sage/provision/domino.py` calls `/v4/...` paths.

Conversely the **public** spec has **no** `servers` block, because its paths already carry their own prefix (`/api/datasource/v1/...`). AutoML's public client uses the bare host: `get_domino_public_api_client_sync()` sets `base_url = resolve_domino_api_host()` with no suffix. The user's global notes say the same thing: *"Do NOT add `/api/` prefix to v4 platform endpoints."*

#### Auth — already solved in this repo

Both specs declare the same two schemes, and the root `security` block lists them as **alternatives** (either alone suffices):
```json
"security": [ { "BearerAuthentication": [] }, { "DominoApiKey": [] } ]
"DominoApiKey":          { "type": "apiKey", "in": "header", "name": "X-Domino-Api-Key" }
"BearerAuthentication":  { "type": "apiKey", "in": "header", "name": "Authorization" }
```

Sage already sends exactly the Bearer form — `/Users/subirmansukhani/Desktop/domino-sage/backend/sage/provision/domino.py:142`:
```python
return {"Authorization": f"Bearer {self._token_provider()}", "Accept": "application/json"}
```
**A new data-source client method reuses this auth path verbatim.** No new credential plumbing.

#### Which to use — recommendation

Use the **public** `GET /api/datasource/v1/datasources`. Reasons:
- It is the stable, versioned, permission-scoped surface, and its semantics ("active" + "user has access to") are precisely the panel's requirement.
- **It matches the precedent already set in this repo.** `DominoAssetProvider` calls `GET {api_host}/api/datasetrw/v2/datasets` (`backend/sage/assets/provider.py:194`, documented at `:8`). Same `/api/<service>/v<n>/` family.
- **This repo already treats the two families differently, and says so.** The module docstring of `backend/sage/provision/domino.py:16-19` explicitly flags its `/v4/...` calls as the internal / unverified seam, while projects, apps and datasets deliberately use the public `/api/.../beta|v1|v2` families. Of the 21 Domino calls in that file only 6 are `/v4` (all workspace/environment lifecycle, where no public equivalent exists). **Choosing the public data-source endpoint is the house convention, not a new opinion.**

Consider *supplementing* with the private spec only if the panel needs the `Pending` readiness state or a trustworthy connector-type enum for icon/label mapping — both of which the public spec omits.

#### 🔴 LIVE RESULT — the project-scoped endpoint returned an EMPTY ARRAY

Live probe on cloud-dogfood, project "Sage", 2026-08-18 (`spikes/domino-probes/datasource_probe.sh`):

```
GET {DOMINO_API_HOST}/v4/datasource/projects/6a5e8b03242fc543ed24282d
Authorization: Bearer <sidecar token from http://localhost:8899/access-token, 1921 chars>
-> HTTP 200
-> []
```

**200 with zero rows, even though the user has a Snowflake data source configured.** The `/v4` prefix is confirmed correct (200, not 404). The empty result is a *scoping* result, not an error.

**Cause — and it is a real product precondition.** Data sources are global, but being *attached to a project* is a separate, optional step. From [use-data-sources](https://docs.dominodatalab.com/en/latest/user_guide/fa5f3a/use-data-sources/), there is an explicit **"Add an existing Data Source to a project"** flow:

1. In your project, go to **Data** > **Data Sources** > **Add Data Source**.
2. Select an existing Data Source from the list.
3. Click **Add to Project**.

The docs are explicit that this is **not** required in order to use the data source:

> *"If you don't add a Data Source to a project, you can still use it in your code if you have permission to access it."*

and that the step exists mainly for visibility:

> *"This step is not required, but helps you see the data sources used in projects."*

It becomes mandatory only for generated code snippets: *"The Data Source must be [added to the project] to enable snippets."* Attachment can also happen implicitly — the docs note a data source gets associated when it is referenced in project code.

**This is the single most important design consequence of the live probe:**

> The project-scoped private endpoint (`/v4/datasource/projects/{projectId}`) reflects an **optional bookkeeping relationship**, not usability. A user can be fully able to query Snowflake while that endpoint returns `[]`. **Building the picker on it would show an empty panel to a user who has a working data source** — the worst possible first impression, and exactly the "dead end" the design principles warn against.

Therefore: **use the user-scoped public endpoint `GET /api/datasource/v1/datasources`** (`getAccessibleAndActiveDataSources` — *"all active Data Source the user has access to"*). It is keyed on permission, which is what actually determines usability. This live result promotes that from "cleaner" to **required**.

Secondary consequence for the UI: if Sage ever *does* show project-scoped data, it should surface the attach step rather than an empty state — and note that attaching is what enables Domino's own snippet generation.

### LIVE-VERIFY

Settled by the live probe: the `/v4` prefix (HTTP 200) and Bearer-sidecar auth. Still open:

- `LIVE-VERIFY` **The top priority now.** Does `GET {DOMINO_API_HOST}/api/datasource/v1/datasources` return the Snowflake source? The project-scoped endpoint returned `[]`, so **this call is what the whole picker rests on and it has not yet been run.** If it also returns empty, the discovery story needs rethinking.
- `LIVE-VERIFY` What the free-form `dataSourceType` string actually contains for Snowflake — `SnowflakeConfig` (matching the private enum) or something friendlier like `Snowflake`? **Decides the UI's type→label/icon mapping**; unknowable from the spec because the public spec leaves the field unenumerated.
- `LIVE-VERIFY` Whether the Snowflake source's `credentialType` is `Individual` or `Shared` on dogfood. **This decides whether Q3's hazard applies to the demo at all.**
- `LIVE-VERIFY` Whether the public endpoint is mounted on this deployment. Public spec says `6.4.0`; check `GET /version` (the first-party `download_api_specs.sh` pins against it).
- `LIVE-VERIFY` Pagination defaults — implicit `limit` if omitted?
- `LIVE-VERIFY` Whether a source the user can *see* but has not *authenticated* to (Individual credentials, none entered yet) appears in the public list or is filtered out. The private `authentication-status` endpoints exist because this state is real; whether the public list hides it is unknown.
- `LIVE-VERIFY` Whether attaching the Snowflake source to the Sage project (Data > Data Sources > Add Data Source) makes the project-scoped endpoint return it. Worth one click to confirm the mechanism, even though the picker should not depend on it.

---

## Q2 — Reading data from inside a Domino execution

### VERIFIED

**Library / package name.** Import package `domino_data`; PyPI distribution **`dominodatalab-data`**. Confirmed from the library's own `pyproject.toml` (`name = "dominodatalab-data"`, `version = "6.7.4"`, repository `https://github.com/dominodatalab/domino-data`, Apache-2.0, `python = "^3.10"`).

Runtime deps it drags in are non-trivial: `pandas>=1.3.0`, `httpx>=0.24.0`, `attrs`, `python-dateutil`, **`pyarrow>=15.0.2`**, `loguru`, `backoff`, `bson`, `urllib3>=2.6.0,<3`.

**Naming caveat:** the Domino docs page shows `from domino.data_sources import DataSourceClient`, but the actual current library source uses `domino_data.data_sources`. The plugin skill also uses `domino_data`. Treat `domino_data` as correct for `dominodatalab-data>=6`; the `domino.` form appears to be stale docs.

**Call sequence** (verified against real source in `domino_data/data_sources.py`):
```python
from domino_data.data_sources import DataSourceClient

client = DataSourceClient()            # reads env, builds two clients
ds = client.get_datasource("my-snowflake")   # HTTP GET
result = ds.query("SELECT ...")        # Arrow Flight do_get
df = result.to_pandas()
```

**What each step actually does:**

1. `DataSourceClient.__attrs_post_init__` (`data_sources.py:637-675`) resolves hosts:
   ```python
   flight_host = os.getenv(DOMINO_DATASOURCE_PROXY_FLIGHT_HOST)
   domino_host = os.getenv(DOMINO_API_PROXY, os.getenv(DOMINO_API_HOST, os.getenv(DOMINO_USER_HOST, "")))
   proxy_host  = os.getenv(DOMINO_DATASOURCE_PROXY_HOST, "")
   ```
   then builds `self.domino = AuthenticatedClient(base_url=f"{domino_host}/v4", ...)` (line 667) and `self.proxy_http = ProxyClient(base_url=proxy_host, ...)` (line 656).

2. `get_datasource(name)` (`data_sources.py:694-728`) → `GET {domino_host}/v4/datasource/name/{name}?runId={DOMINO_RUN_ID}`. The generated path is `"/datasource/name/{name}"` with a `runId` query param (`datasource_api_client/api/datasource/get_datasource_by_name.py:14-33`).

3. `.query(sql)` → `client.execute(...)` (`data_sources.py:858-898`) which serialises a `BoardingPass` to JSON and does an **Arrow Flight** `do_get`:
   ```python
   return self.proxy.do_get(flight.Ticket(ticket))     # line 898
   ```
   The ticket payload is `{"datasourceId", "sqlQuery", "configOverwrites", "credentialOverwrites"}` (`data_sources.py:612-620`).

**So yes — there is a Data API proxy, and it is TWO services, not one:**

| Env var | What it is | Used for |
|---|---|---|
| `DOMINO_DATASOURCE_PROXY_FLIGHT_HOST` | **Arrow Flight (gRPC)** endpoint | All SQL `query()` — tabular sources |
| `DOMINO_DATASOURCE_PROXY_HOST` | HTTP endpoint | Object-store ops (`/objectstore/key`, `/objectstore/list`, `/objectstore/metric`) |
| `DOMINO_API_PROXY` | Domino API proxy **and** the token sidecar base | `/v4` metadata calls; `{url}/access-token` |
| `DOMINO_DATA_API_GATEWAY` | default **`http://127.0.0.1:8766`** | VectorDB path only (`domino_data/vectordb.py:20`) |

**Token / auth resolution** — `domino_data/auth.py`. Precedence in `_get_auth_headers()` (lines 50-70): explicit `token` → `token_url` (sidecar) → `token_file` → `api_key`.
- Sidecar fetch is `GET {url}/access-token` (`auth.py:30`), wrapped in backoff.
- Token file default: **`/var/lib/domino/home/.api/token`** (`data_sources.py:59`), overridable by `DOMINO_TOKEN_FILE`.
- Header emitted: `Authorization: Bearer <jwt>` or `X-Domino-Api-Key: <key>` (`auth.py:53,68`).
- The **proxy** path uses different header names — `ProxyClient._get_auth_headers` sends `X-Domino-Jwt` (not `Authorization`) plus `X-Domino-Client-Source` and `X-Domino-Run-Id` (`auth.py:89-116`). Arrow Flight middleware sends lowercase `x-domino-jwt` / `x-domino-api-key` (`auth.py:168-177`).

Full env var list the library reads (`data_sources.py:51-60`): `DOMINO_API_HOST`, `DOMINO_API_PROXY`, `DOMINO_CLIENT_SOURCE`, `DOMINO_DATASOURCE_PROXY_HOST`, `DOMINO_DATASOURCE_PROXY_FLIGHT_HOST`, `DOMINO_RUN_ID`, `DOMINO_USER_API_KEY`, `DOMINO_USER_HOST`, `DOMINO_TOKEN_FILE`.

**A second, simpler token sidecar exists** and is what Domino app/plugin code actually uses: `http://localhost:8899/access-token`. Verified in the first-party plugin's MCP server (`mcp-servers/domino_mcp_server/domino_mcp_server.py`):
```python
resp = requests.get("http://localhost:8899/access-token")
token = resp.text.strip()
return {"Authorization": token if token.startswith("Bearer ") else f"Bearer {token}"}
```
The user's own global notes stress: **re-acquire this token on every API call because it expires very quickly.**

**`DOMINO_API_PROXY` and the `:8899` sidecar are the same thing — and this repo already assumes it.** `auth.py:30` fetches `{DOMINO_API_PROXY}/access-token`; this repo's probes do the identical thing with `8899` as the literal default — `spikes/domino-probes/control_plane.py:25`:
```python
SIDECAR = os.environ.get("DOMINO_API_PROXY", "http://localhost:8899").rstrip("/")
```
and `spikes/domino-probes/README.md:3-5` documents the contract as *"auth via the workspace sidecar (`DOMINO_API_PROXY` → `:8899/access-token`)"*. Production code encodes the resolved form directly: `backend/sage/gateway/client.py:26` sets `DEFAULT_SIDECAR_URL = "http://localhost:8899/access-token"`, with `static_token()` (`:29-31`) for a long-lived `dgw_` PAT and `sidecar_token()` (`:34-47`) fetching per call and stripping the `Bearer ` prefix.

Naming inconsistency to be aware of: **probes read `DOMINO_API_PROXY`, but the backend reads `GATEWAY_TOKEN_URL`** (defaulting to `DEFAULT_SIDECAR_URL`). A new data-source provider should follow the backend convention.

**Does it work in a plain container?** No — not for querying. The Flight/proxy hosts come **only** from Domino-injected env vars and have no defaults (`os.getenv(...)` with `""` fallback → an unusable client). `DOMINO_USER_API_KEY` still exists as an external escape hatch (`+ DOMINO_API_HOST`), but the plugin skill flags it as **deprecated and slated for removal**, and the docs warn that calling the Data API externally *"does not offer the same benefits and safeguards"*.

#### 🟢 LIVE RESULT — the injected environment, verbatim

From a live cloud-dogfood workspace, 2026-08-18. **`dominodatalab-data` 6.7.4 is ALREADY INSTALLED in the Sage workspace image** — `import domino_data` succeeds, **no install step needed**.

```
DOMINO_API_HOST=http://nucleus-frontend.domino-platform:80
DOMINO_API_PROXY=http://localhost:8899
DOMINO_DATA_API_GATEWAY=http://127.0.0.1:8766
DOMINO_DATASOURCE_PROXY_HOST=http://datasource-proxy.domino-platform:80
DOMINO_DATASOURCE_PROXY_FLIGHT_HOST=grpc://datasource-proxy.domino-platform:8080
DOMINO_MLFLOW_DEPLOYMENTS=http://127.0.0.1:8767
DOMINO_STARTING_USER_ID=66a821b1e77f2b566a1e5534
DOMINO_USER_API_KEY=<64 chars, PRESENT>
DOMINO_IS_LOCAL_DATA_PLANE=true
SF_PARTNER=DominoDataLab
```

This confirms the earlier inference: **`DOMINO_API_PROXY` is exactly `http://localhost:8899`.**

**What `datasource-proxy` is.** A cluster-internal Domino platform service (Kubernetes DNS `datasource-proxy.domino-platform`) that holds the actual database drivers and credentials. Client code never talks to Snowflake directly and never sees the credential — it asks the proxy, which fetches the credential from Vault, connects, and streams results back. It exposes **two ports for two different protocols**, and the library uses each for a different job:

| Env var | Live value | Protocol | Used for |
|---|---|---|---|
| `DOMINO_DATASOURCE_PROXY_FLIGHT_HOST` | `grpc://datasource-proxy.domino-platform:8080` | **Arrow Flight over gRPC** | **All SQL queries.** `ds.query(sql)` → `flight.FlightClient.do_get(Ticket(json))`. Results arrive as Arrow record batches, hence the `pyarrow` dependency and why `to_pandas()` is cheap. |
| `DOMINO_DATASOURCE_PROXY_HOST` | `http://datasource-proxy.domino-platform:80` | plain HTTP | **Object-store operations only** — `POST /objectstore/list`, `/objectstore/key` (signed URLs), `/objectstore/metric`. Not used for SQL. |

So for Snowflake/BigQuery (both tabular) **the Arrow Flight gRPC port is the one that matters**; the HTTP port is for S3/GCS/ADLS-style sources.

Two further consequences of these values:

1. **`DOMINO_API_HOST` is internal cluster DNS** (`http://nucleus-frontend.domino-platform:80`), *not* `https://cloud-dogfood.domino.tech`. Every one of these services is reachable **only from inside the Domino cluster**. Nothing here can be exercised from a laptop — which is why the `FakeAssetProvider` fallback in `_build_assets()` exists, and why a fake data-source provider is mandatory for local development.
2. **The library's `/v4` metadata calls go through the sidecar, not the platform host.** `data_sources.py:639-641` resolves `domino_host` as `DOMINO_API_PROXY` **first**, so `get_datasource()` actually hits `http://localhost:8899/v4/datasource/name/{name}`. The `:8899` sidecar therefore proxies `/v4` as well as minting tokens at `/access-token`. Sage's probes hit `DOMINO_API_HOST` directly instead; both work, but they are different routes to the same API.

#### The exact 6.7.4 call sequence

Verified against the 6.7.4 source (`pyproject.toml` `version = "6.7.4"`). For a tabular source such as Snowflake:

```python
from domino_data.data_sources import DataSourceClient

client = DataSourceClient()                    # reads env; builds AuthenticatedClient + FlightClient
ds     = client.get_datasource("<name>")       # GET {DOMINO_API_PROXY}/v4/datasource/name/<name>?runId=$DOMINO_RUN_ID
res    = ds.query("SELECT 1 AS ok")            # Arrow Flight do_get against the gRPC proxy
df     = res.to_pandas()                       # or res.to_parquet("out.parquet")
```

Snowflake-specific config override (`schema`, `warehouse`) when the data source's defaults are wrong:
```python
from domino_data.data_sources import DatasourceConfig
ds.update(DatasourceConfig(schema="PRODUCTION", warehouse="COMPUTE_WH"))
res = ds.query("SELECT * FROM users")
ds.reset_config()
```
Useful metadata for a UI: `ds.name`, `ds.datasource_type`, `ds.auth_type`, `ds.owner`. Errors: `UnauthenticatedError` and `DominoError` (both importable from `domino_data.data_sources`).

**Env vars 6.7.4 actually reads**, with which of the live values each resolves to:

| Env var | Read at | Resolves to (live) | Purpose |
|---|---|---|---|
| `DOMINO_API_PROXY` | `data_sources.py:634`, `:640` | `http://localhost:8899` | **Both** the token URL (`{url}/access-token`) and, first in precedence, the `/v4` metadata base |
| `DOMINO_API_HOST` | `:640` (2nd) | `http://nucleus-frontend...` | Fallback metadata base |
| `DOMINO_USER_HOST` | `:640` (3rd) | unset | Last-resort metadata base |
| `DOMINO_DATASOURCE_PROXY_FLIGHT_HOST` | `:638`, `:679` | `grpc://...:8080` | Arrow Flight — all SQL |
| `DOMINO_DATASOURCE_PROXY_HOST` | `:642` | `http://...:80` | Object-store HTTP |
| `DOMINO_TOKEN_FILE` | `:633` | (default `/var/lib/domino/home/.api/token`) | JWT file fallback |
| `DOMINO_USER_API_KEY` | `:632` | **present, 64 chars** | Legacy key auth — last in precedence |
| `DOMINO_RUN_ID` | `:645`, `:708` | — | `runId` query param + `X-Domino-Run-Id` |
| `DOMINO_CLIENT_SOURCE` | `:644`, `:678` | (default `"Python"`) | `X-Domino-Client-Source` |
| `DOMINO_DATA_API_GATEWAY` | `vectordb.py:20` | `http://127.0.0.1:8766` | VectorDB only — **not** used for SQL |

Note `DOMINO_USER_API_KEY` **is present in the workspace**. Because it sits last in the precedence chain (`auth.py:50-70`) the JWT normally wins, but its presence matters for Q3: a container that has it can authenticate as its owner regardless of any viewer token.

#### Is it a Sage *backend* dependency? NO — but the distinction matters

**Two different Python environments, and only one has the library:**

| Environment | Has `dominodatalab-data`? | Evidence |
|---|---|---|
| **Sage backend venv** (runs the orchestrator/hub) | **NO** | verified negative below |
| **Sage workspace/Environment image** (runs the agent + user code) | **YES, 6.7.4** | live `import domino_data` succeeded |

This is good news for scoping, and it splits cleanly along the recommended slice:

- **Discovery** (the picker) runs in the **backend**, and needs **no library** — it is one authenticated REST call with the auth Sage already has at `provision/domino.py:142`. No new dependency.
- **Querying** runs in the **workspace**, where the library is **already installed**. The agent can `from domino_data.data_sources import DataSourceClient` today, with zero environment work.

So the build-time slice requires **no new Python dependency anywhere**. Adding `dominodatalab-data` to the backend would only be needed if the *backend* had to run queries itself — which the recommended design avoids, and which would drag `pyarrow` + `pandas` into the orchestrator.

**Backend venv — verified negative:**
- `grep -i domino backend/pyproject.toml` → only the project's own `description` line. No dependency.
- `grep -i "dominodatalab\|domino-data" backend/uv.lock` → no match.
- `backend/.venv/lib/python3.*/site-packages/domino_data` → does not exist.

### LIVE-VERIFY

Settled by the live probe: library preinstalled at 6.7.4; all proxy/host env values; `DOMINO_API_PROXY == http://localhost:8899`. Still open:

- `LIVE-VERIFY` **Whether a Snowflake `query()` actually succeeds end-to-end.** Everything above proves the *plumbing is present*; nothing yet proves a query returns rows. Run the 4-line sequence above. Also capture cold-start latency for the first Flight connection — it decides whether the picker needs a loading state.
- `LIVE-VERIFY` What a permission failure looks like in practice (`UnauthenticatedError` vs `DominoError`, and the message text), so the UI can distinguish "you lack credentials" from "your SQL is wrong". Per the design principles these need different treatment: system error → human-readable guidance; user SQL error → raw output.
- `LIVE-VERIFY` Whether Snowflake needs `DatasourceConfig(schema=..., warehouse=...)` on dogfood, or whether the data source's stored config suffices. `SF_PARTNER=DominoDataLab` in the env suggests a first-class Snowflake integration.
- `LIVE-VERIFY` **The same env dump from inside a published App**, not a workspace. Everything above was measured in a workspace; the App case is what Q3 turns on and is still entirely unproven. Specifically: is `DOMINO_USER_API_KEY` present there, and whose key is it?
- `LIVE-VERIFY` Whether `grpc://datasource-proxy.domino-platform:8080` is reachable from the **App** hardware tier (`DOMINO_IS_LOCAL_DATA_PLANE=true` suggests yes for this workspace, but the docs caution that data sources *"may not be usable in every data plane due to network restrictions"*).

---

## Q3 — Credential model and identity

### VERIFIED — the two credential models

Domino Data Sources support two credential initialisation methods. The docs phrase it as service accounts that authenticate all users, versus individual accounts where each user supplies their own credentials ([work-with-data-source-connectors](https://docs.dominodatalab.com/en/latest/user_guide/fbb41f/work-with-data-source-connectors/)):

| Model | Meaning | Notes from docs |
|---|---|---|
| **`Shared`** (service account) | One set of credentials; everyone permitted to use the data source uses the same ones. | **Only a Domino admin can create a service-account data source.** |
| **`Individual`** | Each user supplies their own credentials. | Per-user; enforced per identity. |

Both are stored in **HashiCorp Vault**. **The credential method cannot be changed after creation** — you must create a new data source.

This is machine-readable, which is the useful part: `credentialType` is a **required** field on the public list response (`DataSourceCredentialTypeV1`, enum `["Individual","Shared"]`) and also present privately as `DataSourcePermissionsDto.credentialType`. **Sage can therefore branch on it at pick time without an extra call.**

Other docs facts worth holding:
- Data sources are **global in scope** — *"accessible to anyone with the appropriate permissions in any project"* ([use-data-sources](https://docs.dominodatalab.com/en/latest/user_guide/fa5f3a/use-data-sources/)).
- Adding one to a project is **optional**: *"If you don't add a Data Source to a project, you can still use it in your code if you have permission to access it."* — this argues for the user-scoped public list endpoint over the project-scoped private one.
- Credential resolution order in an execution: *"The system will try to use a Domino JWT token. If that's not available, it will use a user API key instead."*

### VERIFIED — whose credentials in a published App (the decisive answer)

Domino publishes an explicit identity table for data source access ([share-data-sources-securely](https://docs.dominodatalab.com/en/latest/user_guide/33ea62/share-data-sources-securely/)):

| Execution type | Identity used |
|---|---|
| Workspaces and Jobs | *"The user who started the execution."* |
| Launchers | *"The user who started the launcher regardless of who created the launcher."* |
| **Domino Apps** | **"The user who published the app regardless of who is accessing the app."** |
| Domino endpoint (Model API) | *"No user identity."* |

**So: yes, a published App does get data-source access — as the publisher, not the viewer.** Every viewer reads through the publisher's credentials. This holds for `Individual` sources too, which is the sharp edge: publishing an app over an Individual-credential Snowflake source effectively re-exports the publisher's personal database access to everyone who can open the URL.

Corroborating, from [app-security-and-identity](https://docs.dominodatalab.com/en/latest/user_guide/cb9195/app-security-and-identity/): by default Apps mount Datasets and NetApp Volumes accessible to the App creator, which the page warns means viewers can inherit permissions to data they would not normally have.

**The override — extended identity propagation.** Three documented levels:
- **Basic identity** — username only. Domino passes it in an HTTP header literally named **`domino-username`**; for unauthenticated viewers the value is `Anonymous`. Only works in frameworks that surface proxied HTTP headers.
- **Enhanced identity** — a JWT in the standard `Authorization` HTTP header; decoded payload includes `sub`, `preferred_username`, `email`, `given_name`, `family_name`. Token audience is scoped to a limited set of Domino endpoints. Base path via `DOMINO_RUN_HOST_PATH`.
- **Extended identity** — act on behalf of the viewer with their full permissions.

Extended identity's preconditions (all required): the `SecureIdentityPropagationToAppsEnabled` feature flag must be `true`, which gates the `com.cerebro.domino.apps.extendedIdentityPropagationToAppsEnabled` setting; the app must have it enabled; the app's code must actually use the viewer's token; and the viewer must consent. **Only SysAdmins or CloudAdmins can publish apps with it enabled. It is disabled by default. Once enabled on an app it cannot be disabled.** The docs warn such apps *"can access all data a user can access and perform actions as the user."*

**A working first-party implementation of the correct pattern exists** — Domino's own AutoML extension. [`app/core/domino_http.py`](https://github.com/dominodatalab/AutoML_Extension/blob/main/automl-service/app/core/domino_http.py) lines 7-19 state the strategy outright:

> *"All outbound Domino API calls MUST use the visiting user's forwarded JWT so that Domino enforces its own RBAC. The sidecar token and static API keys are **never** used as silent fallbacks — if no user token is present, the request fails loudly. … The previous fallback chain (sidecar → API key) has been removed to prevent accidental privilege escalation to the App-owner identity."*

Mechanically: FastAPI middleware grabs the incoming header and stashes it per-request — `app/main.py:81-82`:
```python
auth_header = request.headers.get("authorization")
set_request_auth_header(auth_header)
```
and every Domino call rebuilds headers from it (`domino_http.py:45-57`), raising `MissingUserTokenError` rather than falling back. `app/main.py:185` also reads `request.headers.get("domino-username", "anonymous")`.

This is direct first-party confirmation of both halves: the App-owner identity **is** the default (they had to remove a fallback to stop it), and forwarding the viewer JWT **is** the sanctioned fix.

### VERIFIED — the runtime verdict: what actually happens for an `Individual` source in a published App

The live env dump closes this. A Domino execution container is injected with **`DOMINO_USER_API_KEY` (64 chars, present)** and a sidecar minting tokens at `http://localhost:8899/access-token`. Those credentials belong to **whoever the execution runs as**. Per the identity table, for an App that is *"the user who published the app regardless of who is accessing the app."*

Chaining that with the library's auth precedence (`auth.py:50-70`: explicit token → sidecar → token file → API key), the mechanism is unambiguous:

> A published App's container holds only the **publisher's** credentials. `DataSourceClient()` reads them from the environment. It has **no knowledge of the HTTP request** that triggered it — a viewer's JWT arrives as an inbound header on the web request, which the library never sees. So **any query a viewer triggers executes as the publisher.**

Answering the three questions plainly:

**1. Does an `Individual`-credential data source work at all in a published App?** **Yes — as the publisher.** `Individual` means each *user* supplies their own credentials; the App runs as one specific user (the publisher), so Domino resolves the publisher's stored credential from Vault and the query succeeds. It does **not** fail closed, and it does **not** prompt the viewer. `LIVE-VERIFY` — this is derived from the identity table plus the auth precedence, and is the one inference in this section I could not find stated in a single sentence of Domino's own docs.

**2. Is the publisher's credential used for every viewer?** **Yes, by default** — that is precisely what *"regardless of who is accessing the app"* means. Every viewer, including ones with no Snowflake access of their own, reads through the publisher's credential.

**3. Is there a per-viewer credential mechanism?** **For Domino API calls, yes; for data-source credentials, not really.** Extended identity propagation forwards the viewer's token so the app can act with the viewer's permissions (the AutoML pattern). But note what that gives you: it lets the app call **Domino APIs** as the viewer. Whether a viewer-scoped token can drive a *data-source query* through the Flight proxy — i.e. whether the proxy will resolve *that viewer's* Vault credential — is **not documented anywhere I could find**, and the docs explicitly say the enhanced-identity token's *"audience [is] scoped to a limited set of Domino endpoints."* This is the load-bearing unknown for any live design.

**Is running viewer queries as the publisher supported, or a governance hazard?** **It is a supported, documented default — and simultaneously a genuine governance hazard.** Both are true, and the docs say both:

- Supported: it is the documented behaviour, and [app-security-and-identity](https://docs.dominodatalab.com/en/latest/user_guide/cb9195/app-security-and-identity/) frames apps as *"trusted interfaces to governed resources"*, which is a legitimate pattern — a curated dashboard over a `Shared` service-account source is exactly this, done right.
- Hazard: Domino warns that default mounting means *"viewers can inherit permissions to data they wouldn't normally have"*, and [best-practices-for-domino-apps](https://docs.dominodatalab.com/en/latest/user_guide/b04682/best-practices-for-domino-apps/) adds:
  > *"Review permissions before publishing or republishing—especially when cloning or renaming Apps."*
  > *"Remember: App URLs can be shared externally. Use Domino's access controls to enforce protection."*
  > *"This helps prevent unintentional exposure and keeps your platform governance strong."*

**The distinction that should drive Sage's design is `credentialType`, which Sage gets for free in the list response:**

| `credentialType` | Publishing an app over it | Sage's stance |
|---|---|---|
| **`Shared`** (service account, admin-created) | Reasonable. The credential is already institutional and shared by design; the app is a curated interface over it. | Allow. |
| **`Individual`** | **Hazard.** Publishing re-exports one person's personal database access to everyone with the URL. The publisher may not realise this. | **Warn explicitly at publish time**, naming the data source and that viewers will read as the publisher. |

This maps onto machinery Sage already has. The sensitivity model in `assets/provider.py` (`is_sensitive`, `DEFAULT_SENSITIVITY_TAG = "sensitive"`) and the sticky sovereign lock re-fired by `_rehydrate_attached` (`service.py:987-988`) exist for exactly this class of concern. **An `Individual`-credential data source is a natural new trigger for that existing lock/warning path** — not a new subsystem. That reuse is the cheapest correct answer to the governance problem, and it is available in the smallest slice.

### LIVE-VERIFY

- `LIVE-VERIFY` Whether `SecureIdentityPropagationToAppsEnabled` is on for cloud-dogfood at all. If it is off, per-viewer querying is **impossible** there without an admin change, and the build-time slice is the only option. **Check this before designing anything live.**
- `LIVE-VERIFY` Whether the forwarded viewer JWT's audience scope actually permits `/api/datasource/v1/datasources` and a Flight `query()`. The docs say the enhanced-identity token is *"scoped to a limited set of Domino endpoints"* but do not enumerate them. If data-source endpoints are outside that audience, viewer-scoped querying fails even with propagation enabled.
- `LIVE-VERIFY` Whether an `Individual`-credential source is usable at all from an App when the publisher has entered personal credentials — does the App inherit them from Vault, or fail?
- `LIVE-VERIFY` Whether Sage's published apps currently receive a `domino-username` header (cheap to test, and would enable a viewer-attribution display with no propagation flag).

---

## Q4 — Static app feasibility

### VERIFIED — a Domino App always runs a server process

Domino's app-publishing model is a launch script, not a static upload. From Domino's static-HTML hosting docs ([host-html-pages](https://archive.docs.dominodatalab.com/en/latest/user_guide/9b11ea/host-html-pages/)):

> *"When you publish an app, Domino looks for an `app.sh` file in your project to find the launch instructions."*

and that file *"must contain the commands to start the web hosting process."* The documented `app.sh` is:
```bash
#!/usr/bin/env bash
python ./app.py
```
with `app.py` being a real server:
```python
import http.server
import socketserver
PORT = 8888
Handler = http.server.SimpleHTTPRequestHandler
httpd = socketserver.TCPServer(("", PORT), Handler)
print ("serving at port", PORT)
httpd.serve_forever()
```

**This is the answer: even Domino's own "static HTML" recipe runs a Python HTTP server.** There is no serverless/static-bucket publishing mode. Consequently **there is always a process you could add a Python endpoint to.**

Port/bind: bind `0.0.0.0`, port `8888`. Note [common-app-frameworks](https://docs.dominodatalab.com/en/latest/user_guide/a537c2/common-app-frameworks/) adds a modern caveat: *"Port selection is flexible and port `8888` is no longer required."* All examples still use 8888 and `0.0.0.0`. Apps sit behind an nginx proxy that rewrites the root URL, so **absolute URL paths must be avoided** (user's global notes; corroborated by `DOMINO_RUN_HOST_PATH` and Dash's `routes_pathname_prefix` guidance).

### VERIFIED — Sage's published apps already run a server

`/Users/subirmansukhani/Desktop/domino-sage/template/react-vite/app.sh:35`:
```bash
exec npx vite preview --base / --host 0.0.0.0 --port 8888 --strictPort
```
Preceded by `npm ci` (line 21), `node scripts/rehydrate-data.mjs` (line 26), `npm run build` (line 29). Its own comment (lines 4-7) says a Domino App *"checks out the project's repo to `/mnt/code` and runs this file on the chosen hardware tier, bound to `0.0.0.0:8888` behind Domino's app proxy."*

Sage's **Hub** likewise is a server — `/Users/subirmansukhani/Desktop/domino-sage/app.sh:14-20` execs `hub.sh`.

So the precise status is: **the published artifact is a static *bundle*, but the deployment is a live Node process.** The template's `package.json` confirms there is no backend today — dependencies are purely frontend (`react`, `react-dom`, `react-router-dom`, `recharts`, `date-fns`, `lucide-react`), and the only "server" is `vite preview` from the `vite` devDependency.

**Implication for scoping:** live per-viewer querying is **not blocked by Domino**. It is blocked by (a) the template having no backend process, and (b) Q3's identity problem. (a) is a modest change — swap `vite preview` for a small server that serves `dist/` *and* exposes a query endpoint. (b) is the hard part. **Do not mistake (a) for the blocker.**

The existing build-time data path is the seam to reuse: `scripts/rehydrate-data.mjs` rebuilds `public/data/` from the committed `.sage/attachments.json` manifest **before** `npm run build`, so Vite copies the files into `dist/`. Manifest entries carry at least `path` (workspace-relative, `public/data/<slug>/...`) and `dataset`; its `mountRoots()` deliberately mirrors `resolve_mount_roots()` in `backend/sage/assets/provider.py`. A data-source query result saved as CSV/Parquet/JSON into `public/data/` would flow through this machinery **unchanged**.

### LIVE-VERIFY

- `LIVE-VERIFY` Whether a published Sage app can bind a port other than 8888, and whether Domino's proxy forwards non-GET methods (needed for a `POST /api/query`).
- `LIVE-VERIFY` Whether the Sage Environment image has Python + uvicorn available alongside Node, if you later add a Python endpoint to the published app.
- `LIVE-VERIFY` Whether outbound network from the App hardware tier can reach the Flight proxy — the docs caution that data sources *"may not be usable in every data plane due to network restrictions."*

---

## Q5 — What `domino-claude-plugin` already gives us

Cloned `https://github.com/dominodatalab/domino-claude-plugin` (shallow, `main`). Paths below are repo-relative.

### VERIFIED — data-source-related content

| Path | What it is | Reusable? |
|---|---|---|
| `skills/domino-data-sdk/SKILL.md` | Overview of `dominodatalab-data`: install, components table, quick-start for `DataSourceClient`, `DatasetClient`, TrainingSets, VectorDB. Documents the auth env vars and flags `DOMINO_USER_API_KEY` as deprecated. | **Yes — as prompt context.** Directly droppable into a Sage skill. |
| `skills/domino-data-sdk/DATA-SOURCES.md` | The substantive one. `get_datasource`, `.query()` → `to_pandas()`/`to_parquet()`, `DatasourceConfig` override (`schema`, `warehouse` — Snowflake-relevant), object-store ops, signed URLs, a type→class table, error handling. | **Yes — highest-value reusable asset.** |
| `skills/python-sdk/API-ADMIN.md:245-311` | REST reference for the **public** Data Sources API, including `GET /api/datasource/v1/datasources` with a `requests` example. | **Yes — corroborates Q1.** |
| `skills/python-sdk/API-REFERENCE.md:516-541` | Same endpoints, condensed. | Reference. |
| `skills/data-connectivity/SKILL.md` | **Not data sources.** S3 Mountpoint CSI, AWS IRSA, Azure Entra credential propagation, External Data Volumes. | No — different concern. |
| `skills/domino-data-sdk/VECTORDB.md` | Pinecone via `domino_pinecone3x_init_params`. | No. |

### VERIFIED — directly reusable code

**`mcp-servers/domino_mcp_server/domino_mcp_server.py`** — a FastMCP server. Its `_get_auth_headers()` is the cleanest reference implementation of Domino auth-from-inside-a-container that I found:
```python
api_key_override = os.environ.get("API_KEY_OVERRIDE")
if api_key_override:
    return {"X-Domino-Api-Key": api_key_override}
if _is_domino_workspace():                       # checks DOMINO_API_HOST is set
    resp = requests.get("http://localhost:8899/access-token")
    token = resp.text.strip()
    return {"Authorization": token if token.startswith("Bearer ") else f"Bearer {token}"}
api_key = os.getenv("DOMINO_API_KEY")            # laptop fallback
return {"X-Domino-Api-Key": api_key}
```
It also auto-detects project context from `DOMINO_PROJECT_OWNER` / `DOMINO_PROJECT_NAME` / `DOMINO_PROJECT_ID`. **Reusable as the pattern for a local-dev override in a Sage probe** — though Sage's own `provision/domino.py:142` already covers the in-Domino case.

**`templates/vite-react/app.sh`** — the plugin's own React-on-Domino entrypoint, and an interesting contrast with Sage's:
```bash
cd /mnt/code
npm ci
npm run build
npx serve -s dist -l 8888 --no-clipboard
```
Confirms independently that (a) Domino Apps run a server, (b) 8888 is the convention, (c) `/mnt/code` is the checkout path. It uses `npx serve -s` (SPA fallback) where Sage uses `vite preview`. Also echoes `DOMINO_PROJECT_NAME`, `DOMINO_PROJECT_OWNER`, **`DOMINO_STARTING_USERNAME`** — note the last one names the *starter*, matching Q3's identity table.

Also present and possibly relevant later: `skills/apps/REACT-VITE-GUIDE.md`, `skills/apps/TROUBLESHOOTING.md`, `skills/apps/REACT-CICD.md`, `skills/domino-ui-design/` (Domino design tokens + `references/ux-design-rules.md`).

### What the plugin does NOT give us

- **No data-source *listing* code.** No command, skill, or script calls a list endpoint — only the REST paths documented in markdown. There is nothing to copy for the picker itself.
- **No published-app data-source example**, and nothing about viewer identity for data access.
- **No `/v4/datasource/...` usage anywhere** (only `/api/datasource/v1/...` in docs).

### Not from the plugin, but the best code find overall

`dominodatalab/AutoML_Extension` `automl-service/` is a **Domino App that is a FastAPI server calling Domino APIs as the forwarded viewer** — architecturally the closest thing to "Sage app with a live backend that respects viewer identity". Reusable pieces:
- `app/core/domino_http.py` — host resolution (`DOMINO_API_PROXY` > settings > `DOMINO_API_HOST`), `/v4` base-URL helper, public vs private client construction, strict no-fallback auth.
- `app/core/context/auth.py` — `ContextVar`-based per-request token stash, with `AUTH_TOKEN_EXTRACT_PATTERN = r'bearer\s+(.*)'`.
- `app/main.py:73-86` — the middleware that captures `request.headers.get("authorization")`.
- `automl-service/scripts/download_api_specs.sh` — canonical spec URLs + a version-pinning guard. **Worth mirroring in `spikes/domino-probes/`** so specs get refreshed against a known Domino version.

---

## Where this lands in the repo (the seam for the recommended slice)

All VERIFIED by reading the code. This exists so the scoping conversation can point at real lines.

**Discovery — extend the asset seam.** `backend/sage/assets/provider.py:82-84` is a two-method Protocol:
```python
class AssetProvider(Protocol):
    def list_datasets(self, project_id: str | None) -> list[Asset]: ...
    def list_files(self, asset: Asset) -> list[DatasetFile]: ...
```
`Asset` (`:42-51`) is `id, name, tags, project, mount_path, tag_snapshots`. That shape is dataset-specific — `mount_path` is meaningless for a Snowflake source, and `DominoAssetProvider` **filters out anything not mounted on disk** (`:184-189`, `:213-214`). So data sources are **not** a good fit for `list_datasets`; they want a sibling method (`list_data_sources() -> list[DataSource]`) or a separate provider, not a widened `Asset`.

Auth for it is free: `DominoAssetProvider` already takes `(api_host, token_provider)` and sends `{"Authorization": f"Bearer {self._token_provider()}"}` (`:195`). Note it uses bare module-level `httpx.get` (`:205`) with no injectable transport — unlike `DominoControlPlane`, which has a `transport` test seam (`provision/domino.py:138-139`). **A new data-source provider should copy the `DominoControlPlane` pattern so it is testable.**

Wiring: `_build_assets()` at `backend/sage/orchestrator/app.py:114-121` returns a `FakeAssetProvider` whenever `DOMINO_API_HOST` is unset, else a real one authed by `DOMINO_API_KEY` (static PAT) or `GATEWAY_TOKEN_URL` (sidecar). **A fake data-source provider is required for local Mac testing**, matching `FakeAssetProvider` (`:96-124`).

**API routes** — existing shape to mirror, `backend/sage/orchestrator/app.py`:

| Line | Route |
|---|---|
| `:571` | `GET /api/assets` → `{assets, sensitivity_tag, default_dataset_id}` |
| `:580` | `GET /api/project/assets/{dataset_id}/files` |
| `:588` | `POST /api/project/assets/{dataset_id}/files/attach` |
| `:609` | `POST /api/project/files/detach` |
| `:620` | `POST /api/project/upload` (raw body; name/sensitive/dataset as query params) |
| `:650` | `POST /api/project/files/delete` |

Routes reach the single module-level `orchestrator` singleton (`:191-206`) — no `Depends`. `list_assets` (`service.py:2501-2513`) deliberately returns only `{id, name, tags, project, sensitive, writable}`, withholding `mount_path`/`tag_snapshots` from the UI. A data-source list endpoint should be similarly reduced — and per Q3 it **should** surface `credentialType`, because that is what the UI must warn on.

**Build-time data path — the manifest.** Written by `workspace/manager.py:282-284` to `<workspace>/.sage/attachments.json` (plain `json.dumps`, no atomic rename, no lock). Entries today carry 8 fields, from both writers (`service.py:2557-2560` attach, `:2648-2652` upload):
```python
{"dataset_id", "dataset", "file", "path", "size", "sensitive", "source", "dataset_rel_path"}
```
plus a lazily-persisted `descriptor` (`{kind, summary, detail, size}`) added by `_descriptor()` (`:1098-1122`). `source` is `"dataset"` (pre-existing bytes, never deleted) vs `"upload"` (Sage-created, deletable). `path` is workspace-relative `public/data/<slug>/...`.

Restored at orchestrator start by `_rehydrate_attached` (`service.py:978-1004`), which re-fires the sticky sovereign lock if any restored entry is `sensitive` (`:987-988`). `public/data/` is force-gitignored by `_ensure_data_gitignored` (`:2984-2989`) — **which is precisely why the manifest exists**: data never enters git, so the published App rebuilds it from the manifest.

**A query result is a natural third `source`.** The published-app side (`template/react-vite/scripts/rehydrate-data.mjs:42-53`) resolves each entry via `e.path`, `e.dataset`, `e.dataset_rel_path || e.file` and **symlinks from a dataset mount**. A `source: "datasource"` entry has no dataset mount to link from — so the smallest correct design is to **write the query result into the project's default Domino dataset** (reusing `_resolve_upload_target`, `service.py:2677-2699`, which already picks a writable dataset and an `uploads/` vs `sensitive/` subfolder). Then it is an ordinary attachment and **`rehydrate-data.mjs` needs no change at all**. That is the cheapest possible path to a data-source-backed published app.

Publish itself (`service.py:2362-2407`) refreshes `app.sh` from the template (`:2379`) and commits via `_save_to_git` (`:2392`) — which is what actually pushes `.sage/attachments.json`.

---

## Open questions / what to probe on dogfood

Probes live in `/Users/subirmansukhani/Desktop/domino-sage/spikes/domino-probes/`. Suggested new probe: `spikes/domino-probes/datasource_probe.py` + a `datasource_probe.sh` wrapper.

**House style to follow** (verified consistent across `control_plane.py`, `gateway.py`, `app_publish_probe.py`, `git_discovery.sh`, `repo_provision_probe.sh`):

1. Top docstring: phase + step number, the ONE question it answers, the exact endpoints, the run command (`cd /mnt/code/spikes/domino-probes`), and "Paste the whole output back."
2. `from __future__ import annotations`; deps via `uv run --with httpx <script>.py` — **never** added to `backend/pyproject.toml`.
3. Env constants at module top: `DOMINO_API_HOST` required via `os.environ[...]`; everything else `os.environ.get(..., default)`.
4. A `token()` helper hitting the sidecar per call, normalising the `Bearer ` prefix.
5. A `call(method, path, **kw)` helper that **never raises** — prints `### METHOD url`, the body, the status, and truncated pretty JSON, **so 4xx bodies become the spec**.
6. Read-only by default; mutation behind `PROBE_CREATE=1` (Python) or `DRY_RUN=0` (bash), self-cleaning via `finally` / EXIT trap.
7. Secrets never echoed — redact or print length only; all stdout must be paste-safe.
8. Findings get promoted into a `# LIVE-VERIFY` comment in the production module (precedent: `provision/domino.py:233-234`, `:250-251`, `:288-296`).

The standard preamble, matching `control_plane.py:23-38`:
```python
API_HOST = os.environ["DOMINO_API_HOST"].rstrip("/")
SIDECAR  = os.environ.get("DOMINO_API_PROXY", "http://localhost:8899").rstrip("/")

def token() -> str:
    t = httpx.get(f"{SIDECAR}/access-token", timeout=10).text.strip()
    return t if t.lower().startswith("bearer ") else f"Bearer {t}"
```

Ordered so each step's answer changes whether the next one matters. **All of these are read-only** — no `PROBE_CREATE` needed for steps 1-3.

### 1. Does the public list endpoint work, and what does Snowflake look like? (blocks the picker)

Run **inside a dogfood Workspace**:
```bash
# House style: DOMINO_API_HOST is the API; DOMINO_API_PROXY is only the token sidecar.
HOST="${DOMINO_API_HOST%/}"
TOKEN=$(curl -s "${DOMINO_API_PROXY:-http://localhost:8899}/access-token")
AUTH="Authorization: Bearer ${TOKEN#Bearer }"

# The recommended public endpoint
curl -s -H "$AUTH" -H 'Accept: application/json' \
  "$HOST/api/datasource/v1/datasources" | jq '.'
```
Record: HTTP status; `metadata.pagination`; and for the Snowflake entry the **exact** `dataSourceType`, `authType`, `credentialType`, `displayName` vs `name`, and `config` keys. **`dataSourceType`'s literal value drives the UI mapping.**

Compare against the private, project-scoped endpoint:
```bash
curl -s -H "$AUTH" -H 'Accept: application/json' \
  "$HOST/v4/datasource/projects/$DOMINO_PROJECT_ID" \
  | jq '[.[] | {id,name,dataSourceType,authType,status,
                credentialType:.dataSourcePermissions.credentialType}]'

# and with the readiness filter
curl -s -H "$AUTH" "$HOST/v4/datasource/projects/$DOMINO_PROJECT_ID?authenticatedOnly=true" | jq 'length'

# batch readiness for Individual-credential sources
curl -s -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"dataSourceIds":["<id-from-step-1>"]}' \
  "$HOST/v4/datasource/authentication-status" | jq '.'
```
Decide from the diff whether the public endpoint alone suffices, or whether you need `status`/`authenticatedOnly` from the private one.

### 2. ~~Dump the injected environment~~ — DONE for a Workspace; redo it in an App

Already captured (see the Q2 live result). The remaining half is the **App** container, which is where identity differs and where nothing has been measured:

```bash
env | grep -E '^DOMINO_' | sort
ls -l /var/lib/domino/home/.api/token
# whose identity is this container?
curl -s -H "$AUTH" "$HOST/api/users/v1/self" | jq '{id,userName,email}'
```
Run it from a published app's `app.sh` (stdout lands in the Domino app log). The `users/v1/self` call is the direct test of Q3: **expect the publisher, not the viewer.** Also confirm whether `DOMINO_USER_API_KEY` is present there and whose key it is.

### 3. Can we actually query Snowflake? (blocks the build-time slice)

```bash
python -c "import domino_data, sys; print('preinstalled', domino_data.__file__)" \
  || pip install -U dominodatalab-data
```
```python
from domino_data.data_sources import DataSourceClient
ds = DataSourceClient().get_datasource("<snowflake-name-from-step-1>")
print(ds.name, ds.datasource_type, ds.auth_type, ds.owner)
df = ds.query("SELECT 1 AS ok").to_pandas()
print(df)
```
Record whether `domino_data` was preinstalled in the Sage Environment, wall-clock time for the first query (cold Flight connection), and the exact exception text on failure. Snowflake may need `DatasourceConfig(schema=..., warehouse=...)` via `ds.update(...)`.

### 4. Is per-viewer identity even possible here? (decides whether live querying is ever in scope)

Ask an admin, or inspect the config record, for:
- feature flag `SecureIdentityPropagationToAppsEnabled`
- setting `com.cerebro.domino.apps.extendedIdentityPropagationToAppsEnabled`

**If either is off, stop designing live per-viewer querying** and commit to the build-time slice. If on, then from a *published* app, log the inbound headers and verify a viewer JWT arrives:
```python
# temporary instrumentation in the published app's server
print("AUTHZ:", request.headers.get("authorization", "<none>"))
print("USER :", request.headers.get("domino-username", "<none>"))
```
Then test whether that forwarded token is accepted by `/api/datasource/v1/datasources` — this is the audience-scope question, and it is the single most likely place the live design dies.

### 5. Re-run steps 1-3 from inside a PUBLISHED app, not a Workspace

Everything above verified in a Workspace proves nothing about Apps, because Apps are the context where identity differs. Publish a throwaway app whose `app.sh` runs the probe and prints to stdout (Domino app logs capture it). Confirm: which `DOMINO_*` vars exist, whether the token sidecar is reachable on 8899, and **which username the data-source call resolves to** (expect the publisher, per Q3).

### 6. Only if live querying survives step 4: prove a backend can be added

Swap `template/react-vite/app.sh`'s last line for a server that serves `dist/` **and** a `POST /api/query`, then confirm Domino's nginx proxy forwards POST and that relative URLs still resolve under the app mount prefix.

### Also worth doing

Mirror `download_api_specs.sh` into `spikes/domino-probes/` so both specs can be refreshed with a version guard. Note that **both spec URLs fetch unauthenticated** — I retrieved `swagger.json` (1.9 MB) and `public-api.json` (1.0 MB) from cloud-dogfood with plain `curl`, HTTP 200, no credentials. That makes spec refresh trivial to automate.

---

## Sources

### Live probe (cloud-dogfood, project "Sage", 2026-08-18)
- `spikes/domino-probes/datasource_probe.sh` — the authoritative empirical source for this file. Established: the `/v4` prefix works (HTTP 200) with a Bearer sidecar token from `http://localhost:8899/access-token`; **`GET /v4/datasource/projects/6a5e8b03242fc543ed24282d` returned `200 []`** despite a configured Snowflake source; `dominodatalab-data` **6.7.4 already installed** (`import domino_data` succeeds); and the full injected env (`DOMINO_API_HOST=http://nucleus-frontend.domino-platform:80`, `DOMINO_API_PROXY=http://localhost:8899`, `DOMINO_DATASOURCE_PROXY_HOST=http://datasource-proxy.domino-platform:80`, `DOMINO_DATASOURCE_PROXY_FLIGHT_HOST=grpc://datasource-proxy.domino-platform:8080`, `DOMINO_DATA_API_GATEWAY=http://127.0.0.1:8766`, `DOMINO_USER_API_KEY` present/64 chars, `DOMINO_IS_LOCAL_DATA_PLANE=true`, `SF_PARTNER=DominoDataLab`). The empty-array result is what promotes the user-scoped public endpoint from "cleaner" to "required".

### Specs (fetched unauthenticated, HTTP 200)
- `https://cloud-dogfood.domino.tech/assets/public-api.json` — **PUBLIC** spec, OpenAPI 3.0.3, "Domino Public API" v6.4.0, 220 paths. Established `GET /api/datasource/v1/datasources` (`getAccessibleAndActiveDataSources`), the `DataSourceEnvelopeV1` shape, and `credentialType` enum `Individual|Shared`. **The Q1 recommendation.**
- `https://cloud-dogfood.domino.tech/assets/swagger.json` — **PRIVATE** spec, 735 paths, 41 `/datasource*`, `servers: [{"url":"/v4"}]`. Established the project-scoped list, `status` enum incl. `Pending`, the enumerated `dataSourceType` (incl. `SnowflakeConfig`, `BigQueryConfig`), `authType` enum, `DataSourcePermissionsDto.credentialType`, and both securitySchemes. Also that `GET /v4/datasource` is a boolean admin check, not a list.
- `/Users/subirmansukhani/Desktop/domino-sage/spikes/domino-probes/dogfood-swagger.json` — the user's authenticated copy of the same private spec; independently corroborates Q1.

### Domino first-party source code
- `https://github.com/dominodatalab/domino-data` — the real `dominodatalab-data` v6.7.4 source. `pyproject.toml` (package name, deps incl. `pyarrow`); `domino_data/data_sources.py` (env vars :51-60, client init :637-675, `get_datasource` :694-728, `BoardingPass` :601-620, `execute`/`_do_get` :858-898); `domino_data/auth.py` (sidecar `/access-token` :30, header precedence :50-70, proxy headers :89-116, Flight middleware :168-177); `domino_data/vectordb.py:20` (`DOMINO_DATA_API_GATEWAY` default `http://127.0.0.1:8766`); `datasource_api_client/api/datasource/get_datasource_by_name.py:14-33`. Established all of Q2, and that the bundled client has **only 4 endpoints and no list**.
- `https://github.com/dominodatalab/domino-claude-plugin` — `skills/domino-data-sdk/{SKILL,DATA-SOURCES}.md` (reusable SDK prompt context); `skills/python-sdk/API-ADMIN.md:245-311` (public REST paths); `mcp-servers/domino_mcp_server/domino_mcp_server.py` (`localhost:8899/access-token` auth pattern); `templates/vite-react/app.sh` (React-on-Domino server entrypoint, port 8888, `/mnt/code`). Established Q5 — and that the plugin has **no** data-source listing code.
- `https://github.com/dominodatalab/AutoML_Extension/blob/main/automl-service/scripts/download_api_specs.sh` — named both canonical spec URLs; settled that `swagger.json` is the *private* spec.
- `https://github.com/dominodatalab/AutoML_Extension/blob/main/automl-service/app/core/domino_http.py` — working first-party client. Confirmed the `/v4` prefix (:116-118), host precedence `DOMINO_API_PROXY > settings > DOMINO_API_HOST` (:96-113), `Authorization` forwarded-JWT auth (:45-57), and — via its removed fallback chain (:7-19) — that the default App identity **is** the App owner.
- `https://github.com/dominodatalab/AutoML_Extension/blob/main/automl-service/app/core/context/auth.py` + `app/main.py:73-86,185` — the viewer-token forwarding mechanism and the `domino-username` header in practice.

### Domino documentation
- [share-data-sources-securely](https://docs.dominodatalab.com/en/latest/user_guide/33ea62/share-data-sources-securely/) — **the identity table.** Domino Apps use *"The user who published the app regardless of who is accessing the app."* The single most decision-relevant citation.
- [app-security-and-identity](https://docs.dominodatalab.com/en/latest/user_guide/cb9195/app-security-and-identity/) — basic/enhanced/extended identity propagation; `domino-username` header; `Anonymous` for unauthenticated; `SecureIdentityPropagationToAppsEnabled`; `com.cerebro.domino.apps.extendedIdentityPropagationToAppsEnabled`; SysAdmin/CloudAdmin-only; disabled by default; irreversible once enabled.
- [host-html-pages](https://archive.docs.dominodatalab.com/en/latest/user_guide/9b11ea/host-html-pages/) — **the Q4 answer.** Domino looks for `app.sh`, which *"must contain the commands to start the web hosting process"*; even static HTML is served by a Python `http.server` on 8888.
- [common-app-frameworks](https://docs.dominodatalab.com/en/latest/user_guide/a537c2/common-app-frameworks/) — bind `0.0.0.0`; *"Port selection is flexible and port 8888 is no longer required"*; `DOMINO_RUN_HOST_PATH`; **no** documented static-site pattern.
- [use-data-sources](https://docs.dominodatalab.com/en/latest/user_guide/fa5f3a/use-data-sources/) — global scope; **the explicit "Add an existing Data Source to a project" flow (Data > Data Sources > Add Data Source > Add to Project)**, that it is *"not required"* and exists for visibility, and that it *is* required for code snippets. This is what explains the live `200 []`. Also JWT-then-API-key resolution; service-account vs individual credentials. **Says nothing about App/Job/Model-API credential identity** — the gap that made the identity table necessary.
- [best-practices-for-domino-apps](https://docs.dominodatalab.com/en/latest/user_guide/b04682/best-practices-for-domino-apps/) — the governance warnings quoted in Q3: review permissions before publishing/republishing, App URLs can be shared externally, unintentional exposure. Notably does **not** state whose credentials an App uses.
- [work-with-data-source-connectors](https://docs.dominodatalab.com/en/latest/user_guide/fbb41f/work-with-data-source-connectors/) — service accounts vs individual accounts; Vault storage; admin-only for some connectors; data-plane caveats (*"may not be usable in every data plane due to network restrictions"*).
- [execution-context-and-authentication](https://docs.dominodatalab.com/en/latest/user_guide/5bef19/execution-context-and-authentication/) — checked and **found not to contain** the env-var/token reference I hoped for; it only discusses App permissions. Recording this so nobody re-reads it expecting env vars.

### This repo
- `/Users/subirmansukhani/Desktop/domino-sage/backend/sage/provision/domino.py:142` — `{"Authorization": f"Bearer {self._token_provider()}", "Accept": "application/json"}`. The auth path a data-source client reuses unchanged. **No `X-Domino-Api-Key` exists anywhere in the backend.** Docstring `:16-19` flags `/v4` as the internal seam vs the public `/api/...` families — 6 of its 21 calls are `/v4`, all workspace/environment lifecycle. `_client()` `:138-139` has an injectable `transport` test seam.
- `/Users/subirmansukhani/Desktop/domino-sage/backend/sage/assets/provider.py` — `:194` calls `GET {api_host}/api/datasetrw/v2/datasets` (params `minimumPermission=ReadDatasetRwV2`, `includeProjectInfo=true`, `offset`, `limit`), auth `:195`. The in-repo precedent for the public `/api/<service>/v<n>/` family. `AssetProvider` Protocol `:82-84`; `Asset` `:42-51`; `FakeAssetProvider` `:96-124`; `resolve_mount_roots()` `:29-39` reads `DOMINO_DATASET_MOUNT_PATH` / `DOMINO_MOUNT_PATHS` over defaults `("/domino/datasets/local", "/mnt/data", "/mnt/imported/data")`. Establishes that `Asset` is mount-path-shaped and filters to on-disk datasets (`:184-189`, `:213-214`) — hence data sources need a sibling method, not a widened `Asset`.
- `/Users/subirmansukhani/Desktop/domino-sage/backend/sage/orchestrator/app.py:114-121` — `_build_assets()`: falls back to `FakeAssetProvider` without `DOMINO_API_HOST`; auth via `DOMINO_API_KEY` or `GATEWAY_TOKEN_URL`. Routes `:571`-`:665` (assets, attach/detach, raw-body upload, delete); single module-level `orchestrator` singleton `:191-206`.
- `/Users/subirmansukhani/Desktop/domino-sage/backend/sage/gateway/client.py:26-47` — `DEFAULT_SIDECAR_URL = "http://localhost:8899/access-token"`, `static_token()`, `sidecar_token()`. The resolved form of the Q2 sidecar.
- `/Users/subirmansukhani/Desktop/domino-sage/backend/sage/orchestrator/service.py` — manifest writers `:2557-2560` / `:2648-2652` (the 8 fields), `_descriptor()` `:1098-1122`, `_rehydrate_attached()` `:978-1004`, `list_assets()` `:2501-2513`, `_resolve_upload_target()` `:2677-2699`, `_ensure_data_gitignored()` `:2984-2989`, `publish()` `:2362-2407`. Establishes the build-time data seam and why `source: "datasource"` should route through a dataset.
- `/Users/subirmansukhani/Desktop/domino-sage/backend/sage/workspace/manager.py:265-284` — `.sage/attachments.json` read/write (non-atomic, unlocked).
- `/Users/subirmansukhani/Desktop/domino-sage/spikes/domino-probes/README.md:3-5` + `control_plane.py:23-38` — the probe auth contract (`DOMINO_API_PROXY` → `:8899/access-token`) and the house style codified in the probes section above.
- `/Users/subirmansukhani/Desktop/domino-sage/template/react-vite/app.sh:21-35` — **the Q4 evidence for Sage specifically:** `npm ci` → `rehydrate-data.mjs` → `npm run build` → `exec npx vite preview --base / --host 0.0.0.0 --port 8888 --strictPort`. A live Node process serving a static bundle.
- `/Users/subirmansukhani/Desktop/domino-sage/template/react-vite/package.json` — frontend-only deps; no backend today.
- `/Users/subirmansukhani/Desktop/domino-sage/template/react-vite/scripts/rehydrate-data.mjs:1-47` — the `.sage/attachments.json` → `public/data/` rebuild that runs before the build; entries carry `path` and `dataset`; mirrors `resolve_mount_roots()`. **The seam the build-time slice reuses.**
- `/Users/subirmansukhani/Desktop/domino-sage/app.sh:14-20` — the Hub's own App entrypoint (execs `hub.sh`).
- `backend/pyproject.toml`, `backend/uv.lock`, `backend/.venv/lib/python3.*/site-packages/` — all checked: **`dominodatalab-data` is NOT currently a dependency** (verified negative, no match in any of the three).

---

## Addendum — live results after this file was written

Verified live on cloud-dogfood, project `Sage`, 2026-08-18, via
`spikes/domino-probes/datasource_probe.sh` and follow-up curls. These supersede the
matching LIVE-VERIFY items above.

### The picker endpoint works, and needs no project attachment — VERIFIED

`GET $DOMINO_API_HOST/api/datasource/v1/datasources?limit=50` with the sidecar bearer
token returned **200** and listed the Snowflake source **without it being attached to the
project**. This was the file's top unrun check. It passes, and it confirms the
recommendation to build the picker on the permission-keyed public endpoint.

For contrast, both private endpoints were also exercised:
`GET /v4/datasource/projects/{projectId}` → **200 `[]`** (the `/v4` prefix is correct; the
empty result is correct behaviour, not a fault). `GET /v4/datasource/dataSources/all` →
**403**, missing `ManageDataSourceExternalData`. Sage must never call that one.

### The response is 78% noise, and it overlaps Sage's existing dataset panel — NEW

22 rows came back, of which only **6** are real external connectors:

| Group | Rows | Note |
|-------|------|------|
| External connectors | 6 | 2x Snowflake, 2x SQL Server, Postgres, Databricks |
| `DatasetConfig` | 14 | Domino **Datasets** as pseudo-sources, named `dataset-<name>-<id>` |
| `NetAppVolumeConfig` | 2 | Volume mounts |

The 14 `DatasetConfig` rows are the same Domino Datasets that Sage's existing asset panel
already lists as attachable mounts (`assets/provider.py`, `/api/project/assets`). Rendering
them unfiltered puts one dataset in the UI twice under two mental models, with a
machine-generated name.

**Filter with an allowlist of the SQL/warehouse types, not a denylist** — an unknown future
connector type then hides instead of rendering broken. That group also shares one access
path (Arrow Flight gRPC `:8080`, per Q2) and one shape: query in, table out. On this
deployment the allowlist yields 6 rows instead of 22.

Excluded in v1: `DatasetConfig` and `NetAppVolumeConfig` (already covered by the dataset
panel); object stores (`S3Config`, `GCSConfig`, `ADLSConfig`, `AzureBlobStorageConfig`,
`GenericS3Config` — different protocol, HTTP `:80`); vector DBs (`PineconeConfig`,
`QdrantConfig` — different shape).

### `displayName` is the connector type, not the instance name — NEW, CORRECTION

```
name: "test"                      displayName: "Snowflake"
name: "Snowflake-Data-Warehouse"  displayName: "Snowflake"
```

Both Snowflake sources share `displayName`. The panel's primary identifier must be `name`;
`displayName` is a type badge. Reversing them renders two indistinguishable rows.

### Credential spread on this deployment — VERIFIED

`Snowflake-Data-Warehouse` is `authType: KeyPair`, `credentialType: Shared` — a service
account, so it is the safe demo source. `test` is `Basic` / `Individual`, and `AWS_MSSQL` is
`Basic` / `Individual` — keep them as test cases for the Q3 warning path.

`status` is absent from the public response: `getAccessibleAndActiveDataSources` is
pre-filtered to active, so the private spec's `Pending|Active|Deleted` does not surface here.

---

## Addendum 2 — querying verified live

`spikes/domino-probes/snowflake_query_probe.py` against `Snowflake-Data-Warehouse`,
cloud-dogfood, 2026-08-18. Closes the last LIVE-VERIFY item in the v1 path.

### The build-time slice is viable — VERIFIED

`domino_data` is **preinstalled** (`/opt/conda/lib/python3.12/site-packages/domino_data/`),
the source resolves by name with **no project attachment**, and `SELECT 1` returns in
1.6s. A second query took the same time, so there is **no cold Arrow Flight penalty** and
querying can happen inline in a chat turn rather than backgrounded.

### The connector is not fully specified, but the picker can cascade — VERIFIED

Session context: `USR=DOMINO  ROLE=APP_ROLE_DOMINO  WAREHOUSE=DOMINO_WH  DB=None
SCHEMA=None`.

An unqualified `information_schema` query fails: *"This session does not have a current
database."* That is a session-context gap, **not** missing introspection. Qualified names
work, and every level enumerates:

| Level | Call | Cost |
|-------|------|------|
| databases | `SHOW DATABASES` | 2.3s |
| schemas | `SHOW SCHEMAS IN DATABASE <db>` | 3.5s |
| tables | `SELECT ... FROM <db>.INFORMATION_SCHEMA.TABLES` | 2.9s |

**So the picker cascades — source, database, schema, table — with nothing typed by the
user.** Load each level lazily on expand; a prefetched tree would cost ~9s. This replaces
the free-text warehouse/database fields the NULL values first implied.

### Snowflake identity is one shared service account — VERIFIED

Every Domino user reads as `DOMINO` / `APP_ROLE_DOMINO`. This confirms
`credentialType: Shared` end to end, and it is why letting a published app use its
creator's access adds no privilege **within Domino** — the credential was never personal.

### But the blast radius is the whole company warehouse — NEW, and it sets the guard

`DWH` is Domino's production warehouse. Visible schemas include `MARTS`, `REPORTING`,
`INTERMEDIATE`, `STAGING`, `LEGACY`, plus per-source schemas (`AMAZON`, `NEWRELIC`, `ORCA`,
`ROCKETLANE`, `ABACUM`, `FLEETCOMMAND`, `CORTEX`). Tables include Gong call transcripts and
CRM context, Jira issue history, Anthropic and Cursor usage and cost reports, and AWS cost
and usage.

The `DOMINO` service account reads across all of it. So two guards are requirements, not
polish:

1. **Runtime querying only when `credentialType == Shared`.** An `Individual` source would
   re-export one person's private access to every viewer.
2. **Never allow a data-source-querying app to be published `PUBLIC`.** Authenticated at
   minimum. A `PUBLIC` app on `INTERMEDIATE.INT_GONG__TRANSCRIPT_SPEAKERS` would put
   customer call transcripts on the open internet. Sage sets app visibility at publish
   time, so it can enforce this.

For the demo itself, prefer the curated layers (`MARTS`, `REPORTING`) over `INTERMEDIATE`
or `STAGING`.

---

## Addendum 3 — what #10 shipped on

`backend/sage/resources/provider.py` now lists Data Sources. It takes the recommendation above
verbatim: the public `GET /api/datasource/v1/datasources`, unscoped, `name` as the row's
identifier and `displayName` as the connector badge.

### Readiness needed a second call, because the listing cannot answer it

`getAccessibleAndActiveDataSources` is pre-filtered to active-and-accessible, so every row it
returns is one the caller has permission on. That makes it unable to answer *"would this one
open for me"* — an `Individual` credential is entered per person, and a source can be listed
while this particular person has entered nothing. So the provider follows the listing with
`POST /v4/datasource/authentication-status`, `{dataSourceIds: [...]}` → `array<boolean>`.

`credentialType` is **shown, not enforced** at browse time. An `Individual` source is
queryable in a build session, which runs as the creator; it only becomes a hazard at publish
time, where ADR-0001 puts the guard. Inferring "unusable" from it at browse time would grey
out two of the three sources on this deployment that the creator can query today.

### `VERIFIED` — the readiness call answers, and it answers positionally

Run against the dogfood deployment from inside a workspace, August 20, 2026. Both properties
`merge_readiness` leans on hold.

**It answers at all.** `POST {api}/v4/datasource/authentication-status` with
`{"dataSourceIds": [...]}` returns a bare JSON array of booleans, one per requested id — no
envelope, no ids. Private spec, `/v4`, no public equivalent, but present here.

**The booleans are positional, in request order.** Proved by swapping the first two ids and
watching the answer follow:

```
["65eed…07f7", "6615…156a", "67ab…931f"]  ->  [false, true,  false]
["6615…156a", "65eed…07f7", "67ab…931f"]  ->  [true,  false, false]
```

The first arrangement alone proves nothing, and this is the trap worth carrying forward:
`[false,true,false]` is a palindrome, so reversing the request returns a byte-identical array
whether the server honours order or sorts the ids and ignores it. Moving the single `true` off
centre is what makes the test decisive. When probing any positional API, choose inputs whose
expected answer is asymmetric.

A failure still degrades to `ready=None` — every row lists, with the rail saying Domino did
not answer — rather than failing the listing. That is now the deployment-varies fallback
rather than the expected case: a 404 would mean the route is absent on some other deployment,
not that Sage guessed the route wrong.

### The allowlist as shipped

23 SQL/warehouse types in `SQL_CONNECTORS`. On this deployment that turns 22 rows into 6.
Excluded, each one line from being offered: `DatasetConfig` and `NetAppVolumeConfig` (mount-
shaped, already Assets, and 16 of the 22 rows), the object stores, the vector databases, and
`MongoDBConfig` / `PalantirConfig` (neither speaks SQL).

---

## Addendum 4 — what #11 shipped on

The cascade Addendum 2 proved possible is now the picker. `backend/sage/resources/provider.py`
holds the statements, `sage/orchestrator/service.py` resolves and validates, and the rail draws
one select per level. Three things are worth recording, because the file above does not predict
them.

### One connector is verified, twelve are honest guesses — the split is the design

`SQL_DIALECTS` covers **13 of the 23** entries in `SQL_CONNECTORS`. Only `SnowflakeConfig` is
marked `verified`, and it carries exactly the three statements timed in Addendum 2. The rest are
the standard `information_schema` shape: Postgres (and Redshift, Greenplum), MySQL (and MariaDB,
SingleStore, ClickHouse), SQL Server and Synapse via `sys.databases`, Databricks and Trino via
`SHOW CATALOGS`, BigQuery.

Left out, each one line from being added: DB2, Druid, GenericJDBC, Ignite, Netezza, Oracle, SAP
HANA, Teradata, Vertica. None of them serves the ANSI `information_schema` views the table leans
on, so an entry would be a guess with nothing behind it.

That split is affordable only because of **how an unverified dialect fails**: a wrong statement
comes back as the store's own error, on one level, shown in the rail — "this connector said
*x*", not an empty schema. A source with no dialect at all never opens a picker; `dialect_for`
refuses by name, and Use records the source without a scope, which is all a Binding meant before
this issue. Neither path is a dead end, and neither dresses a failure as an answer.

### Two-level stores are a third state, not a missing database

Postgres, MySQL and BigQuery connect **inside** one database. `SqlDialect.databases is None`
models that, and `cascade_levels` returns `["schema", "table"]`. This is distinct from `[]`,
which means Sage cannot look inside at all. Collapsing the two would either show a Database
select holding one item, or hide a schema list that works perfectly well.

### The identifier allowlist is the guard Addendum 2 asked for, at both edges

Addendum 2 established the blast radius: the `DOMINO` service account reads the whole company
warehouse. So a name that reaches a `format()` template is validated against
`[A-Za-z0-9_$]+` — an **allowlist, not an escape** — in `safe_identifier`, called at the
orchestrator edge and again where the SQL is built. Quoting is applied on top, but only to
preserve case; the validation has already ruled out what quoting would be protecting against.
A refused name is a 400 naming the character class, not a silent drop.

Two smaller findings from the same reading:

- `readable_error` redacts any run of 32+ identifier characters before showing a store's
  failure, because `DataSourceClient.__repr__` prints its api_key in plaintext (the probe script
  had to avoid printing the client for that reason) and `DOMINO_USER_API_KEY` is 64 chars.
  Redaction runs **before** truncation — a cut that ran first would leave the front of a key
  showing.
- Every introspection statement is `SELECT` or `SHOW`, asserted over the whole table in
  `test_the_introspection_statements_only_read`. The credential can write; Sage's SQL cannot.

### Levels resolve against a live listing, and are not cached

`Orchestrator._data_source` re-lists Data Sources per level opened. One extra listing next to a
query that costs seconds is cheap, and a cached row would let the cascade keep walking into a
source Domino had stopped offering this caller.

### Verified through the orchestrator, 2026-08-20

**All three Snowflake levels enumerate in a real builder**: databases, schemas, and tables. Until
this date none of them had. The 2.3s/3.5s/2.9s timings recorded when #11 shipped came from a probe
script running on the image's system python, while the orchestrator runs from uv's isolated venv,
which carried no `domino_data` until 49bc66e. For as long as #11 had been shipped, what the rail
actually answered in a live builder was "Sage reads a Data Source's contents through the Domino data
library, which is not installed here". `/api/diag` now reports that import, because the builder has
no terminal and the two pythons were otherwise indistinguishable from outside.

The third level is worth naming separately, because it is not a third of the same thing. Levels one
and two are `SHOW DATABASES` and `SHOW SCHEMAS IN DATABASE {db}`; level three is `SELECT TABLE_NAME
... FROM {db}.INFORMATION_SCHEMA.TABLES`, and its result is read by `name_column` from a named column
rather than from `SHOW`'s output shape. So this run proves both statement kinds and both result
shapes on Snowflake, not one kind three times.

### Still not verified

Every dialect except Snowflake's, and the timings for any of them. The `INFORMATION_SCHEMA`
statements are now proven on Snowflake but unrun on the other twelve dialects, where the same SQL
meets a different catalog. First contact with a non-Snowflake source is the test, and the failure it
produces is designed to be legible enough to fix the table from.

---

## Addendum 4 — what #12 shipped on, and one correction

The two guards above are now enforced, in `backend/sage/resources/publish_guard.py`, at both
publish routes: the builder's (`Orchestrator.publish`) and the hub's (`HubService.publish_app`).
The hub reads the app's Resource list from the committed `.sage/bindings.json` over the repo
provider, because it publishes without a builder and has no workspace to read.

**Correction to guard 2.** "Sage sets app visibility at publish time, so it can enforce this" is
only half true. Sage sets `visibility: "GRANT_BASED"` when it *creates* an App, and a re-publish
posts a **version** (`POST /api/apps/beta/apps/{id}/versions`), whose body carries no visibility at
all. So Sage sets it once and can never set it again — an App's sharing can be changed afterwards
on its own settings page, which is the page Sage's own Publish links to as "Manage settings in
Domino". Enforcement therefore has to *read* the value back before a re-publish, which
`ControlPlane.app_visibility` does.

**Verified live, 2026-08-20, cloud-dogfood** (`sage.tools.app_visibility`, on an app Sage had
published). The detail response carries a **top-level `visibility`**, and an app Sage published
reads **`GRANT_BASED`**. Its top-level keys in full:

```
accessStatuses, configurationType, currentVersion, discoverable, entryPoint, id, mountDatasets,
name, project, properties, publisher, renderIFrame, updatedAt, url, vanityUrl, views, visibility
```

So the guard matches an **allow** list (`ALLOWED_VISIBILITY = {GRANT_BASED, PRIVATE,
AUTHENTICATED}`) and refuses everything else, quoting the value it saw. Before the field was verified
this had to be inverted — a guessed field name read as "not GRANT_BASED" would have refused every
re-publish of every app — but that risk is gone once the name and the real values have been seen.

**The whole sharing vocabulary, read back 2026-08-20.** Two independent axes, and only one is about
access:

| Control | Field and value |
|---|---|
| Dropdown: *Restricted (project collaborators)* | `visibility: "GRANT_BASED"` — what Sage sets at create |
| Dropdown: *Anyone in Domino* | `visibility: "AUTHENTICATED"` |
| Checkbox: *Globally discoverable* | the separate top-level `discoverable` flag; "All Domino users can find this App and **request access** to view" |

Both were read from live apps: `GRANT_BASED` from one Sage published and nobody re-shared, and
`AUTHENTICATED` from one deliberately republished at *Anyone in Domino*. `discoverable` was `false`
on both, which is what confirms it is an independent field rather than the same setting seen twice.

**`AUTHENTICATED` is ALLOWED**, and that is this document's own line — "never publish a
resource-querying app as `PUBLIC`; authenticated at minimum". Every viewer of such an app is a named
Domino user who signed in, and since #13 what they can run is the set of named queries the creator
declared rather than the warehouse. The exposure the guard was written against is an anonymous app
on the open internet.

**Consequence worth naming: on this deployment guard 2 stops nothing.** There is no anonymous
setting to refuse, so it is a guard for a case that cannot arise here, and it becomes active on a
deployment that offers one. That is the honest outcome of drawing the line where the research drew
it, not a defect. The credential guard carries the weight in the meantime, and it is the one that
fires on real configurations.

`discoverable` is deliberately **not** guarded on: finding an app and being able to read what it
queries are different things, and a request for access is still a request.

**Two id spaces, resolved.** They are not two systems — they are two fields on one record.
`id` is the 24-hex ObjectId the beta API keys on and the manage URL uses; `vanityUrl` is the UUID
that appears in `/apps/{uuid}` viewer links. Same app, same response.

**`extendedIdentityPropagationToAppsEnabled: false`**, seen on `currentVersion`. This is the
per-viewer identity setting ADR-0001 names as admin-only and unavailable to Sage's users, and it is
the assumption the entire shared-credential guard rests on. First time it has been observed rather
than assumed.

**`?projectId=` IS honored for beta apps** (same probe): the request that returns 284 rows
unfiltered returned exactly this project's 1. The earlier "not reliably honored" reading came from
projects holding only classic, non-beta apps, whose empty answer was the truth rather than a filter
failing. `list_project_apps` keeps its client-side `project.id` match anyway, because it reads only
one page of 100: a filter that silently stopped working would otherwise mean publishing a SECOND
app instead of a new version, and taking the visibility guard down with it.

---

## Addendum 4 — what a Scope can travel as (#14, read 2026-08-20)

**VERIFIED against the installed package**, `dominodatalab-data` 6.7.4 (`domino_data/configuration_gen.py`,
generated 2025-10-15), read class by class — all 23 SQL connectors.

The plan for #14 was that the Binding's database and schema ride along as `configOverwrites` so the
generated SQL stays unqualified. That works, but **not for most connectors**. Which keys a Data
Source will accept is fixed per connector by the SDK's generated config classes, and only **three of
the twenty-three carry a schema**.

| `dataSourceType` | database level → | schema level → | Cascade (#11) records |
|---|---|---|---|
| `SnowflakeConfig` | `database` | `schema` | database, schema, table |
| `DatabricksConfig` | `catalog` | `schema` | catalog, schema, table |
| `TrinoConfig` | `catalog` | `schema` | catalog, schema, table |
| `MySQLConfig` | — | `database` | schema, table (one namespace level) |
| `PostgreSQLConfig` | `database` | **none** | schema, table |
| `RedshiftConfig` | `database` | **none** | schema, table |
| `SQLServerConfig` | `database` | **none** | database, schema, table |
| `SynapseConfig` | **none** | **none** | database, schema, table |
| `BigQueryConfig` | **none** (only `project`) | **none** | schema, table |
| `GreenplumConfig`, `MariaDBConfig`, `SingleStoreConfig`, `ClickHouseConfig` | **none** | **none** | schema, table |
| `OracleConfig`, `DB2NativeConfig` | `database` | **none** | no dialect — no Scope recorded |

Every remaining class carries only `datetimePrecision`.

**MySQL is the one that reads backwards.** Its family has a single namespace level, which the cascade
offers as a *schema* and the SDK calls a *database*. Sent under the name the cascade used, it would
be dropped.

**Why this is not a detail.** A schema that cannot travel leaves an unqualified statement running on
whatever the connection defaults to. That does not fail — it answers, with rows from the wrong
schema, and a warehouse holding the same table in `dev` and `prod` answers convincingly. So
`serve.py` refuses such a query at startup **unless the statement names the level itself**
(`_SCOPE_KEYS`, `_scope_problem`): the Scope must either be enforceable or already stated. A
connector Sage cannot scope is then not a dead end — #15's agent writes `FROM marts.orders` and the
query runs.

**Consequence for the Binding.** The published app has no Sage around it and nothing to ask at boot,
so `connector_type` (Domino's own `dataSourceType`) is now recorded in `.sage/bindings.json` beside
the Scope. A Binding written before this has none, and is treated exactly as a connector that cannot
carry the level: refused unless the statement says where it reads.

**Two SDK details the executor depends on**, same reading:

- `Datasource.update(config)` stores an override that `query()` reads via `config.config()`, and
  `query()` is also where `_get_credential_override()` runs — so going through `update` + `query`
  rather than `DataSourceClient.execute` directly is what keeps an OAuth or AWS-IAM source working.
- `AuthMiddlewareFactory.start_call` fetches a **fresh** JWT from the token sidecar for every RPC
  (`auth.py`), so a long-lived `DataSourceClient` in a published App does not go stale and nothing in
  `serve.py` needs to hold a token.

**LIVE-VERIFY.** Only Snowflake has been run for real, and only through the builder (#11's cascade).
Nothing in this table has been exercised from inside a *published* App yet: `DOMINO_API_PROXY` is
confirmed present there (ADR-0002), `DOMINO_DATASOURCE_PROXY_FLIGHT_HOST` is not.

---

## Addendum 5 — the fourth level: columns (#15)

The cascade (#11) stops at tables. The agent writing an app's queries needs one level further down,
so `SqlDialect` gained a `columns` statement — `INFORMATION_SCHEMA.COLUMNS`, selecting
`TABLE_NAME`, `COLUMN_NAME`, `DATA_TYPE`, ordered by `ORDINAL_POSITION`.

**One query for the whole Scope**, not one per table. The cascade measures ~3s a level against the
live warehouse, so a schema of 200 tables would otherwise cost minutes at bind time. The statement
takes an optional `AND TABLE_NAME = '…'` clause (`{table_clause}`) so the same statement narrows to
one table when the creator picked one.

**Same verification status as the rest of the table**: only Snowflake's has been run live, and it
reuses the shape its `tables` statement already proved. The others are the standard
`information_schema` form and are honest guesses, failing the same way — the connector's own error
on that one level, not an empty answer.

**Types are the store's own word** (`VARCHAR`, `NUMBER`, `TIMESTAMP_NTZ`), not normalised. The agent
reads them to decide what a comparison should look like, and a tidied-up name would be Sage guessing
on its behalf about a store it cannot see.

**No rows, ever.** Names and types only. Sample data is production data in a model's context, and
whether that is acceptable is the creator's explicit decision (#16) — the AGENTS.md region tells the
agent not to read the store itself, which is what actually holds the line, since the agent has a
shell and could otherwise go and look.

---

## Addendum 6 — reading rows, and where they may live (#16)

`SqlDialect.sample` reads rows. It is not a cascade level — nothing opens it, and it runs only when a
creator explicitly asks — but it lives beside the rest because it is the same per-connector problem.

**Three spellings, because the standard one is not universal.**

| Shape | Connectors | Statement |
|---|---|---|
| Three levels | Snowflake, Databricks, Trino | `SELECT * FROM {db}.{schema}.{table} LIMIT {n}` |
| Two levels | PostgreSQL, Redshift, Greenplum, MySQL family, BigQuery | `SELECT * FROM {schema}.{table} LIMIT {n}` |
| `TOP` | SQL Server, Synapse | `SELECT TOP {n} * FROM {db}.{schema}.{table}` |

SQL Server's family has no `LIMIT` and takes `TOP` before the select list, so appending ` LIMIT 5` to
one statement for every connector is a syntax error on two of them. A store with no database level
must not be left with an empty `{db}.` in front of the schema either. Same verification status as
everything else in the table: only Snowflake has been run live.

**Where the rows may live is the load-bearing decision.** Every other manifest under `.sage/` is
committed and rides into the published app's container — right for column names and types, wrong for
rows. So `.sage/samples.json` is gitignored, and `AGENTS.md`, which IS committed, can only name the
shared tables and point at the file. It can never quote a row.

Two consequences worth stating:

- A fresh clone has neither the rows nor the sovereign lock they set. That is the point of
  gitignoring them, not a gap.
- The sovereign lock is in-memory and is re-fired at session open. For attachments that is done from
  the committed `.sage/attachments.json`; for samples the only record of the treatment is the
  gitignored file itself, so `_relock_for_samples` reads it directly.

**Cells are cut at 80 characters** and non-JSON values stringified. The agent is being shown the
SHAPE of the data — a value cut at that length still says "this is an email address" or "this is a
currency code", which is the whole reason for showing it, while a base64 blob in full just spends
context.
