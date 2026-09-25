"""A preview whose server will not start must cost the session nothing.

Measured on a live workspace 2026-09-24, on a fastapi-antd app whose `app.py` would not import.
Two separate faults, and the first one hid the second:

1. **uvicorn's URL banner is printed before the app is imported.** Its reloader prints
   "Uvicorn running on <url>" and only then does the child try `import app`. So a missing or broken
   app printed a URL, the supervisor read it as ready, `start()` returned SUCCESS, and the proxy
   forwarded to a socket nothing was serving. Vite has no such gap — it prints its URL once it is
   already serving — which is why only the fastapi-antd stack showed this.

2. **`start()` blocks 30 s and then raises, and it was called from the request path.** The pane
   re-polls about once a second, so each poll took a request thread for the whole timeout.
   Consecutive `/preview/` entries in that workspace's log sat 29,999 ms and 29,302 ms apart, and
   while it ran Chat and the model drawer stopped answering although the session looked alive.

And a third thing that made both invisible: `uvicorn --reload` on an app that will not import does
NOT exit. It sits watching for a file change. So the bounded restart loop never ran — `_restarts`
stayed 0 — and `_last_error`, which is only written when the process exits, stayed None. Nothing
anywhere could say why the pane was empty.
"""
import threading
import time

import pytest

from sage.preview.supervisor import UvicornSupervisor, ViteSupervisor


class _Pipe:
    """Stands in for a process's stdout: yields lines, then optionally blocks like a live one."""

    def __init__(self, lines, hang=False):
        self._lines = list(lines)
        self._hang = hang
        self.returncode = 0

    def __iter__(self):
        yield from self._lines
        while self._hang:                       # a reloader that fails the import and sits there
            time.sleep(0.01)

    def wait(self):
        return 1


class _Proc:
    def __init__(self, pipe):
        self.stdout = pipe

    def wait(self):
        return self.stdout.wait()

    def poll(self):
        return 1


_UVICORN_BANNER = "INFO:     Uvicorn running on http://127.0.0.1:8771 (Press CTRL+C to quit)"
_UVICORN_SERVING = "INFO:     Application startup complete."
_IMPORT_FAILED = 'ERROR:    Error loading ASGI app. Could not import module "app".'


def _read(sup, lines, hang=False):
    """Run the supervisor's own output reader over `lines`, as a spawn would."""
    proc = _Proc(_Pipe(lines, hang=hang))
    t = threading.Thread(target=sup._read_output, args=(proc,), daemon=True)
    t.start()
    t.join(timeout=2 if not hang else 0.3)
    return t


def test_uvicorns_banner_alone_is_not_taken_as_ready(tmp_path):
    """Fault 1. The banner is printed before the import is even attempted."""
    sup = UvicornSupervisor(tmp_path, "")
    sup._stopped = True                          # no respawn; this is about the reading
    _read(sup, [_UVICORN_BANNER + "\n", _IMPORT_FAILED + "\n"], hang=True)
    assert not sup._ready.is_set(), "the banner was read as a working server"
    with pytest.raises(RuntimeError):
        sup.upstream()


def test_uvicorn_is_ready_once_it_says_it_is_serving(tmp_path):
    """The other half of fault 1: a HEALTHY app must still come up."""
    sup = UvicornSupervisor(tmp_path, "")
    sup._stopped = True
    _read(sup, [_UVICORN_BANNER + "\n", _UVICORN_SERVING + "\n"], hang=True)
    assert sup._ready.is_set()
    assert sup.upstream() == "http://127.0.0.1:8771"


def test_vite_still_becomes_ready_on_its_url_line(tmp_path):
    """Vite prints its URL only when serving, so it keeps the behaviour it had. No `_READY_LINE`."""
    sup = ViteSupervisor(tmp_path, "")
    sup._stopped = True
    _read(sup, ["  ->  Local:   http://localhost:5173/\n"], hang=True)
    assert sup._ready.is_set()
    assert sup.upstream() == "http://localhost:5173"


def test_a_server_that_printed_a_url_and_then_died_leaves_no_address_behind(tmp_path):
    """`upstream()` must not hand the proxy a socket nothing is listening on."""
    sup = ViteSupervisor(tmp_path, "")
    sup._stopped = True
    _read(sup, ["  ->  Local:   http://localhost:5173/\n"])   # pipe closes -> process exited
    with pytest.raises(RuntimeError):
        sup.upstream()


def test_retry_start_does_not_block_the_caller(tmp_path, monkeypatch):
    """Fault 2, the one that took the session down. `start()` may block; `retry_start()` may not."""
    started = threading.Event()

    def slow_start(self, ready_timeout_s=30.0):
        started.set()
        time.sleep(5)                            # stands in for the 30 s timeout
        raise RuntimeError("uvicorn failed to start (timed out)")

    monkeypatch.setattr(UvicornSupervisor, "start", slow_start)
    sup = UvicornSupervisor(tmp_path, "")
    t0 = time.monotonic()
    sup.retry_start()
    assert time.monotonic() - t0 < 0.5, "retry_start waited for the server"
    assert started.wait(2), "retry_start never actually tried"


def test_retry_start_never_raises_even_though_start_does(tmp_path, monkeypatch):
    monkeypatch.setattr(UvicornSupervisor, "start",
                        lambda self, ready_timeout_s=30.0: (_ for _ in ()).throw(RuntimeError("no")))
    sup = UvicornSupervisor(tmp_path, "")
    sup.retry_start()
    sup._retry_thread.join(2)                    # the exception must die on that thread


def test_one_broken_app_does_not_spawn_a_server_per_poll(tmp_path, monkeypatch):
    """The pane polls about once a second. Without a floor, every poll was a fresh server."""
    calls = []
    monkeypatch.setattr(UvicornSupervisor, "start",
                        lambda self, ready_timeout_s=30.0: calls.append(1))
    sup = UvicornSupervisor(tmp_path, "")
    for _ in range(20):                          # twenty polls, well inside `_RETRY_EVERY_S`
        sup.retry_start()
        if sup._retry_thread:
            sup._retry_thread.join(1)
    assert len(calls) == 1, f"rate limit let {len(calls)} starts through"


def test_a_stopped_supervisor_is_not_restarted_from_the_request_path(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(UvicornSupervisor, "start",
                        lambda self, ready_timeout_s=30.0: calls.append(1))
    sup = UvicornSupervisor(tmp_path, "")
    sup.stop()
    sup.retry_start()
    assert calls == []
