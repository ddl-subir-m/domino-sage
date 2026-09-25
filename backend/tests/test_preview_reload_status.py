"""The reload parent being alive does not prove that the app can serve a page (#504)."""
import json
import subprocess
import threading
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sage.preview.proxy import make_preview_app
from sage.preview.supervisor import UvicornSupervisor, ViteSupervisor

_APP = '''from fastapi import FastAPI
from fastapi.responses import HTMLResponse
app = FastAPI()
@app.get("/")
def page():
    return HTMLResponse("<html><body>healthy fixture</body></html>")
'''


def _fixture(root):
    (root / ".sage").mkdir()
    (root / ".sage/settings.json").write_text(json.dumps({"stack": "fastapi-antd"}))
    (root / "static").mkdir()
    (root / "static/app.js").write_text("// fixture")
    (root / "app.py").write_text(_APP)


def _wait(predicate, timeout=12):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("preview did not reach the required state")


def test_real_uvicorn_reload_failure_and_repair_keep_the_parent(tmp_path, monkeypatch):
    monkeypatch.delenv("SAGE_PREVIEW_PORT", raising=False)
    _fixture(tmp_path)
    sup = UvicornSupervisor(tmp_path)
    try:
        url = sup.start(ready_timeout_s=12)
        first = sup.status()
        parent = sup._proc
        assert first["state"] == "ready"
        assert httpx.get(url, timeout=1).status_code == 200
        # StatReload records its initial mtimes on its first poll, after the child starts.
        time.sleep(0.6)
        (tmp_path / "app.py").write_text("import sage_missing_preview_fixture_dependency\n" + _APP)
        try:
            _wait(lambda: sup.status()["state"] == "failed")
        except AssertionError as exc:
            raise AssertionError(sup.status()) from exc
        failed = sup.status()
        assert parent is sup._proc and parent.poll() is None
        assert failed["server"] == "uvicorn"
        assert failed["generation"] != first["generation"]
        assert "sage_missing_preview_fixture_dependency" in failed["error"]
        assert len(failed["output"]) <= 20
        with pytest.raises(RuntimeError):
            sup.upstream()
        sup.retry_start()  # polling must not kill a reloader that can see a repair
        assert sup._proc is parent
        time.sleep(0.6)  # let StatReload take the new child's baseline before the next edit
        (tmp_path / "app.py").write_text(_APP)
        _wait(lambda: sup.status()["state"] == "ready")
        assert sup._proc is parent
        assert sup.status()["error"] is None
        assert httpx.get(sup.upstream(), timeout=1).status_code == 200
    finally:
        sup.stop()


def test_empty_and_incomplete_status_do_not_name_a_guessed_server(tmp_path):
    sup = ViteSupervisor(tmp_path)
    assert sup.status()["state"] == "empty"
    assert sup.status()["server"] is None
    assert sup.status()["appId"] == tmp_path.name
    _fixture(tmp_path)
    (tmp_path / "app.py").unlink()
    sup = UvicornSupervisor(tmp_path)
    assert sup.status()["state"] == "failed"
    assert "app.py" in sup.status()["error"]


def test_initial_import_failure_is_reported_before_start_timeout(tmp_path, monkeypatch):
    monkeypatch.delenv("SAGE_PREVIEW_PORT", raising=False)
    _fixture(tmp_path)
    (tmp_path / "app.py").write_text("import sage_missing_preview_fixture_dependency\n" + _APP)
    sup = UvicornSupervisor(tmp_path)
    try:
        before = time.monotonic()
        with pytest.raises(RuntimeError, match="sage_missing_preview_fixture_dependency"):
            sup.start(ready_timeout_s=10)
        assert time.monotonic() - before < 5
        assert sup.status()["state"] == "failed"
        assert sup._proc.poll() is None, "the reloader must remain able to observe a corrected file"
    finally:
        sup.stop()


@pytest.mark.parametrize("change", ["none", "process", "stop"])
def test_pending_http_readiness_is_guarded_by_process_and_stop(tmp_path, monkeypatch, change):
    from .test_a_dead_preview_does_not_take_the_session_with_it import _Pipe, _Proc

    _fixture(tmp_path)
    sup = UvicornSupervisor(tmp_path)
    proc = _Proc(_Pipe([]))
    sup._proc = proc
    entered, release = threading.Event(), threading.Event()

    def check(url):
        entered.set()
        release.wait(2)
        return True

    monkeypatch.setattr(sup, "_entry_serves", check)
    thread = threading.Thread(target=sup._confirm_ready, args=(proc, "http://127.0.0.1:7", 0))
    thread.start()
    assert entered.wait(1)
    assert sup.status()["state"] == "starting"
    if change == "process":
        sup._proc = _Proc(_Pipe([]))
    elif change == "stop":
        sup.stop()
    release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert (sup.status()["state"] == "ready") is (change == "none")
    assert bool(sup._upstream) is (change == "none")


def test_request_retries_are_bounded_and_explicit_retry_opens_a_new_attempt(tmp_path, monkeypatch):
    _fixture(tmp_path)
    calls = []

    def fail(self, ready_timeout_s=30):
        calls.append(True)
        raise RuntimeError("broken import")

    monkeypatch.setattr(UvicornSupervisor, "start", fail)
    sup = UvicornSupervisor(tmp_path)
    for _ in range(12):
        sup.retry_start()
        if sup._retry_thread:
            sup._retry_thread.join(1)
    assert len(calls) == 1
    sup.retry_start(explicit=True)
    sup._retry_thread.join(1)
    assert len(calls) == 2
    sup.stop()
    sup.retry_start()
    assert len(calls) == 2


def test_explicit_retry_invalidates_old_ready_state_before_returning(tmp_path, monkeypatch):
    _fixture(tmp_path)
    sup = UvicornSupervisor(tmp_path)
    sup._upstream = "http://127.0.0.1:7"
    sup._state = "ready"
    before = sup.status()["generation"]
    release = threading.Event()
    monkeypatch.setattr(sup, "start", lambda: release.wait(2))
    try:
        assert sup.retry_start(explicit=True) is True
        assert sup.status()["state"] == "starting"
        assert sup.status()["generation"] != before
        with pytest.raises(RuntimeError):
            sup.upstream()
        assert sup.retry_start(explicit=True) is False, "a pending attempt cannot overlap another"
    finally:
        release.set()
        sup._retry_thread.join(1)


def test_old_process_output_cannot_mark_the_new_process_ready(tmp_path, monkeypatch):
    from .test_a_dead_preview_does_not_take_the_session_with_it import _Pipe, _Proc

    sup = UvicornSupervisor(tmp_path)
    sup._proc = _Proc(_Pipe([]))
    stale = _Proc(_Pipe([
        "Uvicorn running on http://127.0.0.1:7777\n", "Application startup complete.\n",
    ]))
    monkeypatch.setattr(sup, "_entry_serves", lambda url: True)
    sup._read_output(stale)
    assert sup._upstream is None
    assert sup.recent_output() == []


@pytest.mark.parametrize("error", [httpx.ReadTimeout("read timed out"), httpx.ConnectError("refused")])
def test_proxy_failure_carries_structured_server_status(error, monkeypatch):
    status = {"appId": "app1", "generation": "run:2", "server": "uvicorn", "state": "failed",
              "attempt": 1, "error": "ModuleNotFoundError: missing_fixture", "output": ["trace"]}

    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(httpx.AsyncClient, "send", fail)
    app = make_preview_app(lambda: "http://127.0.0.1:7", get_status=lambda: status)
    reply = TestClient(app).get("/")
    assert reply.status_code == 502
    assert reply.json()["preview"] == status
    assert "Vite" not in reply.text


def test_pending_start_does_not_block_control_requests(tmp_path, monkeypatch):
    _fixture(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def pending(self, ready_timeout_s=30):
        entered.set()
        release.wait(3)

    monkeypatch.setattr(UvicornSupervisor, "start", pending)
    sup = UvicornSupervisor(tmp_path)
    control = FastAPI()
    control.get("/health")(lambda: {"ok": True})

    def upstream():
        sup.retry_start()
        return sup.upstream()

    control.mount("/preview", make_preview_app(upstream, get_status=sup.status))
    try:
        with TestClient(control) as client:
            start = time.monotonic()
            assert client.get("/preview/").status_code == 502
            assert entered.wait(1)
            assert client.get("/health").json() == {"ok": True}
            assert time.monotonic() - start < 1
    finally:
        release.set()
        if sup._retry_thread:
            sup._retry_thread.join(1)


def test_status_does_not_seed_and_retry_refuses_a_stale_app(tmp_path, monkeypatch):
    from sage.orchestrator import app as appmod

    from .test_switch_app import _orch

    orch, _oc, _root = _orch(tmp_path)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)
    response = client.get("/api/preview/status")
    assert response.status_code == 200
    assert response.json()["state"] == "empty"
    assert not orch._wm.app_path.exists()
    assert appmod._preview_platform() is None
    retry = client.post("/api/preview/retry?appId=not-the-selected-app")
    assert retry.status_code == 409
    assert not orch._wm.app_path.exists()


def test_status_and_stop_do_not_wait_for_process_launch(tmp_path, monkeypatch):
    _fixture(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def delayed_port_check(self, port):
        entered.set()
        release.wait(2)

    monkeypatch.setattr(UvicornSupervisor, "_clear_stale_port", delayed_port_check)
    sup = UvicornSupervisor(tmp_path)
    sup.retry_start()
    try:
        assert entered.wait(1)
        before = time.monotonic()
        assert sup.status()["state"] == "starting"
        sup.stop()
        assert time.monotonic() - before < 0.5
    finally:
        release.set()
        sup._retry_thread.join(3)
        sup.stop()
    assert sup._proc is None


def _generation_fixture(root, supervisor):
    if supervisor is UvicornSupervisor:
        _fixture(root)
    else:
        (root / 'package.json').write_text('{}')
        (root / 'src').mkdir()
        (root / 'src/App.tsx').write_text('// app')
    return supervisor(root)


@pytest.mark.parametrize('supervisor', [ViteSupervisor, UvicornSupervisor])
def test_retry_reservation_is_single_use_and_crash_gets_a_new_generation(tmp_path, monkeypatch, supervisor):
    from .test_a_dead_preview_does_not_take_the_session_with_it import _Pipe, _Proc

    sup = _generation_fixture(tmp_path, supervisor)
    launched = []
    def launch(command, env, port, generation):
        launched.append(generation)
        sup._proc = _Proc(_Pipe([]))
        sup._upstream = 'http://127.0.0.1:7'
        sup._state = 'ready'
        sup._ready.set()
        sup._settled.set()
    monkeypatch.setattr(sup, '_launch', launch)
    try:
        sup.start(ready_timeout_s=1)  # direct start still allocates its own generation
        assert sup.retry_start(explicit=True)
        sup._retry_thread.join(2)
        assert not sup._retry_thread.is_alive()
        sup._read_output(sup._proc)  # actual crash-reader -> _spawn(previous=proc)
        assert sup.retry_start(explicit=True)
        sup._retry_thread.join(2)
        assert not sup._retry_thread.is_alive()
        assert launched == [1, 2, 3, 4]
        assert sup.status()['generation'].endswith(':4')
    finally:
        sup.stop()


@pytest.mark.parametrize('supervisor', [ViteSupervisor, UvicornSupervisor])
def test_stop_before_reserved_spawn_is_not_undone_by_the_retry_thread(tmp_path, monkeypatch, supervisor):
    sup = _generation_fixture(tmp_path, supervisor)
    entered, release = threading.Event(), threading.Event()
    launched = []
    start = sup.start
    def delayed_start():
        entered.set()
        assert release.wait(2)
        return start(ready_timeout_s=1)
    monkeypatch.setattr(sup, 'start', delayed_start)
    monkeypatch.setattr(sup, '_launch', lambda *args: launched.append(args))
    try:
        assert sup.retry_start(explicit=True)
        assert entered.wait(1)
        generation = sup.status()['generation']
        sup.stop()
        release.set()
        sup._retry_thread.join(2)
        assert not sup._retry_thread.is_alive()
        assert launched == []
        assert sup.status()['generation'] == generation
        assert sup.status()['state'] == 'failed'
        assert sup._stopped
    finally:
        release.set()
        sup._retry_thread.join(2)
        sup.stop()


def test_real_uvicorn_restarts_wait_for_their_own_listener_to_close(tmp_path, monkeypatch):
    import socket

    _fixture(tmp_path)
    (tmp_path / 'app.py').write_text(_APP + '''
import asyncio
from pathlib import Path
@app.get('/slow')
async def slow():
    Path('request-started').write_text('started')
    await asyncio.sleep(0.8)
    return {'ok': True}
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv('SAGE_PREVIEW_PORT', str(port))
    sup = UvicornSupervisor(tmp_path)
    requests = []
    old_processes = []
    def slow_request():
        try:
            httpx.get(f'http://127.0.0.1:{port}/slow', timeout=3)
        except httpx.HTTPError:
            pass  # Restart can cancel the in-flight client; the listener must still be retired.
    try:
        sup.start(ready_timeout_s=8)
        for _ in range(3):
            marker = tmp_path / 'request-started'
            marker.unlink(missing_ok=True)
            client = threading.Thread(target=slow_request)
            requests.append(client)
            client.start()
            _wait(marker.exists, timeout=3)
            old_processes.append(sup._proc)
            before = time.monotonic()
            assert sup.retry_start(explicit=True)
            reserved = sup.status()['generation']
            assert time.monotonic() - before < 0.5, 'Retry and status must not wait for port release'
            sup._retry_thread.join(8)
            assert not sup._retry_thread.is_alive()
            status = sup.status()
            assert status['state'] == 'ready', status
            assert status['generation'] == reserved, status
            assert not any('Address already in use' in line for line in status['output'])
            assert httpx.get(sup.upstream(), timeout=1).status_code == 200
            client.join(3)
    finally:
        sup.stop()
        if sup._retry_thread:
            sup._retry_thread.join(3)
        for client in requests:
            client.join(3)
        for process in old_processes:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
