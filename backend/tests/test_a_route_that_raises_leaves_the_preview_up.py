"""A serving preview that prints an error is still serving (#598).

Measured on a fastapi-antd app: a route raised `TypeError: object of type 'NoneType' has no len()`,
uvicorn printed its `ERROR:    Exception in ASGI application` traceback, and the supervisor read that
as the server dying. The pane grayed out over a server that was answering every other request, and
stayed gray until a `.py` edit or Retry. Vite's transform errors carry esbuild's `ERROR:` and did the
same to the react-vite stack. And the build never heard about the route at all, because the proxy
watches only query and platform reads.
"""
import json
import time
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sage.preview.supervisor import UvicornSupervisor, ViteSupervisor

from .test_a_dead_preview_does_not_take_the_session_with_it import _read
from .test_a_no_build_app_serves_from_static_files import _load, _seed
from .test_changed_page_validation import build, run  # noqa: F401 - `build` is a fixture

_ROUTE_RAISED = [
    "ERROR:    Exception in ASGI application\n",
    "Traceback (most recent call last):\n",
    '  File "app.py", line 274, in governance\n',
    "TypeError: object of type 'NoneType' has no len()\n",
]
_VITE_TRANSFORM = [
    "[vite] Internal server error: Transform failed with 1 error:\n",
    '/app/src/App.tsx:10:2: ERROR: Unexpected "}"\n',
]


@pytest.mark.parametrize("cls, lines, last", [
    (UvicornSupervisor, _ROUTE_RAISED, "TypeError: object of type 'NoneType' has no len()"),
    (ViteSupervisor, _VITE_TRANSFORM, '/app/src/App.tsx:10:2: ERROR: Unexpected "}"'),
])
def test_an_error_printed_while_serving_is_recorded_not_fatal(tmp_path, cls, lines, last):
    sup = cls(tmp_path, "")
    sup._state, sup._upstream = "ready", "http://127.0.0.1:7"
    generation = sup.status()["generation"]
    with _read(sup, lines, hang=True):
        assert sup._state == "ready"  # status() reads an app-less tmp dir as "empty" first
        assert sup.upstream() == "http://127.0.0.1:7"
        fault = sup.runtime_fault()
        assert fault["message"] == last
        assert fault["generation"] == generation
        assert lines[0].strip() in fault["output"]


def test_an_error_printed_while_starting_still_fails_the_start(tmp_path):
    sup = UvicornSupervisor(tmp_path, "")
    sup._stopped = True
    with _read(sup, ['ERROR:    Error loading ASGI app. Could not import module "app".\n'], hang=True):
        assert sup._state == "failed"  # status() reads an app-less tmp dir as "empty" first
        assert "Could not import" in sup.last_error()
        assert sup.runtime_fault() is None


_RAISING_APP = '''from fastapi import FastAPI
from fastapi.responses import HTMLResponse
app = FastAPI()
@app.get("/")
def page():
    return HTMLResponse("<html><body>healthy fixture</body></html>")
@app.get("/api/governance")
def governance():
    return {"n": len(None)}
'''


def test_a_real_route_that_raises_leaves_uvicorn_serving(tmp_path, monkeypatch):
    monkeypatch.delenv("SAGE_PREVIEW_PORT", raising=False)
    (tmp_path / ".sage").mkdir()
    (tmp_path / ".sage/settings.json").write_text(json.dumps({"stack": "fastapi-antd"}))
    (tmp_path / "static").mkdir()
    (tmp_path / "static/app.js").write_text("// fixture")
    (tmp_path / "app.py").write_text(_RAISING_APP)
    sup = UvicornSupervisor(tmp_path)
    try:
        url = sup.start(ready_timeout_s=12)
        assert httpx.get(f"{url}/api/governance", timeout=2).status_code == 500
        deadline = time.monotonic() + 5
        while sup.runtime_fault() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "has no len()" in sup.runtime_fault()["message"]
        assert sup.status()["state"] == "ready"
        assert httpx.get(sup.upstream(), timeout=2).status_code == 200
    finally:
        sup.stop()


def _with_fault(project, generation):
    project.supervisor.runtime_fault = lambda: {
        "message": "TypeError: object of type 'NoneType' has no len()",
        "output": ["ERROR:    Exception in ASGI application", "TypeError: ..."],
        "generation": generation}


def test_a_route_that_raised_during_validation_sends_its_traceback_back(build):  # noqa: F811
    orch, project, oc = build
    orch._build_policy = replace(orch._build_policy, runtime_repair_limit=1)

    def raised(event):
        orch.record_preview_ack(event["validationId"])
        if len(oc.prompts) == 1:
            _with_fault(project, event["generation"])
        else:
            project.supervisor.runtime_fault = lambda: None

    from .fake_opencode import Turn
    oc.turns.append(Turn(writes={"src/App.tsx": "// repaired\n"}))
    events, done = run(orch, report=raised)
    assert any(e["type"] == "iterate" and "has no len()" in e["reason"] for e in events)
    assert "server routes raised" in oc.prompts[1]["text"]
    assert "Exception in ASGI application" in oc.prompts[1]["text"]
    assert done["verification"]["overall"] == "passed"


def test_a_fault_from_an_earlier_server_generation_is_not_this_documents(build):  # noqa: F811
    orch, project, _ = build
    _with_fault(project, "preview:0")
    _, done = run(orch, report=lambda event: orch.record_preview_ack(event["validationId"]))
    assert done["verification"]["overall"] == "passed"


@pytest.mark.parametrize("preview", [True, False])
def test_a_route_that_raises_answers_json_and_still_logs(tmp_path, monkeypatch, preview):
    import sys

    monkeypatch.delenv("DOMINO_API_HOST", raising=False)
    if preview:
        monkeypatch.setenv("SAGE_PREVIEW", "1")
    else:
        monkeypatch.delenv("SAGE_PREVIEW", raising=False)
    _, ws = _seed(tmp_path)
    for stale in [k for k in sys.modules if k in ("sage_queries", "sage_domino")]:
        del sys.modules[stale]
    serve = _load(ws.path, f"fastapi_antd_serve_raise_{tmp_path.name}")
    # The boot log's sidecar and platform probes run on threads that outlive the test.
    monkeypatch.setattr(serve, "_log_boot", lambda state, preview: None)
    app = FastAPI()
    serve.mount(app)

    @app.get("/api/governance")
    def governance():
        return {"n": len(None)}

    with TestClient(app, raise_server_exceptions=False) as client:
        res = client.get("/api/governance")
    assert res.status_code == 500
    body = res.json()
    assert body["path"] == "/api/governance"
    assert ("has no len()" in body["error"]) is preview
    # Starlette re-raises after the handler answers; that re-raise is uvicorn's logged traceback.
    with TestClient(app) as client, pytest.raises(TypeError):
        client.get("/api/governance")
