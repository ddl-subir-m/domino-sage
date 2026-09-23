"""ONE-APP-PLAN.md §2.3: `/p/<slug>/...` dispatches that request to that project's Orchestrator.

Unit-level, against `_ProjectDispatchMiddleware` directly rather than the full `control_app` route
stack — the mechanism under test is the middleware's `root_path` extension and its `ContextVar`
set/reset around one request, not any one route's business logic.
"""
from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from sage.orchestrator.app import _ProjectDispatchMiddleware, current_orchestrator


class _FakeRegistry:
    def __init__(self, projects):
        self._projects = projects
        self.opened = []

    def open(self, slug):
        self.opened.append(slug)
        if slug not in self._projects:
            raise KeyError(slug)
        return self._projects[slug]


def _echo_app() -> Starlette:
    async def echo(request):
        root_path = request.scope.get("root_path", "")
        path = request.scope["path"]
        return PlainTextResponse(f"{root_path}|{path}|{id(current_orchestrator())}")

    return Starlette(routes=[Route("/{rest:path}", echo)])


def _wrapped(projects: dict) -> Starlette:
    app = _echo_app()
    app.add_middleware(_ProjectDispatchMiddleware, registry=_FakeRegistry(projects))
    return app


def test_a_request_with_no_slug_passes_through_untouched():
    client = TestClient(_wrapped({}))
    r = client.get("/api/me")
    root_path, path, orch_id = r.text.split("|")
    assert root_path == ""
    assert path == "/api/me"
    assert int(orch_id) == id(current_orchestrator())  # unchanged: still the default


def test_a_slug_request_extends_root_path_and_binds_that_orchestrator():
    alpha = object()
    client = TestClient(_wrapped({"alpha": alpha}))
    r = client.get("/p/alpha/api/threads")
    root_path, path, orch_id = r.text.split("|")
    assert root_path == "/p/alpha"
    assert path == "/p/alpha/api/threads"
    assert int(orch_id) == id(alpha)


def test_two_projects_bind_two_different_orchestrators():
    alpha, beta = object(), object()
    client = TestClient(_wrapped({"alpha": alpha, "beta": beta}))
    r1 = client.get("/p/alpha/api/threads")
    r2 = client.get("/p/beta/api/threads")
    assert int(r1.text.split("|")[2]) == id(alpha)
    assert int(r2.text.split("|")[2]) == id(beta)


def test_an_unknown_slug_is_a_404_not_a_500():
    client = TestClient(_wrapped({}))
    r = client.get("/p/ghost/api/threads")
    assert r.status_code == 404


def test_a_trailing_slash_with_no_slug_segment_passes_through():
    client = TestClient(_wrapped({}))
    r = client.get("/p/")
    root_path, path, _ = r.text.split("|")
    assert root_path == ""
    assert path == "/p/"


def test_the_contextvar_resets_after_the_request():
    alpha = object()
    client = TestClient(_wrapped({"alpha": alpha}))
    client.get("/p/alpha/api/threads")
    assert current_orchestrator() is not alpha


class _FakeUpstreamPrefix:
    """Stands in for `_PrefixMiddleware`, which runs upstream of `_ProjectDispatchMiddleware` in the
    real app and has already set `root_path` to Domino's own mount prefix by the time this one runs
    (verified empirically for this ordering — see ONE-APP-PLAN.md §2.3's spike addendum)."""

    def __init__(self, app, prefix: str) -> None:
        self._app = app
        self._prefix = prefix

    async def __call__(self, scope, receive, send):
        scope = dict(scope)
        scope["root_path"] = self._prefix
        await self._app(scope, receive, send)


def test_a_slug_dispatch_extends_rather_than_replaces_an_existing_root_path():
    alpha = object()
    dispatch = _ProjectDispatchMiddleware(_echo_app(), _FakeRegistry({"alpha": alpha}))
    app = _FakeUpstreamPrefix(dispatch, "/apps/some-uuid")
    client = TestClient(app)
    r = client.get("/apps/some-uuid/p/alpha/api/threads")
    root_path, path, orch_id = r.text.split("|")
    assert root_path == "/apps/some-uuid/p/alpha"
    assert path == "/apps/some-uuid/p/alpha/api/threads"
    assert int(orch_id) == id(alpha)
