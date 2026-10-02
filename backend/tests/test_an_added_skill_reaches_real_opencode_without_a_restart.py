"""A skill added to a Project reaches the next Chat and Build model request, with no restart (#619).

ADR-0071. The pinned OpenCode reads project skills from `.opencode/` at the git root of the session
directory, once per directory, and does not watch it. So a skill added while the server runs is
absent until Sage disposes that directory's instance. Proved against the request OpenCode actually
sent to the model, captured by a local stand-in for the gateway. The names are unusual because
OpenCode also lists skills from the machine's own `~/.claude/skills`.
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

from .opencode_server import BINARY, _opencode_server

REPO = Path(__file__).resolve().parents[2]
ALPHA, BETA = "proj-alpha-619", "proj-beta-619"
PROVIDER = REPO / "node_modules" / "@ai-sdk" / "openai-compatible" / "dist" / "index.mjs"


def _skill(name: str) -> dict:
    return {"kind": "skill", "name": name,
            "files": {"SKILL.md": f"---\nname: {name}\ndescription: The {name} skill.\n---\nGo.\n"}}


class _Capture:
    """A model endpoint that records each request and refuses it, so the turn ends at once."""

    def __init__(self) -> None:
        self.seen: list[dict] = []
        capture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length", 0)))
                capture.seen.append(json.loads(body))
                self.send_response(400)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"captured"}}')

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


def _system(client: OpenCodeClient, capture: _Capture, directory: Path) -> str:
    before = len(capture.seen)
    sid = client.create_session(str(directory))
    client.send_prompt(sid, "hello", model={"providerID": "cap", "modelID": "m"}, agent="build")
    deadline = time.monotonic() + 120
    while len(capture.seen) == before:
        assert time.monotonic() < deadline, "OpenCode never called the model"
        time.sleep(0.1)
    return "\n".join(str(m.get("content")) for m in capture.seen[-1]["messages"]
                     if m.get("role") == "system")


@pytest.mark.opencode
@pytest.mark.skipif(not BINARY.exists(), reason="the pinned OpenCode binary is not installed")
def test_a_skill_added_while_opencode_runs_reaches_chat_and_build_after_a_dispose(tmp_path):
    root = tmp_path / "project"
    chat, build = root / ".sage" / "chat-work", root / "apps" / "app1"
    chat.mkdir(parents=True)
    build.mkdir(parents=True)
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
    extensions.add(root, _skill(ALPHA))

    try:
        with _opencode_server(runtime, env) as url:
            client = OpenCodeClient(url)
            for directory in (chat, build):
                system = _system(client, capture, directory)
                assert f"<name>{ALPHA}</name>" in system and f"<name>{BETA}</name>" not in system

            extensions.add(root, _skill(BETA))
            # Why Sage has to reload: the instance OpenCode holds still has the old list.
            assert f"<name>{BETA}</name>" not in _system(client, capture, chat)

            for directory in (chat, build):
                client.dispose_instance(str(directory))
            for directory in (chat, build):
                system = _system(client, capture, directory)
                assert f"<name>{ALPHA}</name>" in system and f"<name>{BETA}</name>" in system
    finally:
        capture.server.shutdown()
        capture.server.server_close()
