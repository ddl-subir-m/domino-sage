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


# ---------------------------------------------------------------------------
# /api/diag/mcp says whose end broke, in words.
#
# The fields above are the right fields, and reading them still means holding three of them against
# each other and knowing which combination means what. That is the ask this page cannot make of the
# person opening it, because they opened it knowing only that Chat said it could not see their data.


class _Full(_Handle):
    """`_Handle` plus the CLI half `/api/diag/mcp` shells out to."""

    def cli(self, args, cwd=None, timeout_s=30.0):
        return "sage-live-read  connected"


def _verdict(tmp_path: Path, monkeypatch, route_port: int, oc_server: object,
             *, dialled: bool = False) -> str:
    """`dialled` says whether OPENCODE itself ever connected, which is what separates a late
    handshake from a server it has never dialled. Set here rather than by making a real call:
    `app_module.orchestrator` is a module-level singleton, so one test's dial leaks into every
    later one — which is exactly how the two cases below first passed alone and failed together.
    """
    monkeypatch.setattr(app_module.orchestrator, "_opencode_mcp_at",
                        (app_module.orchestrator._boot_at + 5.0) if dialled else None,
                        raising=False)
    cfg = tmp_path / ".config" / "opencode"
    cfg.mkdir(parents=True)
    (cfg / "opencode.json").write_text(json.dumps({"mcp": {"sage-live-read": {
        "type": "remote", "enabled": True,
        "url": f"http://127.0.0.1:{route_port}/mcp/live-read"}}}))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SAGE_CONTROL_PORT", str(route_port))
    monkeypatch.setattr(app_module.orchestrator, "_oc_server", oc_server, raising=False)
    r = TestClient(app_module.control_app).get("/api/diag/mcp")
    assert r.status_code == 200
    return r.text


def test_everything_healthy_says_the_model_chose_not_to_call_it(tmp_path, monkeypatch,
                                                                live_read_route):
    """The reading that used to send people hunting the wiring for an evening. Nothing is broken —
    so the page has to say so, and point at the turn instead. The turn line is stated rather than
    left to chance: without one the honest answer is "could not check", which is its own test."""
    _with_log(monkeypatch, "INFO sage.shim: chat tools: live read "
                           "sage-live-read_live_read_files, sage-live-read_live_read_table")
    srv = _opencode({"sage-live-read": {"status": "connected"}})
    try:
        out = _verdict(tmp_path, monkeypatch, live_read_route,
                       _Full(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert "NOBODY'S" in out
    assert "the model's choice" in out
    assert out.index("VERDICT") < out.index("$ opencode mcp")   # verdict first, evidence under it


def test_a_server_opencode_is_not_holding_names_opencodes_end(tmp_path, monkeypatch,
                                                              live_read_route):
    srv = _opencode({})
    try:
        out = _verdict(tmp_path, monkeypatch, live_read_route,
                       _Full(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert "OPENCODE'S END" in out
    assert "does not retry" in out


def test_a_status_that_is_not_connected_is_named_as_opencodes_end(tmp_path, monkeypatch,
                                                                  live_read_route):
    srv = _opencode({"sage-live-read": {"status": "failed"}})
    try:
        out = _verdict(tmp_path, monkeypatch, live_read_route,
                       _Full(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert "OPENCODE'S END" in out
    assert "failed" in out


def test_our_own_route_being_down_is_named_as_ours(tmp_path, monkeypatch):
    """No `live_read_route` fixture: nothing is listening on the port the config names."""
    srv = _opencode({})
    try:
        out = _verdict(tmp_path, monkeypatch, 1, _Full(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert "OURS" in out
    assert "never handed a working server" in out


def test_a_lazy_dial_is_not_reported_as_a_fault(tmp_path, monkeypatch, live_read_route):
    """`opencode_connected: never` with no turn behind it is the normal reading, and the sentence
    that cost an evening. It is spelled out rather than left for the reader to know."""
    _with_log(monkeypatch, "INFO sage.shim: chat tools: live read "
                           "sage-live-read_live_read_files, sage-live-read_live_read_table")
    srv = _opencode({"sage-live-read": {"status": "connected"}})
    try:
        out = _verdict(tmp_path, monkeypatch, live_read_route,
                       _Full(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert "is NORMAL here" in out
    assert "the dial is lazy" in out


def test_no_opencode_yet_still_says_what_to_do(tmp_path, monkeypatch, live_read_route):
    """Unchanged behaviour, pinned: with no server there is nothing to ask and the page says so."""
    out = _verdict(tmp_path, monkeypatch, live_read_route, None)

    assert "has not been started yet" in out
    assert "Send one chat message" in out


# ---------------------------------------------------------------------------
# A connected server says nothing about the turn that already ran.
#
# Live, 2026-09-09: Chat said it had no `sage-live-read_` tool, and the verdict under it read
# NOBODY'S — our route answered, `opencode mcp list` said connected, and the page therefore blamed
# the model. It was wrong. A turn's tool list is fixed when the turn STARTS and the handshake has
# been seen landing after that, which the shim has recorded all along and the verdict was not
# reading. So the verdict now reads the turn, and the server's own state is the tie-breaker.


def _with_log(monkeypatch, line: str | None):
    """The shim's per-turn line, as it sits in the ring `/api/diag/log` serves."""
    monkeypatch.setattr(app_module, "_LOG_RING",
                        __import__("collections").deque([] if line is None else [line], maxlen=400))


def test_a_turn_that_went_out_without_the_tools_is_not_blamed_on_the_model(tmp_path, monkeypatch,
                                                                          live_read_route):
    """A genuine late handshake: OpenCode DID dial on its own, and a turn still went out before it.

    The dial is what separates this from the case below. Without one, "connected" only means the
    diagnostic connected it, and telling somebody to ask again is telling them to repeat a failure.
    """
    _with_log(monkeypatch, "INFO sage.shim: chat tools: live read NOT OFFERED")
    srv = _opencode({"sage-live-read": {"status": "connected"}})
    try:
        out = _verdict(tmp_path, monkeypatch, live_read_route,
                       _Full(f"http://127.0.0.1:{srv.server_port}"), dialled=True)
    finally:
        srv.shutdown()
        srv.server_close()

    assert "LATE, NOT MISSING" in out
    assert "Ask the same question again" in out
    assert "NOBODY'S" not in out          # the wrong answer this case used to get
    assert "model's choice" not in out


def test_a_turn_that_was_offered_the_tools_does_blame_the_model(tmp_path, monkeypatch,
                                                                live_read_route):
    _with_log(monkeypatch, "INFO sage.shim: chat tools: live read "
                           "sage-live-read_live_read_files, sage-live-read_live_read_table")
    srv = _opencode({"sage-live-read": {"status": "connected"}})
    try:
        out = _verdict(tmp_path, monkeypatch, live_read_route,
                       _Full(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert "NOBODY'S" in out
    assert "the last turn WAS offered the tools" in out


def test_a_rolled_ring_says_it_could_not_check_rather_than_passing(tmp_path, monkeypatch,
                                                                   live_read_route):
    """The ring holds 400 lines and one build turn emits hundreds. Silence there is not a pass, and
    reporting it as one is how the reader gets sent to the wrong end again."""
    _with_log(monkeypatch, None)
    srv = _opencode({"sage-live-read": {"status": "connected"}})
    try:
        out = _verdict(tmp_path, monkeypatch, live_read_route,
                       _Full(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert "PROBABLY NOBODY'S" in out
    assert "could NOT be checked" in out


def test_the_turn_line_does_not_override_a_server_that_is_actually_down(tmp_path, monkeypatch,
                                                                        live_read_route):
    """`NOT OFFERED` with a server OpenCode is not holding is still OpenCode's end, not a late
    handshake — the tools are not coming on the next turn either."""
    _with_log(monkeypatch, "INFO sage.shim: chat tools: live read NOT OFFERED")
    srv = _opencode({})
    try:
        out = _verdict(tmp_path, monkeypatch, live_read_route,
                       _Full(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert "OPENCODE'S END" in out
    assert "LATE" not in out


def test_a_turn_that_missed_while_serve_never_dialled_is_not_called_late(tmp_path, monkeypatch,
                                                                        live_read_route):
    """Their 2026-09-09 reading, pinned. `opencode serve` had not dialled once on its own — every
    connection on record came from opening this page — and three turns went out with nothing. The
    page called that LATE and told them to ask again, which was advice to repeat the failure."""
    _with_log(monkeypatch, "INFO sage.shim: chat tools: live read NOT OFFERED")
    srv = _opencode({"sage-live-read": {"status": "connected"}})
    try:
        out = _verdict(tmp_path, monkeypatch, live_read_route,
                       _Full(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert "OPENCODE'S END" in out
    assert "never reached a turn" in out
    assert "connected because you asked" in out
    assert "LATE" not in out
