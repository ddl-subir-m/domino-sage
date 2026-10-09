"""Against the pinned OpenCode: the sweep disposes an idle directory, keeps the others, and the
disposed one works again on its next turn (#742).

Disposal is proved by what OpenCode sent the model, not by Sage's own ledger: a skill added after
both directories loaded reaches the next request only from an instance that was rebuilt (ADR-0071).
The directory still in use keeps its instance, so its next request still lacks the skill. A turn
still running in a directory keeps it through a sweep, and that turn is not aborted.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sage import extensions
from sage.driver.opencode import OpenCodeClient
from sage.orchestrator import service as svc

from .opencode_server import BINARY, _opencode_server
from .test_an_approved_plan_runs_as_implement import _build

REPO = Path(__file__).resolve().parents[2]
PROVIDER = REPO / "node_modules" / "@ai-sdk" / "openai-compatible" / "dist" / "index.mjs"
ADDED = "proj-added-742"
MODEL = {"providerID": "cap", "modelID": "m"}


class _Capture:
    """A model endpoint that records each request and refuses it. While `hold` is clear, it keeps
    the request open, so the turn stays running."""

    def __init__(self) -> None:
        self.seen: list[dict] = []
        self.hold = threading.Event()
        self.hold.set()
        capture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length", 0)))
                capture.seen.append(json.loads(body))
                capture.hold.wait(60)
                self.send_response(400)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"captured"}}')

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.hold.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)


def _prompt(client: OpenCodeClient, capture: _Capture, sid: str) -> str:
    """Send a turn and return the system prompt OpenCode sent the model for it."""
    before = len(capture.seen)
    client.send_prompt(sid, "hello", model=MODEL, agent="build")
    deadline = time.monotonic() + 120
    while len(capture.seen) == before:
        assert time.monotonic() < deadline, "OpenCode never called the model"
        time.sleep(0.1)
    return "\n".join(str(m.get("content")) for m in capture.seen[-1]["messages"]
                     if m.get("role") == "system")


def _rss_mb(port: int) -> int:
    out = subprocess.run(["ps", "-A", "-o", "rss=,args="], capture_output=True, text=True).stdout
    return sum(int(line.split(None, 1)[0]) for line in out.splitlines()
               if f"--port {port}" in line and "serve" in line) // 1024


@pytest.mark.opencode
@pytest.mark.skipif(not BINARY.exists(), reason="the pinned OpenCode binary is not installed")
def test_an_idle_directory_is_disposed_the_used_one_kept_and_the_disposed_one_works_again(
        tmp_path):
    root = tmp_path / "project"
    a, b = root / "apps" / "a", root / "apps" / "b"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    capture = _Capture()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "opencode.json").write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json", "model": "cap/m",
        "provider": {"cap": {"npm": PROVIDER.as_uri(),
                             "options": {"baseURL": capture.url + "/v1", "apiKey": "x"},
                             "models": {"m": {"name": "m", "tool_call": True}}}},
        "permission": {"*": "allow"}}))
    env = dict(os.environ)
    env.update(OPENCODE_CONFIG=str(runtime / "opencode.json"), OPENCODE_DISABLE_AUTOUPDATE="true",
               XDG_CONFIG_HOME=str(runtime / "config"), XDG_DATA_HOME=str(runtime / "data"),
               XDG_CACHE_HOME=str(runtime / "cache"), XDG_STATE_HOME=str(runtime / "state"),
               OPENCODE_CONFIG_DIR=str(runtime / "config" / "opencode"))
    orch, _ = _build(tmp_path, [])

    try:
        with _opencode_server(runtime, env) as url:
            client = OpenCodeClient(url)
            orch._oc_client = client
            sessions = {}
            for directory in (a, b):
                sessions[directory] = client.create_session(str(directory))
                _prompt(client, capture, sessions[directory])
                client.wait_for_idle(sessions[directory], timeout_s=60, appear_grace_s=5)
            port = int(url.rsplit(":", 1)[1])
            loaded = _rss_mb(port)
            extensions.add(root, {"kind": "skill", "name": ADDED, "files": {
                "SKILL.md": f"---\nname: {ADDED}\ndescription: Added.\n---\nGo.\n"}})

            released = orch._reap_opencode(now=client.instances()[str(a)] + svc._OPENCODE_IDLE_S)

            assert released == [str(a)]
            time.sleep(2)
            disposed = _rss_mb(port)
            assert f"<name>{ADDED}</name>" in _prompt(client, capture, sessions[a])
            client.wait_for_idle(sessions[a], timeout_s=60, appear_grace_s=5)
            assert len(client.messages(sessions[a])) == 4, "A's session lost its first turn"
            assert f"<name>{ADDED}</name>" not in _prompt(client, capture, sessions[b])
            client.wait_for_idle(sessions[b], timeout_s=60, appear_grace_s=5)

            capture.hold.clear()
            _prompt(client, capture, sessions[b])
            assert client.is_running(sessions[b])
            released = orch._reap_opencode(now=time.monotonic() + 10 * svc._OPENCODE_IDLE_S)
            assert released == [str(a)]
            capture.hold.set()
            client.wait_for_idle(sessions[b], timeout_s=60, appear_grace_s=5)
            last = client.messages(sessions[b])[-1]
            assert (last.get("error") or {}).get("name") != "MessageAbortedError"
            print(f"\nOpenCode RSS: two directories loaded {loaded} MB, "
                  f"after disposing one {disposed} MB")
    finally:
        capture.close()
