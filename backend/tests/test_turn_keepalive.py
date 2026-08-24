"""The browser's build stream stays warm through a turn's silent gaps.

The failure, live on 2026-08-24: mid-build the UI showed "Lost the connection to this build — it's
still running", and the diag log had the turn carrying on server-side for minutes afterwards. Nothing
had gone wrong with the turn. It had gone QUIET — a plan turn on a built project spends 20-30s
between tool calls while the model thinks, and an SSE response with no bytes in it is what an
intermediary closes.

The shim's own /v1 stream has been keeping itself warm for exactly this reason since it was losing
OpenCode's requests to a "TypeError: network error". The stream the browser reads never got the same
treatment, which is the one place it mattered to a person rather than to an agent.

Driven through a real server rather than TestClient, which buffers an ASGI stream and would report a
keepalive that never left the process.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest
from fastapi import FastAPI
from starlette.responses import StreamingResponse

from sage.orchestrator.app import _turn_sse
from sage.shim import keepalive as ka


@contextmanager
def _served(app) -> Iterator[str]:
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "the test server did not come up"
    try:
        yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _app(events):
    app = FastAPI()

    @app.post("/turn")
    def turn() -> StreamingResponse:
        return StreamingResponse(_turn_sse(events(), "test_turn"), media_type="text/event-stream")

    return app


@pytest.fixture(autouse=True)
def _fast_keepalive(monkeypatch):
    # 15s is the shipped gap; a test that waited it out would be a 30-second test. The interval is
    # read from the module at each poll, so patching it here is the whole difference.
    monkeypatch.setattr(ka, "KEEPALIVE_INTERVAL_S", 0.2)


def test_a_quiet_turn_keeps_the_connection_warm():
    """The whole point: bytes must reach the browser while the agent is thinking.

    The gap here is 1.2s against a 0.2s interval. Without the keepalive nothing at all crosses the
    wire in that window — which is the state a proxy times out, and the state that produced "Lost the
    connection to this build".
    """
    def events():
        yield {"type": "turn", "prompt": "add a tab"}
        time.sleep(1.2)                       # the model thinking between tool calls
        yield {"type": "done", "ok": True}

    seen: list[tuple[float, str]] = []
    started = time.monotonic()
    with _served(_app(events)) as base, httpx.stream("POST", f"{base}/turn", timeout=20.0) as r:
        for chunk in r.iter_text():
            if chunk.strip():
                seen.append((time.monotonic() - started, chunk))

    body = "".join(chunk for _, chunk in seen)
    assert ": keepalive" in body, "nothing was sent during the gap"
    # And they landed DURING the gap, not bunched at the end with the final event.
    first_keepalive = next(at for at, chunk in seen if ": keepalive" in chunk)
    assert first_keepalive < 1.0


def test_the_turns_own_events_still_arrive_in_order():
    # The filler must be invisible to the real content: same events, same order, nothing dropped.
    def events():
        yield {"type": "turn", "prompt": "p"}
        yield {"type": "tool", "name": "read"}
        yield {"type": "done", "ok": True}

    with _served(_app(events)) as base:
        body = httpx.post(f"{base}/turn", timeout=20.0).text

    payloads = [line for line in body.splitlines() if line.startswith("data: ")]
    assert len(payloads) == 3
    assert '"type": "turn"' in payloads[0]
    assert '"type": "tool"' in payloads[1]
    assert '"type": "done"' in payloads[2]


def test_a_keepalive_is_a_comment_and_not_an_event():
    # `: ` prefixed lines are ignored by every SSE parser, which is why this can be added without the
    # UI's renderEvent learning anything. A `data:` frame would reach it as an unknown event type.
    def events():
        yield {"type": "done", "ok": True}

    with _served(_app(events)) as base:
        body = httpx.post(f"{base}/turn", timeout=20.0).text

    for line in body.splitlines():
        assert not line.startswith("data: :"), "a keepalive was sent as an event payload"


def test_a_turn_that_raises_still_reports_the_error():
    # The generator runs on a worker thread now, so an exception has to be carried back rather than
    # propagating out of the response. Losing it would turn a failed turn into a silent one.
    def events():
        yield {"type": "turn", "prompt": "p"}
        raise RuntimeError("opencode fell over")

    with _served(_app(events)) as base:
        body = httpx.post(f"{base}/turn", timeout=20.0).text

    assert '"type": "error"' in body
    assert "RuntimeError: opencode fell over" in body
