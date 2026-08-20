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
from dataclasses import dataclass
from datetime import date
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

    def do_POST(self):
        """The named-query route, and the only method this server answers besides GET and HEAD.

        Everything a caller can influence is the name in the path and the values in `params`. The
        name selects a statement the app's own repo declared; it is never part of one.
        """
        name = urlsplit(self.path).path[len(_API_PREFIX):] if self._is_query_path() else ""
        try:
            if not name:
                raise QueryProblem(HTTPStatus.NOT_FOUND, "No such endpoint.")
            query = getattr(self.server, "sage_queries", {}).get(name)
            if query is None:
                raise QueryProblem(HTTPStatus.NOT_FOUND, f"This app has no query called {name}.")
            params = query.bind(self._body().get("params"))
            result = getattr(self.server, "sage_executor", unavailable_executor)(query, params)
        except QueryProblem as e:
            return self._send_json(e.status, {"error": e.message})
        except Exception as e:   # an executor that failed in a way it did not describe
            self.log_message("query %s failed: %s: %s", name, type(e).__name__, e)
            return self._send_json(HTTPStatus.BAD_GATEWAY,
                                   {"error": "This app could not read its data source."})
        return self._send_json(HTTPStatus.OK, result)

    def _is_query_path(self) -> bool:
        return urlsplit(self.path).path.startswith(_API_PREFIX)

    def _body(self) -> dict:
        """The request body as an object. Capped, because a parameter object is small and reading an
        unbounded one would let a single request hold a thread and a lot of memory."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise QueryProblem(HTTPStatus.BAD_REQUEST, "The request body was not readable.") from None
        if length > _MAX_BODY:
            raise QueryProblem(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "The request body is too large.")
        if length <= 0:
            return {}
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise QueryProblem(HTTPStatus.BAD_REQUEST, "The request body is not valid JSON.") from None
        return body if isinstance(body, dict) else {}

    def _send_json(self, status, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
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


# ---- Named queries: the app's only path to data (#13) ------------------------------------------
#
# The browser sends a NAME and parameters. It never sends SQL, and there is no route that would
# accept any. That is not tidiness — the Data Source credential is a shared service account with
# broad warehouse read (ADR-0001, and the publish guards in #12 are what keep it shared), so a
# generic SQL endpoint would make every published app a warehouse console for everyone it is shared
# with. The name is looked up in a catalog the app's own repo carries; nothing a caller sends is
# ever used to build a statement.
#
# This file does not execute anything. The executor is injected, #13 lands with a fake, and the real
# Arrow Flight one arrives in #14. What lands here is the boundary and the shape of what crosses it.

_QUERIES_REL = ".sage/queries.json"   # the catalog, written by the creator's agent (#15)
_BINDINGS_REL = ".sage/bindings.json"  # the Resources this app is recorded as using (#6)
_MAX_BODY = 64 * 1024                  # a parameter object; anything larger is not one
_API_PREFIX = "/api/queries/"

# `:name` — the only placeholder form. Declared parameters and placeholders must agree exactly, which
# is what lets #14 substitute without having to guess what was meant to be a value.
_PLACEHOLDER = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class QueryProblem(Exception):
    """A named query cannot answer, and the reason is the caller's to read.

    Carries the status because the three cases need different ones and a viewer-facing app should
    not have to guess: 404 for a name that is not in the catalog, 400 for parameters that do not fit
    what the query declared, 503 for a query that is in the catalog but unusable.
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Param:
    """One declared parameter of a named query.

    A type, and optionally the values it may take. Types rather than free text because a parameter
    reaches a store as part of a statement, and the check that it is an integer is the check that it
    is not a fragment of SQL. `enum` narrows further where the author knows the domain; where they do
    not — a search box, an open date range — the type alone still holds the line.
    """

    name: str
    type: str
    enum: tuple = ()

    def coerce(self, value: object) -> object:
        """The supplied value as the declared type, or raise. Never returns a string it was not
        given: coercing "3" to 3 would let a caller decide which branch of this check to take."""
        if self.enum and value not in self.enum:
            raise QueryProblem(HTTPStatus.BAD_REQUEST, (
                f"'{self.name}' must be one of: {', '.join(str(e) for e in self.enum)}."))
        if self.type == "string":
            if not isinstance(value, str):
                raise QueryProblem(HTTPStatus.BAD_REQUEST, f"'{self.name}' must be text.")
            return value
        if self.type == "bool":
            # Before int: bool IS an int in Python, so an unguarded int check would accept True for
            # a number and quietly send 1 to the store.
            if not isinstance(value, bool):
                raise QueryProblem(HTTPStatus.BAD_REQUEST, f"'{self.name}' must be true or false.")
            return value
        if self.type == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise QueryProblem(HTTPStatus.BAD_REQUEST, f"'{self.name}' must be a whole number.")
            return value
        if self.type == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise QueryProblem(HTTPStatus.BAD_REQUEST, f"'{self.name}' must be a number.")
            return float(value)
        if self.type == "date":
            # The shape is checked before fromisoformat rather than left to it: 3.11 also accepts
            # "20240101" and a full timestamp, and a parameter that parses two ways a caller did not
            # intend is the kind of latitude this boundary exists to remove.
            if isinstance(value, str) and _ISO_DATE.fullmatch(value):
                try:
                    return date.fromisoformat(value)
                except ValueError:
                    pass    # well-shaped but not a real date, e.g. 2024-02-31
            raise QueryProblem(HTTPStatus.BAD_REQUEST,
                               f"'{self.name}' must be a date, written as YYYY-MM-DD.")
        raise QueryProblem(HTTPStatus.SERVICE_UNAVAILABLE,
                           f"'{self.name}' is declared with a type this app cannot check.")


@dataclass(frozen=True)
class Query:
    """One named query, as the catalog records it.

    `problem` set means the query was declared and is not usable — a Binding it names has gone, or
    its declaration does not hold together. It is kept in the catalog rather than dropped so the
    answer to asking for it is the reason, not "no such query", which would send someone looking for
    a typo in a name that is spelled correctly.
    """

    name: str
    binding: str
    sql: str
    params: tuple = ()
    problem: str = ""

    def bind(self, supplied: object) -> dict:
        """The supplied parameters, checked against what this query declares. Values only — the SQL
        is not touched here and never sees a caller's text.

        Every declared parameter is required, and a parameter that was not declared is refused
        rather than ignored: a caller who sends `region` to a query that does not take one has
        misunderstood what they are asking, and silently answering the unfiltered question is a wrong
        answer rather than a partial one.
        """
        if self.problem:
            raise QueryProblem(HTTPStatus.SERVICE_UNAVAILABLE, self.problem)
        if supplied is None:
            supplied = {}
        if not isinstance(supplied, dict):
            raise QueryProblem(HTTPStatus.BAD_REQUEST, "'params' must be an object of name to value.")
        declared = {p.name: p for p in self.params}
        unknown = sorted(set(supplied) - set(declared))
        if unknown:
            raise QueryProblem(HTTPStatus.BAD_REQUEST, (
                f"{self.name} does not take {', '.join(unknown)}."))
        missing = sorted(set(declared) - set(supplied))
        if missing:
            raise QueryProblem(HTTPStatus.BAD_REQUEST, (
                f"{self.name} needs {', '.join(missing)}."))
        return {name: declared[name].coerce(value) for name, value in supplied.items()}


def load_queries(project_root: Path) -> dict:
    """The app's query catalog, checked against its Bindings, at startup rather than per request.

    Startup is the only place this can be honest. A catalog read per request would turn a query
    naming a Binding that has gone into a transport error for whoever happened to ask, over and over,
    with the reason buried in whatever the store said. Checked once, a broken query has a sentence
    attached to it before anyone asks.

    A missing catalog is not a fault: an app that reads no store has none, which is every app the
    template has produced so far. It returns {}, and the query route then answers 404 for any name,
    which is the truth.
    """
    raw = _read_json(project_root / _QUERIES_REL)
    if not isinstance(raw, list):
        return {}
    bound = {
        str(b.get("id") or ""): b for b in _read_json(project_root / _BINDINGS_REL) or []
        if isinstance(b, dict) and b.get("kind") == "data_source"
    }
    out = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        if not name or name in out:
            continue   # unnamed, or a duplicate of one already read — first declaration wins
        out[name] = _one_query(entry, name, bound)
    return out


def _one_query(entry: dict, name: str, bound: dict) -> Query:
    """One catalog entry as a Query, usable or with the reason it is not."""
    binding = str(entry.get("binding") or "")
    sql = entry.get("sql")
    params, bad = _params_of(entry.get("params"))
    placeholders = set(_PLACEHOLDER.findall(sql)) if isinstance(sql, str) else set()
    declared = {p.name for p in params}

    if not isinstance(sql, str) or not sql.strip():
        problem = f"The query {name} has no statement to run."
    elif bad:
        problem = f"The query {name} declares a parameter Sage cannot read: {bad}."
    elif not binding:
        problem = f"The query {name} does not say which Data Source it reads."
    elif binding not in bound:
        problem = (f"The query {name} reads the Data Source {binding}, which this app is no longer "
                   f"recorded as using.")
    elif placeholders - declared:
        problem = (f"The query {name} uses {', '.join(sorted(placeholders - declared))}, which it "
                   f"does not declare as a parameter.")
    elif declared - placeholders:
        problem = (f"The query {name} declares {', '.join(sorted(declared - placeholders))}, which "
                   f"its statement never uses.")
    else:
        problem = ""
    return Query(name, binding, sql if isinstance(sql, str) else "", tuple(params), problem)


def _params_of(raw: object) -> tuple:
    """The declared parameters, and the name of the first one that could not be read."""
    if raw is None:
        return [], ""
    if not isinstance(raw, list):
        return [], "params is not a list"
    out = []
    for p in raw:
        if not isinstance(p, dict) or not str(p.get("name") or ""):
            return out, "an entry with no name"
        name, ptype = str(p["name"]), str(p.get("type") or "")
        if ptype not in ("string", "int", "float", "bool", "date"):
            return out, f"{name} has type {ptype or '(none)'}"
        enum = p.get("enum")
        if enum is not None and not isinstance(enum, list):
            return out, f"{name} has an enum that is not a list"
        out.append(Param(name, ptype, tuple(enum) if enum else ()))
    return out, ""


def _read_json(path: Path) -> object:
    """A JSON file's contents, or None when it is absent or unreadable. Unreadable is reported by the
    caller as an empty catalog rather than raised: a published app that will not boot because a
    manifest has a stray comma is worse than one that says it has no queries."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def unavailable_executor(query: Query, params: dict) -> dict:
    """The default: this app has no way to reach a store.

    #13 ships the boundary; #14 ships the executor that crosses it. Until then a published app says
    so plainly rather than shipping a fake that would answer a viewer's real question with invented
    rows.
    """
    raise QueryProblem(HTTPStatus.SERVICE_UNAVAILABLE,
                       "This app cannot reach its Data Source yet, so it has no data to show.")


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
    srv.sage_queries = load_queries(Path(project_root or "."))
    srv.sage_executor = executor or unavailable_executor
    return srv


def _log_query_catalog(queries: dict) -> None:
    """What the app can answer, and what it cannot, once — in the App log Domino surfaces.

    A broken query is an error line at startup rather than a surprise for the first viewer who asks
    for it, which is the whole point of checking the catalog before serving instead of per request.
    """
    if not queries:
        return
    broken = [q for q in queries.values() if q.problem]
    print(f"[sage] queries: {len(queries) - len(broken)} of {len(queries)} usable", flush=True)
    for q in broken:
        print(f"[sage] query unusable: {q.problem}", flush=True)


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
    srv = build_server(root, host=args.host, port=args.port, project_root=Path(args.project_root))
    _log_query_catalog(srv.sage_queries)
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
