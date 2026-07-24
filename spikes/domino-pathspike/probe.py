"""Phase-0 spike, STEP 2 — diagnostic catch-all.

Purpose: discover how Domino's workspace proxy forwards requests to our port.
It answers two make-or-break unknowns for Path A:
  1. Does Domino PRESERVE its proxy prefix (…/{{owner}}/{{project}}/{{session}}/{{runId}}/)
     in the path our app receives, or STRIP it?
  2. Which in-container env vars let us reconstruct that prefix at launch (so we can set
     Vite's --base without a human copy-paste)?

Run this FIRST (see README). It returns the same JSON for ANY path, so it works whether or
not Domino preserves the prefix. Open the tool in your browser, copy the JSON, send it back.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()


@app.api_route("/{full_path:path}", methods=["GET", "HEAD"])
async def anything(request: Request, full_path: str):
    return JSONResponse(
        {
            # The single most important field: what path did Domino hand us for the tool root?
            "received_path": request.url.path,
            "root_path": request.scope.get("root_path"),
            "query": str(request.url.query),
            "x_forwarded": {
                k: v for k, v in request.headers.items() if k.lower().startswith("x-forwarded")
            },
            "all_headers": dict(request.headers),
            # Everything Domino injected — we want owner / project / run / session ids.
            "domino_env": {
                k: v
                for k, v in sorted(os.environ.items())
                if "DOMINO" in k.upper() or k in ("RUN_ID", "RUN_NUMBER", "SESSION_ID")
            },
        },
        media_type="application/json",
    )
