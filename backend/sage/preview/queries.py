"""The app's named queries, answered while it is still being built (#24).

Until this existed, `POST /api/queries/<name>` was a 404 in the preview and a real answer in the
published app. So the one thing a creator most needs to try — a query the agent wrote for them,
against a store whose shape they have never seen — was the one thing they could only try by
publishing, at a cold start per attempt. It got worse the better #15 and #16 worked, because the
thing that needed trying was exactly the thing that could not be tried.

What answers here is `serve.py` itself, bound to loopback and run in a thread. Not a second
implementation of the query path: the SAME module the published app runs, reached over the same HTTP
route, so the name lookup, the parameter binding, the row cap and every refusal sentence are the
published app's rather than an approximation that drifts from it. `serve_module` already loads that
file for `catalog_problems` and `stranded_levels`; this asks it for one more thing.

No credential is passed, and that is the point rather than an omission. `DataSourceClient` reads the
container's own identity, and this container is the creator's build session — so a preview query runs
as the creator. A query that answered here because of who was previewing would be a query that fails
on publish, which is worse than one that never answered at all.

Two things are deliberately NOT the published app's behaviour:

  - **Results are cached for a few seconds.** A build turn edits `src/App.tsx` a dozen times, every
    edit reloads the page over HMR, and every reload fires every query the screen uses — on one
    observed turn that would have been ~160 round trips against a store billed per byte scanned, for
    no new information. The published app caches nothing and must not: a viewer has to see the store
    as it is now (#14). A creator watching their own dashboard redraw does not need a fresh scan per
    keystroke.
  - **The catalog is re-read when it changes.** A published app loads it once, because it cannot
    change under a running app. Here the agent rewrites it mid-session, and answering from a
    statement that has since been fixed is worse than not answering.

Everything here is best-effort. Every failure path leaves `port` None and the preview behaving
exactly as it did before this module existed — a 404 that `sageQuery.ts` already has a sentence for.
A build session that cannot answer queries is worse than one that can; a build session that will not
START because it cannot answer queries is worse than both.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from ..resources.builtapp import serve_module

log = logging.getLogger("sage.preview.queries")

# Long enough to collapse an HMR reload storm, short enough that a creator who changes a row in the
# warehouse and reloads to look at it sees the change rather than wondering why it did not take.
CACHE_TTL_S = 30.0


class CachingExecutor:
    """`serve.py`'s executor with a short memory, for the preview only.

    Keyed on the query's name AND its statement AND the bound parameters. The statement is in there
    so that the agent rewriting a query is a cache miss by construction — the alternative is an
    explicit invalidation hook that has to be remembered at every point the catalog can change, and
    forgetting it once serves a creator the results of SQL that no longer exists.

    Only successes are kept. An error is a thing the creator is about to go and fix, and replaying it
    from a cache after they have fixed it would be its own bug.
    """

    def __init__(self, inner: Any, ttl_s: float = CACHE_TTL_S, redact: Any = None) -> None:
        self._inner = inner
        self._ttl = ttl_s
        self._entries: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        # `serve.py`'s own one-line redactor, passed in rather than reimplemented: the SDK's client
        # prints its api_key in `__repr__`, so an exception carrying the client carries the key, and
        # there must be exactly one rule about that. Falls back to the type name alone, which is the
        # safe thing to say when we cannot be sure what a message holds.
        self._redact = redact or (lambda exc: type(exc).__name__)

    def __call__(self, query: Any, params: dict) -> dict:
        key = self._key(query, params)
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and now - entry[0] < self._ttl:
                self.hits += 1
                return entry[1]
        # Outside the lock: a query takes seconds against a real warehouse, and holding the lock
        # across it would serialise every screen on the slowest one. Two callers racing the same cold
        # key both run it and the second overwrites — one wasted scan, against a deadlock risk and a
        # stalled preview if this were made exact.
        try:
            result = self._inner(query, params)
        except Exception as exc:
            # Why a failing query said nothing anybody could read. `serve.py` prints its reason to
            # stdout, which is right for a published App — that IS its log, and the page tells the
            # viewer to go and look at it. The preview has no App and no log a creator can open:
            # /api/diag/log is the only one they have, and it reads the `logging` hierarchy, not
            # stdout. So a Data Source that stopped answering produced a page saying "whoever
            # published this app can see the reason in the App's log", a creator who has published
            # nothing, and a reason that reached nobody (live, 2026-08-24).
            #
            # `__cause__` because serve.py converts the real failure into a sanitised QueryProblem
            # and chains the original to it — the sentence is for the viewer, the cause is the part
            # that says which credential or table is the problem.
            log.warning("preview queries: %s failed — %s", getattr(query, "name", "?"),
                        self._redact(exc.__cause__ or exc))
            raise
        with self._lock:
            self._entries[key] = (now, result)
            self.misses += 1
        return result

    @staticmethod
    def _key(query: Any, params: dict) -> str:
        # `default=str` because a declared `date` parameter arrives coerced to a date object, and
        # json refuses it. Its string form is what identifies it here anyway.
        bound = json.dumps(params, sort_keys=True, default=str)
        return f"{getattr(query, 'name', '')}\x00{getattr(query, 'sql', '')}\x00{bound}"


class PreviewQueries:
    """`serve.py`'s query API on loopback, for one workspace.

    Deep module, narrow interface: `start()` / `port` / `refresh()` / `stop()`. How the module is
    loaded, which executor sits behind it and when the catalog is re-read are all in here, so the
    proxy in front of it only has to know a port.
    """

    def __init__(self, workspace: Path, template: Path, ttl_s: float = CACHE_TTL_S) -> None:
        self._workspace = workspace
        self._template = template
        self._ttl = ttl_s
        self._module: Any = None
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._stamp: tuple | None = None
        self.executor: CachingExecutor | None = None

    @property
    def port(self) -> int | None:
        """The loopback port `serve.py` is answering on, or None when it is not."""
        return self._server.server_address[1] if self._server is not None else None

    def start(self) -> None:
        """Bind and serve, or log why not and leave the preview as it was.

        Loopback only. This answers as the creator, with no authentication in front of it, so it must
        not be reachable from anywhere the creator's own browser session is not already trusted —
        and the proxy above it is the only thing that should ever dial it.
        """
        try:
            module = serve_module(self._template)
            if module is None:
                log.info("preview queries: this template has no serve.py, so queries stay unavailable")
                return
            self._module = module     # `_build_executor` reads it
            executor = self._build_executor()
            server = module.build_server(self._workspace / "dist", host="127.0.0.1", port=0,
                                         project_root=self._workspace, executor=executor)
            # 50ms, not `serve_forever`'s 0.5s default: that interval is how long the loop sleeps
            # between checks for the shutdown flag, so it is also the floor on how long `stop()`
            # blocks. A Binding change restarts this server while the creator waits, and half a
            # second of that wait buying nothing is worse than an idle `select` twenty times a
            # second, which costs nothing measurable.
            thread = threading.Thread(target=server.serve_forever, args=(0.05,),
                                      name="sage-preview-queries", daemon=True)
            thread.start()
        except Exception:
            # Never fatal. Losing the preview's query API costs the creator a publish to try a query,
            # which is where they were before this shipped; failing to open the project at all costs
            # them the session.
            log.exception("preview queries: could not start, so queries stay unavailable in the preview")
            return
        self._server, self._thread, self.executor = server, thread, executor
        self._stamp = self._catalog_stamp()
        log.info("preview queries: %d in the catalog, answering on 127.0.0.1:%d",
                 len(getattr(server, "sage_queries", {}) or {}), server.server_address[1])

    def refresh(self) -> None:
        """Re-read whichever of the two manifests has changed on disk.

        Called before a request is forwarded rather than on a timer: it costs two `stat` calls, it is
        exact at the only moment that matters, and a poll would have to be tuned against how fast an
        agent writes — which is "several times a minute during a build, never otherwise".

        The two are not refreshed the same way, because they do not mean the same thing. A rewritten
        catalog is new statements against the same store, so the executor and its connection stay. A
        rewritten Binding is a DIFFERENT store, so the executor, the client it holds and every row
        cached from the old one are all wrong and get dropped together.
        """
        if self._server is None or self._module is None:
            return
        stamp = self._catalog_stamp()
        if stamp == self._stamp:
            return
        try:
            if self._stamp is None or stamp[1] != self._stamp[1]:
                self._server.sage_executor = self.executor = self._build_executor()
            self._server.sage_queries = self._module.load_queries(self._workspace)
        except Exception:
            log.exception("preview queries: could not re-read the catalog, keeping the last one")
            return
        self._stamp = stamp
        log.info("preview queries: catalog re-read, %d in it",
                 len(getattr(self._server, "sage_queries", {}) or {}))

    def _build_executor(self) -> CachingExecutor:
        """A real executor for this workspace's Bindings, behind the preview's cache."""
        return CachingExecutor(
            self._module.FlightExecutor(self._module.load_sources(self._workspace),
                                        getattr(self._module, "_DEFAULT_MAX_ROWS", 5000)),
            self._ttl,
            getattr(self._module, "_readable", None),
        )

    def stop(self) -> None:
        """Best-effort, like the supervisor's: a query server that will not stop must not keep the
        rest of shutdown from running."""
        server, self._server = self._server, None
        if server is None:
            return
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            log.exception("preview queries: failed to stop cleanly")

    def _catalog_stamp(self) -> tuple:
        """What the two manifests look like right now, cheaply.

        Size beside mtime because an agent can rewrite a file inside one mtime tick, and a catalog
        that changed without appearing to is the failure this whole method exists to avoid.
        """
        return tuple(
            self._file_stamp(self._workspace / rel)
            for rel in (getattr(self._module, "_QUERIES_REL", ".sage/queries.json"),
                        getattr(self._module, "_BINDINGS_REL", ".sage/bindings.json"))
        )

    @staticmethod
    def _file_stamp(path: Path) -> tuple:
        try:
            st = path.stat()
        except OSError:
            return (0.0, -1)     # absent is a state like any other, and it can change back
        return (st.st_mtime, st.st_size)
