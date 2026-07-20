"""OpenCode server supervisor (DESIGN Seam 3, driving contract).

Spawns one `opencode serve` per container (sessions are scoped by location.directory, so one
server handles all project workspaces — matches D9). Discovers the server URL from stdout.
Mirrors ViteSupervisor's spawn/discover/stop shape.
"""
from __future__ import annotations

import re
import signal
import subprocess
import threading
from pathlib import Path

# "opencode server listening on http://127.0.0.1:4096"
_LISTEN_RE = re.compile(r"listening on\s+(https?://[^\s]+)")


def parse_server_url(line: str) -> str | None:
    m = _LISTEN_RE.search(line)
    return m.group(1).rstrip("/") if m else None


class OpenCodeServer:
    def __init__(self, cwd: Path, port: int = 0) -> None:
        self._cwd = Path(cwd)
        self._port = port
        self._proc: subprocess.Popen | None = None
        self._url: str | None = None
        self._ready = threading.Event()

    def start(self, ready_timeout_s: float = 30.0) -> str:
        self._proc = subprocess.Popen(
            ["npx", "opencode", "serve", "--port", str(self._port), "--hostname", "127.0.0.1"],
            cwd=self._cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        threading.Thread(target=self._read, args=(self._proc,), daemon=True).start()
        if not self._ready.wait(timeout=ready_timeout_s):
            self.stop()
            raise TimeoutError("opencode serve did not report a URL")
        assert self._url is not None
        return self._url

    def url(self) -> str:
        if self._url is None:
            raise RuntimeError("opencode server not ready")
        return self._url

    def _read(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            if self._url is None and (u := parse_server_url(line)):
                self._url = u
                self._ready.set()

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            import os

            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                self._proc.terminate()
