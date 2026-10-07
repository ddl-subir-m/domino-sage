"""On the pinned OpenCode, a mounted Dataset is read through the Chat workdir's link (#675).

`external_directory: deny` stays as shipped. A `read` of the mount's own path is refused, and the
same file through the link `ensure_chat_workdir` makes is read — OpenCode checks the path it is
given, not where a link points. That difference is the whole fix, so it is pinned against the real
binary with the repo's own permissions and `sage-chat` agent; only the provider is a local stand-in
that scripts the two `read` calls.
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

from sage.driver.opencode import OpenCodeClient
from sage.workspace.threads import ensure_chat_workdir, mounted_dataset_link

from .opencode_server import BINARY, _opencode_server

REPO = Path(__file__).resolve().parents[2]
PROVIDER = REPO / "node_modules" / "@ai-sdk" / "openai-compatible" / "dist" / "index.mjs"
TID = "thr_aaaaaaaaaaaaaaaaaaaaa"


def _chunk(delta: dict, finish: str | None = None) -> bytes:
    body = {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return b"data: " + json.dumps(body).encode() + b"\n\n"


class _Script:
    """A model endpoint that reads `paths` one per step, then answers in text."""

    def __init__(self, paths: list[str]) -> None:
        script = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
                done = sum(1 for m in body.get("messages", []) if m.get("role") == "tool")
                if body.get("tools") and done < len(paths):
                    args = json.dumps({"filePath": paths[done]})
                    frames = [_chunk({"role": "assistant", "tool_calls": [{
                        "index": 0, "id": f"call_{done}", "type": "function",
                        "function": {"name": "read", "arguments": args}}]}),
                        _chunk({}, "tool_calls")]
                else:
                    frames = [_chunk({"role": "assistant", "content": "done"}), _chunk({}, "stop")]
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b"".join(frames) + b"data: [DONE]\n\n")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=script.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)


def _reads(client: OpenCodeClient, sid: str) -> list[dict]:
    return [p for m in client.messages(sid) if m.get("type") == "assistant"
            for p in m.get("content", []) if p.get("type") == "tool"]


@pytest.mark.opencode
@pytest.mark.skipif(not BINARY.exists(), reason="the pinned OpenCode binary is not installed")
def test_the_mount_is_refused_and_the_link_to_it_is_read(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    mount = tmp_path / "mnt" / "data" / "sales-playbooks"
    mount.mkdir(parents=True)
    (mount / "playbook.md").write_text("# battlecards\n")
    work = ensure_chat_workdir(root, "# chat", thread_id=TID, mounts=[str(mount)])
    script = _Script([str(mount / "playbook.md"), f"{mounted_dataset_link(str(mount))}/playbook.md"])

    config = json.loads((REPO / "opencode.json").read_text())
    assert config["permission"]["external_directory"] == "deny"
    config.update(model="cap/m", small_model="cap/m", enabled_providers=["cap"], provider={
        "cap": {"npm": PROVIDER.as_uri(), "options": {"baseURL": script.url + "/v1", "apiKey": "x"},
                "models": {"m": {"name": "m", "tool_call": True}}}})
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "opencode.json").write_text(json.dumps(config))
    env = dict(os.environ)
    env.update(OPENCODE_CONFIG=str(runtime / "opencode.json"), OPENCODE_DISABLE_AUTOUPDATE="true",
               XDG_CONFIG_HOME=str(runtime / "config"), XDG_DATA_HOME=str(runtime / "data"),
               XDG_CACHE_HOME=str(runtime / "cache"), XDG_STATE_HOME=str(runtime / "state"),
               OPENCODE_CONFIG_DIR=str(runtime / "config" / "opencode"))

    try:
        with _opencode_server(runtime, env) as url:
            client = OpenCodeClient(url)
            sid = client.create_session(str(work))
            client.send_prompt(sid, "Read the playbook.", agent="sage-chat",
                               model={"providerID": "cap", "modelID": "m"})
            deadline = time.monotonic() + 120
            while len(reads := _reads(client, sid)) < 2 or any(
                    p["state"]["status"] in ("pending", "running") for p in reads):
                assert time.monotonic() < deadline, (runtime / "opencode.log").read_text()[-4000:]
                time.sleep(0.2)
    finally:
        script.stop()

    outside, linked = reads
    assert outside["state"]["status"] == "error", outside["state"]
    assert "battlecards" not in json.dumps(outside["state"])
    assert linked["state"]["status"] == "completed", linked["state"]
    assert "battlecards" in linked["state"]["output"]
