"""Measure real HTTP connection reuse, with request identity kept out of the pool."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sage.gateway.client import (
    CostLabels,
    GatewayUpstreamError,
    MultiProviderOpenAIClient,
    OpenAICompatibleClient,
)
from sage.gateway.open_models import OpenModel


@pytest.fixture
def endpoint():
    seen = []
    barrier = threading.Barrier(2, timeout=5)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.client_address, dict(self.headers), body))
            if body.get("concurrent"):
                barrier.wait()
            self.send_response(body.get("status", 200))
            self.send_header("Content-Length", "14")
            self.send_header("Set-Cookie", "identity=previous-caller; Path=/")
            self.end_headers()
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(params=["gateway", "multi-provider"])
def client(request, endpoint, monkeypatch):
    base, _ = endpoint
    monkeypatch.setenv("TEST_MODEL_KEY", "first")
    if request.param == "gateway":
        import os

        result = OpenAICompatibleClient(base, lambda: os.environ["TEST_MODEL_KEY"], domino_tags=True)
    else:
        result = MultiProviderOpenAIClient([
            OpenModel(name, "test", base, "TEST_MODEL_KEY") for name in ("closed", "open")])
    try:
        yield result
    finally:
        close = getattr(result, "close", None)
        if close:
            close()


def test_two_models_reuse_one_socket_without_reusing_identity(client, endpoint, monkeypatch):
    _, seen = endpoint
    assert b"".join(client.route({"model": "closed"}, CostLabels("plan", "auto", session="first")))
    monkeypatch.setenv("TEST_MODEL_KEY", "second")
    assert b"".join(client.route({"model": "open"}, CostLabels("implement", "auto")))
    assert seen[0][0] == seen[1][0], "every model call opened a new TCP connection"
    assert [headers["Authorization"] for _, headers, _ in seen] == ["Bearer first", "Bearer second"]
    assert all("Cookie" not in headers for _, headers, _ in seen), "pool retained a caller's cookie"
    assert "X-LLM-Tag-sage-session" not in seen[1][1], "pool retained the previous session tag"


@pytest.mark.parametrize("status", [307, 500])
def test_upstream_failure_releases_the_response_and_keeps_the_pool(client, endpoint, status):
    _, seen = endpoint
    with pytest.raises(GatewayUpstreamError) as error:
        list(client.route({"model": "closed", "status": status}, CostLabels("plan", "auto")))
    assert error.value.status == status
    assert b"".join(client.route({"model": "closed"}, CostLabels("plan", "auto")))
    assert seen[0][0] == seen[1][0]


def test_two_inferences_can_stream_at_once(client):
    def call():
        return b"".join(client.route({"model": "closed", "concurrent": True}, CostLabels("plan", "auto")))

    with ThreadPoolExecutor(max_workers=2) as workers:
        first, second = workers.submit(call), workers.submit(call)
        assert first.result(timeout=10) == second.result(timeout=10) == b"data: [DONE]\n\n"


def test_close_is_idempotent_and_does_not_open_a_new_pool(client, endpoint):
    _, seen = endpoint
    list(client.route({"model": "closed"}, CostLabels("plan", "auto")))
    client.close()
    client.close()
    with pytest.raises(RuntimeError, match="closed"):
        list(client.route({"model": "closed"}, CostLabels("plan", "auto")))
    assert len(seen) == 1


def test_orchestrator_closes_gateway_after_stopping_the_agent(tmp_path):
    from sage.gateway.client import FakeGatewayClient
    from sage.orchestrator.service import Orchestrator
    from sage.router.models import ModelCatalog

    calls = []

    class Gateway(FakeGatewayClient):
        def close(self):
            calls.append("gateway")

    class Server:
        def stop(self):
            calls.append("agent")

    orchestrator = Orchestrator(workspace_dir=tmp_path / "ws", template=tmp_path / "template",
                                gateway=Gateway(), catalog=ModelCatalog("s", "s", "s", "p", "i", "a"))
    orchestrator._oc_server = Server()
    orchestrator.shutdown()
    orchestrator.shutdown()
    assert calls == ["agent", "gateway"]


def test_standalone_shim_closes_gateway_on_shutdown(monkeypatch):
    from fastapi.testclient import TestClient

    from sage.shim import app as appmod

    closed = []

    class Gateway:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(appmod, "_gateway", Gateway())
    with TestClient(appmod.app):
        assert closed == []
    assert closed == [True]
