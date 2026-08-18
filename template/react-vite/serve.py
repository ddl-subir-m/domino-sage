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
import mimetypes
import os
import threading
import time
import urllib.request
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
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
        self._resolve_spa_route()
        return super().send_head()

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
