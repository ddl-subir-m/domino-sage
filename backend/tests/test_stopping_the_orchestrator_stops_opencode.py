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


def test_importing_the_module_starts_no_opencode_server():
    """Startup work belongs to the lifespan, because that is the event the teardown pairs with.

    The preflight and the warm-up used to run on threads started at module import. An import is not
    a launch: `pytest` collecting 3000 tests, a tool reading `control_app`, any `--collect-only`
    imported this module and got a real `npx opencode serve` that no shutdown was ever going to
    reach. One per import, one per xdist worker, ~80 MB each — the same orphan this file's other
    test is about, arriving through the other door.

    Run in a child so the import is a first import. The child joins whatever threads the import
    left running, so a warm-up that spawns slightly later than it returns still gets caught.
    """
    import subprocess
    import sys

    child = subprocess.run(
        [sys.executable, "-c", """
import subprocess, threading
spawned = []
_real = subprocess.Popen
def _popen(args, *a, **kw):
    flat = " ".join(args) if isinstance(args, (list, tuple)) else str(args)
    if "opencode" in flat:
        spawned.append(flat)
        raise AssertionError("import spawned opencode")
    return _real(args, *a, **kw)
subprocess.Popen = _popen

import sage.orchestrator.app  # noqa: F401

for t in threading.enumerate():
    if t is not threading.current_thread():
        t.join(timeout=10)
print("SPAWNED" if spawned else "CLEAN", end="")
"""],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=120,
        check=False,
    )

    assert child.returncode == 0, child.stderr
    assert child.stdout.endswith("CLEAN"), child.stdout


def test_the_lifespan_is_what_warms_opencode(monkeypatch):
    """The other half: moving the warm-up must not mean losing it. `_ensure_opencode` still runs
    ahead of the first turn, so the person's opening message does not pay for the Node boot."""
    import threading

    import sage.orchestrator.app as appmod

    warmed = threading.Event()

    class _Recorder:
        def preflight_slots(self):
            return {"state": "ok", "error": None, "slots": []}

        def _ensure_opencode(self):
            warmed.set()

        def shutdown(self):
            pass

    monkeypatch.setattr(appmod, "orchestrator", _Recorder())
    with TestClient(appmod.control_app):
        assert warmed.wait(timeout=10), "the lifespan never warmed OpenCode"
