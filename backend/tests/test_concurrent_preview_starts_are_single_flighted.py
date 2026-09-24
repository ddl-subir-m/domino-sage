"""A cold project's preview must attach once and start one supervisor, no matter how many browser
tabs ask at the same instant (CONCURRENT-PROJECT-PREVIEW-BUG.md).

Phase 4 moved `get_upstream` (`_preview_upstream` -> `Orchestrator._ensure_seeded` +
`_ensure_preview_running`) onto Starlette's threadpool so a cold start cannot freeze the shared event
loop. That made two first-preview requests to the SAME project run on genuinely parallel threads —
and neither the attach (`project()`) nor the supervisor start held a lock, so both threads built a
Project and spawned a `uvicorn --reload`, orphaning all but the last. Reproduced live 2026-09-24:
six concurrent requests to one fresh project left six uvicorn servers, only one of them tracked.

Driven with real threads (not one event loop, unlike
`test_preview_proxy_does_not_block_the_event_loop.py`) on purpose: the race is between threadpool
workers, so the reproduction has to be threads. A `Barrier` releases them together to widen the
window, and the fakes sleep inside `start()` so a lock-free path reliably double-starts.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sage.orchestrator import service
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _catalog() -> ModelCatalog:
    return ModelCatalog(
        sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
        plan="p", implement="i", ask="a",
    )


def _orch(tmp: Path) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),
        catalog=_catalog(),
        project_id="Sage",
    )


class _FakeSupervisor:
    """One start(), then ready. start() blocks so a lock-free racer reliably calls it twice."""

    def __init__(self, *_args, **_kwargs) -> None:
        self.start_calls = 0
        self._ready = False
        self._lock = threading.Lock()

    def upstream(self) -> str:
        if not self._ready:
            raise RuntimeError("uvicorn not ready")
        return "http://127.0.0.1:12345"

    def start(self, *_a, **_k) -> str:
        with self._lock:
            self.start_calls += 1
        time.sleep(0.15)
        self._ready = True
        return "http://127.0.0.1:12345"


class _FakeQueries:
    def __init__(self) -> None:
        self.start_calls = 0
        self.port = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            self.start_calls += 1
        time.sleep(0.15)
        self.port = 54321


class _FakeProject:
    def __init__(self) -> None:
        self.supervisor = _FakeSupervisor()
        self.queries = _FakeQueries()


def _fire(fn, n: int) -> None:
    """Run `fn` on `n` threads, released together, and re-raise the first failure."""
    barrier = threading.Barrier(n)

    def go():
        barrier.wait()
        return fn()

    with ThreadPoolExecutor(max_workers=n) as ex:
        for fut in [ex.submit(go) for _ in range(n)]:
            fut.result()


def test_concurrent_preview_requests_start_the_supervisor_once(tmp_path: Path):
    orch = _orch(tmp_path)
    project = _FakeProject()

    _fire(lambda: orch._ensure_preview_running(project), n=8)

    assert project.supervisor.start_calls == 1, (
        "eight concurrent preview requests each started the supervisor — a lock-free "
        "_ensure_preview_running spawns one uvicorn per racer, orphaning all but the last"
    )
    assert project.queries.start_calls == 1


def test_concurrent_attach_builds_one_project(tmp_path: Path, monkeypatch):
    orch = _orch(tmp_path)
    built: list[_FakeSupervisor] = []

    def counting_supervisor(_path, _base_prefix) -> _FakeSupervisor:
        sup = _FakeSupervisor()
        built.append(sup)
        return sup

    # Stub the supervisor so the attach never spawns a real `uvicorn --reload`; count how many the
    # attach builds. `project(start_preview=False)` never calls start(), so the fake needs no more.
    monkeypatch.setattr(service, "_supervisor_for", counting_supervisor)

    projects = []
    lock = threading.Lock()

    def attach():
        p = orch.project(start_preview=False, seed_app=True)
        with lock:
            projects.append(p)

    _fire(attach, n=8)

    assert len(built) == 1, (
        "eight concurrent first-attach requests each built a Project and a supervisor — the loser "
        "supervisors are orphaned, never stopped"
    )
    assert all(p is projects[0] for p in projects)
