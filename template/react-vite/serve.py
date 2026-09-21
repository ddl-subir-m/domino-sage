#!/usr/bin/env python3
"""Serve this app's production build as a Domino App (Phase 5).

`app.sh` still installs dependencies and runs `vite build` with Node — only the process that stays
up is Python: this file replaces `vite preview`. See ADR-0002 for why. In short, querying a Domino
Data Source is possible only over Arrow Flight gRPC, whose one client is the Python SDK already
installed in the image, so the process holding the socket has to be a Python one.

It serves the build, and it answers named queries against the Data Sources the app is bound to. The
queries themselves live in `sage_queries.py` beside this file — the half of the server every stack
Sage seeds shares, and the half Sage loads by path during a build to check a catalog — and this file
mounts that route beside the static tree. `sage_domino.py`, beside it too, is the page's read-only
road to the Domino platform API (#489); this file mounts that route the same way.

Stdlib for everything, like spikes/domino-probes/viewer_identity_app/probe_server.py — so this file
imports and serves under any python3 the image ships.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import time
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

# `sage_queries.py` sits beside this file in the app's repo. Running `python3 serve.py` puts that
# directory first on the path already; Sage's tests, which load this file by path, do not.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import sage_domino as sd
import sage_queries as sq

# Content types we pin rather than ask the OS for. `mimetypes` consults /etc/mime.types, so the type
# a file gets would otherwise depend on the base image — and a module served as octet-stream is not a
# soft failure: the browser refuses to execute it and the app renders blank.
_TYPES = {
    ".avif": "image/avif",
    ".css": "text/css",
    ".html": "text/html",
    ".js": "text/javascript",
    ".json": "application/json",
    ".map": "application/json",
    ".mjs": "text/javascript",
    ".svg": "image/svg+xml",
    ".wasm": "application/wasm",
    ".webmanifest": "application/manifest+json",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}
# Types that need a charset so a UTF-8 build isn't decoded as latin-1.
_TEXTUAL = ("text/", "application/json", "application/manifest+json", "image/svg+xml")

# Vite hashes everything it emits into assets/, so those URLs are safe to cache forever. Nothing
# else is: index.html keeps its name across deploys, and a cached copy would go on pointing at the
# previous deploy's assets after a republish.
_HASHED_PREFIX = "/assets/"


# --- mount-prefix shim (#18) --------------------------------------------------------------------
# The proxy strips the app's mount prefix before the request arrives, so the build is served at root
# while the viewer's URL still carries the prefix. That is why the build base is relative — and why a
# route two or more segments deep asks for its assets one directory too deep and gets nothing.
#
# No header is needed to fix it, because the two halves of the answer sit on opposite sides of the
# proxy: this server knows the path it RECEIVED, the browser knows the path it IS ON, and the
# difference is the prefix. Stamp the received path into the page and let a shim subtract:
#
#     received /reports/2026    on /apps/uuid/reports/2026    ->  prefix /apps/uuid
#
# The shim then writes <base href="/apps/uuid/"> before any module script is parsed, which fixes every
# relative asset URL at any depth, and publishes the prefix as window.__SAGE_BASE__ for the router's
# basename — routing is the other half of the same bug: without it, react-router matches the viewer's
# full path against routes that were written without the prefix.
#
# Deployment-agnostic on purpose: the prefix is not one shape per deployment. `/apps/{uuid}/`,
# `/apps-internal/{id}/` and `/u/{owner}/{project}/app/` all reach the same app, so it is a property of
# the link the viewer clicked and nothing decided at build time can be right for all of them.
#
# When the subtraction does not hold — no prefix at all, or the received path is not a suffix of the
# browser's — the shim writes no base and leaves today's relative behaviour untouched.
_BASE_SHIM = (
    "<script>/* sage: recover the mount prefix the app proxy stripped */(function(){"
    "var s={served};var h=location.pathname;var p=h.slice(0,h.length-s.length);"
    'if(h.slice(p.length)!==s){window.__SAGE_BASE__="";return}'
    "window.__SAGE_BASE__=p;"
    "document.write('<base href=\"'+p+'/\">')"
    "})();</script>"
)
_HEAD_OPEN = re.compile(r"<head\b[^>]*>", re.IGNORECASE)


def inject_base_shim(html: str, received_path: str) -> str:
    """`html` with the shim as the first thing in <head>, carrying the path this server received.

    First in <head> because `document.write` inserts at the parser's position: the <base> has to land
    before the <script src> and <link href> tags whose URLs it governs.
    """
    shim = _BASE_SHIM.replace("{served}", json.dumps(received_path))
    m = _HEAD_OPEN.search(html)
    if m:
        return html[: m.end()] + shim + html[m.end() :]
    return shim + html  # no <head> to aim at, but still ahead of every tag that resolves a URL


class _Handler(sq.QueryRoute, SimpleHTTPRequestHandler):
    """Static handler for a Vite build: SPA fallback, pinned content types, no directory listings —
    with the named-query route mixed in ahead of it.

    One deliberate gap against what `vite preview` served: no `Range` support, so a byte-range
    request gets the whole file at 200 instead of a 206. Nothing the template can build needs it —
    the toolbox has no media player and attachments are fetched whole — but a `<video>` in public/
    would not seek in Safari, which asks for a range before it will play. Add ranges then, not now.
    """

    # Keep-alive, so a page's dozens of asset requests don't each pay a new connection. Safe because
    # every response below carries a Content-Length. It does mean an idle connection holds its thread
    # until the client closes, so cap the wait — otherwise a client that connects and says nothing
    # costs a thread indefinitely.
    protocol_version = "HTTP/1.1"
    timeout = 30
    # Don't announce the interpreter and its exact version to every viewer.
    server_version = "sage"
    sys_version = ""

    def guess_type(self, path):
        ext = os.path.splitext(str(path))[1].lower()
        ctype = _TYPES.get(ext) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return f"{ctype}; charset=utf-8" if ctype.startswith(_TEXTUAL) else ctype

    def do_GET(self):
        # The platform relay, ahead of the static tree: its paths are extensionless, so the SPA
        # rewrite below would otherwise answer every one of them with index.html.
        if self._is_platform_path():
            return self._relay_platform()
        super().do_GET()

    def do_POST(self):
        if self._is_platform_path():
            return self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "This endpoint takes GET."})
        super().do_POST()

    def _is_platform_path(self) -> bool:
        return urlsplit(self.path).path.startswith(sd.RELAY_PREFIX)

    def _relay_platform(self) -> None:
        """`GET /api/domino/<path>` → `sage_domino.relay`, whose answer is written as it came: the
        platform's status and body, or the relay's own sentence with its own status."""
        parts = urlsplit(self.path)
        status, ctype, body = sd.relay(parts.path[len(sd.RELAY_PREFIX):], parts.query)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_head(self):
        # A query path is POST-only. Answering it from the static tree would send the SPA rewrite
        # below an extensionless path and hand back index.html — a 200 of HTML where the caller
        # expects JSON, which reads as a broken app rather than as the wrong method.
        if self._is_query_path():
            self._send_json(HTTPStatus.METHOD_NOT_ALLOWED,
                            {"error": "This endpoint takes POST."})
            return None
        # Only HEAD reaches here on a relay path — do_GET answered the GET — and for the same reason.
        if self._is_platform_path():
            self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "This endpoint takes GET."})
            return None
        # Before the SPA rewrite, so this is the path that ARRIVED — and still percent-encoded,
        # because the shim subtracts it from location.pathname, which is encoded too.
        received = urlsplit(self.path).path
        self._resolve_spa_route()
        index = self._index_html_target()
        if index is not None:
            return self._send_patched_index(index, received)
        return super().send_head()

    def _index_html_target(self) -> Path | None:
        """The index.html that will answer this request, or None if a plain file will.

        Two paths lead to it and both need the shim: the SPA rewrite points a route at /index.html,
        and a directory URL — "/" above all — is answered with that directory's index by
        SimpleHTTPRequestHandler without any rewrite of ours.
        """
        target = Path(self.translate_path(urlsplit(self.path).path))
        if target.is_dir():
            target = target / "index.html"
        return target if target.name == "index.html" and target.is_file() else None

    def _send_patched_index(self, index: Path, received: str):
        """index.html with the mount-prefix shim. Built in memory rather than streamed from disk, so
        the Content-Length counts the shim."""
        try:
            html = index.read_text(encoding="utf-8")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None
        body = inject_base_shim(html, received).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        return BytesIO(body)

    def send_response_only(self, code, message=None):
        self._status = int(code)  # end_headers needs it; the base class keeps no record
        super().send_response_only(code, message)

    def end_headers(self):
        # Only a response that actually carries the asset earns the immutable year. Caching a 404
        # that long outlives whatever caused it.
        immutable = getattr(self, "_status", 500) < 400 and urlsplit(self.path).path.startswith(
            _HASHED_PREFIX
        )
        self.send_header(
            "Cache-Control", "public, max-age=31536000, immutable" if immutable else "no-cache"
        )
        super().end_headers()

    def list_directory(self, path):
        # `vite preview` has no listings either, and the build's file names are nobody's business.
        self.send_error(HTTPStatus.NOT_FOUND, "File not found")

    def _resolve_spa_route(self) -> None:
        """Rewrite an unmatched extensionless path to index.html, as Vite's html fallback does.

        The split is on the extension, not on the Accept header, because it decides who hears about
        a missing file. An extensionless path is a client-side route (react-router-dom is in the
        template's toolbox), so index.html is the right answer. A path WITH an extension is an asset
        request, and answering it with HTML would turn a broken build into a blank page and a console
        error instead of a 404 naming the file.
        """
        rel = urlsplit(self.path).path
        if os.path.splitext(rel)[1] or Path(self.translate_path(self.path)).exists():
            return
        self.path = "/index.html"


def build_server(root: Path | str, *, host: str = "0.0.0.0", port: int = 8888,
                 project_root: Path | str | None = None, executor=None) -> ThreadingHTTPServer:
    """A bound (not yet serving) server for the build at `root`. Threaded so one slow client can't
    hold up the rest of a page's assets.

    `project_root` is where the app's `.sage/` manifests live — the repo root, which is the working
    directory app.sh runs from, not the `dist/` being served. `executor` is the seam: a callable
    taking (query, params) and returning {columns, rows}. It is injected rather than imported so a
    test can prove the boundary without a store, and so #14 can put a real one behind it without
    touching anything above.
    """
    srv = ThreadingHTTPServer((host, port), partial(_Handler, directory=str(root)))
    srv.sage_queries = sq.load_queries(Path(project_root or "."))
    srv.sage_executor = executor or sq.unavailable_executor
    return srv


def _cold_start_secs() -> float | None:
    """Seconds since app.sh started, which is what a viewer waits through: dependency install, build,
    and this server coming up. None when app.sh didn't export a start time — the caller then says
    nothing rather than reporting a 0s cold start that ADR-0002 would have someone record as the
    baseline."""
    try:
        return time.time() - float(os.environ["SAGE_APP_T0"])
    except (KeyError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Serve this app's production build.")
    ap.add_argument("--dir", default="dist", help="build directory to serve (default: dist)")
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default: 0.0.0.0)")
    ap.add_argument("--port", type=int, default=8888, help="bind port (default: 8888)")
    ap.add_argument("--project-root", default=".",
                    help="where .sage/ lives (default: the working directory)")
    args = ap.parse_args(argv)

    root = Path(args.dir).resolve()
    if not (root / "index.html").is_file():
        print(f"[sage] nothing to serve: {root}/index.html is missing — did `npm run build` run?", flush=True)
        return 1

    # Constructing the server binds the port, so Domino's proxy can connect while we log. The
    # manifests sit beside app.sh in the repo root, which is where it runs us from — `root` is the
    # build being served, which does not carry them.
    project_root = Path(args.project_root)
    limit = sq.max_rows()
    srv = build_server(root, host=args.host, port=args.port, project_root=project_root,
                       executor=sq.FlightExecutor(sq.load_sources(project_root), limit))
    sq.log_query_catalog(srv.sage_queries)
    elapsed = _cold_start_secs()
    print(f"[sage] serving {root} on {args.host}:{args.port}", flush=True)
    if elapsed is not None:
        print(f"[sage] cold start: {elapsed:.0f}s to serving {root}", flush=True)
    # Diagnostics, not startup: an unreachable sidecar costs its timeout and the data library costs
    # its import, and paying either before serve_forever() would leave the first viewer's request
    # sitting in the backlog for it.
    threading.Thread(target=sq.log_sidecar_status, daemon=True).start()
    threading.Thread(target=sd.log_platform_status, daemon=True).start()
    if srv.sage_queries:
        print(f"[sage] queries return at most {limit} rows (SAGE_QUERY_MAX_ROWS)", flush=True)
        threading.Thread(target=sq.log_data_library, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
