"""Phase-0 spike, STEP 3 — the real Path-A proxy.

Serves, on ONE port (the pluggable-tool port), under Domino's proxy prefix:
  <prefix>/whoami       diagnostic
  <prefix>/preview/*    reverse-proxy to the Vite dev server (HTTP + HMR websocket)
  <prefix>/             landing page that iframes ./preview/

Design: EVERYTHING speaks the prefixed path. The browser hits <prefix>/preview/x, Domino
forwards <prefix>/preview/x to us (it PRESERVES the path, like it does for JupyterLab's
base_url — pluggable-tools.yaml sets rewrite:false to keep it that way), we forward the SAME
path to Vite, and Vite is configured with base=<prefix>/preview/ so it serves and hot-reloads
at that exact path. No stripping, no re-adding — one consistent path end to end.

SAGE_BASE_PREFIX is the Domino proxy prefix discovered in STEP 2 (e.g.
"/sub-user/sage-spike/abc123/1"), no trailing slash. Empty is valid (routes collapse to /).
"""
from __future__ import annotations

import asyncio
import os

import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

VITE = os.environ.get("SPIKE_VITE_URL", "http://127.0.0.1:5173").rstrip("/")
PREFIX = os.environ.get("SAGE_BASE_PREFIX", "").rstrip("/")

_HOP = {
    "connection", "keep-alive", "transfer-encoding", "upgrade", "te", "trailer",
    "proxy-authorization", "proxy-authenticate",
}

app = FastAPI()

_PREVIEW = f"{PREFIX}/preview/{{path:path}}"


@app.get(f"{PREFIX}/whoami")
async def whoami(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "received_path": request.url.path,
            "configured_prefix": PREFIX,
            "vite_upstream": VITE,
            "domino_env": {k: v for k, v in sorted(os.environ.items()) if "DOMINO" in k.upper()},
        }
    )


@app.get(f"{PREFIX}/")
async def index() -> HTMLResponse:
    # iframe src is RELATIVE ("preview/") so it resolves under whatever the Domino prefix is.
    return HTMLResponse(
        f"""<!doctype html><meta charset="utf-8"><title>sage path spike</title>
<body style="margin:0;font-family:system-ui">
  <div style="padding:8px 12px;background:#1820A0;color:#fff;font-size:13px">
    sage path spike &middot; <a style="color:#C9C5F2" href="whoami">whoami</a>
    &middot; prefix=<code>{PREFIX or "(empty)"}</code>
    &middot; success = counter app renders below AND edits to
      <code>app/src/App.jsx</code> hot-reload without a full refresh.
  </div>
  <iframe src="preview/" style="border:0;width:100vw;height:calc(100vh - 36px)"></iframe>
</body>"""
    )


@app.websocket(_PREVIEW)
async def ws_proxy(client: WebSocket, path: str) -> None:
    subs = client.scope.get("subprotocols", [])
    await client.accept(subprotocol=subs[0] if subs else None)
    base = VITE.replace("http://", "ws://").replace("https://", "wss://")
    q = client.url.query
    # Forward the SAME prefixed path Vite expects (its base includes the prefix).
    url = f"{base}{PREFIX}/preview/{path}" + (f"?{q}" if q else "")
    try:
        async with websockets.connect(url, subprotocols=subs or None) as up:
            await _pump(client, up)
    except (WebSocketDisconnect, websockets.WebSocketException, OSError):
        pass
    finally:
        try:
            await client.close()
        except RuntimeError:
            pass


@app.api_route(_PREVIEW, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def http_proxy(request: Request, path: str) -> Response:
    url = f"{VITE}{PREFIX}/preview/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    body = await request.body()
    client = httpx.AsyncClient(timeout=30.0)
    req = client.build_request(request.method, url, headers=headers, params=request.query_params, content=body)
    up = await client.send(req, stream=True)
    resp_headers = {k: v for k, v in up.headers.items() if k.lower() not in _HOP}

    async def body_iter():
        try:
            async for chunk in up.aiter_raw():
                yield chunk
        finally:
            await up.aclose()
            await client.aclose()

    return StreamingResponse(body_iter(), status_code=up.status_code, headers=resp_headers)


async def _pump(client: WebSocket, up) -> None:
    async def c2u() -> None:
        while True:
            m = await client.receive()
            if m["type"] == "websocket.disconnect":
                return
            if (t := m.get("text")) is not None:
                await up.send(t)
            elif (b := m.get("bytes")) is not None:
                await up.send(b)

    async def u2c() -> None:
        async for m in up:
            if isinstance(m, bytes):
                await client.send_bytes(m)
            else:
                await client.send_text(m)

    _, pending = await asyncio.wait(
        {asyncio.create_task(c2u()), asyncio.create_task(u2c())},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
