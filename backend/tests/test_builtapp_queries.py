"""Named queries — the Built App's only path to data (#13).

The boundary is the subject, not the SQL. A published app runs as its publisher against a shared
service-account credential with broad warehouse read (ADR-0001; #12's guards are what keep that
credential shared), so the thing worth proving is that no route accepts SQL, that a caller's values
are checked before they are anywhere near a statement, and that a query the catalog cannot honour
says so with a sentence rather than failing per request inside a store.

Nothing here executes anything. The executor is the injected seam #14 will fill; here it is a fake
that records what crossed the boundary, which is the only way to assert that `sql` and `params`
arrived separately.

The server under test ships IN the app's repo (`template/react-vite/serve.py`), so it is loaded by
path, exactly as `test_builtapp_serve.py` loads it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import httpx
import pytest

_SERVE_PY = Path(__file__).resolve().parents[2] / "template" / "react-vite" / "serve.py"


def _load_serve():
    spec = importlib.util.spec_from_file_location("builtapp_serve_queries", _SERVE_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: `serve.py` uses `from __future__ import annotations`, so a dataclass
    # resolves its field types by looking its own module up in sys.modules. Running the app for real
    # (`python3 serve.py`) puts it there as __main__; loading it by path here does not unless we do.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


serve = _load_serve()

REVENUE_SQL = "SELECT region, SUM(amount) AS total FROM orders WHERE region = :region GROUP BY region"


class FakeExecutor:
    """Stands in for #14's Arrow Flight executor, and records what it was handed.

    Deliberately keeps `query` and `params` apart rather than a rendered statement: the assertion
    that matters is that nothing merged them on the way here.
    """

    def __init__(self, rows=None):
        self.calls = []
        self._rows = rows if rows is not None else [["EMEA", 10]]

    def __call__(self, query, params):
        self.calls.append((query, params))
        return {"columns": ["region", "total"], "rows": self._rows}


@pytest.fixture
def app(tmp_path: Path) -> Path:
    """An app repo: a built `dist/` beside the `.sage/` manifests the server reads at startup."""
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("<!doctype html><div id=root>APP</div>")
    (tmp_path / ".sage").mkdir()
    _write_bindings(tmp_path, [{"kind": "data_source", "id": "ds-dwh", "name": "warehouse",
                                "display_name": "warehouse", "database": "ANALYTICS",
                                "schema": "MARTS"}])
    return tmp_path


def _write_bindings(root: Path, entries: list) -> None:
    (root / ".sage" / "bindings.json").write_text(json.dumps(entries))


def _write_queries(root: Path, entries: list) -> None:
    (root / ".sage" / "queries.json").write_text(json.dumps(entries))


@contextmanager
def running(app: Path, executor=None):
    """The server on a throwaway port, serving app/dist with app/ as the project root."""
    srv = serve.build_server(app / "dist", host="127.0.0.1", port=0,
                             project_root=app, executor=executor)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def _ask(base: str, name: str, params=None, **kw) -> httpx.Response:
    body = {} if params is None else {"params": params}
    return httpx.post(f"{base}/api/queries/{name}", json=body, **kw)


REVENUE = {
    "name": "revenue_by_region", "binding": "ds-dwh", "sql": REVENUE_SQL,
    "params": [{"name": "region", "type": "string", "enum": ["EMEA", "AMER", "APAC"]}],
}


# ---- the boundary: a name goes in, never a statement ---------------------------------------------


def test_a_named_query_runs_and_returns_its_rows(app: Path):
    _write_queries(app, [REVENUE])
    fake = FakeExecutor()
    with running(app, fake) as base:
        r = _ask(base, "revenue_by_region", {"region": "EMEA"})
    assert r.status_code == 200
    assert r.json() == {"columns": ["region", "total"], "rows": [["EMEA", 10]]}


def test_the_executor_is_handed_the_sql_and_the_params_separately(app: Path):
    # The whole reason the executor takes two arguments. A single rendered string arriving here
    # would mean a caller's value had already been made part of a statement upstream.
    _write_queries(app, [REVENUE])
    fake = FakeExecutor()
    with running(app, fake) as base:
        _ask(base, "revenue_by_region", {"region": "APAC"})
    (query, params), = fake.calls
    assert query.sql == REVENUE_SQL          # verbatim, with :region still a placeholder
    assert params == {"region": "APAC"}
    assert ":region" in query.sql


def test_sql_in_the_request_body_is_refused_rather_than_used(app: Path):
    # There is no route that takes SQL, so the only way to try is to smuggle it alongside the
    # parameters. An unknown key is refused, not ignored.
    _write_queries(app, [REVENUE])
    fake = FakeExecutor()
    with running(app, fake) as base:
        r = _ask(base, "revenue_by_region", {"region": "EMEA", "sql": "SELECT * FROM salaries"})
    assert r.status_code == 400
    assert not fake.calls          # nothing reached the executor at all


def test_a_query_path_will_not_answer_a_GET(app: Path):
    # Extensionless, so without the guard the SPA rewrite would answer 200 with index.html — HTML
    # where JSON was asked for, which reads as a broken app rather than as the wrong method.
    _write_queries(app, [REVENUE])
    with running(app, FakeExecutor()) as base:
        r = httpx.get(f"{base}/api/queries/revenue_by_region")
    assert r.status_code == 405
    assert r.headers["content-type"].startswith("application/json")


def test_an_unknown_query_name_is_rejected(app: Path):
    _write_queries(app, [REVENUE])
    fake = FakeExecutor()
    with running(app, fake) as base:
        r = _ask(base, "salaries", {})
    assert r.status_code == 404
    assert "salaries" in r.json()["error"]
    assert not fake.calls


def test_a_query_name_is_never_used_to_build_anything(app: Path):
    # A name that is not in the catalog is a 404 whatever it contains, including a name shaped like
    # an injection attempt.
    _write_queries(app, [REVENUE])
    with running(app, FakeExecutor()) as base:
        r = _ask(base, "revenue_by_region'; DROP TABLE orders; --", {})
    assert r.status_code == 404


# ---- parameters: checked before they are anywhere near a statement --------------------------------


@pytest.mark.parametrize("ptype,good,bad", [
    ("string", "anything", 7),
    ("int", 42, "42"),          # not coerced from text: coercion would let a caller pick the branch
    ("float", 1.5, "1.5"),
    ("bool", True, "true"),
    ("date", "2026-08-20", "20260820"),
])
def test_a_parameter_must_be_the_type_it_was_declared(app: Path, ptype, good, bad):
    _write_queries(app, [{"name": "q", "binding": "ds-dwh", "sql": "SELECT :v",
                          "params": [{"name": "v", "type": ptype}]}])
    fake = FakeExecutor()
    with running(app, fake) as base:
        assert _ask(base, "q", {"v": good}).status_code == 200
        r = _ask(base, "q", {"v": bad})
    assert r.status_code == 400
    assert len(fake.calls) == 1     # only the good one crossed


def test_a_bool_is_not_accepted_where_a_number_was_declared(app: Path):
    # bool IS an int in Python, so an unguarded isinstance check would send True to a store as 1.
    _write_queries(app, [{"name": "q", "binding": "ds-dwh", "sql": "SELECT :n",
                          "params": [{"name": "n", "type": "int"}]}])
    with running(app, FakeExecutor()) as base:
        assert _ask(base, "q", {"n": True}).status_code == 400


def test_a_date_arrives_at_the_executor_as_a_date(app: Path):
    _write_queries(app, [{"name": "q", "binding": "ds-dwh", "sql": "SELECT :d",
                          "params": [{"name": "d", "type": "date"}]}])
    fake = FakeExecutor()
    with running(app, fake) as base:
        assert _ask(base, "q", {"d": "2026-08-20"}).status_code == 200
    assert fake.calls[0][1] == {"d": date(2026, 8, 20)}


def test_a_well_shaped_date_that_is_not_a_real_one_is_rejected(app: Path):
    _write_queries(app, [{"name": "q", "binding": "ds-dwh", "sql": "SELECT :d",
                          "params": [{"name": "d", "type": "date"}]}])
    with running(app, FakeExecutor()) as base:
        assert _ask(base, "q", {"d": "2026-02-31"}).status_code == 400


def test_a_value_outside_a_declared_enum_is_rejected(app: Path):
    _write_queries(app, [REVENUE])
    fake = FakeExecutor()
    with running(app, fake) as base:
        r = _ask(base, "revenue_by_region", {"region": "NOWHERE"})
    assert r.status_code == 400
    assert "EMEA" in r.json()["error"]   # says what it may be, not only that it may not be this
    assert not fake.calls


def test_a_missing_parameter_is_named(app: Path):
    _write_queries(app, [REVENUE])
    with running(app, FakeExecutor()) as base:
        r = _ask(base, "revenue_by_region", {})
    assert r.status_code == 400
    assert "region" in r.json()["error"]


def test_a_body_that_is_not_json_is_rejected(app: Path):
    _write_queries(app, [REVENUE])
    with running(app, FakeExecutor()) as base:
        r = httpx.post(f"{base}/api/queries/revenue_by_region", content=b"{not json",
                       headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_an_oversized_body_is_refused_without_being_read(app: Path):
    _write_queries(app, [REVENUE])
    with running(app, FakeExecutor()) as base:
        r = httpx.post(f"{base}/api/queries/revenue_by_region",
                       content=b"x" * (serve._MAX_BODY + 1),
                       headers={"Content-Type": "application/json"})
    assert r.status_code == 413


# ---- the catalog is checked at startup, not per request ------------------------------------------


def test_a_query_naming_a_binding_this_app_no_longer_has_says_so(app: Path):
    # The case the startup check exists for. Per request this would be whatever the store said about
    # a data source that was never opened, once per viewer, with the reason buried in it.
    _write_queries(app, [{**REVENUE, "binding": "ds-gone"}])
    fake = FakeExecutor()
    with running(app, fake) as base:
        r = _ask(base, "revenue_by_region", {"region": "EMEA"})
    assert r.status_code == 503
    assert "ds-gone" in r.json()["error"]
    assert not fake.calls


def test_a_query_using_a_placeholder_it_never_declared_is_unusable(app: Path):
    _write_queries(app, [{"name": "q", "binding": "ds-dwh",
                          "sql": "SELECT * FROM orders WHERE region = :region AND yr = :yr",
                          "params": [{"name": "region", "type": "string"}]}])
    with running(app, FakeExecutor()) as base:
        r = _ask(base, "q", {"region": "EMEA"})
    assert r.status_code == 503
    assert "yr" in r.json()["error"]


def test_a_query_declaring_a_parameter_its_statement_never_uses_is_unusable(app: Path):
    # The other direction, and it matters as much: a declared-but-unused parameter is a filter the
    # creator believes is being applied and which is not.
    _write_queries(app, [{"name": "q", "binding": "ds-dwh", "sql": "SELECT * FROM orders",
                          "params": [{"name": "region", "type": "string"}]}])
    with running(app, FakeExecutor()) as base:
        r = _ask(base, "q", {"region": "EMEA"})
    assert r.status_code == 503
    assert "region" in r.json()["error"]


def test_a_broken_query_does_not_stop_a_healthy_one(app: Path):
    _write_queries(app, [{**REVENUE, "binding": "ds-gone"},
                         {"name": "totals", "binding": "ds-dwh", "sql": "SELECT 1"}])
    with running(app, FakeExecutor()) as base:
        assert _ask(base, "revenue_by_region", {"region": "EMEA"}).status_code == 503
        assert _ask(base, "totals", {}).status_code == 200


def test_a_query_with_no_statement_is_unusable_rather_than_missing(app: Path):
    # Kept in the catalog on purpose: "no such query" would send someone hunting for a typo in a
    # name that is spelled correctly.
    _write_queries(app, [{"name": "q", "binding": "ds-dwh", "sql": ""}])
    with running(app, FakeExecutor()) as base:
        assert _ask(base, "q", {}).status_code == 503


def test_the_startup_log_names_every_unusable_query(app: Path, capsys):
    _write_queries(app, [{**REVENUE, "binding": "ds-gone"}])
    serve._log_query_catalog(serve.load_queries(app))
    out = capsys.readouterr().out
    assert "0 of 1 usable" in out
    assert "ds-gone" in out


# ---- an app that reads no store is untouched by any of this ---------------------------------------


def test_an_app_with_no_catalog_serves_exactly_as_before(app: Path):
    with running(app) as base:
        assert httpx.get(base + "/").status_code == 200
        assert _ask(base, "anything", {}).status_code == 404


def test_a_catalog_that_is_not_valid_json_does_not_stop_the_app_serving(app: Path):
    # A published app that will not boot because a manifest has a stray comma is worse than one that
    # says it has no queries.
    (app / ".sage" / "queries.json").write_text("{not json")
    with running(app) as base:
        assert httpx.get(base + "/").status_code == 200
        assert _ask(base, "revenue_by_region", {}).status_code == 404


def test_without_an_executor_the_app_says_it_cannot_reach_its_data(app: Path):
    # What #13 ships as. Better than a fake that would answer a viewer's real question with rows
    # nobody measured.
    _write_queries(app, [REVENUE])
    with running(app) as base:
        r = _ask(base, "revenue_by_region", {"region": "EMEA"})
    assert r.status_code == 503
    assert "cannot reach" in r.json()["error"]


def test_an_executor_that_fails_does_not_leak_its_error_to_a_viewer(app: Path):
    def boom(query, params):
        raise RuntimeError("Flight: UNAUTHENTICATED: token expired at 10.4.2.7:5000")

    _write_queries(app, [REVENUE])
    with running(app, boom) as base:
        r = _ask(base, "revenue_by_region", {"region": "EMEA"})
    assert r.status_code == 502
    assert "10.4.2.7" not in r.text and "UNAUTHENTICATED" not in r.text
