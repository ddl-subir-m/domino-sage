"""Which kind of app a Built App is, and what Sage has to know about that kind (#490).

Sage seeds every Built App from `template/fastapi-antd`: a FastAPI server over a page that loads
React, Ant Design, Day.js and Highcharts as plain scripts — no build step, no `node_modules`
(one-app pivot, 2026-09-22). Everything the backend knows about that shape is answered by the one
`Stack` below; every caller takes it and asks it, nobody works the shape out again.

The record is `stack` in the app's own `.sage/settings.json`, beside `createdAt` — the app's record,
committed to its repo, so a collaborator's clone reads the same answer. Absent means the app was born
before the record existed, and every such app is `react-vite`: the stack this Sage carried before the
pivot, and the one this Sage no longer seeds or serves. It is never DETECTED from the files: the seed
sentinel is a file the agent can delete (`WorkspaceManager.ensure` says so), and a stack guessed from
what is left on disk would re-seed the wrong template over a real app.

A `react-vite` record (or an absent one, which reads the same way) still answers a `Stack` from
`stack_of` — `fastapi-antd`'s, the only one there is — rather than `None`. That is a deliberate
simplification, not an oversight: most of `Stack`'s readers are best-effort scans (which globs
match, whether a path is a Sage-owned helper) for which assuming the wrong shape is harmless — the
globs just find nothing in an app that isn't laid out that way, the same "safe direction to be
wrong" every other best-effort reader in this codebase already takes. The one place that must NOT
silently act on a wrong shape is a Build turn about to write into a specific app, and that is
refused explicitly and earlier, off the plain `stack_name` string (`orchestrator/service.py`'s
`build_stream`), before anything here is ever asked.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..resources.app_helpers import TEMPLATE, HelperNames

_REPO = Path(__file__).resolve().parents[3]

#: The key in `.sage/settings.json` that records an app's stack.
STACK_KEY = "stack"
#: What an app with no record is: every app seeded before #490 introduced the record at all.
LEGACY_STACK = "react-vite"


@dataclass(frozen=True)
class Stack:
    """One kind of Built App: where its template is, and every fact the backend keys on its shape."""

    name: str
    template_dir: Path
    # The file whose presence says "an app has been seeded here". `ensure` seeds when it is absent.
    sentinel: str
    # What Domino runs to serve a published App, refreshed from the template at publish. ORDERED:
    # the entry script last, after everything it calls, so a refresh that dies partway leaves an app
    # that still serves (see `WorkspaceManager.refresh_entry_script`).
    deploy_files: tuple[str, ...]
    # Sage-owned sources refreshed at attach, in dependency order (`refresh_owned_sources`).
    owned_sources: tuple[str, ...]
    # What the Sage-owned helpers are called in an app of this kind, and where they live.
    helpers: HelperNames
    # The preview server's config, refreshed at attach — or None where the preview reads none.
    preview_config: str | None
    # The server the entry script execs. Publish refuses an app whose entry script names it and
    # whose tree does not hold it — or skips that check where the entry script IS the server.
    server_script: str | None
    # The file the agent is told to replace first; the placeholder check reads it.
    entry_file: str
    # The app's own source, as globs off the app root: what the agent is shown as existing paths,
    # and what the end-of-turn scans read. Sage-owned helpers and vendored bundles are not the
    # app's source and are skipped by name where it matters.
    source_globs: tuple[str, ...]
    # Where a call to the query helper can appear, for the scan that asks whether the app reads a
    # store at all.
    query_globs: tuple[str, ...]
    # Path prefixes under `source_globs` that are not the app's own source: third-party bundles the
    # template ships, which the agent is neither shown nor expected to read.
    vendored: tuple[str, ...] = ()


# FastAPI serving a page that loads React, Ant Design, Day.js and Highcharts as plain scripts — the
# stack the Workbench itself is built on, with no build step and no node_modules (#490). The page is
# `static/index.html`; the app is `static/app.js`; the creator's own routes go in `app.py`.
FASTAPI_ANTD = Stack(
    name="fastapi-antd",
    template_dir=_REPO / "template" / "fastapi-antd",
    sentinel="app.py",
    # sage_serve.py imports sage_queries.py; app.py imports sage_serve.py; app.sh runs app.py.
    deploy_files=(
        "sage_queries.py",
        "sage_serve.py",
        "scripts/rehydrate_data.py",
        "app.sh",
    ),
    # errorBoundary.js calls reportRuntimeError.js, so the reporter lands first: a refresh that dies
    # partway leaves an older boundary with a newer reporter, never a newer boundary importing
    # exports that are not there.
    owned_sources=(
        TEMPLATE.base_path,
        TEMPLATE.model_api_path,
        TEMPLATE.query_path,
        "static/sage/reportRuntimeError.js",
        "static/sage/errorBoundary.js",
        "static/theme.js",
    ),
    helpers=TEMPLATE,
    preview_config=None,
    # The entry script IS the server (`uvicorn app:app`); there is no second file it execs.
    server_script=None,
    entry_file="static/app.js",
    source_globs=("*.py", "static/**/*"),
    query_globs=("static/**/*.js",),
    vendored=("static/vendor/",),
)

#: Every stack Sage can seed, by the name the record holds. One entry: see the module docstring for
#: what an app naming any other stack (in practice, only `react-vite`) gets instead.
STACKS: dict[str, Stack] = {FASTAPI_ANTD.name: FASTAPI_ANTD}


def stack_of(app_path: Path) -> Stack:
    """The stack behind one app directory's record, for a caller that holds only the path. A
    record naming a stack this Sage no longer carries answers `fastapi-antd` — see the module
    docstring for why that is safe here and not at a Build turn's entry."""
    return STACKS.get(read_stack_name(app_path), FASTAPI_ANTD)


def read_stack_name(app_path: Path) -> str:
    """The stack an app's own record names, or `react-vite` when it names none.

    Reads `.sage/settings.json` directly rather than through the manager's reader, because the
    manager imports this module: a stack is a fact the manager keys on, not one it owns. Unreadable
    reads as absent, for the reason the manager's reader treats a broken settings file as empty — a
    stray comma in a record must not decide that an app on the disk cannot be opened.
    """
    try:
        settings = json.loads((app_path / ".sage" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return LEGACY_STACK
    name = settings.get(STACK_KEY) if isinstance(settings, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else LEGACY_STACK


def default_stack_name() -> str:
    """The stack a NEW app gets when nobody chose one. `SAGE_DEFAULT_STACK` is the deployment's
    say; the fallback is the one stack Sage carries."""
    return os.environ.get("SAGE_DEFAULT_STACK") or FASTAPI_ANTD.name
