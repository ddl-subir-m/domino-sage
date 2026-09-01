"""Phase 1: Domino proxy-prefix handling — derivation, request stripping, and preview forwarding."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from sage.orchestrator.app import _gateway_ui_url, _manage_app_url, _PrefixMiddleware, _slot
from sage.preview.prefix import domino_base_prefix, domino_project_label, publish_available
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
    monkeypatch.delenv("SAGE_PROXY_MODE", raising=False)
    monkeypatch.setenv("DOMINO_PROJECT_OWNER", "sub_user")
    monkeypatch.setenv("DOMINO_PROJECT_NAME", "Sage")
    monkeypatch.setenv("DOMINO_RUN_ID", "abc123")
    monkeypatch.delenv("SAGE_BASE_PREFIX", raising=False)
    assert domino_base_prefix() == "/sub_user/Sage/notebookSession/abc123"


def test_domino_base_prefix_empty_locally(monkeypatch):
    for k in ("DOMINO_PROJECT_OWNER", "DOMINO_PROJECT_NAME", "DOMINO_RUN_ID", "SAGE_BASE_PREFIX",
              "SAGE_PROXY_MODE"):
        monkeypatch.delenv(k, raising=False)
    assert domino_base_prefix() == ""


def test_domino_base_prefix_empty_when_running_as_app(monkeypatch):
    # App nginx strips the mount. Baking the workspace notebookSession path would break ./preview/.
    monkeypatch.setenv("SAGE_PROXY_MODE", "app")
    monkeypatch.setenv("DOMINO_PROJECT_OWNER", "sub_user")
    monkeypatch.setenv("DOMINO_PROJECT_NAME", "Sage")
    monkeypatch.setenv("DOMINO_RUN_ID", "abc123")
    monkeypatch.delenv("SAGE_BASE_PREFIX", raising=False)
    assert domino_base_prefix() == ""


def test_publish_available_false_as_app(monkeypatch, tmp_path):
    monkeypatch.setenv("SAGE_PROXY_MODE", "app")
    assert publish_available(tmp_path / "code") is False


def test_publish_available_true_off_domino(monkeypatch, tmp_path):
    monkeypatch.delenv("SAGE_PROXY_MODE", raising=False)
    assert publish_available(tmp_path / "workspaces" / "app") is True


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


def test_catalog_slot_treats_blank_as_unset(monkeypatch):
    # environment/Dockerfile promotes each SAGE_MODEL_* to ENV so a Domino Environment Variable can
    # reach the container, which means an ARG nobody filled in arrives as "" rather than absent.
    # `.get(var, default)` would hand that empty string to the gateway as a model name.
    monkeypatch.setenv("SAGE_MODEL_IMPLEMENT", "")
    assert _slot("SAGE_MODEL_IMPLEMENT", "bedrock-qwen3-coder") == "bedrock-qwen3-coder"

    monkeypatch.setenv("SAGE_MODEL_IMPLEMENT", "  GLM-5.2  ")
    assert _slot("SAGE_MODEL_IMPLEMENT", "bedrock-qwen3-coder") == "GLM-5.2"

    monkeypatch.delenv("SAGE_MODEL_IMPLEMENT", raising=False)
    assert _slot("SAGE_MODEL_IMPLEMENT", "bedrock-qwen3-coder") == "bedrock-qwen3-coder"


def test_gateway_ui_url_deep_links_filtered_to_this_project(monkeypatch):
    # The dashboard deep-links, so the link must arrive already scoped to this deployment. Both the
    # "=" joining tag key to value and the "/" in "<owner>/<project>" have to survive as data —
    # unescaped they'd read as a query separator and a path segment, and the filter would miss.
    monkeypatch.setenv("SAGE_GATEWAY_UI_URL", "https://apps.dogfood.domino.tech/apps/llm_gateway/v1")
    assert _gateway_ui_url("sub_user/Sage") == (
        "https://apps.dogfood.domino.tech/apps/llm_gateway"
        "/#usage?tag=sage-project%3Dsub_user%2FSage"
    )


def test_gateway_ui_url_unfiltered_without_a_project_label(monkeypatch):
    # No label means no filter to apply; the link still opens the usage view rather than
    # carrying a "sage-project=" that matches nothing and shows an empty dashboard.
    monkeypatch.setenv("SAGE_GATEWAY_UI_URL", "https://apps.dogfood.domino.tech/apps/llm_gateway/")
    assert _gateway_ui_url("") == "https://apps.dogfood.domino.tech/apps/llm_gateway/#usage"


def test_gateway_ui_url_absent_off_the_domino_gateway(monkeypatch):
    # fake/openai traffic never reaches the Domino gateway, so a link there would land on a page with
    # no Sage data and read as broken. The UI hides the button on None.
    monkeypatch.delenv("SAGE_GATEWAY_UI_URL", raising=False)
    monkeypatch.setenv("GATEWAY_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr("sage.orchestrator.app.GATEWAY_MODE", "openai")
    assert _gateway_ui_url("sub_user/Sage") is None


def test_manage_app_url_is_host_relative_in_a_workspace(monkeypatch):
    # Never built from DOMINO_API_HOST: inside Domino that is the internal cluster address, so a
    # URL carrying it is one no browser can open. The path goes out hostless and the UI resolves it
    # against the origin the page came from — the same rule app_manage_url/workspace_open_url use.
    monkeypatch.delenv("SAGE_MANAGE_URL", raising=False)
    monkeypatch.delenv("SAGE_PROXY_MODE", raising=False)
    monkeypatch.setenv("DOMINO_API_HOST", "http://nucleus-frontend.domino-platform:80")
    monkeypatch.setenv("DOMINO_PROJECT_OWNER", "sub_user")
    monkeypatch.setenv("DOMINO_PROJECT_NAME", "Sage")
    monkeypatch.setenv("DOMINO_RUN_ID", "abc123")
    assert _manage_app_url() == "/apps/sage-manage"


def test_manage_app_url_is_host_relative_in_the_published_app(monkeypatch):
    # App mode has nginx strip the mount, so there is no workspace prefix to read. It is still a
    # Domino host, and Manage is still one path away — the UI's `apps.` strip is what lands it on
    # the main host from there.
    monkeypatch.delenv("SAGE_MANAGE_URL", raising=False)
    monkeypatch.delenv("DOMINO_PROJECT_OWNER", raising=False)
    monkeypatch.delenv("DOMINO_RUN_ID", raising=False)
    monkeypatch.setenv("SAGE_PROXY_MODE", "app")
    assert _manage_app_url() == "/apps/sage-manage"


def test_manage_app_url_can_be_overridden_whole(monkeypatch):
    # A Manage deployed somewhere this grammar does not reach needs an absolute URL given to it,
    # and the UI passes an absolute one straight through.
    monkeypatch.setenv("SAGE_PROXY_MODE", "app")
    monkeypatch.setenv("SAGE_MANAGE_URL", "https://cloud-dogfood.domino.tech/apps/manage/")
    assert _manage_app_url() == "https://cloud-dogfood.domino.tech/apps/manage"


def test_manage_app_url_absent_off_domino(monkeypatch):
    # A laptop run is not served from a Domino host, so nothing sits behind that path. None hides
    # the link, which is better than one that 404s. DOMINO_API_HOST set (a local .env pointing at a
    # real Domino for Resource listing) is NOT evidence this page is served from Domino.
    monkeypatch.delenv("SAGE_MANAGE_URL", raising=False)
    monkeypatch.delenv("SAGE_PROXY_MODE", raising=False)
    monkeypatch.delenv("SAGE_BASE_PREFIX", raising=False)
    monkeypatch.delenv("DOMINO_RUN_ID", raising=False)
    monkeypatch.setenv("DOMINO_API_HOST", "https://cloud-dogfood.domino.tech")
    assert _manage_app_url() is None


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
