"""Which kind of app a Built App is, and what Sage has to know about that kind (#490).

Sage seeds a Built App from `template/react-vite`: a React + TypeScript + Vite app that Node builds
and Python serves (ADR-0002). Everything the backend knew about that shape used to be a constant
spread across a dozen modules — `package.json` is the file that says "an app is here", the deploy
files are these four, the Sage-owned helpers live under `src/` and end in `.ts`.

So a Stack is a property of the app in hand, fixed at birth, and this is the ONE place that answers
what a kind of app needs. Every caller takes a `Stack` and asks it; nobody works the shape out
again. There is one stack today. The seam is kept because it is where the answers live, not because
a second stack is expected: an app cannot change its own kind after birth, since a workspace seeded
from a template never re-seeds (#40).

The record is `stack` in the app's own `.sage/settings.json`, beside `createdAt` — the app's record,
committed to its repo, so a collaborator's clone reads the same answer. Absent means the app was born
before the record existed, and every such app is react-vite. It is never DETECTED from the files:
the seed sentinel is a file the agent can delete (`WorkspaceManager.ensure` says so), and a stack
guessed from what is left on disk would re-seed the wrong template over a real app.
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
#: What an app with no record is. Every app seeded before #490 is one of these.
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


REACT_VITE = Stack(
    name="react-vite",
    template_dir=_REPO / "template" / "react-vite",
    sentinel="package.json",
    # sage_queries.py and sage_domino.py before serve.py, which imports both; all three before
    # app.sh, which execs serve.py.
    deploy_files=(
        "sage_queries.py",
        "sage_domino.py",
        "serve.py",
        "scripts/rehydrate-data.mjs",
        "scripts/rehydrate_data.py",
        "app.sh",
    ),
    # ErrorBoundary.tsx imports reportRuntimeError.ts, so the reporter lands first: a refresh that
    # dies partway leaves an older boundary with a newer reporter, never a newer boundary importing
    # exports that are not there.
    owned_sources=(
        TEMPLATE.model_api_path,
        TEMPLATE.query_path,
        "src/reportRuntimeError.ts",
        "src/ErrorBoundary.tsx",
    ),
    helpers=TEMPLATE,
    preview_config="vite.config.ts",
    server_script="serve.py",
    entry_file="src/App.tsx",
    source_globs=("src/**/*",),
    query_globs=("src/**/*.ts", "src/**/*.tsx"),
)

#: Every stack Sage can seed, by the name the record holds.
STACKS: dict[str, Stack] = {REACT_VITE.name: REACT_VITE}


def stack_of(app_path: Path) -> Stack:
    """The stack behind one app directory's record, for a caller that holds only the path."""
    return STACKS.get(read_stack_name(app_path), REACT_VITE)


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
    say; the fallback is react-vite, the only stack Sage carries."""
    return os.environ.get("SAGE_DEFAULT_STACK") or REACT_VITE.name
