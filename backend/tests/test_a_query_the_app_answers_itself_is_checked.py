"""A query the app answers in its own route is checked like one read through the proxy (#730).

Live, Signal Room 21 on `e9bfaea`: a fastapi-antd app read its queries through its OWN routes, which
call `sage_queries.answer()` in-process. The page sent the dates at the top level of the body, and
`answer()` reads values only from `body["params"]`, so every parameterised query refused — and the
route returned that refusal as HTTP 200 `{"error": ...}`. The proxy sees only `/api/queries/<name>`,
so the validation saw no read at all, called data `unverified`, and the turn ended "Page checks
passed". Three repair turns later nobody had read `answer()`'s signature.

So `answer()` itself says, on the preview server's output, what it was asked and what it answered,
whatever the route that called it then returns. And an app with queries whose page asked for none of
them has not shown its data working, which is not a pass.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sage.preview.supervisor import UvicornSupervisor

from . import test_changed_page_validation as page_tests
from .fake_opencode import Turn
from .test_a_dead_preview_does_not_take_the_session_with_it import _read
from .test_a_no_build_app_serves_from_static_files import _load, _seed

build = page_tests.build
run = page_tests.run

NEEDS = "feature_adoption needs end_date, start_date."
_CATALOG = [{"name": "feature_adoption", "binding": "ds-dwh",
             "sql": "SELECT * FROM T WHERE d BETWEEN :start_date AND :end_date",
             "params": [{"name": "start_date", "type": "date"},
                        {"name": "end_date", "type": "date"}]}]
_BINDINGS = [{"kind": "data_source", "id": "ds-dwh", "name": "warehouse",
              "connector_type": "SnowflakeConfig"}]


def _write_catalog(app_dir) -> None:
    sage = app_dir / ".sage"
    sage.mkdir(parents=True, exist_ok=True)
    (sage / "queries.json").write_text(json.dumps(_CATALOG), encoding="utf-8")
    (sage / "bindings.json").write_text(json.dumps(_BINDINGS), encoding="utf-8")


# ---- the template: `answer()` says what it answered, whatever the route returns ------------------

@pytest.mark.parametrize("preview", [True, False])
def test_a_route_returning_a_refusal_as_200_still_reports_it(tmp_path, monkeypatch, capsys, preview):
    monkeypatch.delenv("DOMINO_API_HOST", raising=False)
    if preview:
        monkeypatch.setenv("SAGE_PREVIEW", "1")
    else:
        monkeypatch.delenv("SAGE_PREVIEW", raising=False)
    _, ws = _seed(tmp_path)
    _write_catalog(ws.path)
    for stale in [k for k in sys.modules if k in ("sage_queries", "sage_domino")]:
        del sys.modules[stale]
    serve = _load(ws.path, f"fastapi_antd_serve_answer_{tmp_path.name}")
    monkeypatch.setattr(serve, "_log_boot", lambda state, preview: None)
    app = FastAPI()
    state = serve.mount(app, executor=lambda query, params: {"columns": ["n"], "rows": []})

    @app.post("/api/adoption")
    def adoption(body: dict):
        # The live wrapper: the values at the top level, and the refusal returned as a 200.
        _status, payload = serve.sq.answer(state.queries, state.executor, "feature_adoption", body)
        return payload

    capsys.readouterr()
    with TestClient(app) as client:
        refused = client.post("/api/adoption", json={"start_date": "2026-01-01"})
        answered = client.post("/api/adoption", json={"params": {
            "start_date": "2026-01-01", "end_date": "2026-02-01"}})
    assert refused.status_code == 200 and refused.json() == {"error": NEEDS}
    assert answered.status_code == 200
    lines = [line for line in capsys.readouterr().out.splitlines()
             if line.startswith("[sage] query-read ")]
    if not preview:
        assert lines == []
        return
    reads = [json.loads(line[len("[sage] query-read "):]) for line in lines]
    assert reads == [{"name": "feature_adoption", "status": 400, "error": NEEDS,
                      "sent": json.dumps({"start_date": "2026-01-01"})},
                     {"name": "feature_adoption", "status": 200, "empty": True}]


# ---- the supervisor: the lines become this generation's reads ------------------------------------

def test_the_supervisor_keeps_the_reads_the_app_reported(tmp_path):
    sup = UvicornSupervisor(tmp_path, "")
    sup._state, sup._upstream = "ready", "http://127.0.0.1:7"
    generation = sup.status()["generation"]
    lines = ["INFO:     127.0.0.1 - \"POST /api/adoption HTTP/1.1\" 200 OK\n",
             '[sage] query-read {"name": "feature_adoption", "status": 400, "error": "ERROR: ' + NEEDS + '"}\n',
             "[sage] query-read not json\n",
             '[sage] query-read {"name": "kpis", "status": 200}\n']
    with _read(sup, lines, hang=True):
        assert sup.query_reads() == [
            {"name": "feature_adoption", "status": 400, "error": "ERROR: " + NEEDS,
             "generation": generation},
            {"name": "kpis", "status": 200, "generation": generation}]
        assert sup.runtime_fault() is None, "a reported refusal is not a server fault"
        assert sup._state == "ready"


_ROUTE_APP = '''import sage_queries
import sage_serve
from fastapi import FastAPI

app = FastAPI(title="app", docs_url=None, redoc_url=None)
state = sage_serve.mount(app)


@app.get("/api/adoption")
def adoption():
    status, payload = sage_queries.answer(state.queries, state.executor, "feature_adoption",
                                          {"start_date": "2026-01-01"})
    return payload
'''


def test_a_real_fastapi_antd_route_calling_answer_is_seen_by_the_preview(tmp_path, monkeypatch):
    monkeypatch.delenv("SAGE_PREVIEW_PORT", raising=False)
    _, ws = _seed(tmp_path)
    _write_catalog(ws.path)
    (ws.path / "app.py").write_text(_ROUTE_APP, encoding="utf-8")
    sup = UvicornSupervisor(ws.path)
    try:
        url = sup.start(ready_timeout_s=15)
        res = httpx.get(f"{url}/api/adoption", timeout=5)
        assert res.status_code == 200 and res.json() == {"error": NEEDS}
        deadline = time.monotonic() + 5
        while not sup.query_reads() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert sup.query_reads() == [{"name": "feature_adoption", "status": 400, "error": NEEDS,
                                      "sent": json.dumps({"start_date": "2026-01-01"}),
                                      "generation": sup.status()["generation"]}]
    finally:
        sup.stop()


# ---- the validation: no query read on a loaded page is a failed data check -----------------------

@pytest.mark.parametrize("page, reads, data", [
    ("passed", [], "failed"),
    ("unverified", [], "unverified"),   # a page that never loaded asked for nothing it could
    ("passed", [{"kind": "query", "path": "/api/queries/q", "outcome": "passed"}], "passed"),
])
def test_a_loaded_page_that_read_no_query_fails_the_data_check(page, reads, data):
    from sage.preview.validation import PageValidation

    validation = PageValidation("v", "p", "a", "t", "g", "Syntax check", data_expected=True,
                                data_reads=reads, queries_declared=True)
    validation.stages.update(startup="passed", page=page, runtime="passed")
    summary = validation.summary()
    assert summary["stages"]["data"] == data
    assert summary.get("queriesUnread", False) is (data == "failed")


# ---- the turn ------------------------------------------------------------------------------------

class AppPreview(page_tests.Preview):
    """A preview whose app reports `reads(n)` for its n-th server generation."""

    def __init__(self, app, reads):
        super().__init__(app)
        self.reads = reads

    def query_reads(self):
        return [{**read, "generation": f"preview:{self.generation}"}
                for read in self.reads(self.generation)]


def _ack(orch):
    return lambda event: orch.record_preview_ack(event["validationId"])


def _refused_then(answered: bool):
    refusal = {"name": "feature_adoption", "status": 400, "error": NEEDS}
    return lambda n: [refusal] if n == 1 or not answered else [
        {"name": "feature_adoption", "status": 200}]


def test_a_refusal_inside_the_app_gets_a_repair_naming_the_query_and_params(build):
    orch, project, oc = build
    _write_catalog(project.workspace.path)
    project.supervisor = AppPreview(project.workspace.app_id, _refused_then(answered=True))
    oc.turns.append(Turn(writes={"src/App.tsx": "// sends params\n"}))

    events, done = run(orch, report=_ack(orch))

    assert len(oc.prompts) == 2, "the in-app refusal was not sent back for a repair"
    nudge = oc.prompts[-1]["text"]
    assert "feature_adoption" in nudge and NEEDS in nudge
    assert 'body["params"]' in nudge
    assert any(e["type"] == "iterate" and "feature_adoption" in e["reason"] for e in events)
    assert done["ok"] is True, done
    assert done["verification"]["stages"]["data"] == "passed"


def test_a_refusal_inside_the_app_that_stays_ends_the_turn_failed(build):
    orch, project, oc = build
    orch._build_policy = replace(orch._build_policy, runtime_repair_limit=0)
    _write_catalog(project.workspace.path)
    project.supervisor = AppPreview(project.workspace.app_id, _refused_then(answered=False))

    events, done = run(orch, report=_ack(orch))

    assert len(oc.prompts) == 1
    assert done["ok"] is False
    assert done["decision"] == "queries failed"
    assert done["verification"]["stages"]["data"] == "failed"
    assert done["verification"]["queryFailures"] == {"feature_adoption": NEEDS}
    assert NEEDS in next(e for e in events if e["type"] == "data-source-failed")["message"]


def _proxied(orch, status: int, body: bytes):
    def report(event):
        orch.record_preview_ack(event["validationId"])
        context = orch.capture_preview_read(event["validationId"], "/api/queries/feature_adoption",
                                            kind="query")
        orch.record_platform_read_failure(status, "/api/queries/feature_adoption", context=context,
                                          body=body)
    return report


def test_a_refusal_through_the_query_route_is_repaired_with_its_sentence(build):
    orch, project, oc = build
    orch._build_policy = replace(orch._build_policy, runtime_repair_limit=1)
    _write_catalog(project.workspace.path)

    run(orch, report=_proxied(orch, 400, json.dumps({"error": NEEDS}).encode()))

    assert len(oc.prompts) == 2
    assert NEEDS in oc.prompts[-1]["text"]


def test_a_404_with_no_sentence_is_not_sent_back_as_a_refusal(build):
    # Vite's 404 when the preview has no query server: nobody refused anything, nothing to fix.
    orch, project, oc = build
    _write_catalog(project.workspace.path)

    _, done = run(orch, report=_proxied(orch, 404, b"<html>not found</html>"))

    assert len(oc.prompts) == 1
    assert done["decision"] == "queries failed"


def test_a_read_from_an_earlier_server_generation_is_not_this_documents(build):
    orch, project, _ = build
    orch._build_policy = replace(orch._build_policy, runtime_repair_limit=0)
    _write_catalog(project.workspace.path)
    preview = AppPreview(project.workspace.app_id, lambda n: [])
    preview.query_reads = lambda: [{"name": "feature_adoption", "status": 200,
                                    "generation": "preview:0"}]
    project.supervisor = preview

    _, done = run(orch, report=_ack(orch))

    assert done["verification"]["dataReads"] == []
    assert done["ok"] is False


def test_an_app_with_queries_whose_page_reads_none_does_not_pass(build):
    orch, project, oc = build
    _write_catalog(project.workspace.path)

    events, done = run(orch, report=_ack(orch))

    assert len(oc.prompts) == 1
    assert done["ok"] is False
    assert done["verification"]["stages"]["data"] == "failed"
    assert done["verification"]["overall"] == "failed"
    message = next(e for e in events if e["type"] == "data-source-failed")["message"]
    assert "asked for none of them" in message


def test_an_app_reading_through_the_query_route_still_passes(build):
    orch, project, oc = build
    _write_catalog(project.workspace.path)

    def read(event):
        orch.record_preview_ack(event["validationId"])
        context = orch.capture_preview_read(event["validationId"], "/api/queries/feature_adoption",
                                            kind="query")
        orch.record_platform_read_failure(200, "/api/queries/feature_adoption", context=context,
                                          body=b'{"columns": ["n"], "rows": [[1]]}')

    _, done = run(orch, report=read)

    assert len(oc.prompts) == 1
    assert done["ok"] is True, done
    assert done["verification"]["overall"] == "passed"


def test_an_app_answering_through_its_own_route_passes_when_answered(build):
    orch, project, oc = build
    _write_catalog(project.workspace.path)
    project.supervisor = AppPreview(project.workspace.app_id,
                                    lambda n: [{"name": "feature_adoption", "status": 200}])

    _, done = run(orch, report=_ack(orch))

    assert len(oc.prompts) == 1
    assert done["ok"] is True, done
    assert [(r["path"], r["outcome"]) for r in done["verification"]["dataReads"]] == [
        ("/api/queries/feature_adoption", "passed")]


def test_an_app_without_queries_is_unaffected(build):
    orch, _, oc = build

    _, done = run(orch, report=_ack(orch))

    assert len(oc.prompts) == 1
    assert done["ok"] is True
    assert done["verification"]["overall"] == "passed"
