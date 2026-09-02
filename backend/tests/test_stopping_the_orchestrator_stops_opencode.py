"""Teardown belongs to the app, not to one launcher.

`run()` wires shutdown for `python -m sage.orchestrator.app`. Everything else that imports
`control_app` — `uvicorn sage.orchestrator.app:control_app`, one of its --reload workers — used to
get none, and OpenCode spawns with `start_new_session=True`, so it survives the exit instead of
dying with it. Every such exit leaked one `opencode serve`.
"""
from pathlib import Path

from fastapi.testclient import TestClient

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog


def test_serving_control_app_under_any_asgi_server_tears_down_on_exit(monkeypatch):
    import sage.orchestrator.app as appmod

    calls = []

    class _Recorder:
        def shutdown(self):
            calls.append("shutdown")

    monkeypatch.setattr(appmod, "orchestrator", _Recorder())
    with TestClient(appmod.control_app):
        assert calls == [], "teardown ran at startup"
    assert calls == ["shutdown"]


def test_shutdown_runs_once_even_though_two_callers_reach_it(tmp_path: Path):
    """The lifespan runs it and run()'s `finally` runs it. The git save must not happen twice."""
    stopped = []

    class _Server:
        def stop(self):
            stopped.append("stop")

    orch = Orchestrator(
        workspace_dir=tmp_path / "ws",
        template=tmp_path / "template",
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
    )
    orch._oc_server = _Server()

    orch.shutdown()
    orch.shutdown()

    assert stopped == ["stop"]
