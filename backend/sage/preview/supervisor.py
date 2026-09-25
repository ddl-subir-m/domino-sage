"""Preview process supervisor (SPEC C1, PLAN 3.4).

Spawns the generated app's dev server for a workspace, DISCOVERS its actual port, exposes
`upstream()` for the preview proxy, restarts on crash (bounded), and cleans up its process group
on stop. `ViteSupervisor` runs the react-vite template's Vite dev server; `UvicornSupervisor` runs a
fastapi-antd app's own server with reload (#490). `make_supervisor` picks by the app's stack.

Deep module, narrow interface: start() / upstream() / stop(). How the port is discovered
(parsing the server's own "listening" line) and how the process group is torn down is hidden.
"""
from __future__ import annotations

import collections
import errno
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx

from ..workspace.stack import preview_stack_of, resolve_stack

log = logging.getLogger("sage.preview.supervisor")

# Vite prints e.g.  "  ➜  Local:   http://localhost:5173/"
_LOCAL_RE = re.compile(r"Local:\s+(https?://[^\s/]+)")
# uvicorn prints e.g.  "INFO:     Uvicorn running on http://127.0.0.1:5173 (Press CTRL+C to quit)"
_UVICORN_RE = re.compile(r"Uvicorn running on (https?://[^\s/]+)")

# Vite's default dev server port (before auto-increment). A leftover process from a prior
# session that was killed without going through stop() can squat here on one address family
# (e.g. IPv6-only) while a fresh Vite grabs the other, so "localhost" nondeterministically
# resolves to the stale one. Clearing it before every spawn keeps that from happening.
_DEFAULT_PORT = 5173
_PORT_RELEASE_TIMEOUT_S = 3.0


def _probe_port(port: int) -> None:
    """Check both listener families; a graceful old child may still own its socket."""
    # BSD permits reusable wildcard and loopback listeners to coexist. Probe both addresses
    # used by our launch commands and template configs, not only the wildcard.
    for family, host in (
        (socket.AF_INET, "0.0.0.0"), (socket.AF_INET, "127.0.0.1"),
        (socket.AF_INET6, "::"), (socket.AF_INET6, "::1"),
    ):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind((host, port))
                # A reusable bind alone does not prove another process can listen here.
                probe.listen(1)
        except OSError as exc:
            if family == socket.AF_INET6 and exc.errno in (
                errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL, errno.ENODEV,
            ):
                continue  # IPv4-only hosts need no IPv6 listener.
            raise


def preview_port() -> int:
    """The port Vite is asked to start on. `SAGE_PREVIEW_PORT` overrides it.

    One machine can hold two Sage instances — a worktree under QA beside the checkout it is being
    compared against — and `_clear_stale_port` reaps whatever is LISTENING on this port before
    every spawn. Sharing the port aims that reaping at the OTHER instance's dev server, so the two
    take turns killing each other, silently: neither preview stays up, and the symptom is a preview
    that hangs rather than an error that names the cause. Measured on 2026-09-05, where it cost a
    UI comparison two false regressions before the collision was spotted in the log.

    Vite still auto-increments from here when the port is taken (`strictPort` is false), and the
    supervisor discovers the real port from Vite's own output either way. So this moves the
    starting point and changes nothing else about how the port is settled.
    """
    raw = os.environ.get("SAGE_PREVIEW_PORT", "").strip()
    if not raw:
        return _DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        # Falling back rather than raising: a typo here must not be the reason a build session
        # cannot open a preview, and the warning says which value was ignored.
        log.warning("preview: SAGE_PREVIEW_PORT=%r is not a number; using %d", raw, _DEFAULT_PORT)
        return _DEFAULT_PORT


def parse_vite_url(line: str) -> str | None:
    """Pure helper: extract the base URL from a Vite 'Local:' line, else None."""
    m = _LOCAL_RE.search(line)
    return m.group(1) if m else None


def parse_uvicorn_url(line: str) -> str | None:
    """Pure helper: extract the base URL from uvicorn's 'running on' line, else None."""
    m = _UVICORN_RE.search(line)
    return m.group(1) if m else None


def make_supervisor(workspace: Path, base_prefix: str = "") -> ViteSupervisor:
    """Pick from the shared stack resolution. Unresolved apps get an inert supervisor;
    `start` refuses them with the resolver's reason before it can spawn a process."""
    stack = preview_stack_of(Path(workspace))
    if stack is not None and stack.preview == "uvicorn":
        return UvicornSupervisor(workspace, base_prefix)
    return ViteSupervisor(workspace, base_prefix)


class ViteSupervisor:
    # What the proxy prepends to a path before forwarding: Vite serves at `<prefix>/preview` because
    # that base is baked into what it emits (`vite.config.ts`), so the proxy has to land there.
    # A server that serves at the root answers "".
    def mount_base(self) -> str:
        return f"{self._base_prefix}/preview"

    # The server's own name, for the sentences a failure to start carries.
    _NAME = "Vite dev server"
    _parse_url = staticmethod(parse_vite_url)

    def __init__(self, workspace: Path, base_prefix: str = "", max_restarts: int = 3) -> None:
        self._workspace = Path(workspace)
        self._base_prefix = base_prefix  # baked into Vite's `base`/HMR via SAGE_BASE_PREFIX
        self._max_restarts = max_restarts
        self._proc: subprocess.Popen | None = None
        self._upstream: str | None = None
        self._ready = threading.Event()
        self._settled = threading.Event()
        self._state_lock = threading.RLock()
        self._instance = uuid.uuid4().hex[:12]
        self._generation = 0
        self._attempt = 0
        self._requested = False
        self._state = "starting"
        self._restarts = 0
        self._stopped = False
        self._last_error: str | None = None
        self._tail: collections.deque[str] = collections.deque(maxlen=40)  # recent Vite output
        self._retry_lock = threading.RLock()
        self._retry_thread: threading.Thread | None = None
        self._retry_generation: int | None = None

    def status(self) -> dict:
        """A bounded snapshot independent of the URL. A reload parent can live without a child."""
        resolution = resolve_stack(self._workspace)
        with self._state_lock:
            state, error = self._state, self._last_error
            if not resolution.ready:
                state = "empty" if resolution.state == "empty" else "failed"
                error = resolution.reason
            return {
                "appId": self._workspace.name,
                "generation": f"{self._instance}:{self._generation}",
                "server": self._NAME if resolution.stack is not None else None,
                "state": state, "attempt": self._attempt, "error": error,
                "output": self.recent_output(),
            }

    def start(self, ready_timeout_s: float = 30.0) -> str:
        """Spawn Vite and block until its port is discovered. Returns the upstream base URL.

        Raises RuntimeError (with Vite's own recent output) if Vite exits before reporting a port —
        e.g. an incompatible Node version — so the failure isn't an opaque assertion upstream.
        """
        with self._state_lock:
            # Stop can arrive between scheduling a retry thread and that thread entering here.
            if threading.current_thread() is self._retry_thread and self._stopped:
                raise RuntimeError("Preview stopped.")
            resolution = resolve_stack(self._workspace)
            self._requested = True
            self._attempt += 1
            if not resolution.ready:
                self._last_error = resolution.reason
                self._state = "empty" if resolution.state == "empty" else "failed"
                raise RuntimeError(self._last_error)
            self._stopped = False
            self._restarts = 0
            self._last_error = None
            self._tail.clear()
            self._settled.clear()
            self._kill()
        try:
            self._spawn()
        except OSError as exc:
            with self._state_lock:
                self._state = "failed"
                self._last_error = f"Could not start {self._NAME}: {exc}"
                self._settled.set()
            raise RuntimeError(self._last_error) from exc
        timed_out = not self._settled.wait(timeout=ready_timeout_s)
        if self._upstream is None:
            # `_kill()`, not `stop()`: this attempt is over, the supervisor is not. `stop()` is the
            # owner's verb and its `_stopped` is what `retry_start` reads to know it may not
            # restart. Using it here retired the preview permanently on its first failure.
            # A failed Uvicorn child leaves its reloader watching. Keep it so a corrected file
            # can recover without a new process. A silent timeout has no such recovery evidence.
            if timed_out:
                self._kill()
            why = f"timed out after {ready_timeout_s}s" if timed_out else (self._last_error or "exited early")
            tail = "\n".join(self._tail)
            # Record it as well as raise. The restart loop sets `_last_error` when the process
            # EXITS, and a uvicorn started with `--reload` on an app that will not import does not
            # exit — measured 2026-09-24 (#554): it prints its banner, fails the import, and then sits
            # watching for a file change, so `_restarts` stayed 0 and nothing was ever recorded.
            # Whoever has to tell the person why the pane is empty reads `last_error()`.
            if self._last_error is None:
                self._last_error = f"{self._NAME} failed to start ({why})"
            self._state = "failed"
            raise RuntimeError(f"{self._NAME} failed to start ({why}). Recent output:\n{tail}")
        return self._upstream

    def last_error(self) -> str | None:
        """Why the server is not up, for a caller that has to tell somebody. None while healthy."""
        return self._last_error

    def recent_output(self, lines: int = 20) -> list[str]:
        """The server's own last words. The reason a start failed is almost always in here."""
        return list(self._tail)[-lines:]

    def retry_start(self, *, explicit: bool = False) -> bool:
        """Ask for a restart WITHOUT waiting for it, and without raising.

        `start()` blocks up to `ready_timeout_s` (30 s) and then raises. The preview proxy calls it
        on the request path, and the pane polls about once a second, so one app that would not
        import put a 30-second block on a request thread per poll until the threads ran out.
        Measured 2026-09-24 (#554) on a live workspace: consecutive `/preview/` requests 29,999 ms and
        29,302 ms apart, and while that ran Chat and the model drawer stopped answering although
        the session still looked alive.

        The preview is a pane. It may not take the session down with it — so the caller is told
        nothing and waits for nothing, and the work happens on a thread nobody is holding.
        """
        with self._retry_lock, self._state_lock:
            if self._stopped and not explicit:
                return False
            if self._retry_thread is not None and self._retry_thread.is_alive():
                return False
            # One automatic request opens a bounded crash/restart attempt. Later polls only read
            # its result; they cannot open an unlimited series of fresh budgets. Retry is explicit.
            if self._requested and not explicit:
                return False
            self._requested = True
            self._stopped = False
            self._retry_generation = None
            if explicit:
                # Retire the old serving generation before returning to the caller. A validation
                # or Retry must not mistake the previous ready child for the new attempt.
                self._kill()
                self._retry_generation = self._begin_generation()
                self._last_error = None
                self._tail.clear()
            # A new attempt, not a continuation of the last crash loop — otherwise an app that is
            # FIXED after `_max_restarts` is reached could never come back without a Project reopen.
            self._restarts = 0
            self._retry_thread = threading.Thread(target=self._retry, daemon=True)
            self._retry_thread.start()
            return True

    def _retry(self) -> None:
        # Nothing may escape: this runs on a thread nobody is waiting on, and `start()` raises by
        # design. The reason is already on `_last_error`, where `last_error()` can find it.
        try:
            self.start()
        except Exception as exc:
            with self._state_lock:
                if not self._stopped:
                    self._state = "failed"
                    self._last_error = self._last_error or str(exc)[:1000]
            log.warning("preview: %s is not up: %s", self._NAME, self._last_error)

    def upstream(self) -> str:
        """Current server base URL for the proxy. Raises until the server is ready."""
        if self._upstream is None:
            raise RuntimeError(self._last_error or f"{self._NAME} not ready")
        return self._upstream

    def stop(self) -> None:
        with self._state_lock:
            self._stopped = True
            self._kill()
            self._state = "failed"
            self._last_error = "Preview stopped. Select Retry to start it."
            self._settled.set()

    # --- internals ---

    def _spawn(self, *, previous: subprocess.Popen | None = None) -> None:
        generation = self._spawn_generation(previous)
        if generation is None:
            return
        port = preview_port()
        # start_new_session -> own process group so we can kill Vite + any children (esbuild).
        # SAGE_BASE_PREFIX tells vite.config.ts the Domino proxy prefix to bake into `base`/HMR.
        self._launch(
            # `--port` after `--` so npm forwards it to Vite. Passed on the command line rather
            # than set in `vite.config.ts` because a workspace seeded from an older template never
            # re-seeds (#40) — the flag reaches those too, a config change would not.
            ["npm", "run", "dev", "--", "--port", str(port)],
            {**os.environ, "SAGE_BASE_PREFIX": self._base_prefix}, port, generation,
        )

    def _launch(self, command: list[str], env: dict, port: int, generation: int) -> None:
        # Never hold the status lock across lsof or Popen. Status and Stop are control requests;
        # an OS launch that takes seconds cannot hold them behind its work.
        self._clear_stale_port(port)
        if not self._wait_for_port_release(port, generation):
            return
        with self._state_lock:
            if self._stopped or generation != self._generation:
                return
        proc = subprocess.Popen(
            command,
            cwd=self._workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            env=env,
        )
        with self._state_lock:
            cancelled = self._stopped or generation != self._generation
            if not cancelled:
                self._proc = proc
        if cancelled:
            self._terminate(proc)
        threading.Thread(target=self._read_output, args=(proc,), daemon=True).start()

    def _wait_for_port_release(self, port: int, generation: int) -> bool:
        # SIGTERM is asynchronous. Wait on the background launch path, never in Stop or Retry.
        deadline = time.monotonic() + _PORT_RELEASE_TIMEOUT_S
        while True:
            with self._state_lock:
                if self._stopped or generation != self._generation:
                    return False
            try:
                _probe_port(port)
                return True
            except OSError as exc:
                if exc.errno != errno.EADDRINUSE:
                    raise
                if time.monotonic() >= deadline:
                    raise OSError(
                        errno.EADDRINUSE,
                        f"Preview port {port} is still in use after {_PORT_RELEASE_TIMEOUT_S:g}s",
                    ) from exc
            time.sleep(0.05)

    # A line that proves the server is SERVING, for a server whose URL line does not prove it.
    # Uvicorn's reloader prints "Uvicorn running on <url>" BEFORE the child imports the app, so a
    # missing or unimportable `app.py` prints the URL and then exits. Measured 2026-09-24 (#554) in an
    # empty directory: the banner, then `Error loading ASGI app. Could not import module "app"`,
    # then exit — so the URL alone cannot tell a live server from a dead one, and a dead
    # fastapi-antd app reported a SUCCESSFUL start and then 502'd every request. Vite prints its
    # URL only once it is serving, so it needs none and keeps the behaviour it had.
    _READY_LINE: str | None = None

    def _spawn_generation(self, previous: subprocess.Popen | None) -> int | None:
        """The scheduled retry's first child uses the generation it reserved synchronously."""
        with self._state_lock:
            if (previous is None and threading.current_thread() is self._retry_thread
                    and self._retry_generation is not None):
                generation, self._retry_generation = self._retry_generation, None
                return generation if not self._stopped and generation == self._generation else None
            return self._begin_generation(previous)

    def _begin_generation(self, previous: subprocess.Popen | None = None) -> int | None:
        with self._state_lock:
            if self._stopped or (previous is not None and previous is not self._proc):
                return None
            self._generation += 1
            self._ready.clear()
            self._upstream = None
            self._state = "starting"
            return self._generation

    def _entry_serves(self, url: str) -> bool:
        """Prove that the page endpoint answers; a banner is only a candidate address."""
        try:
            with httpx.Client(timeout=0.5, trust_env=False) as client:
                with client.stream("GET", f"{url}{self.mount_base()}/") as response:
                    return 200 <= response.status_code < 300
        except (httpx.HTTPError, OSError):
            return False

    def _confirm_ready(self, proc: subprocess.Popen, url: str, generation: int) -> None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with self._state_lock:
                if proc is not self._proc or generation != self._generation or self._stopped:
                    return
            served = self._entry_serves(url)
            with self._state_lock:
                if proc is not self._proc or generation != self._generation or self._stopped:
                    return
                if served:
                    self._upstream = url
                    self._state = "ready"
                    self._last_error = None
                    self._ready.set()
                    self._settled.set()
                    return
            time.sleep(0.1)
        with self._state_lock:
            if proc is self._proc and generation == self._generation and not self._stopped:
                self._state = "failed"
                self._last_error = self._last_error or f"{self._NAME} did not serve the page after startup."
                self._settled.set()

    def _read_output(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        pending: str | None = None  # a URL seen, not yet proven to be serving
        for line in proc.stdout:
            with self._state_lock:
                if proc is not self._proc:
                    break
                self._tail.append(line.rstrip()[:1000])
                if self._READY_LINE and ("Reloading..." in line or "Shutting down" in line):
                    self._begin_generation()
                # A failed reload child leaves the parent watching. Keep the final exception.
                if ("ERROR:" in line or re.match(r"^[\w.]+(?:Error|Exception):", line.strip())):
                    self._upstream = None
                    self._ready.clear()
                    self._state = "failed"
                    self._last_error = line.strip()[:1000]
                    self._settled.set()
                if pending is None and (url := self._parse_url(line)):
                    pending = url
                    if self._READY_LINE is None:
                        threading.Thread(target=self._confirm_ready,
                                         args=(proc, pending, self._generation), daemon=True).start()
                if (pending is not None and self._READY_LINE is not None
                        and self._READY_LINE in line):
                    threading.Thread(target=self._confirm_ready,
                                     args=(proc, pending, self._generation), daemon=True).start()
        # stdout closed -> process exited. Restart unless we asked it to stop.
        code = proc.wait()
        restart = False
        with self._state_lock:
            if proc is not self._proc:
                return
            self._upstream = None
            self._ready.clear()
            if not self._stopped and self._restarts < self._max_restarts:
                self._restarts += 1
                self._last_error = f"{self._NAME} exited (code {code}); restart {self._restarts}/{self._max_restarts}"
                restart = True
            elif not self._stopped:
                self._last_error = f"{self._NAME} exited (code {code}); max restarts reached"
                self._state = "failed"
                self._settled.set()
        if restart:
            try:
                self._spawn(previous=proc)
            except OSError as exc:
                with self._state_lock:
                    if proc is self._proc and not self._stopped:
                        self._state = "failed"
                        self._last_error = f"Could not restart {self._NAME}: {exc}"
                        self._settled.set()

    def _kill(self) -> None:
        with self._state_lock:
            proc, self._proc = self._proc, None
            self._upstream = None
            self._ready.clear()
        if proc:
            self._terminate(proc)

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            try:
                import os

                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()

    def _clear_stale_port(self, port: int) -> None:
        """Reap any leftover process still listening on `port` from an unclean prior shutdown."""
        import os

        try:
            # -sTCP:LISTEN restricts to the actual server socket — plain `-ti tcp:{port}` also
            # matches client sockets (e.g. our own proxy's outgoing connections to Vite), which
            # let this kill the orchestrator's own process group when its pid was among them.
            pids = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=5,
                check=False,  # lsof exits 1 when nothing is listening — the empty stdout is the answer
            ).stdout.split()
        except (OSError, subprocess.TimeoutExpired):
            return
        for pid in pids:
            try:
                os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, ValueError):
                continue
            else:
                log.warning("preview: killed stale process %s squatting on port %d", pid, port)


def _free_port() -> int:
    """A port nothing is listening on right now. uvicorn has no auto-increment, so it is told
    one rather than left to collide with the neighbour Vite would have stepped past."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class UvicornSupervisor(ViteSupervisor):
    """A fastapi-antd app's own server, run with reload, for the preview (#490).

    Same interface and the same restart loop as the Vite supervisor; what differs is the process.
    The app is served by the interpreter running Sage — the one interpreter here that is known to
    carry fastapi and uvicorn — with `SAGE_PREVIEW=1`, which `sage_serve.py` stamps into the page
    so the helpers know to reach the builder. `--reload` restarts the server when a `.py` file
    changes; static files are read per request and need no restart at all.

    It serves at the root, so the proxy prepends nothing: `mount_base` is "".
    """

    _NAME = "uvicorn"
    # See `_READY_LINE` on the base class: uvicorn's URL banner is printed by the RELOADER, before
    # the child has imported anything, so this is the first line that means the app itself is up.
    _READY_LINE = "Application startup complete"
    _parse_url = staticmethod(parse_uvicorn_url)

    def mount_base(self) -> str:
        return ""

    def _spawn(self, *, previous: subprocess.Popen | None = None) -> None:
        generation = self._spawn_generation(previous)
        if generation is None:
            return
        port = _free_port() if not os.environ.get("SAGE_PREVIEW_PORT", "").strip() else preview_port()
        self._launch(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port),
             "--reload", "--reload-dir", ".", "--log-level", "info"],
            {**os.environ, "SAGE_PREVIEW": "1"}, port, generation,
        )
