"""The previewed app can call its model (#7).

The bug this closes: a published app calls Domino's LLM Gateway straight from the viewer's browser,
and that works only because the app and the gateway are both served from `apps.<domino-host>` — the
call is same-origin and the viewer's own cookie authenticates it. The preview is served from the
builder's origin, so the identical call is cross-origin, needs CORS headers the gateway does not
send, and throws before it leaves the page. `appLlm.ts` reports that as "Domino's LLM Gateway is not
answering", which is the one thing that was NOT wrong. An app with a model could not be tried until
it shipped.

So `/api/llm/*` is intercepted in the preview proxy and made server-side, the way `/api/queries/*`
already is. A real loopback server stands in for the gateway rather than a patched client: what these
tests are about is what actually goes over the wire — the bearer the page does not have, the tag
headers that decide whose budget the spend lands in, and whether a stream stays a stream.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from sage.preview.proxy import make_preview_app


class _Gateway:
    """A stand-in for Domino's LLM Gateway on loopback. Records what it was sent."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, dict[str, str], bytes]] = []
        self.hold = threading.Event()      # set by a test to release a held stream
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:      # keep pytest output clean
                pass

            def _record(self) -> bytes:
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.seen.append((self.command, self.path, dict(self.headers), body))
                return body

            def do_GET(self) -> None:
                self._record()
                payload = json.dumps({"data": [{"id": "mimo-v2.5"}, {"id": "sonnet"}]}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self) -> None:
                body = self._record()
                if not json.loads(body or b"{}").get("stream"):
                    payload = json.dumps(
                        {"choices": [{"message": {"content": "hello"}}]}).encode()
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"one"}}]}\n\n')
                self.wfile.flush()
                # Held open until the test has read the first chunk. A proxy that buffered the whole
                # response could not have delivered anything yet, which is what makes the timing
                # assertion below a real check rather than a race.
                outer.hold.wait(timeout=5.0)
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"two"}}]}\n\n')
                self.wfile.write(b"data: [DONE]\n\n")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def stop(self) -> None:
        self.hold.set()
        self._server.shutdown()


def _no_vite() -> str:
    """Vite is not running in these tests, and nothing here should reach it. If something does, this
    is the failure — a 502 from the fall-through path rather than a silent pass."""
    raise RuntimeError("upstream Vite dev server not ready")


@pytest.fixture
def gateway() -> Iterator[_Gateway]:
    gw = _Gateway()
    try:
        yield gw
    finally:
        gw.stop()


def _client(gateway: _Gateway | None, token: str = "tok-123") -> TestClient:
    get_llm = None if gateway is None else (lambda: (gateway.base, token))
    return TestClient(make_preview_app(_no_vite, "", None, get_llm))


@contextmanager
def _served(app) -> Iterator[str]:
    """The preview app behind a real HTTP server, yielding its base URL.

    TestClient will not do for the streaming test: it drives the ASGI app in-process and hands back
    the whole body at once, so a proxy that buffered and one that streamed look identical through it
    — verified by making the proxy buffer on purpose and watching the test still pass. Everything
    else in this file uses TestClient, which is the right tool for those.
    """
    import time

    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "the preview app did not come up"
    try:
        yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


# ---- the call the page cannot make itself ----------------------------------------------------------

def test_a_model_call_in_the_preview_reaches_the_gateway(gateway: _Gateway):
    r = _client(gateway).post("/api/llm/chat/completions",
                              json={"model": "mimo-v2.5", "messages": [], "stream": False})

    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "hello"
    method, path, _headers, body = gateway.seen[-1]
    assert (method, path) == ("POST", "/v1/chat/completions")
    assert json.loads(body)["model"] == "mimo-v2.5"


def test_the_availability_check_answers_too(gateway: _Gateway):
    # `checkModel` runs on load and greys out every control when it fails — which is exactly what the
    # creator saw. It resolves against /v1/models, so that path has to come back as well.
    r = _client(gateway).get("/api/llm/models")

    assert r.status_code == 200
    assert [m["id"] for m in r.json()["data"]] == ["mimo-v2.5", "sonnet"]
    assert gateway.seen[-1][1] == "/v1/models"


def test_the_proxy_supplies_the_credential_the_page_does_not_have(gateway: _Gateway):
    # The whole reason this is a server hop and not a rewritten URL: there is no key on the page, on
    # purpose, and in the preview there is no viewer cookie that the gateway would accept either.
    _client(gateway, token="tok-abc").get("/api/llm/models")

    assert gateway.seen[-1][2]["authorization"] == "Bearer tok-abc"


def test_a_fresh_token_is_taken_per_call(gateway: _Gateway):
    # Sidecar tokens are short-lived. One resolved at mount time would work for the first few minutes
    # of a session and then quietly stop, which reads as the gateway breaking halfway through.
    minted: list[str] = []

    def get_llm() -> tuple[str, str]:
        minted.append(f"tok-{len(minted)}")
        return gateway.base, minted[-1]

    client = TestClient(make_preview_app(_no_vite, "", None, get_llm))
    client.get("/api/llm/models")
    client.get("/api/llm/models")

    assert [call[2]["authorization"] for call in gateway.seen] == ["Bearer tok-0", "Bearer tok-1"]


def test_cost_tags_survive_the_hop(gateway: _Gateway):
    # `tagHeaders()` in appLlm.ts is the only thing that says which app the spend came from — a
    # browser call carries no project context. Dropping them here would put preview spend in
    # "unknown" while the published app's lands correctly, and the two would never reconcile.
    _client(gateway).post("/api/llm/chat/completions", json={"stream": False},
                          headers={"X-LLM-Tag-sage-project": "my-app",
                                   "X-LLM-Tag-sage-component": "built-app"})

    headers = gateway.seen[-1][2]
    assert headers["x-llm-tag-sage-project"] == "my-app"
    assert headers["x-llm-tag-sage-component"] == "built-app"


# ---- streaming -------------------------------------------------------------------------------------

def test_a_streamed_answer_is_streamed_and_not_buffered(gateway: _Gateway):
    """`askModel` turns on `stream: true` whenever the app passes `onToken`. A proxy that read the
    whole response first would deliver a "streaming" answer in one lump — the app would look like it
    works and feel like it does not."""
    import time

    import httpx

    seen: list[tuple[float, bytes]] = []
    app = make_preview_app(_no_vite, "", None, lambda: (gateway.base, "tok"))
    with _served(app) as base:
        # Timed from BEFORE the request, not from the headers: a proxy that reads the whole body
        # first cannot send headers either, so a timer started after them measures nothing.
        started = time.monotonic()
        with httpx.stream("POST", f"{base}/api/llm/chat/completions",
                          json={"stream": True}, timeout=20.0) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            for chunk in r.iter_bytes():
                if not chunk.strip():
                    continue
                seen.append((time.monotonic() - started, chunk))
                gateway.hold.set()   # release the held half, now the first half has landed

    # The gateway held the response open for up to 5s after the first chunk. A proxy that read the
    # whole body before answering could not have delivered anything inside that window.
    assert seen[0][0] < 1.5
    assert b"one" in seen[0][1]
    body = b"".join(chunk for _, chunk in seen)
    assert b"two" in body and b"[DONE]" in body


# ---- when there is no gateway ----------------------------------------------------------------------

def test_without_a_gateway_the_call_falls_through_to_vite(gateway: _Gateway):
    # `openai` and `fake` mode have no Domino gateway, and an app in those was never given one to
    # call. Falling through means Vite 404s it, which `appLlm.ts` already reads as "no model" —
    # rather than this proxy inventing an error for a configuration that is not broken.
    r = _client(None).get("/api/llm/models")

    assert r.status_code == 502
    assert r.json()["preview"] == "upstream Vite dev server not ready"


def test_a_gateway_that_will_not_answer_is_reported_readably(gateway: _Gateway):
    dead = "http://127.0.0.1:9/v1"     # discard port: connections are refused, not hung
    client = TestClient(make_preview_app(_no_vite, "", None, lambda: (dead, "tok")))

    r = client.get("/api/llm/models")

    assert r.status_code == 502
    assert "could not reach" in r.json()["error"]["message"]


def test_a_token_that_cannot_be_minted_says_the_published_app_is_fine(gateway: _Gateway):
    # The sidecar is on loopback and mints these per request, so a failure is Sage's problem. The
    # creator's useful next move is to publish anyway, not to debug their app.
    def get_llm() -> tuple[str, str]:
        raise OSError("sidecar refused the connection")

    r = TestClient(make_preview_app(_no_vite, "", None, get_llm)).get("/api/llm/models")

    assert r.status_code == 502
    assert "published app is unaffected" in r.json()["error"]["message"]
