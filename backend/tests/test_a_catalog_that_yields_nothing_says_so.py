"""A build must not report clean on an app whose every query 404s.

From a live run. A Chat handoff was never confirmed, so the Snowflake Data Source stayed a Project
resource and never became a Binding on the Built App. Build then planned and implemented a dashboard
against it anyway: `runQuery("ai_consumption_daily")` in `src/`, a guessed `.sage/queries.json` on
disk. Every screen answered "This app has no query called ai_consumption_daily." Both build turns
reported "Done — build is clean".

Two silences let that through, and this file holds one test for each.

`load_queries` discards a catalog that is not a JSON list, whole and without a word — which is right
for the app, where an unknown name is a 404 and that IS the truth, and wrong for `catalog_problems`,
whose `[]` then means "nothing to fix" for a file that contributed nothing. `catalog_fault` is the
missing sentence, and it lives in `serve.py` so that which shapes are accepted is decided once.

`agents_block` returns "" when no Data Source is bound, to keep the machinery for an absent store off
every turn. But the query helper ships in `src/` whether or not anything is bound, so silence is the
only thing in front of an agent that has already written a query — and it reads as permission. The
second turn of the live run "fixed" the 404 by guessing four query names. `reaching` breaks that
silence for an app that is querying with nothing bound, and leaves it for every app that is not.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from sage.resources.bound_schema import agents_block
from sage.resources.builtapp import catalog_problems

from .test_bound_schema import data_block, orchestrator, workspace_of

TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"
CATALOG = ".sage/queries.json"


def _serve():
    """The app's own `serve.py`, loaded by path — the same trick `test_builtapp_queries.py` uses."""
    spec = importlib.util.spec_from_file_location("builtapp_serve_fault", TEMPLATE / "serve.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write(root: Path, doc) -> Path:
    path = root / CATALOG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return root


GOOD = [{"name": "usage", "binding": "ds-dwh", "sql": "SELECT 1"}]
# The three shapes an agent guesses at when nothing told it the format. Each one leaves
# `load_queries` with an empty catalog, so each one 404s every name the app asks for.
SILENT = {
    "an object keyed by name": {"usage": {"binding": "ds-dwh", "sql": "SELECT 1"}},
    "a list wrapped in an object": {"queries": GOOD},
    "not JSON at all": "{ not json",
}


# ---- the sentence `load_queries` never said ---------------------------------------------------


def test_a_catalog_shaped_wrong_is_a_fault_not_an_empty_catalog(tmp_path: Path):
    serve = _serve()
    for label, doc in SILENT.items():
        root = _write(tmp_path / label.replace(" ", "_"), doc)
        assert serve.load_queries(root) == {}, label
        # The whole bug: an empty catalog and a discarded one used to be the same answer.
        assert serve.catalog_fault(root), label
        assert CATALOG in serve.catalog_fault(root), label


def test_an_entry_with_no_name_declares_no_query_and_says_so(tmp_path: Path):
    serve = _serve()
    root = _write(tmp_path, [GOOD[0], {"binding": "ds-dwh", "sql": "SELECT 2"}])
    assert list(serve.load_queries(root)) == ["usage"]
    fault = serve.catalog_fault(root)
    assert "1 of the 2 entries" in fault


def test_a_duplicate_name_is_a_rule_not_a_fault(tmp_path: Path):
    """First declaration wins is deliberate. Reporting it would make a rule read as a defect."""
    serve = _serve()
    root = _write(tmp_path, GOOD + [{"name": "usage", "binding": "ds-dwh", "sql": "SELECT 2"}])
    assert serve.catalog_fault(root) == ""


def test_an_app_with_no_catalog_has_no_fault(tmp_path: Path):
    """Most apps read no store. An absent catalog is a state, not a mistake."""
    assert _serve().catalog_fault(tmp_path) == ""


def test_a_readable_catalog_has_no_fault(tmp_path: Path):
    assert _serve().catalog_fault(_write(tmp_path, GOOD)) == ""


# ---- what Sage reports on the creator's behalf --------------------------------------------------


def test_a_discarded_catalog_is_reported_instead_of_nothing(tmp_path: Path):
    """The check that said "clean" over the live app that 404'd on every screen."""
    for label, doc in SILENT.items():
        root = _write(tmp_path / label.replace(" ", "_"), doc)
        problems = catalog_problems(TEMPLATE, root)
        assert problems, label
        assert CATALOG in problems[0], label


def test_an_app_with_no_catalog_still_has_no_problems(tmp_path: Path):
    assert catalog_problems(TEMPLATE, tmp_path) == []


def test_the_file_that_was_not_read_is_named_before_anything_read_out_of_it(tmp_path: Path):
    """A per-query problem is about one query; a fault is about the file that holds them all."""
    root = _write(tmp_path, [{"name": "usage", "sql": "SELECT 1"}, {"sql": "SELECT 2"}])
    problems = catalog_problems(TEMPLATE, root)
    assert "entries" in problems[0]
    assert "does not say which Data Source it reads" in problems[1]


# ---- what the agent is told when it queries with nothing bound ----------------------------------


def test_an_app_that_is_not_reaching_is_still_told_none_of_this():
    """Unchanged, and load-bearing: this region is read on every turn of every app."""
    assert agents_block([], None, 5000) == ""
    assert agents_block([], ["a leftover problem"], 5000) == ""


def test_an_app_querying_with_nothing_bound_is_told_to_stop():
    block = agents_block([], None, 5000, reaching=True)
    assert "no Data Source bound" in block
    assert "Stop calling `runQuery`" in block
    assert "src/appQuery.ts" in block


def test_it_is_told_the_name_is_not_the_problem():
    """The live "fix" was four guessed query names in one turn. This is the line against that."""
    block = agents_block([], None, 5000, reaching=True)
    assert "do not guess another one" in block
    assert "four ways to fail in one turn" in block


def test_it_is_told_who_can_actually_bind_one():
    """Without this the agent has no move but to try again, which is how the loop started."""
    block = agents_block([], None, 5000, reaching=True)
    assert "Say this to the user, and stop." in block
    assert "you cannot, from here" in block


def test_the_catalog_already_on_disk_is_quoted_under_it():
    block = agents_block([], ["queries.json is not valid JSON"], 5000, reaching=True)
    assert "queries.json is not valid JSON" in block


# ---- the live run, through a real workspace -----------------------------------------------------


def test_a_dashboard_built_against_an_unbound_store_is_told_so(tmp_path: Path):
    """The regression. No Binding, a catalog on disk, and an AGENTS.md region that used to be empty."""
    orch = orchestrator(tmp_path)
    workspace = workspace_of(orch)
    _write(workspace, SILENT["an object keyed by name"])
    orch._recheck_app_data()

    block = data_block(workspace)
    assert "no Data Source bound" in block
    # Both halves reach the agent: which file is unreadable, and that no name will fix it.
    assert CATALOG in block
    assert "do not guess another one" in block


def test_a_call_in_src_is_enough_on_its_own(tmp_path: Path):
    """An agent that wrote the call and not the catalog has still decided this app reads a store."""
    orch = orchestrator(tmp_path)
    workspace = workspace_of(orch)
    (workspace / "src" / "App.tsx").write_text(
        'import { runQuery } from "./appQuery";\n'
        'export default function App() { runQuery("usage", {}); return null; }\n')
    orch._recheck_app_data()

    assert "no Data Source bound" in data_block(workspace)


def test_the_helper_defining_runquery_does_not_count_as_reaching(tmp_path: Path):
    """`runQuery` is DEFINED in a file Sage owns and ships to every app. Counting it would make
    every app look like it were querying, which would put this block in front of all of them."""
    orch = orchestrator(tmp_path)
    workspace = workspace_of(orch)
    assert "runQuery" in (workspace / "src" / "appQuery.ts").read_text()

    orch._recheck_app_data()

    assert data_block(workspace) == ""
