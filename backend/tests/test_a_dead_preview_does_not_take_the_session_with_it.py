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


def _built_app(path):
    (path / "package.json").write_text("{}")
    (path / "src").mkdir()
    (path / "src/App.tsx").write_text("// app")


def _read(sup, lines, hang=False):
    """Run the supervisor's own output reader over `lines`, as a spawn would.

    `sup._proc = proc` is not decoration: `_read_output` returns early when the process it was
    given is no longer the current one, so an old generation cannot restart over a newer one. A
    fake that skips that assignment emits a state `_spawn` never produces, and the reader then
    takes the early return in every test.
    """
    proc = _Proc(_Pipe(lines, hang=hang))
    sup._proc = proc
    sup._entry_serves = lambda url: True  # HTTP readiness is covered by the real Uvicorn fixture
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
    sup._stopped = False
    _read(sup, [_UVICORN_BANNER + "\n", _UVICORN_SERVING + "\n"], hang=True)
    assert sup._ready.is_set()
    assert sup.upstream() == "http://127.0.0.1:8771"


def test_vite_still_becomes_ready_on_its_url_line(tmp_path):
    """Vite prints its URL only when serving, so it keeps the behaviour it had. No `_READY_LINE`."""
    sup = ViteSupervisor(tmp_path, "")
    sup._stopped = False
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


# --- A failed start must not retire the supervisor --------------------------------------------
#
# This is what made the live 30 seconds, and the first fix missed it. `start()`'s failure path
# called `self.stop()`, which sets `_stopped`; nothing ever cleared it. So on every later start the
# reader skipped BOTH of its branches, `_ready` was never set, and the call waited out the whole
# timeout. `get_upstream()` is called straight from `async def http_proxy` with no threadpool hop,
# so those 30 s block the event loop itself — every route on the control app, not just the pane.


def _failing_spawn(self, **_kwargs):
    """A spawn whose process exits at once with no output, wired like the real one."""
    self._ready.clear()
    self._upstream = None
    proc = _Proc(_Pipe([]))
    self._proc = proc
    threading.Thread(target=self._read_output, args=(proc,), daemon=True).start()


def test_a_failed_start_does_not_retire_the_supervisor(tmp_path, monkeypatch):
    _built_app(tmp_path)                                # an app exists; the server is what fails
    monkeypatch.setattr(ViteSupervisor, "_spawn", _failing_spawn)
    monkeypatch.setattr(ViteSupervisor, "_kill", lambda self: None)
    sup = ViteSupervisor(tmp_path, "")

    with pytest.raises(RuntimeError):
        sup.start(ready_timeout_s=3)
    assert not sup._stopped, "a failed start marked the supervisor stopped, and nothing clears it"

    t0 = time.monotonic()
    with pytest.raises(RuntimeError) as second:
        sup.start(ready_timeout_s=3)
    assert time.monotonic() - t0 < 2.5, "the second start waited out the timeout"
    assert "max restarts reached" in str(second.value)
    assert "timed out" not in str(second.value), "the reader's branches were skipped again"


def test_the_pane_can_still_come_back_after_a_failure(tmp_path, monkeypatch):
    """`retry_start` yields to `_stopped`, which is right for an owner who stopped the preview and
    was wrong when a failed start set it — the pane could then never recover."""
    _built_app(tmp_path)
    monkeypatch.setattr(ViteSupervisor, "_spawn", _failing_spawn)
    monkeypatch.setattr(ViteSupervisor, "_kill", lambda self: None)
    sup = ViteSupervisor(tmp_path, "")
    with pytest.raises(RuntimeError):
        sup.start(ready_timeout_s=3)

    tried = []
    monkeypatch.setattr(ViteSupervisor, "start",
                        lambda self, ready_timeout_s=30.0: tried.append(1))
    sup.retry_start(explicit=True)
    if sup._retry_thread:
        sup._retry_thread.join(2)
    assert tried == [1], "the preview was retired permanently by one failure"


def test_an_owner_who_stops_the_preview_is_still_obeyed(tmp_path, monkeypatch):
    """The other half: `_stopped` must still mean something after the above."""
    _built_app(tmp_path)
    tried = []
    monkeypatch.setattr(ViteSupervisor, "start",
                        lambda self, ready_timeout_s=30.0: tried.append(1))
    sup = ViteSupervisor(tmp_path, "")
    sup.stop()
    sup.retry_start()
    assert tried == []


def test_an_old_reader_does_not_respawn_over_a_newer_server(tmp_path, monkeypatch):
    """Only reachable once a failed start stopped retiring the supervisor, which is why it arrives
    with that fix: a timed-out server killed and immediately restarted leaves the old reader alive,
    and its respawn's `_clear_stale_port` reaps the live one it knows nothing about."""
    sup = ViteSupervisor(tmp_path, "")
    spawns = []
    monkeypatch.setattr(ViteSupervisor, "_spawn", lambda self: spawns.append(1))
    stale = _Proc(_Pipe([]))
    sup._proc = _Proc(_Pipe([]))            # a newer generation is the current one
    sup._read_output(stale)                 # the old reader reaches the end of its pipe
    assert spawns == [], "a retired generation restarted over the current server"


def test_a_start_revives_a_supervisor_that_was_stopped(tmp_path, monkeypatch):
    """`start()` is a fresh attempt, whatever left the flag set.

    Its failure path no longer calls `stop()`, so nothing in the product sets `_stopped` and then
    starts again — but the reader skips BOTH of its branches while it is set, which is what made
    every start wait out the full timeout. The reset is what makes `start()` mean start, so it is
    asserted here rather than left resting on the failure path staying as it is now.
    """
    _built_app(tmp_path)
    monkeypatch.setattr(ViteSupervisor, "_spawn", _failing_spawn)
    monkeypatch.setattr(ViteSupervisor, "_kill", lambda self: None)
    sup = ViteSupervisor(tmp_path, "")
    sup.stop()                                   # the owner stopped it; now something starts it

    t0 = time.monotonic()
    with pytest.raises(RuntimeError) as e:
        sup.start(ready_timeout_s=3)
    assert time.monotonic() - t0 < 2.5, "the reader's branches were skipped; this waited out"
    assert "timed out" not in str(e.value)
