"""Vite process supervisor (SPEC C1, PLAN 3.4).

Spawns the generated app's Vite dev server for a workspace, DISCOVERS its actual port (Vite
may auto-increment), exposes `upstream()` for the preview proxy, restarts on crash (bounded),
and cleans up its process group on stop.

Deep module, narrow interface: start() / upstream() / stop(). How the port is discovered
(parsing Vite's "Local:" line) and how the process group is torn down is hidden.
"""
from __future__ import annotations

import logging
import re
import signal
import subprocess
import threading
from pathlib import Path

log = logging.getLogger("sage.preview.supervisor")

# Vite prints e.g.  "  ➜  Local:   http://localhost:5173/"
_LOCAL_RE = re.compile(r"Local:\s+(https?://[^\s/]+)")

# Vite's default dev server port (before auto-increment). A leftover process from a prior
# session that was killed without going through stop() can squat here on one address family
# (e.g. IPv6-only) while a fresh Vite grabs the other, so "localhost" nondeterministically
# resolves to the stale one. Clearing it before every spawn keeps that from happening.
_DEFAULT_PORT = 5173


def parse_vite_url(line: str) -> str | None:
    """Pure helper: extract the base URL from a Vite 'Local:' line, else None."""
    m = _LOCAL_RE.search(line)
    return m.group(1) if m else None


class ViteSupervisor:
    def __init__(self, workspace: Path, max_restarts: int = 3) -> None:
        self._workspace = Path(workspace)
        self._max_restarts = max_restarts
        self._proc: subprocess.Popen | None = None
        self._upstream: str | None = None
        self._ready = threading.Event()
        self._restarts = 0
        self._stopped = False
        self._last_error: str | None = None

    def start(self, ready_timeout_s: float = 30.0) -> str:
        """Spawn Vite and block until its port is discovered. Returns the upstream base URL."""
        self._spawn()
        if not self._ready.wait(timeout=ready_timeout_s):
            self.stop()
            raise TimeoutError(f"Vite did not report a port in {ready_timeout_s}s: {self._last_error}")
        assert self._upstream is not None
        return self._upstream

    def upstream(self) -> str:
        """Current Vite base URL for the proxy. Raises until the server is ready."""
        if self._upstream is None:
            raise RuntimeError("Vite not ready")
        return self._upstream

    def stop(self) -> None:
        self._stopped = True
        self._kill()

    # --- internals ---

    def _spawn(self) -> None:
        self._ready.clear()
        self._upstream = None
        self._clear_stale_port(_DEFAULT_PORT)
        # start_new_session -> own process group so we can kill Vite + any children (esbuild).
        self._proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=self._workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        threading.Thread(target=self._read_output, args=(self._proc,), daemon=True).start()

    def _read_output(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            if self._upstream is None and (url := parse_vite_url(line)):
                self._upstream = url
                self._ready.set()
        # stdout closed -> process exited. Restart unless we asked it to stop.
        code = proc.wait()
        if not self._stopped and self._restarts < self._max_restarts:
            self._restarts += 1
            self._last_error = f"Vite exited (code {code}); restart {self._restarts}/{self._max_restarts}"
            self._spawn()
        elif not self._stopped:
            self._last_error = f"Vite exited (code {code}); max restarts reached"
            self._ready.set()  # unblock start() so it can surface the failure

    def _kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                import os

                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                self._proc.terminate()

    def _clear_stale_port(self, port: int) -> None:
        """Reap any leftover process still listening on `port` from an unclean prior shutdown."""
        import os

        try:
            # -sTCP:LISTEN restricts to the actual server socket — plain `-ti tcp:{port}` also
            # matches client sockets (e.g. our own proxy's outgoing connections to Vite), which
            # let this kill the orchestrator's own process group when its pid was among them.
            pids = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=5
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
