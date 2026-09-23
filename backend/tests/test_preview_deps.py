"""And a preview that cannot start does not take the orchestrator with it (#500).

fastapi-antd has no build step and so no dependency-optimizer graph to desync — the failure this
file used to pin against Vite's `optimizeDeps` (a starter that imports `lucide-react` for the first
time, mid-preview, forcing a re-optimize that strands the open page holding modules from a graph
that no longer exists) has no equivalent here: nothing here pre-bundles anything, and `#500`'s own
guard — a supervisor that cannot start must not stop the attach from being cached — is the part
that is still real and still worth pinning, since it is about `project()`'s own control flow, not
about Vite.
"""
from __future__ import annotations

from pathlib import Path


def _orch(tmp: Path):
    """A project seeded from a stub template, as a real one does."""
    from sage.orchestrator.service import Orchestrator
    from sage.router.models import ModelCatalog

    t = tmp / "template"
    (t / "static").mkdir(parents=True, exist_ok=True)
    (t / "static" / "app.js").write_text("placeholder")
    (t / "app.py").write_text("# app\n")
    catalog = ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                           plan="p", implement="i", ask="a")
    return Orchestrator(workspace_dir=tmp / "mnt" / "code", template=t, gateway=object(),
                        catalog=catalog, project_id="Sage")


class _DeadSupervisor:
    """A preview server that cannot start, as `vite: not found` and `max restarts reached` are."""

    def __init__(self) -> None:
        self.starts = 0

    def start(self, ready_timeout_s: float = 30.0) -> str:
        self.starts += 1
        raise RuntimeError("uvicorn exited (code 127); max restarts reached")

    def upstream(self) -> str:
        raise RuntimeError("uvicorn not ready")

    def stop(self) -> None:
        pass

    def mount_base(self) -> str:
        return ""


class _StubQueries:
    """PreviewQueries without the subprocess. `raises` plants the second condition."""

    raises = False
    port = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self) -> None:
        if type(self).raises:
            raise RuntimeError("preview queries could not bind a port")

    def stop(self) -> None:
        pass


def _dead_preview(monkeypatch, *, queries_raise: bool = False) -> _DeadSupervisor:
    from sage.orchestrator import service

    dead = _DeadSupervisor()
    monkeypatch.setattr(service, "_supervisor_for", lambda *a, **k: dead)
    monkeypatch.setattr(_StubQueries, "raises", queries_raise)
    monkeypatch.setattr(service, "PreviewQueries", _StubQueries)
    return dead


def test_a_supervisor_that_cannot_start_still_leaves_the_project_attached(tmp_path: Path, monkeypatch):
    """#500. `supervisor.start()` used to raise out of `project()` before `self._project` was ever
    assigned, so the attach never cached.

    What that cost, measured live on 2026-09-22: every later request re-ran the whole attach —
    `_prepare_app_files`, `_effective_catalog`, `_voice_agents_md`, `_rehydrate_attached`, the
    membership backfill — and each one held a thread from Starlette's 40-thread pool for as long as
    the supervisor took to give up. The Builder polls every 1.5-2s, so the pool drained and every
    SYNCHRONOUS route hung behind it at once: Build turns, the model-assignment drawer, /api/health.
    The preview proxy is `async`, so it went on answering 502 and the session looked alive. Chat kept
    working only because it is the one caller that passes `start_preview=False`.
    """
    dead = _dead_preview(monkeypatch)
    orch = _orch(tmp_path)

    project = orch.project(start_preview=True)

    assert project is orch._project, "a failed preview must not stop the attach from being cached"
    assert dead.starts == 1


def test_a_cached_attach_does_not_retry_the_dead_preview_on_every_call(tmp_path: Path, monkeypatch):
    """The half that actually saves the threadpool: the SECOND call must be free.

    Caching the project but re-running the start on every `project()` would drain the pool just the
    same, so this asserts the call count and not only the identity.
    """
    dead = _dead_preview(monkeypatch)
    orch = _orch(tmp_path)

    first = orch.project(start_preview=True)
    for _ in range(5):
        assert orch.project() is first

    assert dead.starts == 1


def test_the_attach_finishes_its_remaining_work_after_a_preview_failure(tmp_path: Path, monkeypatch):
    """Cached-but-half-built would be its own outage.

    Everything after the preview start — voicing AGENTS.md, splicing instructions, the shim, the
    queries object — has to run, or the project that gets cached is one Build cannot use.
    """
    _dead_preview(monkeypatch)
    orch = _orch(tmp_path)

    project = orch.project(start_preview=True)

    assert project.record is not None
    assert project.shim is not None
    assert project.queries is not None
    assert project.workspace.app_entry.is_file(), "the app was seeded despite the dead preview"


def test_preview_queries_failing_does_not_stop_the_attach_either(tmp_path: Path, monkeypatch):
    """The second condition, planted separately: the supervisor starting and the queries server
    failing is a different path through the same block."""
    from sage.orchestrator import service

    monkeypatch.setattr(_StubQueries, "raises", True)
    monkeypatch.setattr(service, "PreviewQueries", _StubQueries)

    class _LiveSupervisor(_DeadSupervisor):
        def start(self, ready_timeout_s: float = 30.0) -> str:
            self.starts += 1
            return "http://127.0.0.1:8000"

    monkeypatch.setattr(service, "_supervisor_for", lambda *a, **k: _LiveSupervisor())

    orch = _orch(tmp_path)
    project = orch.project(start_preview=True)

    assert project is orch._project
