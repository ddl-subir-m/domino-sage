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

from sage.orchestrator import app as app_module
from sage.orchestrator.app import _ProjectDispatchMiddleware, current_orchestrator
from sage.projects.registry import ProjectRow, RegistryEntry


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


# -- ONE-APP-PLAN.md §2.3 step 4's "second half": does a project's loopback MCP traffic actually
# reach THAT project's Orchestrator, end to end through the real `control_app`? Not just the
# middleware in isolation (above) — `_write_project_opencode_config` (ONE-APP-STATUS.md, Phase 2)
# already rewrites every `mcp.*.url` in a project's own opencode.json to carry its `/p/<slug>`
# prefix, so a per-project OpenCode process (today's design: one `opencode serve` per project, not
# yet the shared server the target architecture calls for) dials `/p/<slug>/mcp/live-read` for
# every call. `_ProjectDispatchMiddleware` matches on path generically — it has no allowlist of
# which routes count — so this should already work with no new routing code. Proven here rather
# than assumed: a fake project Orchestrator is registered directly in the real, module-level
# `_REGISTRY`'s cache (bypassing the on-disk scan, the same shortcut `ProjectRegistry.open()`'s own
# cache-hit branch takes), and a real HTTP call through `control_app` is checked to land on it
# instead of the default Orchestrator every other root-scope test in this suite depends on.

class _FakeProjectOrchestrator:
    def __init__(self, project_id=None):
        self.live_read_calls = []
        self.delegated_calls = []
        self._project_id = project_id

    def live_read_call(self, message, probe=False):
        self.live_read_calls.append((message, probe))
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": "ok"}

    def delegated_model_call(self, message):
        self.delegated_calls.append(message)
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": "ok"}


def _register_fake_project(slug: str, fake) -> None:
    with app_module._REGISTRY._lock:
        app_module._REGISTRY._open[slug] = fake


def _forget_project(slug: str) -> None:
    with app_module._REGISTRY._lock:
        app_module._REGISTRY._open.pop(slug, None)


def test_a_project_scoped_live_read_call_reaches_that_projects_orchestrator():
    fake = _FakeProjectOrchestrator()
    _register_fake_project("alpha", fake)
    try:
        r = TestClient(app_module.control_app).post(
            "/p/alpha/mcp/live-read",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}},
        )
        assert r.status_code == 200
        assert len(fake.live_read_calls) == 1
    finally:
        _forget_project("alpha")


def test_a_project_scoped_delegated_model_call_reaches_that_projects_orchestrator():
    fake = _FakeProjectOrchestrator()
    _register_fake_project("beta", fake)
    try:
        r = TestClient(app_module.control_app).post(
            "/p/beta/mcp/delegated-model",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}},
        )
        assert r.status_code == 200
        assert len(fake.delegated_calls) == 1
    finally:
        _forget_project("beta")


def test_two_projects_mcp_traffic_never_crosses(monkeypatch):
    """Not just "reaches a project" — reaches the RIGHT one, and never the default the rest of this
    suite's ~267 monkeypatch-heavy files rely on being untouched by a request that named no slug."""
    default_calls = []
    monkeypatch.setattr(
        app_module._DEFAULT_ORCHESTRATOR, "live_read_call",
        lambda message, probe=False: default_calls.append(message) or {"result": "default"},
    )
    alpha, beta = _FakeProjectOrchestrator(), _FakeProjectOrchestrator()
    _register_fake_project("alpha", alpha)
    _register_fake_project("beta", beta)
    try:
        client = TestClient(app_module.control_app)
        client.post("/p/alpha/mcp/live-read", json={"jsonrpc": "2.0", "id": 1, "method": "x"})
        client.post("/p/beta/mcp/live-read", json={"jsonrpc": "2.0", "id": 2, "method": "x"})
        assert len(alpha.live_read_calls) == 1
        assert len(beta.live_read_calls) == 1
        assert not default_calls
    finally:
        _forget_project("alpha")
        _forget_project("beta")


# -- ONE-APP-PLAN.md §4 Phase 2 step 5: `GET /api/projects` is registry-backed now, not
# `_provision.list_apps()`. The registry's own merge logic (local clones + remote sage-* rows) is
# tested at the unit level in `test_project_registry.py`; these prove the ROUTE calls it correctly
# — with no "current" slug at root scope, and with this request's own project's slug once a call
# arrives scoped through `/p/<slug>/`.

def test_the_projects_route_marks_no_current_at_root_scope(monkeypatch):
    seen = {}

    def fake_list(current=None):
        seen["current"] = current
        return [ProjectRow(slug="alpha", name="Alpha", local=True, current=False)]

    monkeypatch.setattr(app_module._REGISTRY, "list", fake_list)
    r = TestClient(app_module.control_app).get("/api/projects")
    assert r.status_code == 200
    assert seen["current"] is None
    assert r.json() == {"items": [{"slug": "alpha", "name": "Alpha", "local": True,
                                    "dominoProjectId": "", "current": False}]}


def test_the_projects_route_marks_current_for_a_project_scoped_call(monkeypatch):
    fake = _FakeProjectOrchestrator(project_id="alpha")
    _register_fake_project("alpha", fake)
    seen = {}

    def fake_list(current=None):
        seen["current"] = current
        return [
            ProjectRow(slug="alpha", name="Alpha", local=True, current=True),
            ProjectRow(slug="beta", name="Beta", local=True, current=False),
        ]

    monkeypatch.setattr(app_module._REGISTRY, "list", fake_list)
    try:
        r = TestClient(app_module.control_app).get("/p/alpha/api/projects")
        assert r.status_code == 200
        assert seen["current"] == "alpha"
        assert {row["slug"]: row["current"] for row in r.json()["items"]} == {
            "alpha": True, "beta": False,
        }
    finally:
        _forget_project("alpha")


# -- ONE-APP-PLAN.md Phase 3 step 3: `POST /api/projects`/`/api/projects/clone` wired to
# `ProjectRegistry.create()`/`clone()` (replacing the door-era `POST /api/projects/{id}/open`).
# `registry.create`/`clone` are unit-tested directly in `test_project_registry.py`; these prove the
# ROUTE calls them correctly and translates their exceptions to the right status code.

_ENTRY = RegistryEntry(
    slug="alpha", domino_project_id="proj-1", domino_project_name="Alpha",
    owner_name="etan", repo_url="https://github.com/o/sage-alpha.git", created_at="2026-01-01T00:00:00Z",
)


def test_creating_a_project_calls_the_registry_and_returns_its_slug(monkeypatch):
    seen = {}

    def fake_create(name):
        seen["name"] = name
        return _ENTRY

    monkeypatch.setattr(app_module, "_provision", object())  # non-None: past the 503 guard
    monkeypatch.setattr(app_module._REGISTRY, "create", fake_create)
    r = TestClient(app_module.control_app).post("/api/projects", json={"name": "Alpha"})
    assert r.status_code == 200
    assert seen["name"] == "Alpha"
    assert r.json() == {"slug": "alpha", "name": "Alpha"}


def test_creating_a_project_with_no_name_is_refused_before_the_registry_is_asked(monkeypatch):
    called = []
    monkeypatch.setattr(app_module, "_provision", object())
    monkeypatch.setattr(app_module._REGISTRY, "create", lambda name: called.append(name))
    r = TestClient(app_module.control_app).post("/api/projects", json={"name": "   "})
    assert r.status_code == 400
    assert called == []


def test_cloning_a_project_calls_the_registry_and_returns_its_slug(monkeypatch):
    seen = {}

    def fake_clone(domino_project_id):
        seen["id"] = domino_project_id
        return _ENTRY

    monkeypatch.setattr(app_module, "_control_plane", object())  # non-None: past the 503 guard
    monkeypatch.setattr(app_module._REGISTRY, "clone", fake_clone)
    r = TestClient(app_module.control_app).post("/api/projects/clone", json={"dominoProjectId": "proj-1"})
    assert r.status_code == 200
    assert seen["id"] == "proj-1"
    assert r.json() == {"slug": "alpha", "name": "Alpha"}


def test_cloning_an_id_the_token_cannot_see_is_a_404(monkeypatch):
    def fake_clone(domino_project_id):
        raise KeyError(f"no Domino project {domino_project_id!r} visible to this token")

    monkeypatch.setattr(app_module, "_control_plane", object())
    monkeypatch.setattr(app_module._REGISTRY, "clone", fake_clone)
    r = TestClient(app_module.control_app).post("/api/projects/clone", json={"dominoProjectId": "ghost"})
    assert r.status_code == 404


def test_cloning_a_slug_already_on_disk_is_a_409(monkeypatch):
    def fake_clone(domino_project_id):
        raise FileExistsError("projects/alpha already exists locally")

    monkeypatch.setattr(app_module, "_control_plane", object())
    monkeypatch.setattr(app_module._REGISTRY, "clone", fake_clone)
    r = TestClient(app_module.control_app).post("/api/projects/clone", json={"dominoProjectId": "proj-1"})
    assert r.status_code == 409


def test_cloning_with_no_id_is_refused_before_the_registry_is_asked(monkeypatch):
    called = []
    monkeypatch.setattr(app_module, "_control_plane", object())
    monkeypatch.setattr(app_module._REGISTRY, "clone", lambda domino_project_id: called.append(domino_project_id))
    r = TestClient(app_module.control_app).post("/api/projects/clone", json={})
    assert r.status_code == 400
    assert called == []
