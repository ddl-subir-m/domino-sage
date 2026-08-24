"""The preview does not rebuild its dependency graph under a running page.

The failure this pins, reproduced against a real dev server on 2026-08-24: the starter app imports
react and react-dom and nothing else, so a cold start pre-bundles only those. The first time the
agent wrote `import { Search } from "lucide-react"`, Vite met a dependency it had never optimized,
re-ran the optimizer and replaced the shared runtime chunk. The open preview was left holding modules
from a graph that no longer existed —

    ReferenceError: Search is not defined
    Invalid hook call ... You might have more than one copy of React in the same app

— and those went to the agent through `reportRuntimeError`, which spent a long turn hunting an import
that was never missing while `tsc` and `vite build` both passed.

Two halves, tested here. The config has to name every shipped dependency, and it has to actually
reach an app that already exists — the trap `refresh_entry_script` already records, and the one the
LLM helper fell into for #7.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"
CONFIG = (TEMPLATE / "vite.config.ts").read_text()
PACKAGE = json.loads((TEMPLATE / "package.json").read_text())


def test_every_shipped_dependency_is_pre_bundled():
    """The include list is built from package.json, so it cannot drift from what ships.

    Asserted through the source rather than by running Vite: what matters is that the list is
    DERIVED, because a hand-written one is the thing that goes stale the next time a library is
    added — which is exactly how lucide-react came to be missing from it.
    """
    assert "optimizeDeps" in CONFIG
    include = CONFIG.split("optimizeDeps", 1)[1]
    assert "shipped" in include, "the include list must come from package.json, not a hand-kept copy"
    # And `shipped` must be the dependencies, not devDependencies: a build-time tool is never
    # imported by the app, so pre-bundling it would cost start-up time and prevent nothing.
    assert re.search(r"shipped\s*=\s*Object\.keys\(", CONFIG)
    assert ".dependencies" in CONFIG and "devDependencies" not in CONFIG


def test_the_libraries_the_agent_is_told_to_use_are_all_shipped_dependencies():
    # The include list is only complete if package.json is. AGENTS.md offers these by name, so an
    # entry there with no dependency behind it is an import the agent will write and the preview
    # will not have pre-bundled.
    agents = (TEMPLATE / "AGENTS.md").read_text()
    offered = {"recharts", "react-router-dom", "date-fns", "lucide-react"}
    assert offered <= set(PACKAGE["dependencies"]), "AGENTS.md offers a library that is not shipped"
    assert all(f"`{name}`" in agents for name in offered)


def test_react_stays_deduped():
    # The pre-existing half of the guard, and still load-bearing: pre-bundling keeps the optimize
    # pass from being re-run, dedupe keeps one React inside it.
    assert 'dedupe: ["react", "react-dom"]' in CONFIG


def test_the_agent_is_told_not_to_import_from_inside_a_package():
    # The one hole pre-bundling cannot close: a subpath (`date-fns/format`) is its own optimize
    # entry, so importing one re-runs the optimizer exactly as an unknown package would. Verified
    # against a real dev server, with the fix in place: `dependency optimized: date-fns/format`.
    agents = (TEMPLATE / "AGENTS.md").read_text()
    assert "date-fns/format" in agents
    assert "package root" in agents


# ---- and it has to reach an app that already exists -------------------------------------------------

def _orch(tmp: Path):
    """A project seeded from a stub template carrying a preview config, as a real one does."""
    from sage.orchestrator.service import Orchestrator
    from sage.router.models import ModelCatalog

    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "vite.config.ts").write_text("// template v2\n")
    catalog = ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                           plan="p", implement="i", ask="a")
    return Orchestrator(workspace_dir=tmp / "mnt" / "code", template=t, gateway=object(),
                        catalog=catalog, project_id="Sage")


def test_an_existing_app_gets_the_fixed_preview_config(tmp_path: Path):
    """The half that decides whether any of this reaches the person who reported it.

    vite.config.ts is committed when a project is seeded, so an app keeps the copy it was born with.
    Left alone, the fix above would reach new projects only — and the app that hit the bug would go
    on hitting it, which is the failure refresh_entry_script was written for and the one the LLM
    helper repeated for #7.
    """
    orch = _orch(tmp_path)
    config = orch.project(start_preview=False).workspace.path / "vite.config.ts"
    config.write_text("// an older Sage wrote this\n")

    orch._project = None                      # the next call re-attaches, as a restart would
    orch.project(start_preview=False)

    assert config.read_text() == "// template v2\n"


def test_an_unchanged_preview_config_is_not_rewritten(tmp_path: Path):
    # Committed to the app's repo, so an identical rewrite would still show up as a dirty file in
    # the turn's tree comparison and in git history, for no change at all.
    orch = _orch(tmp_path)
    config = orch.project(start_preview=False).workspace.path / "vite.config.ts"
    before = config.stat().st_mtime_ns

    orch._project = None
    orch.project(start_preview=False)

    assert config.stat().st_mtime_ns == before
