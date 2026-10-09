"""A query catalog Sage already knows is broken fails the turn that wrote it (#740).

Live, Signal Room 21 prompt 8 (Usage Drift): the turn wrote `drift_quantiles` declaring a `bins`
parameter its SQL never used. The app refuses that query on every call, and `catalog_problems`
returns the exact sentence from the files on disk — but it was asked only at publish and by the NEXT
turn's AGENTS block. The turn that wrote the catalog ended `data: unverified`, with nothing naming
the query, and its last repair guessed that "the query cache needs to refresh".

#704 already did this for one sentence of the catalog's (a Data Source the app does not record).
Every sentence `catalog_problems` can say is the same kind of fact, so this is that check, widened.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from sage.resources import builtapp

from . import test_changed_page_validation as page_tests
from .fake_opencode import Turn

build = page_tests.build
run = page_tests.run

TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"
_SOURCE = [{"kind": "data_source", "id": "ds-dwh", "name": "warehouse",
            "connector_type": "SnowflakeConfig"}]


@pytest.fixture(autouse=True)
def _fresh_modules(build, monkeypatch):
    # After `build`, for the reason test_a_query_the_catalog_refuses_says_why gives.
    monkeypatch.setattr(builtapp, "_loaded", {})


def _app(tmp_path: Path, project, query: dict) -> None:
    shutil.copy(TEMPLATE / "sage_queries.py", tmp_path / "template" / "sage_queries.py")
    project.workspace.update_bindings(lambda _: _SOURCE)
    sage = project.workspace.path / ".sage"
    sage.mkdir(parents=True, exist_ok=True)
    (sage / "queries.json").write_text(json.dumps([query]), encoding="utf-8")


def _reads(orch, name: str):
    def report(event):
        orch.record_preview_ack(event["validationId"])
        context = orch.capture_preview_read(event["validationId"], f"/api/queries/{name}",
                                            kind="query")
        orch.record_platform_read_failure(200, f"/api/queries/{name}", context=context,
                                          body=b'{"columns": ["n"], "rows": [[1]]}')
    return report


BROKEN = [
    ({"name": "drift_quantiles", "binding": "ds-dwh", "sql": "SELECT * FROM usage",
      "params": [{"name": "bins", "type": "int"}]},
     "The query drift_quantiles declares bins, which its statement never uses."),
    ({"name": "drift_quantiles", "binding": "ds-dwh",
      "sql": "SELECT * FROM usage WHERE period = :period"},
     "The query drift_quantiles uses period, which it does not declare as a parameter."),
    ({"name": "drift_quantiles", "sql": "SELECT * FROM usage"},
     "The query drift_quantiles does not say which Data Source it reads."),
]


@pytest.mark.parametrize("query, sentence", BROKEN, ids=["unused", "undeclared", "unbound"])
def test_a_broken_catalog_is_sent_back_with_its_sentence_and_then_fails(build, tmp_path, query,
                                                                       sentence):
    orch, project, oc = build
    _app(tmp_path, project, query)
    oc.turns.append(Turn(writes={"src/App.tsx": "// did not fix the catalog\n"}))

    events, done = run(orch, report=_reads(orch, "drift_quantiles"))

    assert len(oc.prompts) == 2, "the broken catalog was not sent back for a repair"
    assert sentence in oc.prompts[-1]["text"]
    assert done["ok"] is False
    assert done["decision"] == "queries failed"
    assert done["verification"]["stages"]["data"] == "failed"
    assert done["verification"]["queryFailures"] == {"drift_quantiles": sentence}
    assert sentence in next(e for e in events if e["type"] == "data-source-failed")["message"]


def test_a_catalog_fixed_by_the_repair_passes(build, tmp_path):
    orch, project, oc = build
    query, sentence = BROKEN[0]
    _app(tmp_path, project, query)
    fixed = {**query, "sql": "SELECT * FROM usage LIMIT :bins"}
    oc.turns.append(Turn(writes={".sage/queries.json": json.dumps([fixed])}))

    _, done = run(orch, report=_reads(orch, "drift_quantiles"))

    assert len(oc.prompts) == 2
    assert sentence in oc.prompts[-1]["text"]
    assert done["ok"] is True, done
    assert done["verification"]["overall"] == "passed"


def test_a_catalog_with_no_problems_costs_nothing(build, tmp_path):
    orch, project, oc = build
    _app(tmp_path, project, {"name": "drift_quantiles", "binding": "ds-dwh",
                             "sql": "SELECT * FROM usage LIMIT :bins",
                             "params": [{"name": "bins", "type": "int"}]})

    events, done = run(orch, report=_reads(orch, "drift_quantiles"))

    assert len(oc.prompts) == 1
    assert not [e for e in events if e["type"] == "iterate"]
    assert done["ok"] is True, done
    assert "queryFailures" not in done["verification"]
