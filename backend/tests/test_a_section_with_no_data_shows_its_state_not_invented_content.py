"""A section whose data is missing shows its empty or error state, never invented content (#743).

Live, Signal Room 24 on `e10172cb`: a deal page's news card showed two invented headlines linking to
`https://example.com/news`, its approvals were a threshold rule written into `app.py`, and the
build said "Done — build is clean". Two causes, one in each place:

- The prompt's only rule against stand-in data sat in the platform-API section, about the platform
  API. A section fed by anything else, here an app route, had no rule at all.
- The data check never saw the app's own routes. The page tagged, and the proxy observed, only
  `/api/queries/*` and `/api/domino/*` reads, so a section fed by `app.py` could fail or serve
  invented records while the check passed on a query that answered.

And on the same run, a Usage drift tab opened on its own refusal: its default periods overlapped, so
it never queried. A screen's default selection is part of the rule set too.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.preview.read_outcomes import read_request, read_result

from . import test_changed_page_validation as page_tests

build = page_tests.build
run = page_tests.run

REPO = Path(__file__).resolve().parents[2]
STACKS = ["fastapi-antd", "react-vite"]
HARNESS = Path(__file__).parent / "js" / "preview_read_tagging_harness.mjs"


def _implement_rules(stack: str) -> str:
    """The section every implement turn carries. The platform and design sections are optional, so a
    rule that lives only there is withheld from the turns that do not need them."""
    text = (REPO / "template" / stack / "AGENTS.md").read_text()
    return text[text.index("<!-- sage:build-profile:v1:implement:begin -->"):
                text.index("<!-- sage:build-profile:v1:implement:end -->")]


# ---- the prompt ----------------------------------------------------------------------------------

@pytest.mark.parametrize("stack", STACKS)
def test_every_section_whatever_its_source_shows_its_state_rather_than_invented_content(stack):
    rules = _implement_rules(stack)
    start = rules.index("**A section whose data is missing shows its empty or error state")
    rule = rules[start:rules.index("\n- **", start)]
    # Keyed on the class: every source a section can be fed by, not the one that bit.
    for source in ("query", "platform", "route", "model", "MCP server", "outside API", "file"):
        assert source in rule, f"the rule does not cover a section fed by a {source}"
    for invented in ("sample", "mock", "placeholder", "example.com", "hard-coded"):
        assert invented in rule, f"the rule does not name {invented!r} content as invented"


@pytest.mark.parametrize("stack", STACKS)
def test_a_screen_opens_on_a_default_selection_its_own_checks_accept(stack):
    rules = _implement_rules(stack)
    start = rules.index("**A screen opens on a valid default selection.**")
    rule = rules[start:rules.index("\n- **", start)]
    assert "its own validation" in rule
    assert "first load" in rule


# ---- the read the check could not see ------------------------------------------------------------

def test_an_app_route_read_is_kept_by_its_route_and_never_by_its_values():
    assert read_request("/api/news", kind="route")["path"] == "/api/news"
    # A path segment after the route can be a record's own name: never kept.
    assert read_request("/api/deal-detail/Johnson-Johnson", kind="route")["path"] == "/api/deal-detail"
    assert read_request("/static/app.js", kind="route")["path"] == "/unrecognized"


@pytest.mark.parametrize("body", [
    b'{"items": [{"url": "https://example.com/news"}]}',
    b'[{"link": "http://feeds.example.org/a"}]',
    b'{"source": "https:\\/\\/www.example.net\\/x"}',
    b'{"href": "https://news.example/1"}',
    b'{"href": "https://approvals.test/chain"}',
])
def test_a_route_answering_with_a_reserved_host_is_a_failed_read(body):
    result = read_result(read_request("/api/news", kind="route"), 200, body)
    assert result["outcome"] == "failed"
    assert result["reason"] == "placeholder_host"


@pytest.mark.parametrize("body", [
    b'{"items": [{"url": "https://www.reuters.com/markets/x"}]}',
    b'{"contact": "jane@example.com"}',            # an address is not a link to a page
    b'{"note": "see example.com for the format"}',  # nor is the word, without a scheme
    b'{"url": "https://examples.com/x"}',
])
def test_a_route_answering_with_real_hosts_passes(body):
    assert read_result(read_request("/api/news", kind="route"), 200, body)["outcome"] == "passed"


def test_a_query_or_platform_body_is_not_judged_for_hosts():
    """Their bodies are the store's and the platform's own answer: the model cannot write into them,
    and a warehouse's test rows can carry any URL."""
    body = b'{"rows": [["https://example.com"]]}'
    assert read_result(read_request("/api/queries/q", kind="query"), 200, body)["outcome"] == "passed"


def test_the_diagnostic_record_keeps_a_route_read_and_why_it_failed():
    from sage.build_diagnostics import _verification

    read = {"kind": "route", "path": "/api/news", "resourceIds": [], "status": 200,
            "outcome": "failed", "reason": "placeholder_host"}
    kept = _verification({"overall": "failed", "stages": {"data": "failed"}, "dataReads": [read]})
    assert kept["dataReads"] == [read]


def test_the_proxy_observes_a_read_of_the_apps_own_route(monkeypatch):
    import httpx
    from fastapi.testclient import TestClient

    from sage.preview.proxy import make_preview_app

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"items": []}'

    asked, heard = [], []
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Body()))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
    app = make_preview_app(
        lambda: "http://upstream",
        get_read_context=lambda vid, path, query, kind: asked.append((vid, path, kind)) or {"ok": 1},
        on_platform_read=lambda status, path, **kw: heard.append((status, path)))
    with TestClient(app) as client:
        client.get("/api/news", headers={"X-Sage-Validation": "v1"})
        client.get("/static/app.js", headers={"X-Sage-Validation": "v1"})
        client.post("/api/llm/chat/completions", headers={"X-Sage-Validation": "v1"})
    assert asked == [("v1", "/api/news", "route")]
    assert heard == [(200, "/api/news")]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_page_tags_its_own_route_reads_for_the_check():
    urls = ["api/news", "api/deal-detail?id=1", "api/queries/q", "api/domino/sage/datasets",
            "api/llm/chat/completions", "static/app.css", "https://elsewhere.test/api/x"]
    out = subprocess.run(["node", str(HARNESS)], input=json.dumps({"urls": urls}),
                         capture_output=True, text=True, timeout=30, check=True)
    tagged = json.loads(out.stdout)
    assert tagged == ["api/news", "api/deal-detail?id=1", "api/queries/q", "api/domino/sage/datasets"]


# ---- the turn ------------------------------------------------------------------------------------

def _route_read(orch, status: int, body: bytes, path: str = "/api/news"):
    def report(event):
        orch.record_preview_ack(event["validationId"])
        context = orch.capture_preview_read(event["validationId"], path, kind="route")
        orch.record_platform_read_failure(status, path, context=context, body=body)
    return report


def test_a_route_serving_a_reserved_host_does_not_end_clean(build):
    orch, _project, oc = build
    events, done = run(orch, report=_route_read(
        orch, 200, b'{"items": [{"title": "Q4 expansion", "url": "https://example.com/news"}]}'))

    assert done["ok"] is False, done
    assert done["verification"]["stages"]["data"] == "failed"
    assert done["verification"]["dataReads"][0] == {
        "kind": "route", "path": "/api/news", "resourceIds": [], "status": 200,
        "outcome": "failed", "reason": "placeholder_host"}
    message = next(e for e in events if e["type"] == "data-source-failed")["message"]
    assert "/api/news" in message and "reserved" in message
    assert "Q4 expansion" not in json.dumps(done)
    assert len(oc.prompts) == 1


def test_a_route_that_failed_does_not_end_clean(build):
    orch, _project, _oc = build
    events, done = run(orch, report=_route_read(orch, 502, b'{"error": "news did not answer"}'))

    assert done["ok"] is False, done
    assert done["verification"]["stages"]["data"] == "failed"
    assert "/api/news" in next(e for e in events if e["type"] == "data-source-failed")["message"]


def test_a_caught_route_fetch_rejection_is_a_failed_route_read(build):
    orch, _project, _oc = build

    def report(event):
        orch.record_preview_ack(event["validationId"])
        orch.record_preview_data_error(event["validationId"], "/api/news")

    _, done = run(orch, report=report)
    assert done["ok"] is False
    assert [(r["kind"], r["reason"]) for r in done["verification"]["dataReads"]] == [
        ("route", "transport_error")]


def test_a_phased_build_does_not_retry_a_phase_over_a_failed_route(tmp_path, monkeypatch):
    """A phase retry is another model call over the same missing source, and the easiest way for it
    to pass is to stand something in. Reported, like a failed query, not retried."""
    from dataclasses import replace

    from .fake_opencode import Turn
    from .test_phased_build import _plan_then_phases

    orch, oc, project, _ = _plan_then_phases(tmp_path)
    oc.turns.extend(Turn(writes={"src/Filter.tsx": f"// attempt {i}\n"}) for i in range(3))
    orch._build_policy = replace(orch._build_policy, page_ack_wait_seconds=0.2,
                                 runtime_error_wait_seconds=0)
    monkeypatch.setattr(orch, "_restart_preview_for_config_change", lambda project: None)
    project.supervisor = page_tests.Preview(project.workspace.app_id)
    events = []
    for event in orch.approve_stream():
        events.append(event)
        if event["type"] == "preview-validation":
            _route_read(orch, 502, b'{"error": "news did not answer"}')(event)
    done = next(event for event in events if event["type"] == "done")
    assert len(oc.prompts) == 4, "a phase was retried over a route that did not answer"
    assert done["ok"] is False
    assert done["decision"].endswith("app route failed"), done["decision"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_a_failed_route_does_not_buy_a_listing_of_models():
    """The app's own server did not answer; the gateway had nothing to do with it (ADR-0027)."""
    from .test_a_shape_only_table_artifact_renders_as_a_receipt import _node

    failed = _node("build_stream_harness.mjs", {"history": [], "events": [
        {"type": "data-source-failed", "message": "This app's own server did not answer."},
        {"type": "done", "ok": False, "decision": "app route failed"},
    ]})
    assert failed["healthCalls"] == 0


def test_a_route_that_answered_real_records_ends_clean(build):
    orch, _project, _oc = build
    _, done = run(orch, report=_route_read(
        orch, 200, b'{"items": [{"url": "https://www.reuters.com/markets/x"}]}'))

    assert done["ok"] is True, done
    assert done["verification"]["stages"]["data"] == "passed"
