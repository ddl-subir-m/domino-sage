"""A repair starts from what Sage reproduced, not from what the model guesses (#735).

Live, Signal Room 21 on `e9bfaea`: an app's route passed the dates at the top level of the body, so
`answer()` refused every parameterised query. Three repair turns rewrote a working frontend; none of
them called the route. And on prompt 8, `curStart.isBetween is not a function` got four repairs of
null guards while the template's AGENTS.md already said which dayjs plugins are loaded.

So a query repair carries the failing request (name and body as sent) and the response (status and
body), captured by Sage on both the in-app and the proxied path, each bounded to `EVIDENCE_LIMIT`
characters. And a runtime repair whose error names a missing method or an undefined symbol quotes the
line of the app's template AGENTS.md that names it, when one does.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sage.orchestrator import brand
from sage.orchestrator.service import EVIDENCE_LIMIT, _rules_naming
from sage.preview.proxy import make_preview_app
from sage.preview.supervisor import UvicornSupervisor

from .fake_opencode import Turn
from .test_a_dead_preview_does_not_take_the_session_with_it import _read
from .test_a_no_build_app_serves_from_static_files import _load, _seed
from .test_a_query_the_app_answers_itself_is_checked import (
    NEEDS,
    AppPreview,
    _ack,
    _write_catalog,
    build,  # noqa: F401  (fixture)
    run,
)

REPO = Path(__file__).resolve().parents[2]
MANGLED = {"start_date": "2026-01-01", "end_date": "2026-02-01"}
REFUSAL = json.dumps({"error": NEEDS})
DAYJS_ROW = next(line.strip() for line in brand.apply_voice(
    (REPO / "template" / "fastapi-antd" / "AGENTS.md").read_text(encoding="utf-8")).splitlines()
    if line.startswith("| `dayjs` |"))


# ---- the template: `answer()` says what it was handed when it refuses ----------------------------

def _answer(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGE_PREVIEW", "1")
    monkeypatch.delenv("DOMINO_API_HOST", raising=False)
    _, ws = _seed(tmp_path)
    _write_catalog(ws.path)
    for stale in [k for k in sys.modules if k in ("sage_queries", "sage_domino")]:
        del sys.modules[stale]
    serve = _load(ws.path, f"fastapi_antd_serve_evidence_{tmp_path.name}")
    monkeypatch.setattr(serve, "_log_boot", lambda state, preview: None)
    state = serve.mount(FastAPI(), executor=lambda query, params: {"columns": ["n"], "rows": [[1]]})
    return lambda body: serve.sq.answer(state.queries, state.executor, "feature_adoption", body)


def _printed(capsys) -> list[dict]:
    return [json.loads(line[len("[sage] query-read "):])
            for line in capsys.readouterr().out.splitlines()
            if line.startswith("[sage] query-read ")]


def test_a_refusal_prints_the_body_answer_was_handed(tmp_path, monkeypatch, capsys):
    answer = _answer(tmp_path, monkeypatch)
    capsys.readouterr()
    answer(MANGLED)
    answer({"params": MANGLED})
    refused, answered = _printed(capsys)
    assert refused == {"name": "feature_adoption", "status": 400, "error": NEEDS,
                       "sent": json.dumps(MANGLED)}
    assert "sent" not in answered, "a successful read carries no request values"


def test_the_body_answer_prints_is_bounded(tmp_path, monkeypatch, capsys):
    answer = _answer(tmp_path, monkeypatch)
    capsys.readouterr()
    answer({"start_date": "x" * (3 * EVIDENCE_LIMIT)})
    (refused,) = _printed(capsys)
    assert len(refused["sent"]) <= EVIDENCE_LIMIT


# ---- the supervisor keeps it, bounded -------------------------------------------------------------

def test_the_supervisor_keeps_what_answer_was_sent(tmp_path):
    sup = UvicornSupervisor(tmp_path, "")
    sup._state, sup._upstream = "ready", "http://127.0.0.1:7"
    generation = sup.status()["generation"]
    lines = ['[sage] query-read ' + json.dumps({"name": "a", "status": 400, "error": NEEDS,
                                                "sent": "y" * (3 * EVIDENCE_LIMIT)}) + "\n",
             '[sage] query-read {"name": "b", "status": 400, "error": "e", "sent": 7}\n']
    with _read(sup, lines, hang=True):
        a, b = sup.query_reads()
        shown = sup.recent_output()
    assert a == {"name": "a", "status": 400, "error": NEEDS, "sent": "y" * EVIDENCE_LIMIT,
                 "generation": generation}
    assert "sent" not in b
    # The request's values go to the repair only, not to the server output pane.
    assert shown[0] == '[sage] query-read ' + json.dumps({"name": "a", "status": 400, "error": NEEDS})
    assert not any("sent" in line for line in shown)


# ---- the proxy hands over the body the page sent ---------------------------------------------------

@pytest.mark.parametrize("query_server", [True, False])
def test_the_proxy_reports_the_body_the_page_sent(monkeypatch, query_server):
    class Refusal(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield REFUSAL.encode()

    transport = httpx.MockTransport(lambda request: httpx.Response(400, stream=Refusal()))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kwargs: real_client(transport=transport, **kwargs))
    queries = SimpleNamespace(port=7, refresh=lambda: None) if query_server else None
    heard = []
    app = make_preview_app(lambda: "http://upstream", get_queries=lambda: queries,
                           get_read_context=lambda *args: {"issued": True},
                           on_platform_read=lambda status, path, **kwargs: heard.append(kwargs))
    with TestClient(app) as client:
        client.post("/api/queries/feature_adoption", json=MANGLED)
    assert [json.loads(kwargs["sent"]) for kwargs in heard] == [MANGLED]


# ---- the repair turn reads the request and the response ---------------------------------------------

def test_an_in_app_refusal_is_repaired_from_its_request_and_response(build):  # noqa: F811
    orch, project, oc = build
    _write_catalog(project.workspace.path)
    refusal = {"name": "feature_adoption", "status": 400, "error": NEEDS,
               "sent": json.dumps(MANGLED)}
    project.supervisor = AppPreview(project.workspace.app_id, lambda n: [refusal] if n == 1 else [
        {"name": "feature_adoption", "status": 200}])
    oc.turns.append(Turn(writes={"app.py": "# fixed\n"}))

    run(orch, report=_ack(orch))

    assert len(oc.prompts) == 2
    nudge = oc.prompts[-1]["text"]
    assert json.dumps(MANGLED) in nudge, "the request answer() was handed is not in the repair"
    assert f"400 {REFUSAL}" in nudge, "the response is not in the repair"


def test_a_proxied_refusal_is_repaired_from_its_request_and_response(build):  # noqa: F811
    orch, project, oc = build
    orch._build_policy = replace(orch._build_policy, runtime_repair_limit=1)
    _write_catalog(project.workspace.path)
    sent = json.dumps(MANGLED).encode()

    def report(event):
        orch.record_preview_ack(event["validationId"])
        context = orch.capture_preview_read(event["validationId"], "/api/queries/feature_adoption",
                                            kind="query")
        orch.record_platform_read_failure(400, "/api/queries/feature_adoption", context=context,
                                          body=REFUSAL.encode(), sent=sent)

    run(orch, report=report)

    assert len(oc.prompts) == 2
    nudge = oc.prompts[-1]["text"]
    assert json.dumps(MANGLED) in nudge, "the request the page sent is not in the repair"
    assert f"400 {REFUSAL}" in nudge, "the response is not in the repair"


def test_proxied_evidence_is_bounded(build):  # noqa: F811
    orch, project, oc = build
    orch._build_policy = replace(orch._build_policy, runtime_repair_limit=1)
    _write_catalog(project.workspace.path)

    def report(event):
        orch.record_preview_ack(event["validationId"])
        context = orch.capture_preview_read(event["validationId"], "/api/queries/feature_adoption",
                                            kind="query")
        orch.record_platform_read_failure(
            400, "/api/queries/feature_adoption", context=context,
            body=json.dumps({"error": NEEDS, "pad": "b" * (3 * EVIDENCE_LIMIT)}).encode(),
            sent=json.dumps({"pad": "s" * (3 * EVIDENCE_LIMIT)}).encode())

    run(orch, report=report)

    nudge = oc.prompts[-1]["text"]
    assert "s" * EVIDENCE_LIMIT not in nudge and "s" * (EVIDENCE_LIMIT - 20) in nudge
    assert "b" * EVIDENCE_LIMIT not in nudge and "b" * (EVIDENCE_LIMIT - 100) in nudge


# ---- a runtime error naming a symbol a template rule covers ----------------------------------------

def test_a_missing_method_finds_the_template_rule_that_names_it():
    fastapi = REPO / "template" / "fastapi-antd"
    assert _rules_naming(fastapi, "curStart.isBetween is not a function") == [DAYJS_ROW]
    assert _rules_naming(fastapi, "TypeError: dayjs(...).isBetween is not a function") == [DAYJS_ROW]
    # The rule is the app's template's: react-vite says nothing about dayjs plugins.
    assert _rules_naming(REPO / "template" / "react-vite",
                         "curStart.isBetween is not a function") == []


@pytest.mark.parametrize("message", [
    "curStart.frobnicate is not a function",
    "Cannot read properties of undefined (reading 'isBetween')",
    "render failed",
    "",
])
def test_an_error_naming_no_ruled_symbol_quotes_nothing(message):
    assert _rules_naming(REPO / "template" / "fastapi-antd", message) == []


def _crash_then_ack(orch, message):
    crashed = []

    def report(event):
        orch.record_preview_ack(event["validationId"])
        if not crashed:
            crashed.append(event["validationId"])
            orch.record_runtime_error(message, "at UsageDrift (static/components/drift.js:12)",
                                      validation_id=event["validationId"])
    return report


@pytest.mark.parametrize("message, quoted", [
    ("curStart.isBetween is not a function", True),
    ("curStart.frobnicate is not a function", False),
])
def test_the_runtime_repair_quotes_the_rule_only_when_one_names_the_symbol(build, message,  # noqa: F811
                                                                          quoted, tmp_path):
    orch, _, oc = build
    (tmp_path / "template" / "AGENTS.md").write_text(
        (REPO / "template" / "fastapi-antd" / "AGENTS.md").read_text(encoding="utf-8"),
        encoding="utf-8")
    oc.turns.append(Turn(writes={"src/App.tsx": "// repaired\n"}))

    run(orch, report=_crash_then_ack(orch, message))

    assert len(oc.prompts) == 2
    nudge = oc.prompts[-1]["text"]
    assert message in nudge
    assert (DAYJS_ROW in nudge) is quoted
    assert ("AGENTS.md" in nudge) is quoted
