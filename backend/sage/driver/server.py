"""OpenCode server supervisor (DESIGN Seam 3, driving contract).

Spawns one `opencode serve` per container (sessions are scoped by location.directory, so one
server handles all project workspaces — matches D9). Discovers the server URL from stdout.
Mirrors ViteSupervisor's spawn/discover/stop shape.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
from pathlib import Path

# "opencode server listening on http://127.0.0.1:4096"
_LISTEN_RE = re.compile(r"listening on\s+(https?://[^\s]+)")

# Where `_install_opencode_config` puts the pack-voiced config. Kept in sync with app.py by this
# comment and the test that reads both.
_VOICED_CONFIG = Path(os.path.expanduser("~/.config/opencode/opencode.json"))


def parse_server_url(line: str) -> str | None:
    m = _LISTEN_RE.search(line)
    return m.group(1).rstrip("/") if m else None


class OpenCodeServer:
    def __init__(self, cwd: Path, port: int = 0, log_path: str | None = None) -> None:
        self._cwd = Path(cwd)
        self._port = port
        self._log_path = log_path or os.environ.get("SAGE_OPENCODE_LOG")
        self._proc: subprocess.Popen | None = None
        self._url: str | None = None
        self._ready = threading.Event()

    def _env(self) -> dict:
        """The environment `opencode` runs in. Shared with `cli()` below, which is the point: a
        diagnostic that asked a differently-configured OpenCode would answer about a different
        OpenCode.

        Both candidates are voiced copies today; the global one is preferred only because the
        orchestrator writes it on every boot. Whichever we point at must be a resolved copy — the
        checked-in source names the assistant and the nouns as `{assistantName}` / `{dataset}`
        tokens, deliberately left unresolved so a pack never writes its words into a repo file, and
        handing OpenCode that file makes it read the braces out loud to the user.
        """
        env = dict(os.environ)
        for cfg in (_VOICED_CONFIG, self._cwd / "opencode.json"):
            if cfg.exists():
                env["OPENCODE_CONFIG"] = str(cfg)
                break
        return env

    def cli(self, args: list[str], cwd: str | None = None, timeout_s: float = 30.0) -> str:
        """Run `opencode <args>` the way the server runs, and hand back what it said.

        For the question no log can answer. OpenCode's MCP client has no logger of its own — the
        only `mcp` names in the binary are CLI commands — so a server that never connected and one
        that connected fine print exactly the same nothing, at DEBUG as at INFO. `opencode mcp list`
        is the one thing that will say.

        `cwd` matters and defaults to the server's. Project config resolves off the git root of the
        directory OpenCode is run in, so asking from here answers about here — to ask what a Chat
        turn sees, ask from the Chat work directory.

        Output only, never raised: this exists to be read on a page when something is already wrong.
        """
        try:
            out = subprocess.run(["npx", "opencode", *args], cwd=cwd or str(self._cwd),
                                 env=self._env(), capture_output=True, text=True,
                                 timeout=timeout_s, check=False)
        except Exception as e:
            return f"{type(e).__name__}: {e}"
        return ((out.stdout or "") + (out.stderr or "")).strip() or f"(no output, exit {out.returncode})"

    def start(self, ready_timeout_s: float = 30.0) -> str:
        cmd = ["npx", "opencode", "serve", "--port", str(self._port), "--hostname", "127.0.0.1"]
        if self._log_path:
            cmd.append("--print-logs")
        # OpenCode says nothing about its MCP servers at the default level: it connects, or it
        # silently does not, and the log reads identically either way. That is the whole reason a
        # missing Live read took an evening to chase — `/api/diag` can now prove the config is right
        # and the server answers, and still cannot say why OpenCode never dialled it.
        #
        # Off by default because DEBUG is loud enough to push the interesting line out of any tail
        # worth reading. Set SAGE_OPENCODE_LOG_LEVEL=DEBUG in the workspace's environment, restart,
        # and read it back with /api/diag/opencode?q=mcp.
        level = os.environ.get("SAGE_OPENCODE_LOG_LEVEL", "").strip()
        if level:
            cmd += ["--log-level", level]
        # OpenCode resolves PROJECT config off the git root of the SESSION directory, NOT off this
        # cwd — measured live on 05fded1, see the reopened #199. Every Sage session runs under the
        # workspace volume (`.sage/chat-work` for Chat, `apps/<appId>/` for Build), so the project
        # root is always that volume and nothing beside us here is ever read as project config.
        # An earlier probe concluded otherwise because it used the server's own cwd as the session
        # directory, which is the one case where the two coincide.
        #
        # The cwd is still a dir of Sage's own rather than the repo, for two smaller reasons that
        # do hold: it keeps the unvoiced `/opt/sage/opencode.json` out of reach of any session that
        # did start there, and it keeps a checked-out worktree free of a spurious diff. What
        # actually carries the pack's words to OpenCode is the global copy, below. The dir may not
        # exist yet when the orchestrator was started without its boot path, and Popen will not
        # create a missing cwd.
        self._cwd.mkdir(parents=True, exist_ok=True)
        # OPENCODE_CONFIG loads a file as "custom config" — above global, below project. The
        # project slot is never ours (above), so these two ARE the mechanism rather than a belt:
        # without one of them OpenCode never sees the sage-gateway provider and drops to its
        # built-in free tier (429 FreeUsageLimitError), and without a VOICED one it reads the
        # pack's braces out loud.
        env = self._env()
        self._proc = subprocess.Popen(
            cmd,
            cwd=self._cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            env=env,
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
        logf = open(self._log_path, "a") if self._log_path else None  # noqa: SIM115
        for line in proc.stdout:
            if logf:
                logf.write(line)
                logf.flush()
            if self._url is None and (u := parse_server_url(line)):
                self._url = u
                self._ready.set()

    def stop(self, timeout_s: float = 5.0) -> None:
        """Stop the server and CONFIRM it died.

        SIGTERM is a request, not an outcome, and `start_new_session=True` in start() means a
        survivor is not reaped by this process's death either — it keeps running with no parent.
        So wait for the exit, and escalate to SIGKILL when the wait runs out: a server still
        draining a gateway stream can be slow to take the hint, and slow here means forever.
        """
        if not (self._proc and self._proc.poll() is None):
            return
        for sig, fallback in ((signal.SIGTERM, self._proc.terminate), (signal.SIGKILL, self._proc.kill)):
            try:
                os.killpg(os.getpgid(self._proc.pid), sig)
            except (ProcessLookupError, PermissionError):
                fallback()
            try:
                self._proc.wait(timeout=timeout_s)
                return
            except subprocess.TimeoutExpired:
                continue
