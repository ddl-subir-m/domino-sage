"""App source that reads a Data Source itself instead of through `.sage/queries.json` (#705).

The catalog is the only store path Sage can see: its queries name a recorded Binding, the preview
runs them, and a failure reaches the creator (#203, #704). Code that opens a `DataSourceClient` on
its own goes around all of it. Live, an app recording no Data Source did exactly that from
hand-written `app.py` routes, swallowed the error, answered 200 with a count of 0, and the build
ended `typecheck clean` with data `not_applicable`.

Pure, like `gateway_bypass` next door: the caller reads the tree and hands the answers in. Sage's own
files (`sage_queries.py`, `scripts/rehydrate_data.py`) use `domino_data` legitimately and are passed
in as `owned`, so the scan reports only what the app's agent wrote.
"""
from __future__ import annotations

import re

from ..orchestrator import brand

_DIRECT_CLIENT = re.compile(
    r"domino_data\.data_sources|from\s+domino_data\s+import\s[^\n]*\bdata_sources\b"
    r"|\bDataSourceClient\b")


def direct_store_clients(sources: list[tuple[str, str | None]], owned: frozenset[str]) -> list[str]:
    """The app files, outside `owned`, that reach a Data Source with `domino_data` directly.

    `sources` is `_scan_app_sources`'s answer. A text match, `raw_gateway_calls`'s bargain: a file
    that merely mentions the client is flagged too, and the cost is one bounded repair.
    """
    return [rel for rel, text in sources
            if text is not None and rel not in owned and _DIRECT_CLIENT.search(text)]


def direct_store_notice(files: list[str]) -> str:
    """The sentence for the creator once the repair is spent and the code is still there."""
    return brand.text(
        "{files} {reads} a {dataSource} directly instead of through this app's queries, so "
        "{assistantName} cannot check that data and screens that need it may show nothing. Ask "
        "{assistantName} to read it through a query, or choose a {dataSource} for this app.",
        files=", ".join(f"`{f}`" for f in files),
        reads="reads" if len(files) == 1 else "read",
    )
