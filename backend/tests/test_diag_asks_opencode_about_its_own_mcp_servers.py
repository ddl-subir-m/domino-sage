"""/api/diag asks OPENCODE whether it connected, instead of only inferring it from our side.

Every other field on the `mcp` block is Sage looking at Sage. `configured` reads the file we wrote,
`reachable` probes our own route, and `opencode_connected` reports the first time OpenCode called
US. On 2026-09-08 all three read plausibly while the model's tool list had no Live read in it, and
there was no fourth thing to ask — so the conclusion drawn was that OpenCode never dials an MCP
server, which a later bench test on the pinned 1.18.4 disproved. It dials LAZILY, when a session
first needs tools, and it reports what it thinks on `GET /mcp`.

Run against a real socket rather than a stubbed `httpx.get`, for the reason the sibling file gives:
the thing being checked IS the round trip, and a stub would pass with the URL wrong.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.orchestrator import app as app_module


def _opencode(payload: object | None, status: int = 200):
    """A stand-in `opencode serve`, answering `GET /mcp` the way 1.18.4 does."""
    class _H(BaseHTTPRequestHandler):
        def do_GET(self):  # BaseHTTPRequestHandler's own spelling
            if payload is None:
                self.send_response(status)
                self.end_headers()
                return
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def live_read_route() -> int:
    """Our own MCP route, answering `tools/list`. Present so `reachable` reads healthy in every
    test here — the point of these cases is that OUR end being fine proves nothing about OpenCode's.
    """
    class _H(BaseHTTPRequestHandler):
        def do_POST(self):  # BaseHTTPRequestHandler's own spelling
            self.rfile.read(int(self.headers.get("content-length") or 0))
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": [
                {"name": "live_read_table"}]}}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_port
    finally:
        srv.shutdown()
        srv.server_close()


def _diag(tmp_path: Path, monkeypatch, port: int, oc_server: object) -> dict:
    cfg = tmp_path / ".config" / "opencode"
    cfg.mkdir(parents=True)
    (cfg / "opencode.json").write_text(json.dumps({"mcp": {"sage-live-read": {
        "type": "remote", "enabled": True,
        "url": f"http://127.0.0.1:{port}/mcp/live-read"}}}))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SAGE_CONTROL_PORT", str(port))
    monkeypatch.setattr(app_module.orchestrator, "_oc_server", oc_server, raising=False)
    r = TestClient(app_module.control_app).get("/api/diag")
    assert r.status_code == 200
    return r.json()["mcp"]


class _Handle:
    """The bit of OpenCodeServer this reads: the URL it reported when it came up."""

    def __init__(self, url: str) -> None:
        self._url = url

    def url(self) -> str:
        if self._url is None:
            raise RuntimeError("opencode server not ready")
        return self._url


def test_opencode_saying_connected_is_reported_beside_our_own_probe(tmp_path, monkeypatch,
                                                                    live_read_route):
    """The healthy reading, so the unhealthy ones below mean something."""
    srv = _opencode({"sage-live-read": {"status": "connected"}})
    try:
        out = _diag(tmp_path, monkeypatch, live_read_route,
                    _Handle(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert out["servers"][0]["reachable"]["ok"] is True
    assert out["opencode_says"]["ok"] is True
    assert out["opencode_says"]["servers"] == {"sage-live-read": {"status": "connected"}}


def test_a_server_opencode_dropped_is_visible_although_our_route_answers(tmp_path, monkeypatch,
                                                                        live_read_route):
    """The case that had nothing to report it, and the whole reason for this field.

    Our route answers, the config names the right port, and the tools are still absent from the
    model's list — because OpenCode is not holding the server. Before `opencode_says` this state
    was indistinguishable on the page from a perfectly healthy one.
    """
    srv = _opencode({})
    try:
        out = _diag(tmp_path, monkeypatch, live_read_route,
                    _Handle(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert out["servers"][0]["reachable"]["ok"] is True     # ours is fine
    assert out["opencode_says"]["ok"] is True               # and OpenCode answered
    assert out["opencode_says"]["servers"] == {}            # ...holding nothing


def test_a_failed_server_is_named_with_the_state_opencode_gave_it(tmp_path, monkeypatch,
                                                                  live_read_route):
    srv = _opencode({"sage-live-read": {"status": "failed", "error": "connection refused"}})
    try:
        out = _diag(tmp_path, monkeypatch, live_read_route,
                    _Handle(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert out["opencode_says"]["servers"]["sage-live-read"]["status"] == "failed"


def test_no_opencode_server_says_so_rather_than_reading_as_a_verdict(tmp_path, monkeypatch,
                                                                     live_read_route):
    """`asked: False` and a reason. An empty answer here would read as "OpenCode holds nothing",
    which is the wrong finding when the truth is that nobody was asked."""
    out = _diag(tmp_path, monkeypatch, live_read_route, None)

    assert out["opencode_says"]["asked"] is False
    assert out["opencode_says"]["why"]


def test_a_server_that_has_not_reported_a_url_yet_says_so(tmp_path, monkeypatch, live_read_route):
    """`url()` raises until `opencode serve` prints one. That is a real answer, not a page break."""
    out = _diag(tmp_path, monkeypatch, live_read_route, _Handle(None))

    assert out["opencode_says"]["asked"] is False
    assert "not ready" in out["opencode_says"]["why"]


def test_an_opencode_that_cannot_be_reached_does_not_break_the_page(tmp_path, monkeypatch,
                                                                    live_read_route):
    """A diagnostic must never be the thing that breaks the diagnostics page."""
    out = _diag(tmp_path, monkeypatch, live_read_route, _Handle("http://127.0.0.1:1"))

    assert out["opencode_says"]["asked"] is True
    assert out["opencode_says"]["ok"] is False
    assert out["opencode_says"]["error"]


def test_an_unreadable_reply_is_reported_as_one(tmp_path, monkeypatch, live_read_route):
    srv = _opencode(None, status=500)
    try:
        out = _diag(tmp_path, monkeypatch, live_read_route,
                    _Handle(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert out["opencode_says"]["ok"] is False
    assert out["opencode_says"]["status"] == 500
