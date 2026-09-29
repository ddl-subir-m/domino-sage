"""The findings slice polls OpenCode once a second over plain HTTP on loopback, and on 2026-09-29 a
poll logged `[X509] PEM lib`. httpx builds a TLS context for every bare `httpx.get`, reading the CA
bundle from disk even for an `http://` URL, and a bundle caught mid-rewrite reads as truncated.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import certifi

from sage.driver.opencode import OpenCodeClient


class _Status(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ses_1": {"type": "busy"}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def test_a_status_poll_survives_a_truncated_ca_bundle(tmp_path, monkeypatch):
    half = tmp_path / "half.pem"
    half.write_text(Path(certifi.where()).read_text()[:5000])
    monkeypatch.setenv("SSL_CERT_FILE", str(half))
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Status)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = OpenCodeClient(f"http://127.0.0.1:{server.server_address[1]}")
        assert client.is_running("ses_1") is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
