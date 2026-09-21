"""Sage's half of this app's server (#490). `app.py` calls `mount(app)` once; the rest of `app.py` is
the creator's.

A fastapi-antd app has no build step: the page and everything it loads are files under `static/`,
served as they are. What this file adds is what every published app needs and no creator should
have to write:

  - the page, with the mount-prefix shim stamped in (a Domino App is served under a path its own
    files cannot know, see `inject_base_shim`);
  - the static tree and the attachments under `public/data/`, served with `no-cache` so an edit
    reaches the preview on the next request and a republish reaches every viewer;
  - the app's named queries, answered by `sage_queries.py` beside this file — the same module the
    react-vite stack's server mounts, so the two stacks cannot disagree about a refusal;
  - the boot lines Domino surfaces in the App's log: cold start, the token sidecar, the data
    library, the platform API host.

Refreshed from the template at publish like `sage_queries.py` (`Stack.deploy_files`), so a fix here
reaches every app that deploys. The agent is told not to edit it.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
# `sage_queries.py` sits beside this file in the app's repo. `uvicorn app:app` runs with the repo
# root on the path already; Sage's tests, which load this file by path, do not.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import sage_queries as sq

try:
    import sage_domino  # the platform-API relay (#489), shipped as its own deploy file
except ImportError:  # an app born before the relay existed serves without it
    sage_domino = None

_STATIC = ROOT / "static"
_DATA = ROOT / "public" / "data"
_INDEX = _STATIC / "index.html"

# --- mount-prefix shim -------------------------------------------------------------------------
# Domino's app proxy strips the app's mount prefix before a request arrives, so this server sees `/`
# while the viewer's URL is `/apps/<uuid>/`. Every URL in the page is relative for that reason, and a
# relative URL needs a base to resolve against. The two halves of the answer sit on opposite sides of
# the proxy — this server knows the path it RECEIVED, the browser knows the path it IS ON, and the
# difference is the prefix — so the received path is stamped into the page and a shim subtracts:
#
#     received /    on /apps/uuid/    ->  prefix /apps/uuid
#
# The shim writes <base href="/apps/uuid/"> before any script or stylesheet is fetched and publishes
# the prefix as window.__SAGE_BASE__, which `static/sage/appBase.js` reads. Deployment-agnostic on
# purpose: `/apps/{uuid}/`, `/apps-internal/{id}/` and `/u/{owner}/{project}/app/` all reach the
# same app, so nothing decided in a file can be right for all of them. The same shim serves the
# preview, where the prefix is Sage's own `<prefix>/preview`.
#
# A viewer who lands on `/apps/uuid` with no trailing slash is on a path that does not END with the
# received `/`; the whole path is then the prefix, and the base is written with the slash the link
# left out. Any other mismatch writes no base and leaves the page as it is.
#
# `__SAGE_PREVIEW__` says whether Sage's builder is serving this page: the helpers report runtime
# errors to it and route model calls through it, and a published app has no builder to reach.
_BASE_SHIM = (
    "<script>/* sage: recover the mount prefix the app proxy stripped */(function(){"
    "var s={served};var h=location.pathname;var p;"
    "if(h.slice(h.length-s.length)===s){p=h.slice(0,h.length-s.length)}"
    'else if(s==="/"){p=h}else{window.__SAGE_BASE__="";return}'
    "window.__SAGE_BASE__=p;window.__SAGE_PREVIEW__={preview};"
    "document.write('<base href=\"'+p+'/\">')"
    "})();</script>"
)
_HEAD_OPEN = re.compile(r"<head\b[^>]*>", re.IGNORECASE)


def inject_base_shim(html: str, received_path: str, preview: bool = False) -> str:
    """`html` with the shim as the first thing in <head>, carrying the path this server received.

    First in <head> because `document.write` inserts at the parser's position: the <base> has to land
    before the <script src> and <link href> tags whose URLs it governs.
    """
    shim = (_BASE_SHIM.replace("{served}", json.dumps(received_path))
            .replace("{preview}", "true" if preview else "false"))
    m = _HEAD_OPEN.search(html)
    if m:
        return html[: m.end()] + shim + html[m.end():]
    return shim + html


def is_preview() -> bool:
    """Whether Sage's builder started this server. The supervisor sets the variable; nothing else
    does, and a published App never has it."""
    return os.environ.get("SAGE_PREVIEW") == "1"


class _Revalidating(StaticFiles):
    """Static files that are asked about every time. Nothing here carries a hash in its name, so a
    year-long `immutable` would let a viewer's tab keep running the previous deploy's JS. `no-cache`
    means "ask every time", not "do not store": the ETag Starlette already sends turns the question
    into a 304 whenever the bytes are unchanged."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class _State:
    """What the query route reads: the catalog checked once at startup, and the executor."""

    def __init__(self) -> None:
        self.queries: dict = {}
        self.executor = sq.unavailable_executor


def mount(app: FastAPI, *, executor=None) -> _State:
    """Give `app` its page, its static tree, its attachments and its named queries.

    `executor` is the seam `sage_queries.build_server` has: a callable taking (query, params) and
    returning {columns, rows}. The default is the real one — Arrow Flight through the Domino SDK,
    reading a fresh token from the sidecar per call — and a test passes its own.
    """
    state = _State()
    state.queries = sq.load_queries(ROOT)
    state.executor = executor or sq.FlightExecutor(sq.load_sources(ROOT), sq.max_rows(),
                                                   preview=is_preview())
    preview = is_preview()

    @app.get("/", include_in_schema=False)
    def index(request: Request) -> Response:
        try:
            html = _INDEX.read_text(encoding="utf-8")
        except OSError:
            return JSONResponse({"error": "static/index.html is missing from this app."},
                                status_code=404)
        return HTMLResponse(inject_base_shim(html, request.url.path, preview),
                            headers={"Cache-Control": "no-cache"})

    @app.post("/api/queries/{name}", include_in_schema=False)
    async def named_query(name: str, request: Request) -> Response:
        body = await request.body()
        if len(body) > sq._MAX_BODY:
            return JSONResponse({"error": "The request body is too large."}, status_code=413)
        try:
            payload = json.loads(body) if body else {}
        except ValueError:
            return JSONResponse({"error": "The request body is not valid JSON."}, status_code=400)
        status, answer = sq.answer(state.queries, state.executor, name,
                                   payload if isinstance(payload, dict) else {})
        return JSONResponse(answer, status_code=status)

    @app.get("/api/queries/{name}", include_in_schema=False)
    def named_query_get(name: str) -> Response:
        # POST-only, and saying so beats a 404 that reads as a missing query.
        return JSONResponse({"error": "This endpoint takes POST."}, status_code=405)

    if sage_domino is not None:
        @app.get(sage_domino.RELAY_PREFIX + "{path:path}", include_in_schema=False)
        def platform_relay(path: str, request: Request) -> Response:
            # GET only and allow-listed inside `sage_domino.relay` (#489); this route adds nothing
            # to it, and the headers it answers with are the relay's own.
            status, headers, payload = sage_domino.relay(path, str(request.url.query))
            return Response(payload, status_code=status, headers=headers)

    # Sub-paths only, never `/`: a mount at the root would answer for every route the creator adds
    # after this call, and the order of `app.py` would silently decide whether their API exists.
    app.mount("/static", _Revalidating(directory=str(_STATIC), check_dir=False), name="static")
    # Attachments are symlinks into Domino dataset mounts (`scripts/rehydrate_data.py`), which
    # Starlette refuses to follow unless told to. The directory may not exist for an app with none.
    app.mount("/data", _Revalidating(directory=str(_DATA), check_dir=False, follow_symlink=True),
              name="data")

    _log_boot(state, preview)
    return state


def _cold_start_secs() -> float | None:
    """Seconds since app.sh started. None when app.sh did not export a start time — the caller
    then says nothing rather than reporting a 0s cold start (ADR-0002)."""
    try:
        return time.time() - float(os.environ["SAGE_APP_T0"])
    except (KeyError, ValueError):
        return None


def _log_boot(state: _State, preview: bool) -> None:
    """The lines a creator reads in the App's log when something is wrong, once, at startup.

    Diagnostics run in threads: an unreachable sidecar costs its timeout and the data library costs
    its import, and paying either before uvicorn binds would leave the first viewer's request
    waiting for it.
    """
    if preview:
        return  # Sage's own log carries the preview; these lines are for the App's
    sq.log_query_catalog(state.queries)
    elapsed = _cold_start_secs()
    print(f"[sage] serving {_STATIC} with no build step", flush=True)
    if elapsed is not None:
        print(f"[sage] cold start: {elapsed:.0f}s to serving {_STATIC}", flush=True)
    threading.Thread(target=sq.log_sidecar_status, daemon=True).start()
    if state.queries:
        print(f"[sage] queries return at most {sq.max_rows()} rows (SAGE_QUERY_MAX_ROWS)", flush=True)
        threading.Thread(target=sq.log_data_library, daemon=True).start()
    if sage_domino is not None:
        # Whether this container has a platform API host, beside the sidecar line (#489). Host and
        # status only — the relay's own line, so both stacks' logs read the same.
        threading.Thread(target=sage_domino.log_platform_status, daemon=True).start()
