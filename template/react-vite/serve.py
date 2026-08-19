#!/usr/bin/env python3
"""Serve this app's production build as a Domino App (Phase 5).

`app.sh` still installs dependencies and runs `vite build` with Node — only the process that stays
up is Python: this file replaces `vite preview`. See ADR-0002 for why. In short, querying a Domino
Data Source is possible only over Arrow Flight gRPC, whose one client is the Python SDK already
installed in the image, so the process holding the socket has to be a Python one.

Right now it serves static files and nothing else — no data access, no query API. Those land on top
of this, and this file's job is to make the swap boring before anything depends on it.

Stdlib only, like spikes/domino-probes/viewer_identity_app/probe_server.py: the App container's
python3 is whichever one the image ships, so a dependency here is one we cannot install.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import threading
import time
import urllib.request
from collections.abc import Iterable
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

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

# Where Domino's per-container sidecar mints a short-lived access token. Env override first, then the
# documented default — the same order sage's own gateway client and the viewer-identity probe use.
_SIDECAR_DEFAULT = "http://localhost:8899"

# Vite hashes everything it emits into assets/, so those URLs are safe to cache forever. Nothing
# else is: index.html keeps its name across deploys, and a cached copy would go on pointing at the
# previous deploy's assets after a republish.
_HASHED_PREFIX = "/assets/"


def sidecar_url() -> str:
    proxy = (os.environ.get("DOMINO_API_PROXY") or _SIDECAR_DEFAULT).rstrip("/")
    return f"{proxy}/access-token"


def probe_token_sidecar(url: str, timeout: float = 5.0) -> str:
    """One line saying whether the token sidecar answers, for the App's log.

    The sidecar is the prerequisite for querying a Data Source as this app (ADR-0002), and a
    container that doesn't have one should say so at boot rather than at the first query. Reports the
    token's LENGTH only — an app log is readable by anyone who can see the deployment.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            length = len((resp.read() or b"").strip())
    except Exception as e:
        return f"not reachable at {url} ({type(e).__name__}: {e})"
    if not length:
        return f"reachable at {url} but returned an empty body"
    return f"reachable at {url} ({length} chars)"


# --- mount-prefix probe (#18) -------------------------------------------------------------------
# Domino's app proxy strips the app's mount prefix before the request reaches this container, so the
# build it serves knows nothing about the URL the viewer is on. That is why the build base is relative,
# and why a route two or more segments deep cannot resolve its assets. Worse, the prefix is not even
# one shape per deployment — the same app answers under several (`/apps/{uuid}/`,
# `/apps-internal/{id}/`, `/u/{owner}/{project}/app/`), so no build-time base can be right for every
# link a viewer might follow.
#
# A forwarded-prefix header would settle it: this server could inject a <base href> and keep clean
# URLs. Nobody has seen the headers a published App actually receives, so dump them once and find out.
# Off by default, because the dump prints header VALUES and an App's log is readable by anyone who can
# see the deployment.
_OFF = ("", "0", "false", "no", "off")
_SECRET_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-domino-api-key", "x-domino-jwt"}
)
# A budget rather than a single dump: the first request to arrive is not necessarily a viewer's. A
# platform health check on the app port — or, as happened while testing this, serve.py's own sidecar
# probe when the ports collide — would otherwise consume the one dump and teach us nothing. Every
# request from the same page load carries the same forwarded headers, so a few is plenty, and a few is
# still a bounded number of lines once per boot.
_DUMP_BUDGET = 5
_dumps_left = _DUMP_BUDGET
_dump_lock = threading.Lock()


def debug_headers_enabled() -> bool:
    return (os.environ.get("SAGE_DEBUG_HEADERS") or "").strip().lower() not in _OFF


def redacted_header_lines(items: Iterable[tuple[str, str]]) -> list[str]:
    """Header lines for the log, credentials replaced by their length.

    Length rather than nothing: it keeps "the header was absent" and "the header was there and we
    withheld it" apart, which is the difference between an unauthenticated request and a redaction.
    """
    return [
        f"{name}: <withheld, {len(value)} chars>" if name.lower() in _SECRET_HEADERS else f"{name}: {value}"
        for name, value in items
    ]


def _claim_dump() -> bool:
    """True for the first `_DUMP_BUDGET` callers. The server is threaded, so this has to be atomic."""
    global _dumps_left
    with _dump_lock:
        if _dumps_left <= 0:
            return False
        _dumps_left -= 1
        return True


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


class _Handler(SimpleHTTPRequestHandler):
    """Static handler for a Vite build: SPA fallback, pinned content types, no directory listings.

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

    def send_head(self):
        self._maybe_dump_headers()
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

    def log_request(self, code="-", size="-"):
        status = getattr(code, "value", code)
        if isinstance(status, int) and status >= 400:  # 2xx/3xx would drown the App log
            self.log_message("%s %s", status, self.requestline)

    def log_message(self, fmt, *args):
        # Domino surfaces the App's stdout, so that is where problems have to go.
        print(f"[sage] {fmt % args}", flush=True)

    def log_error(self, fmt, *args):
        # Reaping an idle keep-alive connection is the timeout above doing its job, not a fault.
        if not str(fmt).startswith("Request timed out"):
            self.log_message(fmt, *args)

    def _maybe_dump_headers(self) -> None:
        """Dump an early request's headers, if asked. See the mount-prefix probe note above.

        Logged BEFORE the SPA rewrite, so the path is the one that arrived — the value that has to be
        compared with the browser's URL to see what the proxy took off.
        """
        if not debug_headers_enabled() or not _claim_dump():
            return
        self.log_message("--- request as this container saw it (SAGE_DEBUG_HEADERS) ---")
        self.log_message("%s", self.requestline)
        for line in redacted_header_lines(self.headers.items()):
            self.log_message("  %s", line)
        self.log_message("--- end of dump; #18 wants a header carrying the browser-side prefix ---")

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


def build_server(root: Path | str, *, host: str = "0.0.0.0", port: int = 8888) -> ThreadingHTTPServer:
    """A bound (not yet serving) server for the build at `root`. Threaded so one slow client can't
    hold up the rest of a page's assets."""
    return ThreadingHTTPServer((host, port), partial(_Handler, directory=str(root)))


def _log_sidecar_status() -> None:
    print(f"[sage] token sidecar: {probe_token_sidecar(sidecar_url())}", flush=True)


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
    args = ap.parse_args(argv)

    root = Path(args.dir).resolve()
    if not (root / "index.html").is_file():
        print(f"[sage] nothing to serve: {root}/index.html is missing — did `npm run build` run?", flush=True)
        return 1

    # Constructing the server binds the port, so Domino's proxy can connect while we log.
    srv = build_server(root, host=args.host, port=args.port)
    elapsed = _cold_start_secs()
    print(f"[sage] serving {root} on {args.host}:{args.port}", flush=True)
    if elapsed is not None:
        print(f"[sage] cold start: {elapsed:.0f}s to serving {root}", flush=True)
    # Diagnostics, not startup: an unreachable sidecar costs its timeout, and paying that before
    # serve_forever() would leave the first viewer's request sitting in the backlog for it.
    threading.Thread(target=_log_sidecar_status, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
