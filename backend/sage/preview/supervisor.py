"""Preview process supervisor (SPEC C1, PLAN 3.4).

Spawns the generated app's own server for a workspace, DISCOVERS its actual port, exposes
`upstream()` for the preview proxy, restarts on crash (bounded), and cleans up its process group
on stop. `UvicornSupervisor` runs a fastapi-antd app's own server with reload (#490) — the only
stack Sage carries, so the only supervisor.

Deep module, narrow interface: start() / upstream() / stop(). How the port is discovered
(parsing the server's own "listening" line) and how the process group is torn down is hidden.
"""
from __future__ import annotations

import collections
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..platform.auth import TokenSource

log = logging.getLogger("sage.preview.supervisor")

# uvicorn prints e.g.  "INFO:     Uvicorn running on http://127.0.0.1:5173 (Press CTRL+C to quit)"
_UVICORN_RE = re.compile(r"Uvicorn running on (https?://[^\s/]+)")


def parse_uvicorn_url(line: str) -> str | None:
    """Pure helper: extract the base URL from uvicorn's 'running on' line, else None."""
    m = _UVICORN_RE.search(line)
    return m.group(1) if m else None


def _free_port() -> int:
    """A port nothing is listening on right now. uvicorn has no auto-increment, so it is told
    one rather than left to collide with a neighbour Sage instance's own preview."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class UvicornSupervisor:
    """A fastapi-antd app's own server, run with reload, for the preview (#490).

    The app is served by the interpreter running Sage — the one interpreter here that is known to
    carry fastapi and uvicorn — with `SAGE_PREVIEW=1`, which `sage_serve.py` stamps into the page
    so the helpers know to reach the builder. `--reload` restarts the server when a `.py` file
    changes; static files are read per request and need no restart at all.

    It serves at the root, so the proxy prepends nothing: `mount_base` is "".
    """

    # The server's own name, for the sentences a failure to start carries.
    _NAME = "uvicorn"
    _parse_url = staticmethod(parse_uvicorn_url)

    def mount_base(self) -> str:
        return ""

    def __init__(self, workspace: Path, base_prefix: str = "", max_restarts: int = 3,
                 token_source: TokenSource | None = None) -> None:
        self._workspace = Path(workspace)
        self._base_prefix = base_prefix  # unused by this stack; kept for the proxy's uniform call
        self._max_restarts = max_restarts
        # For the child's own `sage_domino.py`/`sage_queries.py` to reach the platform without a
        # sidecar (ONE-APP-PLAN.md §2.4) — a laptop preview has no sidecar at localhost:8899 at all.
        self._token_source = token_source
        self._proc: subprocess.Popen | None = None
        self._upstream: str | None = None
        self._ready = threading.Event()
        self._restarts = 0
        self._stopped = False
        self._last_error: str | None = None
        self._tail: collections.deque[str] = collections.deque(maxlen=40)  # recent server output

    def start(self, ready_timeout_s: float = 30.0) -> str:
        """Spawn the server and block until its port is discovered. Returns the upstream base URL.

        Raises RuntimeError (with the server's own recent output) if it exits before reporting a
        port, so the failure isn't an opaque assertion upstream.
        """
        self._spawn()
        timed_out = not self._ready.wait(timeout=ready_timeout_s)
        if self._upstream is None:
            self.stop()
            why = f"timed out after {ready_timeout_s}s" if timed_out else (self._last_error or "exited early")
            tail = "\n".join(self._tail)
            raise RuntimeError(f"{self._NAME} failed to start ({why}). Recent output:\n{tail}")
        return self._upstream

    def upstream(self) -> str:
        """Current server base URL for the proxy. Raises until the server is ready."""
        if self._upstream is None:
            raise RuntimeError(f"{self._NAME} not ready")
        return self._upstream

    def stop(self) -> None:
        self._stopped = True
        self._kill()

    # --- internals ---

    def _spawn(self) -> None:
        self._ready.clear()
        self._upstream = None
        port = _free_port()
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port),
             "--reload", "--reload-dir", ".", "--log-level", "info"],
            cwd=self._workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            env={**os.environ, "SAGE_PREVIEW": "1", **self._platform_env()},
        )
        threading.Thread(target=self._read_output, args=(self._proc,), daemon=True).start()

    def _platform_env(self) -> dict[str, str]:
        """What this project's `sage_domino.py`/`sage_queries.py` need to reach the platform.

        `DOMINO_API_HOST` is set explicitly rather than left to `**os.environ` above so a laptop
        (which has no such variable in its own shell) gets it too — `TokenSource` already resolved
        it from Settings or the injected env, so this is the same host either way. `SAGE_DOMINO_TOKEN`
        is only set for a `static` source: a `sidecar` source means a real sidecar is reachable at
        localhost:8899 inside this same container, which is what the app already reads by default.
        """
        ts = self._token_source
        if ts is None:
            return {}
        env: dict[str, str] = {}
        if ts.api_host:
            env["DOMINO_API_HOST"] = ts.api_host
        if ts.kind == "static":
            env["SAGE_DOMINO_TOKEN"] = ts.bearer()
        return env

    def _read_output(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self._tail.append(line.rstrip())
            if self._upstream is None and (url := self._parse_url(line)):
                self._upstream = url
                self._ready.set()
        # stdout closed -> process exited. Restart unless we asked it to stop.
        code = proc.wait()
        if not self._stopped and self._restarts < self._max_restarts:
            self._restarts += 1
            self._last_error = f"{self._NAME} exited (code {code}); restart {self._restarts}/{self._max_restarts}"
            self._spawn()
        elif not self._stopped:
            self._last_error = f"{self._NAME} exited (code {code}); max restarts reached"
            self._ready.set()  # unblock start() so it can surface the failure

    def _kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                self._proc.terminate()
