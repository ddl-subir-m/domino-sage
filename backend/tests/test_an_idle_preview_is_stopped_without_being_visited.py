"""Previews do not pile up (#739).

Every Build's page check starts the built app's preview, and switching app leaves the old one
running on purpose. The idle reap ran only when preview traffic arrived and skipped a preview whose
`last_traffic` was None, so a preview nobody had visited — exactly what a page check leaves behind —
was never stopped, and each one is a process tree (Vite + esbuild, or uvicorn's reloader + worker).

Three rules now hold, none of which waits for a request:

- A running preview nobody has viewed for `_PREVIEW_IDLE_S` is stopped, visited or not.
- At most `_PREVIEW_CAP` run at once; the least recently viewed go first.
- Neither rule stops the selected app's preview by the cap, nor the preview a running turn's page
  check is reading.
"""
from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.orchestrator import service as svc
from sage.preview.supervisor import ViteSupervisor

from .fake_opencode import Turn
from .test_an_approved_plan_runs_as_implement import _build

IDLE = svc._PREVIEW_IDLE_S


class FakePreview:
    """A supervisor with no process: `running` is the whole of what the reap reads besides traffic."""

    def __init__(self, workspace, base_prefix: str = "", **_ignored) -> None:
        self.workspace = Path(workspace)
        self.running = False
        self.last_traffic: float | None = None
        self.generation = 0
        self.idled = threading.Event()

    def note_traffic(self, now: float) -> None:
        self.last_traffic = now

    def idle_stop(self) -> None:
        self.running = False
        self.last_traffic = None
        self.idled.set()

    def stop(self) -> None:
        self.running = False

    def retry_start(self, *, explicit: bool = False) -> bool:
        self.running = True
        self.generation += 1
        return True

    def upstream(self) -> str:
        if not self.running:
            raise RuntimeError("not ready")
        return "http://127.0.0.1:1"

    def status(self) -> dict:
        return {"appId": self.workspace.name, "generation": f"fake:{self.generation}",
                "state": "ready" if self.running else "failed", "error": None}

    def runtime_fault(self):
        return None

    def query_reads(self):
        return []


class FakeQueries:
    def __init__(self, workspace, template=None) -> None:
        self.port: int | None = None

    def start(self) -> None:
        self.port = 7777

    def refresh(self) -> None:
        pass

    def stop(self) -> None:
        self.port = None


@pytest.fixture
def previews(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "ViteSupervisor", FakePreview)
    monkeypatch.setattr(svc, "PreviewQueries", FakeQueries)

    def make(n: int):
        orch, _oc = _build(tmp_path, [Turn(writes={})])
        project = orch.project(start_preview=False)
        ids = [project.workspace.app_id] + [orch.create_app()["id"] for _ in range(n - 1)]
        views = [orch._view_for(project, app_id) for app_id in ids]
        assert views[-1] is project._selected_view
        return orch, project, views

    return make


def _running(views) -> list:
    return [v for v in views if v.supervisor.running]


# --- the idle window holds for a preview nobody visited ------------------------------------------

def test_a_preview_nobody_ever_visited_is_stopped_after_the_idle_window(previews):
    orch, project, views = previews(2)
    built = views[0]
    built.supervisor.retry_start(explicit=True)   # what a page check does; no traffic follows
    assert built.supervisor.last_traffic is None

    orch._reap_previews(project, now=1000.0)
    orch._reap_previews(project, now=1000.0 + IDLE - 1)
    assert built.supervisor.running

    orch._reap_previews(project, now=1000.0 + IDLE)
    assert not built.supervisor.running


def test_the_reaper_stops_an_idle_preview_with_no_request_arriving(previews):
    orch, _project, views = previews(2)
    sup = views[0].supervisor
    sup.retry_start(explicit=True)
    sup.note_traffic(time.monotonic() - IDLE - 1)

    orch.start_preview_reaper(interval_s=0.01)
    try:
        assert sup.idled.wait(5), "no sweep ran without a request to drive it"
    finally:
        orch.stop_preview_reaper()
    assert not any(t.name == "sage-preview-reaper" for t in threading.enumerate())


def test_shutdown_stops_the_reaper_and_every_preview(previews):
    orch, _project, views = previews(3)
    for view in views:
        view.supervisor.retry_start(explicit=True)
    orch.start_preview_reaper(interval_s=60)
    reaper = orch._preview_reaper
    assert reaper is not None and reaper.is_alive()

    orch.shutdown()

    assert not reaper.is_alive()
    assert _running(views) == []


def test_a_shut_down_orchestrator_starts_no_reaper(previews):
    orch, _project, _views = previews(1)
    orch.shutdown()
    orch.start_preview_reaper(interval_s=60)
    assert orch._preview_reaper is None


def test_the_app_starts_the_reaper_and_its_teardown_stops_it(monkeypatch):
    import sage.orchestrator.app as appmod

    calls = []

    class _Recorder:
        def start_preview_reaper(self):
            calls.append("start")

        def shutdown(self):
            calls.append("shutdown")

    monkeypatch.setattr(appmod, "orchestrator", _Recorder())
    for step in ("_run_slot_preflight", "_warm_opencode", "_run_permission_preflight"):
        monkeypatch.setattr(appmod, step, lambda: None)
    with TestClient(appmod.control_app):
        assert calls == ["start"]
    assert calls == ["start", "shutdown"]


# --- at most three run --------------------------------------------------------------------------

def test_at_most_three_previews_run_and_the_least_recently_viewed_go_first(previews):
    orch, project, views = previews(5)
    for i, view in enumerate(views):
        view.supervisor.retry_start(explicit=True)
        view.supervisor.note_traffic(100.0 + i)
    project._selected_view.supervisor.note_traffic(50.0)  # least recent, and still kept

    orch._reap_previews(project, now=110.0)

    assert [v.workspace.app_id for v in _running(views)] == [v.workspace.app_id for v in views[2:]]


def test_the_preview_a_running_build_needs_is_never_the_one_stopped(previews):
    orch, project, views = previews(5)
    built = views[0]
    for i, view in enumerate(views):
        view.supervisor.retry_start(explicit=True)
        view.supervisor.note_traffic(100.0 + i)
    built.supervisor.note_traffic(10.0)          # idle AND the least recently viewed
    project.turn_app = built.workspace

    orch._reap_previews(project, now=10.0 + IDLE + 50)

    running = _running(views)
    assert built in running
    assert len(running) == 3


def test_a_preview_being_started_counts_toward_the_cap(previews):
    orch, project, views = previews(4)
    target = views[0]
    base = time.monotonic()
    for i, view in enumerate(views[1:]):
        view.supervisor.retry_start(explicit=True)
        view.supervisor.note_traffic(base - 10 + i)

    orch._account_preview_traffic(project, target)
    orch._ensure_preview_running(project, target)

    running = _running(views)
    assert len(running) == 3
    assert target in running and project._selected_view in running


def test_the_preview_being_asked_for_is_never_the_one_the_cap_stops(previews):
    # The others have never been seen, so this sweep starts their clocks at the same instant as the
    # request's. A tie must not be broken against the preview somebody is asking for.
    orch, project, views = previews(4)
    target = views[0]
    target.supervisor.retry_start(explicit=True)
    for view in views[1:]:
        view.supervisor.retry_start(explicit=True)

    orch._account_preview_traffic(project, target)

    running = _running(views)
    assert len(running) == 3
    assert target in running and project._selected_view in running


def test_a_page_check_counts_as_a_view_of_the_preview_it_starts(previews):
    orch, project, views = previews(4)
    built = views[0]
    base = time.monotonic()
    for i, view in enumerate(views[1:]):
        view.supervisor.retry_start(explicit=True)
        view.supervisor.note_traffic(base - 10 + i)
    project.turn_app = built.workspace
    orch._build_policy = replace(orch._build_policy, page_ack_wait_seconds=5)

    check = orch._validate_page(project, "Typecheck")
    try:
        next(check)
    finally:
        check.close()
        project.turn_app = None

    assert built.supervisor.last_traffic is not None
    assert len(_running(views)) == 3


# --- the supervisor's half ------------------------------------------------------------------------

class _Exited:
    pid = -1

    def poll(self):
        return 0


def test_an_idle_stop_leaves_no_idle_clock_behind(tmp_path):
    sup = ViteSupervisor(tmp_path)
    sup.note_traffic(5.0)
    sup.idle_stop()
    assert sup.last_traffic is None


def test_running_means_a_process_or_a_start_under_way(tmp_path):
    sup = ViteSupervisor(tmp_path)
    assert not sup.running

    sup._proc = _Exited()
    assert sup.running
    sup.idle_stop()
    assert not sup.running

    release = threading.Event()
    sup._retry_thread = threading.Thread(target=release.wait)
    sup._retry_thread.start()
    try:
        assert sup.running
    finally:
        release.set()
        sup._retry_thread.join()
    assert not sup.running
