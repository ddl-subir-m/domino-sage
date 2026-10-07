"""A Build turn on an app that is not the selected one refreshes that app's Sage-owned files (#690).

WHAT THIS GUARDS. `_prepare_app_files` brings an app's Sage-owned sources and preview config in line
with the template, and it used to run only at attach, at seed and at a real select. A tab opened on
an app from the picker (`?app=<id>`) builds through a request view and never selects it, so that app
kept the files it was born with. Seen live: `Signal Room 3` kept the pre-#657
`static/sage/reportRuntimeError.js`, its page ack never reached Sage, and every page check timed out
while the selected app, holding the fixed copy, passed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator, _request_view, _request_workspace
from sage.router.models import ModelCatalog
from sage.workspace.stack import FASTAPI_ANTD

_REPORTER = "static/sage/reportRuntimeError.js"


class _StopAfterPreparation(Exception):
    pass


def _react_template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True)
    (t / "src" / "App.tsx").write_text("placeholder\n")
    (t / "package.json").write_text("{}\n")
    (t / "vite.config.ts").write_text("// template v2\n")
    return t


def _two_apps(tmp_path: Path, monkeypatch, stack: str):
    """App A exists. App B is the selected one, because minting selects it."""
    monkeypatch.setenv("SAGE_DEFAULT_STACK", stack)
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_react_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
    )
    project = orch.project(start_preview=False)
    app_a = project.workspace
    app_b = orch.create_app()["id"]
    assert orch._wm.selected_app_id() == app_b
    return orch, project, app_a, app_b


def _build_on(orch: Orchestrator, project, app_id: str, monkeypatch, *, bind_view: bool = True) -> None:
    """Start a Build turn the way a `?app=` tab does, and stop it once the turn is prepared.

    The middleware binds the named app's view for the request; the turn pins it from there.
    `bind_view=False` stashes only the workspace: a request that named the then-selected app and
    waited for the lock while a select moved the selection elsewhere."""
    def stop():
        raise _StopAfterPreparation

    monkeypatch.setattr(orch, "_ensure_opencode", stop)
    view = orch._view_for(project, app_id)
    view_token = _request_view.set(view) if bind_view else None
    workspace_token = _request_workspace.set(view.workspace)
    try:
        with pytest.raises(_StopAfterPreparation):
            orch.build("add a chart")
    finally:
        _request_workspace.reset(workspace_token)
        if view_token is not None:
            _request_view.reset(view_token)


def test_a_build_through_the_request_view_refreshes_a_stale_reporter(tmp_path: Path, monkeypatch):
    orch, project, app_a, app_b = _two_apps(tmp_path, monkeypatch, FASTAPI_ANTD.name)
    stale = app_a.path / _REPORTER
    stale.write_text('sage.base.replace(/\\/preview\\/?$/, "")\n')

    _build_on(orch, project, app_a.app_id, monkeypatch)

    assert stale.read_bytes() == (FASTAPI_ANTD.template_dir / _REPORTER).read_bytes()
    assert orch._wm.selected_app_id() == app_b


@pytest.mark.parametrize("bind_view", [True, False], ids=["request-view", "pinned-before-a-select"])
def test_a_changed_preview_config_restarts_that_apps_preview_only(tmp_path: Path, monkeypatch,
                                                                  bind_view: bool):
    orch, project, app_a, app_b = _two_apps(tmp_path, monkeypatch, "react-vite")
    (app_a.path / "vite.config.ts").write_text("// an older Sage wrote this\n")
    view_a = orch._view_for(project, app_a.app_id)
    held_a = view_a.supervisor
    held_b = project._selected_view.supervisor

    _build_on(orch, project, app_a.app_id, monkeypatch, bind_view=bind_view)

    assert (app_a.path / "vite.config.ts").read_text() == "// template v2\n"
    assert view_a.supervisor is not held_a
    assert project._selected_view.supervisor is held_b
    assert orch._wm.selected_app_id() == app_b
