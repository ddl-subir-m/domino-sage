"""Live Data Source execution in the Built App (#14).

#13 landed the boundary and a seam where an executor goes. This is the executor: a named query, the
Scope its Binding recorded, and a store that answers now rather than at build time.

The Domino SDK is faked at `sys.modules` rather than installed. Not to avoid a dependency — the
laptop running these tests has no Domino cluster to reach anyway, so a real `DataSourceClient` could
only fail — but because what is worth asserting is what CROSSES to it: which configuration override
was attached, what statement was built, and that a store's own failure never reaches a viewer.

The server under test ships IN the app's repo (`template/react-vite/serve.py`), so it is loaded by
path, exactly as `test_builtapp_serve.py` and `test_builtapp_queries.py` load it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import types
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

_SERVE_PY = Path(__file__).resolve().parents[2] / "template" / "react-vite" / "serve.py"


def _load_serve():
    spec = importlib.util.spec_from_file_location("builtapp_serve_flight", _SERVE_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # see test_builtapp_serve.py: dataclasses resolve through sys.modules
    spec.loader.exec_module(mod)
    return mod


serve = _load_serve()


# ---- a stand-in for the SDK, recording what reached it -------------------------------------------


class _Array:
    def __init__(self, values):
        self._values = list(values)

    def to_pylist(self):
        return list(self._values)


class _Batch:
    """One Arrow record batch's worth of the interface `_drain` uses: columns by position."""

    def __init__(self, columns):
        self.columns = [_Array(c) for c in columns]
        self.num_rows = len(columns[0]) if columns else 0


class _Reader:
    def __init__(self, names, batches):
        self.schema = types.SimpleNamespace(names=list(names))
        self._batches = list(batches)
        self.cancelled = False

    def read_chunk(self):
        if not self._batches:
            raise StopIteration
        return types.SimpleNamespace(data=self._batches.pop(0), app_metadata=None)

    def cancel(self):
        self.cancelled = True


class Store:
    """What the app did to a Data Source, and what the Data Source did back."""

    def __init__(self, names=("region", "total"), batches=None, on_open=None, on_query=None):
        self.reader = _Reader(names, batches if batches is not None else [_Batch([["EMEA"], [10]])])
        self.on_open = on_open      # raised instead of resolving the source
        self.on_query = on_query    # raised instead of answering
        self.opened: list = []      # the names `get_datasource` was called with
        self.config: dict = {}      # the configuration override attached to the query
        self.sql = ""               # the statement that reached the store
        self.clients = 0            # how many DataSourceClients were built

    @property
    def module(self):
        """The fake `domino_data.data_sources`."""
        store = self

        class _Datasource:
            def update(self, config):
                store.config = config.config()

            def query(self, sql):
                store.sql = sql
                if store.on_query:
                    raise store.on_query
                return types.SimpleNamespace(reader=store.reader)

        class DataSourceClient:
            def __init__(self):
                store.clients += 1

            def get_datasource(self, name):
                store.opened.append(name)
                if store.on_open:
                    raise store.on_open
                return _Datasource()

        mod = types.ModuleType("domino_data.data_sources")
        mod.DataSourceClient = DataSourceClient
        return mod


@contextmanager
def sdk(store: Store | None):
    """The Domino SDK importable, or (with None) an image where it is not installed at all."""
    saved = {k: sys.modules.get(k) for k in ("domino_data", "domino_data.data_sources")}
    try:
        if store is None:
            sys.modules["domino_data"] = None            # import raises, as a missing package does
            sys.modules["domino_data.data_sources"] = None
        else:
            sys.modules["domino_data"] = types.ModuleType("domino_data")
            sys.modules["domino_data.data_sources"] = store.module
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


# ---- the app the executor runs inside -------------------------------------------------------------


SNOWFLAKE = {"kind": "data_source", "id": "ds-dwh", "name": "warehouse", "display_name": "warehouse",
             "database": "ANALYTICS", "schema": "MARTS", "connector_type": "SnowflakeConfig"}
REVENUE_SQL = "SELECT region, SUM(amount) AS total FROM orders WHERE region = :region"
REVENUE = {"name": "revenue", "binding": "ds-dwh", "sql": REVENUE_SQL,
           "params": [{"name": "region", "type": "string"}]}


@pytest.fixture
def app(tmp_path: Path) -> Path:
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("<!doctype html><div id=root>APP</div>")
    (tmp_path / ".sage").mkdir()
    return tmp_path


def write(root: Path, bindings: list, queries: list) -> None:
    (root / ".sage" / "bindings.json").write_text(json.dumps(bindings))
    (root / ".sage" / "queries.json").write_text(json.dumps(queries))


@contextmanager
def running(app: Path, store: Store, max_rows: int = 5000):
    """The server on a throwaway port, with the real executor over the fake SDK."""
    executor = serve.FlightExecutor(serve.load_sources(app), max_rows)
    srv = serve.build_server(app / "dist", host="127.0.0.1", port=0, project_root=app,
                             executor=executor)
    thread = threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        with sdk(store):
            yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def ask(base: str, name: str, params=None) -> httpx.Response:
    return httpx.post(f"{base}/api/queries/{name}",
                      json={} if params is None else {"params": params}, timeout=10)


# ---- the Scope travels as configuration, so the statement stays unqualified -----------------------


def test_the_query_reaches_the_store_and_its_rows_reach_the_viewer(app: Path):
    write(app, [SNOWFLAKE], [REVENUE])
    store = Store()
    with running(app, store) as base:
        r = ask(base, "revenue", {"region": "EMEA"})
    assert r.status_code == 200
    assert r.json() == {"columns": ["region", "total"], "rows": [["EMEA", 10]], "truncated": False}
    assert store.opened == ["warehouse"]     # resolved by NAME, which is what get_datasource takes


def test_the_chosen_database_and_schema_travel_as_configuration(app: Path):
    # The criterion this ticket turns on. The statement the creator's agent wrote says `FROM orders`,
    # and it stays that way — where `orders` lives is configuration attached to the query, not text
    # spliced into it.
    write(app, [SNOWFLAKE], [REVENUE])
    store = Store()
    with running(app, store) as base:
        ask(base, "revenue", {"region": "EMEA"})
    assert store.config == {"database": "ANALYTICS", "schema": "MARTS"}
    assert "FROM orders" in store.sql and "ANALYTICS" not in store.sql


@pytest.mark.parametrize("connector, recorded, expected", [
    # Same two ideas, different keys. Read off the SDK's own config classes, which is the only thing
    # that decides what a store will accept.
    ("SnowflakeConfig", {"database": "DWH", "schema": "MARTS"}, {"database": "DWH", "schema": "MARTS"}),
    ("DatabricksConfig", {"database": "main", "schema": "gold"}, {"catalog": "main", "schema": "gold"}),
    ("TrinoConfig", {"database": "hive", "schema": "gold"}, {"catalog": "hive", "schema": "gold"}),
    # MySQL's family has ONE namespace level. The cascade offers it as a schema; the SDK calls it a
    # database. Sending it under the name the cascade used would send nothing at all.
    ("MySQLConfig", {"schema": "sales"}, {"database": "sales"}),
])
def test_each_connector_carries_the_scope_under_the_key_it_accepts(connector, recorded, expected):
    source = serve.Source("ds", "src", recorded.get("database", ""), recorded.get("schema", ""),
                          connector)
    config, stranded = source.scope()
    assert config == expected
    assert stranded == []


def test_a_connector_that_carries_only_half_the_scope_sends_that_half(app: Path):
    # SQL Server has three cascade levels and a config class that takes only the outer one. The
    # database still travels; the schema is what the statement has to name.
    source = serve.Source("ds", "mssql", "underwriting", "dbo", "SQLServerConfig")
    config, stranded = source.scope()
    assert config == {"database": "underwriting"}
    assert stranded == [("schema", "dbo")]


# ---- a Scope that cannot travel is refused, not quietly dropped ------------------------------------


def test_a_schema_that_cannot_travel_stops_the_query_at_startup(app: Path):
    # A PostgreSQL Data Source takes no schema as configuration, so an unqualified statement would run
    # on whatever the connection defaults to. That reads as an answer, which is why it is refused
    # here rather than reported by whichever viewer happens to notice the numbers are wrong.
    postgres = dict(SNOWFLAKE, connector_type="PostgreSQLConfig", database="")
    write(app, [postgres], [REVENUE])
    queries = serve.load_queries(app)
    assert "MARTS" in queries["revenue"].problem
    assert "cannot carry" in queries["revenue"].problem
    store = Store()
    with running(app, store) as base:
        r = ask(base, "revenue", {"region": "EMEA"})
    assert r.status_code == 503
    assert "MARTS" in r.json()["error"]
    assert store.opened == []       # nothing was asked of the store


def test_a_statement_that_names_the_schema_itself_needs_nothing_from_configuration(app: Path):
    # And this is why the rule is "the scope must be enforceable OR stated", not a blanket refusal:
    # a connector Sage cannot scope is then not a dead end. The agent writes `FROM marts.orders`.
    postgres = dict(SNOWFLAKE, connector_type="PostgreSQLConfig", database="")
    qualified = dict(REVENUE, sql="SELECT region FROM MARTS.orders WHERE region = :region")
    write(app, [postgres], [qualified])
    store = Store(names=("region",), batches=[_Batch([["EMEA"]])])
    with running(app, store) as base:
        r = ask(base, "revenue", {"region": "EMEA"})
    assert r.status_code == 200
    assert store.config == {}       # nothing to send, and nothing invented to send


def test_a_schema_named_inside_a_longer_word_does_not_count(app: Path):
    # SMARTS is not MARTS. A substring match here would let a query through on a coincidence and put
    # it back on the default schema.
    postgres = dict(SNOWFLAKE, connector_type="PostgreSQLConfig", database="")
    lookalike = dict(REVENUE, sql="SELECT region FROM SMARTS_STAGING WHERE region = :region")
    write(app, [postgres], [lookalike])
    assert "MARTS" in serve.load_queries(app)["revenue"].problem


def test_a_binding_that_does_not_say_what_kind_of_store_it_is_is_refused_the_same_way(app: Path):
    # Every Binding written before #14 is this: a Scope with no connector type beside it. Guessing
    # would mean guessing whether a schema reaches the store, so it is refused with the same way out.
    unknown = dict(SNOWFLAKE)
    unknown.pop("connector_type")
    write(app, [unknown], [REVENUE])
    problem = serve.load_queries(app)["revenue"].problem
    # The outermost stranded level first — one sentence at a time, as the catalog's other checks do.
    assert "does not record what kind" in problem and "ANALYTICS" in problem


def test_a_connector_with_no_scope_recorded_runs_with_no_configuration(app: Path):
    # "This app uses this Data Source" was the whole of a Binding before #11, and it stays valid:
    # nothing recorded is nothing to enforce, so there is nothing to refuse.
    bare = {"kind": "data_source", "id": "ds-dwh", "name": "warehouse", "display_name": "warehouse",
            "connector_type": "OracleConfig"}
    write(app, [bare], [REVENUE])
    store = Store()
    with running(app, store) as base:
        assert ask(base, "revenue", {"region": "EMEA"}).status_code == 200
    assert store.config == {}


# ---- values become literals, and only values do ---------------------------------------------------


def test_a_string_parameter_is_quoted_and_its_quotes_are_doubled(app: Path):
    write(app, [SNOWFLAKE], [REVENUE])
    store = Store()
    with running(app, store) as base:
        assert ask(base, "revenue", {"region": "O'Brien"}).status_code == 200
    assert store.sql.endswith("WHERE region = 'O''Brien'")


def test_a_backslash_is_refused_rather_than_escaped(app: Path):
    # Doubling a quote is the standard escape and is only enough where a backslash is not also one.
    # MySQL's family says it is, so `x\` would end its own literal and let the rest read as SQL.
    write(app, [SNOWFLAKE], [REVENUE])
    store = Store()
    with running(app, store) as base:
        r = ask(base, "revenue", {"region": "EMEA\\"})
    assert r.status_code == 400
    assert "backslash" in r.json()["error"]
    assert store.sql == ""


@pytest.mark.parametrize("connector, value, expected", [
    ("SnowflakeConfig", True, "TRUE"),
    ("SnowflakeConfig", False, "FALSE"),
    # SQL Server and Synapse have BIT and no boolean keyword, so TRUE is a syntax error there.
    ("SQLServerConfig", True, "1"),
    ("SQLServerConfig", False, "0"),
])
def test_a_boolean_is_rendered_the_way_the_store_spells_one(connector, value, expected):
    assert serve.render("WHERE active = :on", {"on": value}, connector) == f"WHERE active = {expected}"


def test_a_date_is_rendered_quoted(app: Path):
    # Quoted, not ANSI `DATE '...'`: every store here converts the quoted form in a comparison, and
    # SQL Server rejects the keyword one.
    assert serve.render("WHERE d >= :from", {"from": date(2026, 8, 20)}) == "WHERE d >= '2026-08-20'"


def test_numbers_are_rendered_bare():
    assert serve.render("LIMIT :n", {"n": 25}) == "LIMIT 25"
    assert serve.render("> :x", {"x": 1.5}) == "> 1.5"


def test_a_postgres_cast_is_not_read_as_a_placeholder():
    # `amount::text` is a cast. Read as a placeholder it would be an undeclared parameter called
    # `text`, and #13's agreement check would refuse the query with a sentence about a parameter its
    # author never wrote.
    assert serve._PLACEHOLDER.findall("SELECT amount::text WHERE r = :region") == ["region"]


# ---- the cap, so one question cannot take the app down --------------------------------------------


def test_a_result_larger_than_the_cap_is_cut_and_says_so(app: Path):
    write(app, [SNOWFLAKE], [REVENUE])
    store = Store(names=("region", "total"),
                  batches=[_Batch([["a", "b"], [1, 2]]), _Batch([["c", "d"], [3, 4]])])
    with running(app, store, max_rows=3) as base:
        r = ask(base, "revenue", {"region": "EMEA"})
    body = r.json()
    assert body["truncated"] is True
    assert body["rows"] == [["a", 1], ["b", 2], ["c", 3]]
    assert store.reader.cancelled   # the store stops streaming into a socket nobody is reading


def test_a_result_exactly_the_size_of_the_cap_is_not_called_truncated(app: Path):
    write(app, [SNOWFLAKE], [REVENUE])
    store = Store(names=("region",), batches=[_Batch([["a", "b"]])])
    with running(app, store, max_rows=2) as base:
        body = ask(base, "revenue", {"region": "EMEA"}).json()
    assert body["rows"] == [["a"], ["b"]] and body["truncated"] is False
    assert not store.reader.cancelled


def test_the_cap_is_the_default_unless_the_environment_names_another(monkeypatch):
    monkeypatch.delenv("SAGE_QUERY_MAX_ROWS", raising=False)
    assert serve.max_rows() == 5000
    monkeypatch.setenv("SAGE_QUERY_MAX_ROWS", "250")
    assert serve.max_rows() == 250
    # A typo should not decide that this app answers with nothing.
    monkeypatch.setenv("SAGE_QUERY_MAX_ROWS", "lots")
    assert serve.max_rows() == 5000
    monkeypatch.setenv("SAGE_QUERY_MAX_ROWS", "0")
    assert serve.max_rows() == 5000


# ---- what a store answers with, as JSON --------------------------------------------------------


def test_types_json_has_no_word_for_arrive_as_something_a_browser_can_read(app: Path):
    write(app, [SNOWFLAKE], [REVENUE])
    store = Store(names=("day", "amount", "note"),
                  batches=[_Batch([[date(2026, 8, 20)], [Decimal("12.50")], [None]])])
    with running(app, store) as base:
        body = ask(base, "revenue", {"region": "EMEA"}).json()
    assert body["rows"] == [["2026-08-20", 12.5, None]]


def test_two_columns_with_the_same_name_both_answer(app: Path):
    # Rows are taken by position. Keyed by name, one of these would overwrite the other and the app
    # would answer with a table that has two identical columns.
    write(app, [SNOWFLAKE], [REVENUE])
    store = Store(names=("n", "n"), batches=[_Batch([[1], [2]])])
    with running(app, store) as base:
        body = ask(base, "revenue", {"region": "EMEA"}).json()
    assert body["rows"] == [[1, 2]]


# ---- failures reach a viewer as a sentence, and the reason reaches the log ------------------------


def test_a_store_that_refuses_the_query_does_not_show_the_viewer_its_error(app: Path, capsys):
    write(app, [SNOWFLAKE], [REVENUE])
    store = Store(on_query=RuntimeError(
        "Flight: UNAUTHENTICATED at 10.4.2.7:5000 apikey=abcdefghijklmnopqrstuvwxyz0123456789"))
    with running(app, store) as base:
        r = ask(base, "revenue", {"region": "EMEA"})
    assert r.status_code == 502
    assert "did not answer" in r.json()["error"]
    assert "10.4.2.7" not in r.text and "UNAUTHENTICATED" not in r.text
    logged = capsys.readouterr().out
    assert "UNAUTHENTICATED" in logged           # the creator gets the reason
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in logged   # but never a token


def test_a_source_that_cannot_be_opened_says_what_to_check(app: Path):
    write(app, [SNOWFLAKE], [REVENUE])
    store = Store(on_open=RuntimeError("Received unexpected response: 404 datasource not found"))
    with running(app, store) as base:
        r = ask(base, "revenue", {"region": "EMEA"})
    assert r.status_code == 503
    assert "could not open" in r.json()["error"] and "404" not in r.text


def test_an_image_without_the_domino_library_says_so_once_per_ask(app: Path):
    write(app, [SNOWFLAKE], [REVENUE])
    executor = serve.FlightExecutor(serve.load_sources(app), 100)
    srv = serve.build_server(app / "dist", host="127.0.0.1", port=0, project_root=app,
                             executor=executor)
    thread = threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        with sdk(None):
            r = ask(f"http://127.0.0.1:{srv.server_address[1]}", "revenue", {"region": "EMEA"})
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
    assert r.status_code == 503
    assert "cannot reach its Data Source" in r.json()["error"]


def test_the_client_is_built_once_and_the_source_resolved_every_time(app: Path):
    # Built once because it holds a connection and the SDK re-reads the sidecar token per call, so it
    # cannot go stale. Resolved every time because a source that has been renamed or revoked should be
    # reported to whoever asks next, not remembered from whenever this container booted.
    write(app, [SNOWFLAKE], [REVENUE])
    store = Store(batches=[_Batch([["EMEA"], [10]]), _Batch([["EMEA"], [10]])])
    with running(app, store) as base:
        ask(base, "revenue", {"region": "EMEA"})
        store.reader = _Reader(("region", "total"), [_Batch([["AMER"], [7]])])
        ask(base, "revenue", {"region": "AMER"})
    assert store.clients == 1
    assert store.opened == ["warehouse", "warehouse"]


def test_how_long_the_query_took_is_recorded(app: Path, capsys):
    # The App log is the only place a creator can read this, and it sits beside the cold start there.
    write(app, [SNOWFLAKE], [REVENUE])
    with running(app, Store()) as base:
        ask(base, "revenue", {"region": "EMEA"})
    logged = capsys.readouterr().out
    assert "[sage] query revenue: 1 rows in" in logged


# ---- #13's boundary, with a real executor behind it ------------------------------------------------


def test_there_is_still_no_route_that_takes_a_statement(app: Path):
    write(app, [SNOWFLAKE], [REVENUE])
    store = Store()
    with running(app, store) as base:
        assert httpx.post(f"{base}/api/queries", json={"sql": "SELECT 1"}).status_code == 404
        assert ask(base, "revenue", {"region": "EMEA", "sql": "DROP TABLE orders"}).status_code == 400
    assert store.sql == ""


def test_a_parameter_still_cannot_become_part_of_the_statement(app: Path):
    # The value is quoted as one value. What it looks like does not change what it is.
    write(app, [SNOWFLAKE], [REVENUE])
    store = Store()
    with running(app, store) as base:
        assert ask(base, "revenue", {"region": "EMEA' OR 1=1 --"}).status_code == 200
    assert store.sql.endswith("WHERE region = 'EMEA'' OR 1=1 --'")
