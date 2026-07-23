"""Phase 1: Domino proxy-prefix handling — derivation, request stripping, and preview forwarding."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from sage.orchestrator.app import _PrefixMiddleware
from sage.preview.prefix import domino_base_prefix
from sage.preview.proxy import make_preview_app


def _app_with_prefix(prefix: str) -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping(request: Request) -> dict:
        return {"path": request.scope["path"], "root_path": request.scope.get("root_path", "")}

    app.add_middleware(_PrefixMiddleware, prefix=prefix)
    return app


def test_prefix_stripped_so_bare_route_matches():
    client = TestClient(_app_with_prefix("/owner/proj/notebookSession/run"))
    r = client.get("/owner/proj/notebookSession/run/ping")
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == "/ping"
    assert body["root_path"] == "/owner/proj/notebookSession/run"


def test_empty_prefix_is_noop():
    client = TestClient(_app_with_prefix(""))
    r = client.get("/ping")
    assert r.status_code == 200
    assert r.json() == {"path": "/ping", "root_path": ""}


def test_bare_root_path_maps_to_slash():
    # Domino serves the builder page at "<prefix>/" — after stripping, that must become "/".
    app = FastAPI()

    @app.get("/")
    def index(request: Request) -> dict:
        return {"path": request.scope["path"]}

    app.add_middleware(_PrefixMiddleware, prefix="/p/q")
    r = TestClient(app).get("/p/q/")
    assert r.status_code == 200
    assert r.json()["path"] == "/"


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
    client.get(f"/{path}")  # mounted app sees prefix-stripped, /preview-stripped path
    assert captured["url"] == expected
