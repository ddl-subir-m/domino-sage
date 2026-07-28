"""Streaming behavior of the shim endpoint: keep OpenCode's fetch alive through gpt-5.4's silent
thinking gaps, and end a broken stream readably instead of leaking a raw truncation.

The failure this guards: the shim used to pull the first byte before returning the response, so a
model that thought for minutes withheld all headers/body -> OpenCode's Node fetch aborted with
"TypeError: network error". Now the response commits early and emits SSE keepalive comments during
silent gaps; a mid-stream upstream break becomes a readable assistant message + clean [DONE].
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

import sage.shim.app as appmod
from sage.gateway.client import GatewayUpstreamError


def _client(monkeypatch, gen_factory):
    """Point the app's shim at a fake whose handle() returns gen_factory()'s generator."""
    class _FakeShim:
        def handle(self, body, project, session=None):
            return gen_factory()

    monkeypatch.setattr(appmod, "_shim", _FakeShim())
    return TestClient(appmod.control_app if hasattr(appmod, "control_app") else appmod.app)


def _post(client):
    return client.post("/v1/chat/completions", json={"model": "gpt-5.4", "messages": []})


def test_keepalive_is_emitted_during_a_silent_gap(monkeypatch):
    # Tiny timers so the test is fast: no byte within the budget -> commit + keepalive; the real
    # chunk lands one keepalive interval later.
    monkeypatch.setattr(appmod, "_FIRST_BYTE_BUDGET_S", 0.05)
    monkeypatch.setattr(appmod, "_KEEPALIVE_INTERVAL_S", 0.05)

    def gen():
        time.sleep(0.2)              # model "thinks" past both the budget and a keepalive interval
        yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'

    client = _client(monkeypatch, gen)
    body = _post(client).text
    assert ": keepalive" in body          # connection kept warm during the silent gap
    assert '"content":"hi"' in body       # the real chunk still arrives intact afterwards


def test_mid_stream_break_becomes_a_readable_message_not_a_raw_truncation(monkeypatch):
    monkeypatch.setattr(appmod, "_FIRST_BYTE_BUDGET_S", 0.5)

    def gen():
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        raise RuntimeError("RemoteProtocolError: peer closed connection")

    client = _client(monkeypatch, gen)
    resp = _post(client)
    assert resp.status_code == 200                 # already committed to the stream
    assert '"content":"partial"' in resp.text      # the bytes we did get are preserved
    assert "closed the stream mid-response" in resp.text  # readable graceful ending
    assert "data: [DONE]" in resp.text             # turn closed cleanly


def test_fast_upstream_error_still_returns_a_clean_502(monkeypatch):
    monkeypatch.setattr(appmod, "_FIRST_BYTE_BUDGET_S", 0.5)

    def gen():
        raise GatewayUpstreamError(401, "http://gw/v1/chat/completions", "unauthorized")
        yield  # pragma: no cover - generator marker

    client = _client(monkeypatch, gen)
    resp = _post(client)
    assert resp.status_code == 502
    assert resp.json()["error"]["upstream_status"] == 401
