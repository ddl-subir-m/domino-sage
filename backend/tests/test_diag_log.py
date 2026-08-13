"""/api/diag/log — the log ring as plain text, for a workspace with no shell."""
from __future__ import annotations

from fastapi.testclient import TestClient

from sage.orchestrator import app as app_module


def _client(lines: list[str]) -> TestClient:
    app_module._LOG_RING.clear()
    app_module._LOG_RING.extend(lines)
    return TestClient(app_module.control_app)


def test_returns_the_ring_newest_last_as_plain_text():
    r = _client(["first", "second"]).get("/api/diag/log")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == "first\nsecond"


def test_q_filters_case_insensitively():
    # The whole point: /api/diag/log?q=rescue in a URL bar, with no shell to pipe through.
    r = _client(["model policy: RESCUE examined=2", "turn: agent=None", "rescue examined=3"]).get(
        "/api/diag/log", params={"q": "rescue"})
    assert r.text == "model policy: RESCUE examined=2\nrescue examined=3"


def test_no_match_says_so_rather_than_returning_blank():
    # An empty page reads like "the endpoint is broken"; it isn't.
    r = _client(["turn: agent=None"]).get("/api/diag/log", params={"q": "rescue"})
    assert "no lines match" in r.text and "rescue" in r.text


def test_n_caps_to_the_newest_lines():
    r = _client([f"line {i}" for i in range(10)]).get("/api/diag/log", params={"n": 3})
    assert r.text == "line 7\nline 8\nline 9"
