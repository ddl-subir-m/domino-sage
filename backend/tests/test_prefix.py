"""Phase 1: Domino proxy-prefix handling — derivation, request stripping, and preview forwarding."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from sage.orchestrator.app import _PrefixMiddleware
from sage.preview.prefix import domino_base_prefix, domino_project_label
from sage.preview.proxy import make_preview_app


def _app_with_prefix(prefix: str) -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping(request: Request) -> dict:
        return {"path": request.scope["path"], "root_path": request.scope.get("root_path", "")}

    app.add_middleware(_PrefixMiddleware, prefix=prefix)
    return app


def test_prefixed_request_matches_bare_route_via_root_path():
    # The middleware records root_path (does NOT rewrite path); Starlette routes on path-root_path.
    client = TestClient(_app_with_prefix("/owner/proj/notebookSession/run"))
    r = client.get("/owner/proj/notebookSession/run/ping")
    assert r.status_code == 200
    assert r.json()["root_path"] == "/owner/proj/notebookSession/run"


def test_empty_prefix_is_noop():
    client = TestClient(_app_with_prefix(""))
    r = client.get("/ping")
    assert r.status_code == 200
    assert r.json() == {"path": "/ping", "root_path": ""}


def test_bare_root_path_matches_index():
    # Domino serves the builder page at "<prefix>/" — it must still match the "/" route.
    app = FastAPI()

    @app.get("/")
    def index() -> dict:
        return {"ok": True}

    app.add_middleware(_PrefixMiddleware, prefix="/p/q")
    r = TestClient(app).get("/p/q/")
    assert r.status_code == 200


def test_domino_base_prefix_from_env(monkeypatch):
    monkeypatch.setenv("DOMINO_PROJECT_OWNER", "sub_user")
    monkeypatch.setenv("DOMINO_PROJECT_NAME", "Sage")
    monkeypatch.setenv("DOMINO_RUN_ID", "abc123")
    monkeypatch.delenv("SAGE_BASE_PREFIX", raising=False)
    assert domino_base_prefix() == "/sub_user/Sage/notebookSession/abc123"


def test_domino_base_prefix_empty_locally(monkeypatch):
    for k in ("DOMINO_PROJECT_OWNER", "DOMINO_PROJECT_NAME", "DOMINO_RUN_ID", "SAGE_BASE_PREFIX"):
        monkeypatch.delenv(k, raising=False)
    assert domino_base_prefix() == ""


def test_domino_project_label_includes_owner(monkeypatch):
    # The gateway's admin usage view shows every user's traffic, so two people whose project is
    # called "Sage" must not collapse into one row and report one build's cost as two.
    monkeypatch.setenv("DOMINO_PROJECT_OWNER", "sub_user")
    monkeypatch.setenv("DOMINO_PROJECT_NAME", "Sage")
    assert domino_project_label(fallback="app") == "sub_user/Sage"


def test_domino_project_label_falls_back_readably(monkeypatch):
    # Every step of the chain has to stay recognisable in a Group By dropdown — an id hash there is
    # useless, which is the whole reason this isn't DOMINO_PROJECT_ID.
    monkeypatch.delenv("DOMINO_PROJECT_OWNER", raising=False)
    monkeypatch.setenv("DOMINO_PROJECT_NAME", "Sage")
    assert domino_project_label(fallback="app") == "Sage"

    monkeypatch.delenv("DOMINO_PROJECT_NAME", raising=False)
    monkeypatch.setenv("DOMINO_PROJECT_ID", "6620f1a9c3e14b0001d2f8aa")
    assert domino_project_label(fallback="app") == "app"


@pytest.mark.parametrize(
    "prefix,path,expected",
    [
        ("", "assets/x.js", "http://vite:5173/preview/assets/x.js"),
        ("/o/p/notebookSession/r", "assets/x.js", "http://vite:5173/o/p/notebookSession/r/preview/assets/x.js"),
    ],
)
def test_preview_forwards_to_vite_base(prefix, path, expected, monkeypatch):
    # The preview proxy must forward to the SAME path Vite bakes as `base` (<prefix>/preview/),
    # else Vite (which only serves at its base) 404s. Capture the URL httpx is asked to build.
    captured = {}

    class _StubClient:
        def __init__(self, *a, **k):
            pass

        def build_request(self, method, url, **k):
            captured["url"] = url
            raise _Stop

        async def aclose(self):
            pass

    class _Stop(Exception):
        pass

    monkeypatch.setattr("sage.preview.proxy.httpx.AsyncClient", _StubClient)
    app = make_preview_app(lambda: "http://vite:5173", prefix)
    client = TestClient(app, raise_server_exceptions=False)
    client.get(f"/{path}")  # sub-app in isolation sees the route-relative path
    assert captured["url"] == expected


def test_preview_returns_502_not_500_while_vite_restarting():
    # While opencode (re)starts Vite, get_upstream() raises "not ready". The proxy must degrade to a
    # transient 502 ("still starting, refresh"), never an uncaught 500 in the preview pane.
    def not_ready() -> str:
        raise RuntimeError("Vite not ready")

    app = make_preview_app(not_ready, "")
    r = TestClient(app, raise_server_exceptions=False).get("/src/main.tsx")
    assert r.status_code == 502
    assert r.json()["preview"] == "upstream Vite dev server not ready"


def test_preview_through_mount_and_middleware_no_double_prefix(monkeypatch):
    # Reproduces production exactly: preview mounted at /preview behind the prefix middleware. A
    # regression that rewrites path instead of root_path double-counts the prefix (…/preview/preview/…).
    captured = {}

    class _Stop(Exception):
        pass

    class _StubClient:
        def __init__(self, *a, **k):
            pass

        def build_request(self, method, url, **k):
            captured["url"] = url
            raise _Stop

        async def aclose(self):
            pass

    monkeypatch.setattr("sage.preview.proxy.httpx.AsyncClient", _StubClient)
    prefix = "/o/p/notebookSession/r"
    app = FastAPI()
    app.mount("/preview", make_preview_app(lambda: "http://vite:5173", prefix))
    app.add_middleware(_PrefixMiddleware, prefix=prefix)
    client = TestClient(app, raise_server_exceptions=False)
    client.get(f"{prefix}/preview/src/main.tsx")
    assert captured["url"] == "http://vite:5173/o/p/notebookSession/r/preview/src/main.tsx"


def _app_with_internal_routes(prefix: str) -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict:
        return {"ok": True}

    @app.post("/v1/chat/completions")
    def shim() -> dict:
        return {"ok": True}

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    app.add_middleware(_PrefixMiddleware, prefix=prefix)
    return app


def test_internal_localhost_routes_do_not_spend_the_one_shot_prefix_warning(caplog):
    """The warning fires once per process. OpenCode's model calls hit /v1 over localhost and never
    cross Domino's proxy, so they legitimately carry no prefix — if they consumed the warning, a
    real misconfiguration on browser traffic would then be silent forever."""
    import logging

    client = TestClient(_app_with_internal_routes("/owner/proj/notebookSession/run"))
    with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
        client.post("/v1/chat/completions")          # internal: expected to have no prefix
        client.get("/healthz")                       # internal
        assert "not found in request path" not in caplog.text

        client.get("/ping")                          # browser traffic missing its prefix -> warn
        assert "not found in request path" in caplog.text
