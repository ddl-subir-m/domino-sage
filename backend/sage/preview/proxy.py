"""Preview proxy (SPEC C5, PLAN 3.3).

Reverse-proxies the generated app's Vite dev server into the builder UI: HTTP for the app,
and the HMR WebSocket so edits live-reload inside the preview iframe.

Deep module, narrow interface: `make_preview_app(get_upstream, base_prefix, get_queries)`.
`get_upstream()` returns the current Vite base URL (e.g. "http://127.0.0.1:5173") — a callable
because the supervisor (3.4) DISCOVERS the real port at runtime (Vite may auto-increment) and Vite
can restart on a new port.

`get_queries()` returns the project's `PreviewQueries`, or None. It is here because this proxy is
the only thing between the previewed page and a server, so `/api/queries/*` is intercepted on the
way past and answered by `serve.py` itself rather than 404'd by Vite, which has never served an
app's data (#24). Also a callable, and for the same reason: the project it belongs to is attached
lazily on the first request.

`get_llm()` returns `(gateway_v1_base, bearer)` for the app's own model calls, or None. Same
reasoning one step further: a published app calls Domino's LLM Gateway straight from the viewer's
browser, which works only because both are served from `apps.<domino-host>` and the call is
therefore same-origin. The preview is served from the builder's origin, so that same call is
cross-origin and the browser blocks it — an app with a model was untestable until it shipped. So
`/api/llm/*` is intercepted here too and made server-side (#7).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

log = logging.getLogger(__name__)

# Hop-by-hop headers must not be forwarded across a proxy.
_HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade", "te", "trailer", "proxy-authorization", "proxy-authenticate"}

# What `sageQuery.ts` asks for, minus the leading slash this app's paths arrive without.
_QUERY_PREFIX = "api/queries/"

# What `sageLlm.ts` asks for in the preview only — the published build calls the gateway directly.
_LLM_PREFIX = "api/llm/"

# Headers the app sets that must survive the hop. The tag headers are how spend is attributed to the
# app (see `tagHeaders` in sageLlm.ts); dropping them would put preview traffic in "unknown".
_LLM_FORWARD = ("content-type", "accept")

# No read timeout, on purpose. A scalar httpx timeout becomes the INTER-CHUNK read timeout on a
# stream, so a model that thinks for longer than it between two tokens kills a working response —
# the same failure that took builds down mid-stream. The connect budget still bounds a dead gateway.
_LLM_TIMEOUT = httpx.Timeout(15.0, read=None)

# A warehouse query is seconds, not milliseconds — the live baseline is 2.0-3.9s and a first scan of
# a cold table is slower. Generous, but bounded: a Flight call that never returns would otherwise
# hold the creator's request open until they gave up on the preview rather than on the query. The
# handler thread behind it stays stuck (a blocking C call cannot be interrupted), which is why they
# are daemon threads — a few leaked ones are survivable and none of them delay shutdown.
_QUERY_TIMEOUT_S = 60.0


async def _answer_query(request: Request, path: str, queries) -> Response | None:
    """One named-query call, answered by `serve.py` on loopback. None when there is nobody to ask.

    Everything the creator reads on a failure here is `serve.py`'s own sentence, forwarded verbatim
    with its own status — that is the whole point of running the real module rather than a second
    implementation, and rewording anything in flight would undo it.
    """
    if queries is None or queries.port is None:
        return None
    queries.refresh()   # the agent may have rewritten the catalog since the last request
    url = f"http://127.0.0.1:{queries.port}/{path}"
    try:
        async with httpx.AsyncClient(timeout=_QUERY_TIMEOUT_S) as client:
            answer = await client.request(
                request.method, url, content=await request.body(),
                headers={"content-type": request.headers.get("content-type", "application/json")},
            )
    except httpx.TimeoutException:
        return JSONResponse(status_code=504, content={"error": (
            "This query is taking longer than the preview will wait. It may still be fine in the "
            "published app — or it may be scanning more than it needs to."
        )})
    except (httpx.HTTPError, OSError) as e:
        # The server is ours and on loopback, so this is a bug rather than a condition. Say so
        # plainly instead of dressing it as something the creator can act on.
        return JSONResponse(status_code=502, content={
            "error": "Sage could not reach this app's query server in the preview.",
            "detail": f"{type(e).__name__}: {e}",
        })
    return Response(content=answer.content, status_code=answer.status_code,
                    media_type=answer.headers.get("content-type", "application/json"))


async def _forward_llm(request: Request, path: str, get_llm) -> Response | None:
    """One LLM Gateway call from the previewed app, made server-side. None when there is no gateway.

    Why it cannot just go to the gateway like the published app's does: `sageLlm.ts` is built around
    the call being SAME-ORIGIN. A published app is served from `apps.<domino-host>` and so is the
    gateway, so the viewer's own Domino cookie authenticates it with no key on the page and no server
    hop — which is the whole design, and it is worth keeping. The preview is served from the
    builder's origin instead, so the identical call is cross-origin, and a credentialed cross-origin
    fetch needs `Access-Control-Allow-Origin` naming that exact origin plus `Allow-Credentials`,
    which the gateway does not send. The fetch throws, and the app reports the gateway as not
    answering when the gateway is fine. So an app with a model could not be tried until it shipped.

    One difference from the published path is real and deliberate: this spends SAGE's credential, so
    preview traffic is attributed to whoever is building, and it does not exercise a viewer's own
    grant on the Alias. Both are correct here — in the preview the viewer IS the builder — and the
    per-viewer check is `checkModel`'s job in the published app, where each viewer really does call
    under their own identity.
    """
    if get_llm is None:
        return None
    try:
        resolved = await run_in_threadpool(get_llm)
    except Exception as e:
        # The sidecar mints these per request and is on loopback, so a failure here is Sage's
        # problem, not something the creator can act on. Say which.
        log.warning("preview llm: could not resolve a gateway token: %s", e)
        return JSONResponse(status_code=502, content={"error": {"message": (
            "Sage could not get a token for Domino's LLM Gateway, so the preview cannot make this "
            "app's model calls. The published app is unaffected — it calls the gateway directly."
        )}})
    if resolved is None:
        return None    # no Domino gateway configured; fall through to Vite, which 404s
    base, token = resolved

    headers = {k: v for k, v in request.headers.items()
               if k.lower() in _LLM_FORWARD or k.lower().startswith("x-llm-tag-")}
    headers["authorization"] = f"Bearer {token}"
    url = f"{base}/{path[len(_LLM_PREFIX):].lstrip('/')}"
    client = httpx.AsyncClient(timeout=_LLM_TIMEOUT)
    try:
        upstream = await client.send(
            client.build_request(request.method, url, content=await request.body(), headers=headers),
            stream=True,
        )
    except (httpx.HTTPError, OSError) as e:
        await client.aclose()
        log.warning("preview llm: %s %s failed: %s", request.method, url, e)
        return JSONResponse(status_code=502, content={"error": {"message": (
            "The preview could not reach Domino's LLM Gateway."
        )}})

    # Streamed rather than read whole: `askModel` turns on `stream: true` whenever the app passes
    # `onToken`, and buffering the response here would deliver a "streaming" answer in one lump —
    # the app would look like it works and feel like it does not.
    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    passthrough = {k: v for k, v in upstream.headers.items()
                   if k.lower() not in _HOP and k.lower() != "content-length"}
    return StreamingResponse(body(), status_code=upstream.status_code, headers=passthrough)


def make_preview_app(get_upstream: Callable[[], str], base_prefix: str = "",
                     get_queries: Callable[[], object | None] | None = None,
                     get_llm: Callable[[], tuple[str, str] | None] | None = None) -> FastAPI:
    """Preview proxy mounted at `/preview` on the control app.

    Vite bakes `base = <base_prefix>/preview/` into the HTML/JS it serves, so it only responds at
    that full path. This app is reached at a prefix-stripped `/{path}` (the control app's ASGI
    middleware strips Domino's proxy prefix, and the `/preview` Mount strips its own segment), so we
    re-add `<base_prefix>/preview` when forwarding upstream to land on Vite's base. `base_prefix` is
    "" for local dev, where `base` is just `/preview/`.

    `get_queries` is optional: without it — and whenever it answers None — `/api/queries/*` goes to
    Vite and 404s exactly as it did before #24, which `sageQuery.ts` already reads correctly as
    "not published yet".
    """
    vite_base = f"{base_prefix}/preview"  # what the browser sees == what Vite serves at

    app = FastAPI(title="sage preview proxy")

    def _starting(detail: str) -> JSONResponse:
        # 502, not 500: the upstream Vite dev server isn't ready — still booting on first launch, or
        # restarting/re-optimizing after the agent installs a dependency. Transient; a refresh recovers.
        return JSONResponse(
            status_code=502,
            content={
                "preview": "upstream Vite dev server not ready",
                "error": detail,
                "hint": "The preview server is still starting or reloading (first launch installs deps; "
                "adding a dependency triggers a restart). Wait a moment, then refresh.",
            },
        )

    @app.websocket("/{path:path}")
    async def ws_proxy(client_ws: WebSocket, path: str) -> None:
        # Vite HMR connects to "/" with the "vite-hmr" subprotocol. Preserve subprotocol + query.
        subprotocols = client_ws.scope.get("subprotocols", [])
        await client_ws.accept(subprotocol=subprotocols[0] if subprotocols else None)

        try:
            upstream = get_upstream()  # raises RuntimeError while Vite is (re)starting
        except Exception:
            # Nothing to proxy yet; close cleanly — the Vite HMR client reconnects on its own.
            await client_ws.close()
            return
        base = upstream.replace("http://", "ws://").replace("https://", "wss://")
        query = client_ws.url.query
        upstream_url = f"{base}{vite_base}/{path}" + (f"?{query}" if query else "")

        try:
            async with websockets.connect(upstream_url, subprotocols=subprotocols or None) as up:
                await _pump(client_ws, up)
        except (WebSocketDisconnect, websockets.WebSocketException, OSError):
            pass
        finally:
            try:
                await client_ws.close()
            except RuntimeError:
                pass

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    async def http_proxy(request: Request, path: str) -> Response:
        # The app's own named queries, before Vite gets a chance to 404 them (#24). Vite serves the
        # app; it has never served its data. Answered here rather than by a route on the control app
        # because this proxy is already the one thing standing between the previewed page and its
        # server, so it is the only place the interception costs nothing to reach.
        if path.startswith(_QUERY_PREFIX):
            answered = await _answer_query(request, path, get_queries and get_queries())
            if answered is not None:
                return answered
            # Fall through when there is no query server: Vite 404s, and `sageQuery.ts` already has
            # the right sentence for that — "only available once it is published". Which is true.
        if path.startswith(_LLM_PREFIX):
            # Same shape, one line up the stack: the previewed page cannot make this call itself
            # (cross-origin), so the proxy makes it. Falls through to Vite when no gateway is
            # configured, and `sageLlm.ts` reads that 404 as "this app has no model", as it should.
            forwarded = await _forward_llm(request, path, get_llm)
            if forwarded is not None:
                return forwarded
        try:
            upstream = get_upstream()  # raises RuntimeError while Vite is (re)starting
        except Exception as e:
            return _starting(f"{type(e).__name__}: {e}")
        url = f"{upstream}{vite_base}/{path}"
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
        body = await request.body()
        client = httpx.AsyncClient(timeout=30.0)
        req = client.build_request(request.method, url, headers=headers, params=request.query_params, content=body)
        try:
            upstream = await client.send(req, stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError, OSError) as e:
            await client.aclose()
            return JSONResponse(
                status_code=502,
                content={
                    "preview": "upstream Vite dev server not reachable",
                    "error": f"{type(e).__name__}: {e}",
                    "hint": "The preview server is still starting (first launch installs deps). "
                    "Check the workspace logs for Vite's 'Local:' line, then refresh.",
                },
            )
        resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP}

        async def body_iter():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(body_iter(), status_code=upstream.status_code, headers=resp_headers)

    return app


async def _pump(client_ws: WebSocket, upstream) -> None:
    """Shuttle messages both ways until either side closes."""

    async def client_to_upstream() -> None:
        while True:
            msg = await client_ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if (t := msg.get("text")) is not None:
                await upstream.send(t)
            elif (b := msg.get("bytes")) is not None:
                await upstream.send(b)

    async def upstream_to_client() -> None:
        async for msg in upstream:
            if isinstance(msg, bytes):
                await client_ws.send_bytes(msg)
            else:
                await client_ws.send_text(msg)

    _done, pending = await asyncio.wait(
        {asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
