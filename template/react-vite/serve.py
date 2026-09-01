#!/usr/bin/env python3
"""Serve this app's production build as a Domino App (Phase 5).

`app.sh` still installs dependencies and runs `vite build` with Node — only the process that stays
up is Python: this file replaces `vite preview`. See ADR-0002 for why. In short, querying a Domino
Data Source is possible only over Arrow Flight gRPC, whose one client is the Python SDK already
installed in the image, so the process holding the socket has to be a Python one.

It serves the build, and it answers named queries against the Data Sources the app is bound to (#13
for the boundary, #14 for the executor behind it).

Stdlib for everything except the query itself, like
spikes/domino-probes/viewer_identity_app/probe_server.py — so this file imports and serves under any
python3 the image ships. The Domino SDK is imported late, inside the executor, because it is the one
dependency here that cannot be installed and might be absent: `app.sh` picks an interpreter that
carries it, and an app that reads no Data Source never needs one.
"""
from __future__ import annotations

import argparse
import json
import math
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
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
# The executor is injected rather than reached for, which is what lets a test prove the boundary with
# no store anywhere near it. `FlightExecutor` below is the real one; `main` wires it.

_QUERIES_REL = ".sage/queries.json"   # the catalog, written by the creator's agent (#15)
_BINDINGS_REL = ".sage/bindings.json"  # the Resources this app is recorded as using (#6)
_MAX_BODY = 64 * 1024                  # a parameter object; anything larger is not one
_API_PREFIX = "/api/queries/"

# `:name` — the only placeholder form. Declared parameters and placeholders must agree exactly, which
# is what lets the executor substitute without having to guess what was meant to be a value.
#
# Not preceded by another colon, so Postgres's `amount::text` cast is a cast and not a placeholder
# called `text`. Without the lookbehind, #13's own agreement check refuses every statement that casts
# — the query is declared unusable and the sentence sends its author looking for a parameter they
# never wrote.
_PLACEHOLDER = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# How many rows one query may return. A published app holds the whole result in memory before it
# answers, so an uncapped `SELECT *` against a warehouse table is not a slow response — it is the App
# container being killed, for every viewer at once, by whoever asked first. Raise it per app with
# SAGE_QUERY_MAX_ROWS when an app genuinely needs more.
_DEFAULT_MAX_ROWS = 5000

# ---- What a Scope can travel as (#14) -----------------------------------------------------------
#
# A Binding records the database and schema the app reads (#11). Sending them as configuration
# OVERRIDES on the query — rather than qualifying every table name in the SQL — is what lets the
# statement stay unqualified. But which keys a Data Source will accept is fixed per connector by the
# Domino SDK's own config classes, and they are not alike: read across all 23 of them, only three
# carry a schema.
#
# Keyed on Domino's `dataSourceType`, value is (the key carrying the Binding's DATABASE level, the key
# carrying its SCHEMA level). MySQL is the odd one: its family has a single namespace level, which the
# cascade offers as a schema and the SDK calls a database, so the schema level travels as `database`.
#
# Everything else the cascade can list carries NOTHING of its Scope, having been read one class at a
# time: BigQuery (which has only a GCP project), ClickHouse, Greenplum, MariaDB, SingleStore and
# Synapse have no database or schema key at all, and PostgreSQL and Redshift have a database key with
# no database LEVEL to put in it — theirs is a two-level cascade. Oracle and DB2 (native) have a
# database key and no cascade at all, so no Scope is ever recorded for them to carry.
#
# A Scope level with nowhere to go is not sent and not ignored: `load_queries` refuses the query at
# startup unless the statement names that level itself. Silently reading whatever schema the
# connection defaults to is the one outcome worth ruling out — a warehouse with the same table in
# `dev` and `prod` answers such a query with rows that look right.
_SCOPE_KEYS = {
    "SnowflakeConfig": ("database", "schema"),
    "DatabricksConfig": ("catalog", "schema"),
    "TrinoConfig": ("catalog", "schema"),
    "MySQLConfig": (None, "database"),
    # Three levels, and only the outer one travels. A SQL Server query still has to name its schema.
    "SQLServerConfig": ("database", None),
}

# Stores with no boolean literal. `TRUE`/`FALSE` is standard SQL and works everywhere else, including
# Snowflake, where `WHERE active = TRUE` is the natural rendering; SQL Server and Synapse have BIT and
# reject the keyword outright.
_BOOL_AS_BIT = frozenset({"SQLServerConfig", "SynapseConfig"})


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
            # `isfinite` as well as the type, because Python's own JSON reader accepts the bare tokens
            # NaN, Infinity and -Infinity — so a caller really can send a float that no store has a
            # literal for, and it would otherwise reach a statement spelled `nan`.
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value)):
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
class Source:
    """One Data Source Binding, as this app's own manifest records it (#11, #14).

    The manifest is the whole record: a published app has no Sage around it and nothing to ask, so
    what is written here at pick time is what it knows. `connector_type` is Domino's own
    `dataSourceType` and is what decides whether the recorded Scope can be sent as configuration.
    """

    id: str
    name: str            # the Data Source's own name — what `get_datasource` resolves
    database: str = ""
    schema: str = ""
    connector_type: str = ""

    @property
    def kind(self) -> str:
        """The connector, as a sentence would say it: `SnowflakeConfig` reads back as `Snowflake`."""
        return self.connector_type[:-6] if self.connector_type.endswith("Config") else self.connector_type

    def scope(self) -> tuple:
        """(the configuration override this Scope becomes, the levels of it that cannot travel).

        A level with no key for this connector is NOT sent under some other name and NOT dropped — it
        is handed back so the caller can refuse the query. See `_SCOPE_KEYS` for why the two halves
        differ so much between connectors.
        """
        database_key, schema_key = _SCOPE_KEYS.get(self.connector_type, (None, None))
        config, stranded = {}, []
        for level, value, key in (("database", self.database, database_key),
                                  ("schema", self.schema, schema_key)):
            if not value:
                continue
            if key:
                config[key] = value
            else:
                stranded.append((level, value))
        return config, stranded


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
    sources = load_sources(project_root)
    out = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        if not name or name in out:
            continue   # unnamed, or a duplicate of one already read — first declaration wins
        out[name] = _one_query(entry, name, sources)
    return out


def catalog_fault(project_root: Path) -> str:
    """Why the catalog declares queries `load_queries` did not return, or "" when it does not.

    `load_queries` answers with what it could read and says nothing about what it could not. That is
    right for the app — a name it cannot serve is a 404, which is the truth — and it is the whole
    hole in the one check made on the creator's behalf. A catalog in a shape this file discards
    whole reads back as an app with no queries, which is exactly what an app that wanted none reads
    back as. So `catalog_problems` found nothing to report and the build called itself clean. Live,
    that shipped an app whose every screen answered "this app has no query called ..." — twice, the
    second turn spending itself guessing at the name rather than at the file.

    Here rather than in the orchestrator because the rule is this file's own: which shapes are
    accepted is decided in `load_queries` directly above, and a second copy of that decision would
    be right today and wrong the day either one moved.

    An absent catalog is not a fault. Most apps read no store and have none.
    """
    path = project_root / _QUERIES_REL
    if not path.is_file():
        return ""
    raw = _read_json(path)
    if raw is None:
        return (f"{_QUERIES_REL} is not valid JSON, so this app has no queries at all — every name "
                f"it asks for is refused as one this app does not have.")
    if not isinstance(raw, list):
        return (f"{_QUERIES_REL} has to be a LIST of query objects and this one is not, so nothing "
                f"it declares was read. Every name the app asks for is refused as one this app does "
                f"not have.")
    # In step with `load_queries` above, deliberately: an entry it skips is one this has to count,
    # and the two tests are the same test. A duplicate name is NOT dropped — first declaration wins
    # there on purpose, and reporting it here would make a deliberate rule read as a fault.
    dropped = sum(1 for e in raw if not isinstance(e, dict) or not str(e.get("name") or ""))
    if dropped:
        return (f"{dropped} of the {len(raw)} entries in {_QUERIES_REL} declare no query, because an "
                f'entry has to be an object carrying a "name". The app cannot ask for what they hold.')
    return ""


def load_sources(project_root: Path) -> dict:
    """The Data Source Bindings this app recorded, by id.

    Only the data_source kind: an LLM Alias Binding names a model the browser calls directly and has
    no Scope, so it is not something a query can read.
    """
    raw = _read_json(project_root / _BINDINGS_REL)
    out = {}
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or entry.get("kind") != "data_source":
            continue
        rid = str(entry.get("id") or "")
        if rid:
            out[rid] = Source(rid, str(entry.get("name") or rid), str(entry.get("database") or ""),
                              str(entry.get("schema") or ""), str(entry.get("connector_type") or ""))
    return out


def _one_query(entry: dict, name: str, sources: dict) -> Query:
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
    elif binding not in sources:
        problem = (f"The query {name} reads the Data Source {binding}, which this app is no longer "
                   f"recorded as using.")
    elif placeholders - declared:
        problem = (f"The query {name} uses {', '.join(sorted(placeholders - declared))}, which it "
                   f"does not declare as a parameter.")
    elif declared - placeholders:
        problem = (f"The query {name} declares {', '.join(sorted(declared - placeholders))}, which "
                   f"its statement never uses.")
    else:
        problem = _scope_problem(name, sql, sources[binding])
    return Query(name, binding, sql if isinstance(sql, str) else "", tuple(params), problem)


def _scope_problem(name: str, sql: str, source: Source) -> str:
    """Why this query cannot honour the Scope its Binding recorded, or "" when it can.

    A Scope level that cannot travel as configuration (`_SCOPE_KEYS`) leaves the statement running
    against whatever the connection happens to default to. That is the one failure worth refusing at
    startup rather than reporting per viewer, because it does not look like a failure: a warehouse
    holding the same table in `dev` and in `prod` answers such a query with rows that read correctly.

    Unless the statement names the level itself. A query written as `FROM marts.orders` has already
    said where it reads, and needs nothing from configuration — which is what keeps a connector Sage
    cannot scope from being a dead end, rather than a rule about what SQL may look like.
    """
    _, stranded = source.scope()
    for level, value in stranded:
        if _names(sql, value):
            continue
        if source.connector_type:
            return (f"The query {name} reads the {level} {value}, which a {source.kind} Data Source "
                    f"cannot carry as configuration. Its statement has to name {value} itself.")
        return (f"The query {name} reads the {level} {value}, and this app does not record what kind "
                f"of Data Source {source.name} is, so it cannot send the {level} as configuration. "
                f"Its statement has to name {value} itself.")
    return ""


def _names(sql: str, identifier: str) -> bool:
    """Whether `sql` uses `identifier` as a name of its own, rather than inside a longer one.

    Delimited by the same character set `safe_identifier` allows a name to hold, so the schema MART
    is not found inside SMARTS. Case-insensitive, because an unquoted identifier is folded by every
    store here and the creator picked this name from a list rather than typing it to match.
    """
    edge = "[A-Za-z0-9_$]"
    return re.search(f"(?<!{edge}){re.escape(identifier)}(?!{edge})", sql, re.IGNORECASE) is not None


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
    """The default: this server was built without a way to reach a store.

    `main` wires `FlightExecutor`, so a published app does not land here. What does is a server built
    by a test, and a caller who deliberately passed nothing — both of which are better answered by
    saying there is no data than by a fake that would answer a viewer's real question with invented
    rows.
    """
    raise QueryProblem(HTTPStatus.SERVICE_UNAVAILABLE,
                       "This app cannot reach its Data Source yet, so it has no data to show.")


# ---- Running one named query against the store it reads (#14) ------------------------------------
#
# Arrow Flight over gRPC, through the Domino SDK, because there is no other way in: `datasource-proxy`
# runs no SQL over HTTP and no client for the wire contract exists outside Python (ADR-0002). The
# credential is the container's own — the SDK reads a fresh JWT from the token sidecar for every RPC,
# so nothing here holds one and nothing here can leak one.
#
# A published app runs as its publisher against a shared service-account credential (ADR-0001), which
# is what #12's publish guards keep true. That is the whole reason the statement is not a caller's to
# choose: what crosses this boundary is a name from the app's own catalog and values already checked
# against declared types.

_NO_LIBRARY = ("This app cannot reach its Data Source. Whoever published it can see why in the App's "
               "log.")
_CANNOT_OPEN = ("This app could not open the Data Source it reads. Whoever published it needs to "
                "check that it still exists and that this app is allowed to use it.")
_NO_ANSWER = ("The Data Source did not answer this question. Try again — if it keeps failing, "
              "whoever published this app can see the reason in the App's log.")

# Anything long enough to be a token. `DataSourceClient.__repr__` prints its api_key in plaintext, so
# an exception carrying the client carries the key with it — and the App log is readable by everyone
# who can see the deployment.
_SECRET_SHAPED = re.compile(r"[A-Za-z0-9_\-]{32,}")


def render(sql: str, params: dict, connector_type: str = "") -> str:
    """`sql` with every placeholder replaced by the SQL literal for its value.

    Rendering, not binding: `TabularDatasource.query` takes one string, and the Flight ticket the SDK
    builds carries `{datasourceId, sqlQuery, configOverwrites, credentialOverwrites}` with nowhere to
    put a parameter array. So the check that a value is safe is the one that already happened —
    `Param.coerce` accepted a Python value of a declared type, and this renders that value rather than
    any text a caller sent.
    """
    return _PLACEHOLDER.sub(lambda m: _literal(m.group(1), params[m.group(1)], connector_type), sql)


def _literal(name: str, value: object, connector_type: str) -> str:
    """One checked value as the literal a store will read it as."""
    if isinstance(value, bool):
        # Before int, for the reason `coerce` tests it first: bool IS an int in Python.
        return ("1" if value else "0") if connector_type in _BOOL_AS_BIT else ("TRUE" if value else "FALSE")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)      # finite, because `coerce` refused NaN and the infinities
    if isinstance(value, date):
        # Quoted, not the ANSI `DATE '...'` form: every store here converts a quoted date in a
        # comparison, and SQL Server rejects the keyword outright.
        return f"'{value.isoformat()}'"
    if isinstance(value, str):
        # A doubled quote is the standard escape, and it is enough only where a backslash is not one.
        # MySQL's family treats `\` as an escape by default, so `abc\` would end its literal at the
        # doubled quote and let the rest of the value be read as SQL. Refused rather than escaped per
        # dialect: a refusal cannot be subtly wrong, and an escape can.
        if "\\" in value:
            raise QueryProblem(HTTPStatus.BAD_REQUEST, f"'{name}' cannot contain a backslash.")
        return "'" + value.replace("'", "''") + "'"
    raise QueryProblem(HTTPStatus.SERVICE_UNAVAILABLE, f"'{name}' has a value this app cannot send.")


class _ScopeConfig:
    """A Scope in the shape `Datasource.update` takes.

    Duck-typed rather than one of the SDK's generated config classes, because there is a different
    class per connector and each spells the same two ideas with different keyword names — which is
    what `_SCOPE_KEYS` already decides. Going through `update` and `query` rather than calling
    `DataSourceClient.execute` directly is deliberate: `query` is also where the SDK assembles the
    credential override, so an OAuth or AWS-IAM Data Source keeps working.
    """

    def __init__(self, config: dict) -> None:
        self._config = dict(config)

    def config(self) -> dict:
        return dict(self._config)

    def credential(self) -> dict:
        return {}


class FlightExecutor:
    """Runs one named query against the Data Source its Binding names, and returns what came back.

    Every call resolves the Data Source again, so what a viewer sees is the store as it is now: a
    source that has been renamed, revoked or deleted is reported the first time someone asks, not
    remembered from whenever this container booted.

    The client under it is built once and shared. It holds a gRPC channel, and the SDK's auth
    middleware fetches a fresh token from the sidecar at the start of every call — so a long-running
    App does not go stale, and rebuilding the client per query would only pay for a new connection.
    """

    def __init__(self, sources: dict, max_rows: int = _DEFAULT_MAX_ROWS) -> None:
        self._sources = sources
        self._max_rows = max_rows
        self._client = None
        self._lock = threading.Lock()

    def __call__(self, query: Query, params: dict) -> dict:
        source = self._sources.get(query.binding)
        if source is None:      # `load_queries` refuses these at startup; a catalog cannot get here
            raise QueryProblem(HTTPStatus.SERVICE_UNAVAILABLE,
                               "This app no longer records the Data Source this question reads.")
        sql = render(query.sql, params, source.connector_type)
        config, _ = source.scope()
        started = time.monotonic()
        client = self._client_once()
        try:
            datasource = client.get_datasource(source.name)
        except Exception as exc:
            print(f"[sage] query {query.name}: cannot open {source.name}: {_readable(exc)}", flush=True)
            raise QueryProblem(HTTPStatus.SERVICE_UNAVAILABLE, _CANNOT_OPEN) from exc
        try:
            datasource.update(_ScopeConfig(config))
            columns, rows, truncated = _drain(datasource.query(sql).reader, self._max_rows)
        except Exception as exc:
            print(f"[sage] query {query.name} failed: {_readable(exc)}", flush=True)
            raise QueryProblem(HTTPStatus.BAD_GATEWAY, _NO_ANSWER) from exc
        # The only place this is measurable. A creator reading the App log is the one person who can
        # act on a query that takes 40s, and the number is in the same place as the cold start.
        print(f"[sage] query {query.name}: {len(rows)} rows in {time.monotonic() - started:.1f}s"
              f"{' (truncated)' if truncated else ''}", flush=True)
        return {"columns": columns, "rows": rows, "truncated": truncated}

    def _client_once(self):
        """The client this app queries through, built on first use.

        The import is local and late for the same reason it is in Sage's own provider: the SDK pulls
        pandas and pyarrow, and an app that never queries should not pay for them at boot. It failing
        is a real state, not a broken install — `app.sh` picks the interpreter that has the library,
        and if none did, this is where a viewer finds out in a sentence.
        """
        with self._lock:
            if self._client is None:
                try:
                    from domino_data.data_sources import DataSourceClient
                    self._client = DataSourceClient()
                except Exception as exc:
                    print(f"[sage] no data source client under {sys.executable}: {_readable(exc)}",
                          flush=True)
                    raise QueryProblem(HTTPStatus.SERVICE_UNAVAILABLE, _NO_LIBRARY) from exc
            return self._client


def _drain(reader, max_rows: int) -> tuple:
    """At most `max_rows` rows off a Flight stream, and whether the store had more.

    A chunk at a time rather than the SDK's own `Result.to_pandas()`, which reads the whole result
    into memory before anyone can count it — a cap applied after that is not a cap. Rows are taken by
    POSITION, not by column name, so a query selecting two columns with the same name still answers
    with two columns.
    """
    columns = [str(c) for c in reader.schema.names]
    rows: list = []
    while len(rows) <= max_rows:    # one row past the cap, so "there was more" is a fact, not a guess
        try:
            chunk = reader.read_chunk()
        except StopIteration:
            break
        batch = getattr(chunk, "data", None)
        if batch is None:
            continue
        values = [c.to_pylist() for c in batch.columns]
        rows.extend([_jsonable(c[i]) for c in values] for i in range(batch.num_rows))
    truncated = len(rows) > max_rows
    if truncated:
        del rows[max_rows:]
        try:
            reader.cancel()     # stop the store streaming into a socket nobody is reading
        except Exception:
            pass
    return columns, rows, truncated


def _jsonable(value):
    """One store value as something `json.dumps` can write.

    A warehouse answers with types JSON has no word for. A Decimal becomes a float because the browser
    asked for something to chart, which costs precision past 2^53 and is the right trade for money
    columns and the wrong one for an account number — the alternative, a quoted string, breaks every
    chart. Anything unrecognised is stringified rather than dropped: a column a viewer can read is
    better than a column that silently went missing.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None      # JSON has no NaN that a browser will read
    if hasattr(value, "isoformat"):     # date, time, timestamp — whatever the connector's type maps to
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def _readable(exc: Exception, limit: int = 300) -> str:
    """A failure as one line for the App log, with anything token-shaped taken out.

    The same redaction Sage's own provider does, duplicated rather than imported for the reason this
    whole file is: it ships in the creator's app repo, where there is no sage package to import from.
    """
    text = _SECRET_SHAPED.sub("[redacted]", " ".join(str(exc).split()))
    return f"{type(exc).__name__}: {text[:limit]}" if text else type(exc).__name__


def max_rows() -> int:
    """How many rows one query may answer with. `SAGE_QUERY_MAX_ROWS` overrides the default.

    An app whose answer is a chart needs hundreds; one whose answer is a table someone exports may
    genuinely need more, and that is the creator's call to make per app rather than a number Sage
    fixes for everyone. A value that is not a positive whole number is reported and ignored, because
    a typo in an environment variable should not decide that this app returns no rows.
    """
    raw = (os.environ.get("SAGE_QUERY_MAX_ROWS") or "").strip()
    if not raw:
        return _DEFAULT_MAX_ROWS
    try:
        wanted = int(raw)
    except ValueError:
        wanted = 0
    if wanted < 1:
        print(f"[sage] SAGE_QUERY_MAX_ROWS={raw!r} is not a row count; using {_DEFAULT_MAX_ROWS}",
              flush=True)
        return _DEFAULT_MAX_ROWS
    return wanted


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


def _log_data_library() -> None:
    """Whether THIS interpreter can reach a Data Source, next to the sidecar line, for apps that read
    one. Both answer the same question a creator has when a query fails and there is no terminal to
    ask from: was the right python chosen (`app.sh`), and is the sidecar there (ADR-0002).

    Importing here also means the first viewer does not pay for pandas and pyarrow loading.
    """
    try:
        from domino_data.data_sources import DataSourceClient  # noqa: F401
    except Exception as exc:
        print(f"[sage] data library: NOT available to {sys.executable} — {_readable(exc)}", flush=True)
        return
    print(f"[sage] data library: ready ({sys.executable})", flush=True)


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
    limit = max_rows()
    srv = build_server(root, host=args.host, port=args.port, project_root=project_root,
                       executor=FlightExecutor(load_sources(project_root), limit))
    _log_query_catalog(srv.sage_queries)
    elapsed = _cold_start_secs()
    print(f"[sage] serving {root} on {args.host}:{args.port}", flush=True)
    if elapsed is not None:
        print(f"[sage] cold start: {elapsed:.0f}s to serving {root}", flush=True)
    # Diagnostics, not startup: an unreachable sidecar costs its timeout and the data library costs
    # its import, and paying either before serve_forever() would leave the first viewer's request
    # sitting in the backlog for it.
    threading.Thread(target=_log_sidecar_status, daemon=True).start()
    if srv.sage_queries:
        print(f"[sage] queries return at most {limit} rows (SAGE_QUERY_MAX_ROWS)", flush=True)
        threading.Thread(target=_log_data_library, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
