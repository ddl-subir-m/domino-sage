"""A named query answers while the app is still being built (#24).

The thing worth pinning is not that a query can answer — `test_builtapp.py` already covers
`serve.py`'s route — but the four properties that make answering it in the preview *useful*, each of
which is easy to lose in a refactor and none of which shows up as a failing request:

  - it is `serve.py` answering, not a second implementation, so a refusal reads identically in the
    preview and in the published app (criteria 3 and 4);
  - a rewritten catalog is picked up without restarting anything, because the loop this ticket
    exists to close is agent-writes-query, creator-tries-it, agent-fixes-it;
  - the cache collapses an HMR reload storm without ever changing what a query returns;
  - nothing here can stop a build session opening, which is the one way this feature could cost more
    than it is worth.

No network. The executor is `serve.py`'s own injectable seam, so the whole path is exercised with a
fake behind it — which is the same reason #13 put the seam there.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from sage.preview.proxy import make_preview_app
from sage.preview.queries import CachingExecutor, PreviewQueries

TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"

SOURCE_ID = "ds-dwh"
SOUND = {"name": "usage", "binding": SOURCE_ID,
         "sql": "SELECT ACCOUNT_ID FROM FCT_USAGE_DAILY WHERE USAGE_DATE >= :since",
         "params": [{"name": "since", "type": "date"}]}
# The placeholder the statement uses and the declaration never mentions — the mistake an agent
# actually makes, and the one whose sentence must match the published app's word for word.
BROKEN = {"name": "revenue", "binding": SOURCE_ID,
          "sql": "SELECT REGION FROM FCT_REVENUE WHERE MONTH >= :since", "params": []}
BINDINGS = [{"kind": "data_source", "id": SOURCE_ID, "name": "DWH", "display_name": "DWH",
             "database": "DWH", "schema": "MARTS", "connector_type": "SnowflakeConfig"}]


def _workspace(tmp: Path, queries: list, bindings: list = BINDINGS) -> Path:
    root = tmp / "ws"
    (root / ".sage").mkdir(parents=True, exist_ok=True)
    (root / ".sage" / "queries.json").write_text(json.dumps(queries))
    (root / ".sage" / "bindings.json").write_text(json.dumps(bindings))
    return root


class _Recorder:
    """Stands in for the Flight executor, and counts how often it was actually asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, query, params) -> dict:
        self.calls.append((query.name, dict(params)))
        return {"columns": ["ACCOUNT_ID"], "rows": [["a-1"]], "truncated": False}


def _started(root: Path, executor=None) -> PreviewQueries:
    """A running PreviewQueries with a fake behind it, torn down by the caller."""
    pq = PreviewQueries(root, TEMPLATE, ttl_s=30.0)
    pq.start()
    assert pq.port is not None, "serve.py did not come up"
    if executor is not None:
        pq._server.sage_executor = executor
    return pq


def _ask(pq: PreviewQueries, name: str, params: dict | None = None) -> httpx.Response:
    return httpx.post(f"http://127.0.0.1:{pq.port}/api/queries/{name}",
                      json={"params": params or {}}, timeout=10.0)


# ---- it answers, and it is serve.py answering ------------------------------------------------------


def test_a_named_query_answers_before_the_app_is_published(tmp_path: Path):
    pq = _started(_workspace(tmp_path, [SOUND]), _Recorder())
    try:
        r = _ask(pq, "usage", {"since": "2026-01-01"})
    finally:
        pq.stop()

    assert r.status_code == 200
    assert r.json()["rows"] == [["a-1"]]


def test_a_broken_query_refuses_in_the_words_the_published_app_would_use(tmp_path: Path):
    # Criterion 3, and the reason `serve.py` runs here rather than something like it: the catalog
    # check that refuses this is the published app's, so the sentence cannot drift from it.
    pq = _started(_workspace(tmp_path, [BROKEN]), _Recorder())
    try:
        r = _ask(pq, "revenue")
    finally:
        pq.stop()

    assert r.status_code == 503
    assert "since" in r.json()["error"]


def test_an_unknown_name_is_refused_rather_than_guessed(tmp_path: Path):
    pq = _started(_workspace(tmp_path, [SOUND]), _Recorder())
    try:
        r = _ask(pq, "nope")
    finally:
        pq.stop()

    assert r.status_code == 404


# ---- the loop this exists to close -----------------------------------------------------------------


def test_a_rewritten_catalog_is_picked_up_without_a_restart(tmp_path: Path):
    # The whole point. The agent fixes the query mid-session; the creator's next click must run the
    # fixed one, not the one that was on disk when the session opened.
    root = _workspace(tmp_path, [BROKEN])
    pq = _started(root, _Recorder())
    try:
        assert _ask(pq, "revenue").status_code == 503

        (root / ".sage" / "queries.json").write_text(json.dumps([dict(BROKEN, params=[
            {"name": "since", "type": "date"}])]))
        pq.refresh()

        assert _ask(pq, "revenue", {"since": "2026-01-01"}).status_code == 200
    finally:
        pq.stop()


# ---- the cache, which must save calls without changing answers -------------------------------------


def test_a_reload_storm_asks_the_store_once(tmp_path: Path):
    recorder = _Recorder()
    pq = _started(_workspace(tmp_path, [SOUND]), CachingExecutor(recorder, ttl_s=30.0))
    try:
        answers = [_ask(pq, "usage", {"since": "2026-01-01"}).json() for _ in range(5)]
    finally:
        pq.stop()

    assert len(recorder.calls) == 1, "the cache let more than one call reach the store"
    assert all(a == answers[0] for a in answers), "cached answers differ from the first one"


def test_different_parameters_are_different_questions(tmp_path: Path):
    recorder = _Recorder()
    pq = _started(_workspace(tmp_path, [SOUND]), CachingExecutor(recorder, ttl_s=30.0))
    try:
        _ask(pq, "usage", {"since": "2026-01-01"})
        _ask(pq, "usage", {"since": "2026-02-01"})
    finally:
        pq.stop()

    assert len(recorder.calls) == 2


def test_a_rewritten_statement_is_not_served_from_the_old_one(tmp_path: Path):
    # Why the SQL is in the cache key rather than only the name: an agent that fixes a statement and
    # keeps the name is the common case, and serving the previous rows for it would look exactly
    # like the fix not working.
    recorder = _Recorder()
    cache = CachingExecutor(recorder, ttl_s=30.0)

    class Q:
        name, sql = "usage", "SELECT 1"

    class Q2:
        name, sql = "usage", "SELECT 2"

    cache(Q(), {"since": "x"})
    cache(Q2(), {"since": "x"})

    assert len(recorder.calls) == 2


def test_an_error_is_never_cached(tmp_path: Path):
    # A creator who hits a failure, fixes it, and retries must not be shown the failure again.
    class Failing:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, query, params):
            self.calls += 1
            raise RuntimeError("store said no")

    inner = Failing()
    cache = CachingExecutor(inner, ttl_s=30.0)

    class Q:
        name, sql = "usage", "SELECT 1"

    for _ in range(3):
        with pytest.raises(RuntimeError):
            cache(Q(), {})
    assert inner.calls == 3


# ---- and it can never cost a build session ---------------------------------------------------------


def test_a_template_with_no_serve_py_does_not_raise(tmp_path: Path):
    # `start` is called while a project is being attached. Anything it raised would be a build
    # session that will not open, which is a far worse outcome than a preview that cannot query.
    pq = PreviewQueries(_workspace(tmp_path, [SOUND]), tmp_path / "not-a-template")
    pq.start()

    assert pq.port is None
    pq.stop()      # also has to be safe when nothing ever started


def test_a_catalog_that_will_not_parse_empties_it_as_the_published_app_would(tmp_path: Path):
    """A broken catalog leaves the app with no queries — here exactly as after a publish.

    Written the other way round first, asserting the last good catalog kept serving. That is the
    friendlier behaviour and it is the wrong one: `AGENTS.md` tells the agent in as many words that
    "a catalog that will not parse leaves the app with no queries at all", and a preview that quietly
    went on answering from the previous version would hide a real defect until the creator published.
    Criterion 4 is that the two do not drift, including when the news is bad.
    """
    root = _workspace(tmp_path, [SOUND])
    pq = _started(root, _Recorder())
    try:
        (root / ".sage" / "queries.json").write_text("{ not json")
        pq.refresh()

        assert _ask(pq, "usage", {"since": "2026-01-01"}).status_code == 404
    finally:
        pq.stop()


# ---- through the proxy, which is what the browser actually reaches ----------------------------------


def test_the_proxy_sends_queries_to_serve_py_and_everything_else_to_vite(tmp_path: Path):
    # The interception must be exact. A proxy that swallowed more than `api/queries/` would take the
    # app's own routes with it, and the failure would look like a broken preview rather than a
    # broken rule.
    from fastapi.testclient import TestClient

    def upstream() -> str:
        raise RuntimeError("vite is not running in this test")

    pq = _started(_workspace(tmp_path, [SOUND]), _Recorder())
    try:
        app = make_preview_app(upstream, "", lambda: pq)
        client = TestClient(app)

        answered = client.post("/api/queries/usage", json={"params": {"since": "2026-01-01"}})
        passed_through = client.get("/src/App.tsx")
    finally:
        pq.stop()

    assert answered.status_code == 200 and answered.json()["rows"] == [["a-1"]]
    # Vite is not up, so anything NOT intercepted reaches the "still starting" 502 — which is proof
    # it was forwarded rather than answered here.
    assert passed_through.status_code == 502
    assert "vite" in passed_through.json()["preview"].lower()


def test_the_proxy_falls_through_when_there_is_no_query_server(tmp_path: Path):
    # Every build session that predates a Data Source, and every one where `start` failed. The
    # preview has to behave exactly as it did before #24 rather than fail in some new way.
    from fastapi.testclient import TestClient

    def upstream() -> str:
        raise RuntimeError("vite is not running in this test")

    client = TestClient(make_preview_app(upstream, "", lambda: None))
    r = client.post("/api/queries/usage", json={"params": {}})

    assert r.status_code == 502
    assert "vite" in r.json()["preview"].lower()


# ---- why a query failed has to reach somebody -------------------------------------------------


def test_a_failing_query_says_why_in_a_log_the_creator_can_open(caplog):
    """The preview's only readable log is /api/diag/log, and it reads `logging`, not stdout.

    `serve.py` prints its reason, which is right for a published App — that IS its log, and the page
    it renders tells the viewer to go and read it. In the preview there is no App and no log a
    creator can open, so a Data Source that stopped answering produced a page pointing at a log that
    does not exist and a reason that reached nobody. Live on 2026-08-24: a BigQuery source stopped
    answering and neither the creator nor Sage could see the cause.
    """
    class Boom:
        def __call__(self, query, params):
            raise RuntimeError("sanitised sentence for the viewer") from OSError("Flight: UNAUTHENTICATED")

    executor = CachingExecutor(Boom(), 30.0, lambda exc: f"{type(exc).__name__}: {exc}")

    with caplog.at_level("WARNING"), pytest.raises(RuntimeError):
        executor(_Query("usage"), {})

    logged = " ".join(r.message for r in caplog.records)
    assert "usage" in logged
    # The CAUSE, not the sanitised sentence: the viewer's message is written to be uninformative on
    # purpose, and it is the cause that names the credential or the table.
    assert "UNAUTHENTICATED" in logged


def test_a_failure_is_not_cached(caplog):
    # Only successes are kept (see the class docstring). A failure replayed from cache after the
    # creator fixed it would be its own bug — and would also swallow the log line above.
    calls = []

    class Boom:
        def __call__(self, query, params):
            calls.append(1)
            raise RuntimeError("nope")

    executor = CachingExecutor(Boom(), 30.0)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            executor(_Query("usage"), {})

    assert len(calls) == 2


def test_a_redactor_is_used_when_one_is_available():
    # serve.py's `_readable` exists because the SDK client prints its api_key in __repr__. The
    # preview must not be the one place that rule is missing.
    seen = []
    executor = CachingExecutor(_Raiser(), 30.0, lambda exc: seen.append(exc) or "[redacted]")
    with pytest.raises(RuntimeError):
        executor(_Query("usage"), {})
    assert seen, "the redactor was bypassed"


class _Query:
    def __init__(self, name: str) -> None:
        self.name = name
        self.sql = "SELECT 1"


class _Raiser:
    def __call__(self, query, params):
        raise RuntimeError("boom")
