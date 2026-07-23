"""Preview proxy (SPEC C5, PLAN 3.3).

Reverse-proxies the generated app's Vite dev server into the builder UI: HTTP for the app,
and the HMR WebSocket so edits live-reload inside the preview iframe.

Deep module, narrow interface: `make_preview_app(get_upstream)`. `get_upstream()` returns the
current Vite base URL (e.g. "http://127.0.0.1:5173") — a callable because the supervisor (3.4)
DISCOVERS the real port at runtime (Vite may auto-increment) and Vite can restart on a new port.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

# Hop-by-hop headers must not be forwarded across a proxy.
_HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade", "te", "trailer", "proxy-authorization", "proxy-authenticate"}


def make_preview_app(get_upstream: Callable[[], str], base_prefix: str = "") -> FastAPI:
    """Preview proxy mounted at `/preview` on the control app.

    Vite bakes `base = <base_prefix>/preview/` into the HTML/JS it serves, so it only responds at
    that full path. This app is reached at a prefix-stripped `/{path}` (the control app's ASGI
    middleware strips Domino's proxy prefix, and the `/preview` Mount strips its own segment), so we
    re-add `<base_prefix>/preview` when forwarding upstream to land on Vite's base. `base_prefix` is
    "" for local dev, where `base` is just `/preview/`.
    """
    vite_base = f"{base_prefix}/preview"  # what the browser sees == what Vite serves at

    app = FastAPI(title="sage preview proxy")

    @app.websocket("/{path:path}")
    async def ws_proxy(client_ws: WebSocket, path: str) -> None:
        # Vite HMR connects to "/" with the "vite-hmr" subprotocol. Preserve subprotocol + query.
        subprotocols = client_ws.scope.get("subprotocols", [])
        await client_ws.accept(subprotocol=subprotocols[0] if subprotocols else None)

        base = get_upstream().replace("http://", "ws://").replace("https://", "wss://")
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
        url = f"{get_upstream()}{vite_base}/{path}"
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

    done, pending = await asyncio.wait(
        {asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
