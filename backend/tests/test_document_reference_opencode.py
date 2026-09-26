"""The real OpenCode Build path sends a prepared explicit reference on its first request (#516)."""

from __future__ import annotations

import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sage.assets.provider import FakeAssetProvider
from sage.driver.opencode import OpenCodeClient
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode, ModelCatalog

from .opencode_server import BINARY, _opencode_server
from .test_chat_turn import OkFeedback

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.opencode
@pytest.mark.skipif(not BINARY.exists(), reason="Install the pinned OpenCode package for the real flow")
def test_real_opencode_first_build_request_contains_typed_mixed_carriers_only(tmp_path: Path):
    calls: list[dict] = []
    # `SessionPrompt.ensureTitle`'s request, which used to arrive on every fresh session and be
    # answered here so the rest of the turn could proceed. A titled session ends it (#549), so the
    # branch below should now never fire — and this is the only rig in the suite that can say so,
    # because the title request never reaches `Gateway.route`.
    titles: list[dict] = []
    failures: list[str] = []
    state: dict = {}

    class Gateway:
        def route(self, body, labels):
            calls.append(body)
            frame = {
                "id": "controlled", "object": "chat.completion.chunk", "model": "alias",
                "choices": [{"index": 0, "delta": {
                    "content": "# Reference app\n\n## Plan\n1. Follow the prepared rule"
                }, "finish_reason": None}],
            }
            yield ("data: " + json.dumps(frame) + "\n\n").encode()
            frame["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            yield ("data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n").encode()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if "title generator" in str(body.get("messages", [{}])[0].get("content", "")).lower():
                    titles.append(body)
                    frame = {"id": "title", "choices": [{"index": 0,
                             "delta": {"content": "Reference app"}, "finish_reason": "stop"}]}
                    self.wfile.write(
                        ("data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n").encode()
                    )
                    return
                project = state["project"]
                for chunk in project.shim.handle(body, project="synthetic", session="controlled"):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except Exception as error:  # reported in the test process after the stream settles
                failures.append(repr(error))

        def log_message(self, *ignored):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    config = json.loads((REPO / "opencode.json").read_text())
    config["provider"]["sage-gateway"]["options"]["baseURL"] = (
        f"http://127.0.0.1:{server.server_port}/v1"
    )
    config["plugin"] = []
    config["mcp"] = {}
    config["permission"] = {"*": "allow"}
    config_path = runtime / "opencode.json"
    config_path.write_text(json.dumps(config))
    tools = runtime / "config" / "opencode" / "tools"
    tools.mkdir(parents=True)
    (tools / "live_read.ts").write_text(
        (REPO / "backend/sage/liveread/tools/live_read.ts").read_text()
    )
    env = dict(os.environ)
    env.update(
        OPENCODE_CONFIG=str(config_path), OPENCODE_DISABLE_AUTOUPDATE="true",
        XDG_CONFIG_HOME=str(runtime / "config"), XDG_DATA_HOME=str(runtime / "data"),
        XDG_CACHE_HOME=str(runtime / "cache"), XDG_STATE_HOME=str(runtime / "state"),
        OPENCODE_CONFIG_DIR=str(runtime / "config" / "opencode"),
        SAGE_CONTROL_PORT=str(server.server_port),
    )

    try:
        with _opencode_server(runtime, env) as url:
            template = tmp_path / "template"
            (template / "src").mkdir(parents=True)
            (template / "src" / "App.tsx").write_text(
                "export default function App() { return null }\n"
            )
            (template / "package.json").write_text("{}")
            assets = FakeAssetProvider()
            orch = Orchestrator(
                workspace_dir=tmp_path / "mnt" / "code", template=template,
                gateway=Gateway(), catalog=ModelCatalog(
                    sovereign_plan="sonnet", sovereign_implement="sonnet", sovereign_ask="sonnet",
                    plan="sonnet", implement="sonnet", ask="sonnet",
                ), project_id="Sage",
                feedback=OkFeedback(), opencode_client=OpenCodeClient(url), assets=assets,
            )
            project = orch.project(start_preview=False)
            state["project"] = project
            project.control.set_mode(Mode.PLAN)
            shell = orch.upload_file(
                "requirements.md", b"# Rules\nUNIQUE REAL OPENCODE RULE\n"
            )["path"]
            table = orch.upload_file(
                "shape.csv", b"subject,arm\n01,A\n02,B\n03,C\n04,D\n05,E\n06,F\n07,G\n"
                b"08,H\n09,I\n10,J\n11,K\n12,L\n13,M\n"
            )["path"]
            image_bytes = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
            image = orch.upload_file("design.png", image_bytes)["path"]
            orch.upload_file("private.csv", b"id,value\n1,PRIVATE REAL CSV SENTINEL\n")

            list(orch.build_stream("Follow the attached requirements, table shape, and image",
                                   [shell, table, image]))

            assert not failures, failures
            assert not titles, "a fresh session still spends a model call naming itself (#549)"
            assert calls
            first_messages = json.dumps(calls[0].get("messages", []))
            assert "UNIQUE REAL OPENCODE RULE" in first_messages
            assert "BEGIN PREPARED TABLE STRUCTURE" in first_messages
            assert "subject: digits" in first_messages
            assert "image_url" in first_messages
            assert "PRIVATE REAL CSV SENTINEL" not in first_messages
            assert not any(message.get("role") in {"assistant", "tool"}
                           for message in calls[0].get("messages", []))
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
