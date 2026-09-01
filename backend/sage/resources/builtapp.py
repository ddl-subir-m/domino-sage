"""The Built App's own rules, read from the file that enforces them (#15).

Two things Sage needs to know during a build session are already decided, in code, by
`template/react-vite/serve.py`: whether a recorded Scope can travel as configuration, and whether a
query catalog the agent just wrote will actually run. Both are #14's, and both are enforced at app
startup — which is after a publish and a cold start.

So this loads that file and asks it, rather than restating its tables here. A second copy of
`_SCOPE_KEYS` in the orchestrator would be right on the day it was written and wrong on the day
someone added a connector to one of them, and the failure would be a query Sage promised was fine
that the published app then refuses. `backend/tests/` already loads `serve.py` by path for exactly
this reason; this is the same trick with the same justification.

Loaded once and memoised per template directory: it is a small stdlib-only module, but a build turn
should not pay to exec it, and re-execing would give two `Source` classes whose instances are not
each other's.

Every function here degrades rather than raises. A template without `serve.py` is not a state worth
failing a build turn over — it means Sage cannot say whether the queries are good, which is exactly
what "no problems found" must not be confused with, so the caller gets `None` and says so.
"""
from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from typing import Any

from .bindings import Binding

# The template directory IS the app template (`template/react-vite`, per SAGE_TEMPLATE), so this is
# the same `serve.py` that `_DEPLOY_FILES` copies into every app.
_SERVE_REL = Path("serve.py")
_MODULE_NAME = "sage_builtapp_serve"
_loaded: dict[str, Any] = {}
_lock = threading.Lock()


def serve_module(template_dir: Path) -> Any | None:
    """`serve.py` as a module, or None when this template has none.

    The module is registered in `sys.modules` BEFORE it is executed, because it uses
    `from __future__ import annotations` and its dataclasses resolve their field types by looking
    their own module up there.
    """
    key = str(template_dir)
    with _lock:
        if key in _loaded:
            return _loaded[key]
        path = template_dir / _SERVE_REL
        module = None
        if path.is_file():
            try:
                spec = importlib.util.spec_from_file_location(f"{_MODULE_NAME}_{abs(hash(key))}", path)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[spec.name] = module
                    spec.loader.exec_module(module)
            except Exception:       # a template we cannot read is "cannot say", never a failed turn
                module = None
        _loaded[key] = module
        return module


def stranded_levels(template_dir: Path, binding: Binding) -> list[tuple[str, str]] | None:
    """The Scope levels this Binding records that cannot travel as configuration, or None when Sage
    could not ask. `[]` means every recorded level travels, which is a different answer.

    This is what turns #14's rule into a sentence the agent can act on while it is still writing the
    query, instead of one the app prints after it is published.
    """
    module = serve_module(template_dir)
    if module is None:
        return None
    source = module.Source(binding.id, binding.name, binding.database or "", binding.schema or "",
                           binding.connector_type or "")
    return list(source.scope()[1])


def catalog_problems(template_dir: Path, workspace_dir: Path) -> list[str] | None:
    """Why each unusable query in this app's catalog is unusable, in the app's own words, or None
    when Sage could not check.

    The app's own words matter more than the check itself: this returns the exact sentence a viewer
    would eventually be shown, so the agent fixes the thing the published app is going to complain
    about rather than something adjacent that Sage decided to say instead.

    An app with no catalog has no problems, which is not the same as having no queries — `[]` here
    covers both, because neither is anything for a creator to do.

    A catalog that yields NOTHING is a third thing, and it used to be reported as the first. A file
    in a shape `load_queries` discards whole leaves an empty catalog behind, and an empty catalog has
    no per-query problems to list — so this said `[]`, the build said clean, and the app answered
    "this app has no query called ..." to every name it was asked for. `catalog_fault` is the
    sentence for that, and it comes first because a file that was not read at all outranks anything
    read out of it.
    """
    module = serve_module(template_dir)
    if module is None:
        return None
    try:
        queries = module.load_queries(workspace_dir)
    except Exception:
        return None
    # `getattr` for the same reason everything here degrades: a template whose `serve.py` predates
    # this check can still answer the per-query half, and half an answer beats "could not check".
    check = getattr(module, "catalog_fault", None)
    fault = ""
    if check is not None:
        try:
            fault = check(workspace_dir)
        except Exception:
            fault = ""
    problems = [q.problem for q in queries.values() if q.problem]
    return ([fault] if fault else []) + problems
