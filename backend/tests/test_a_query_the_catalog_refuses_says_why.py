"""A query the app's own catalog refuses is reported with the catalog's sentence (#678).

Live, 2026-10-07: "2 queries failed while building: drift_bins … (Read failed (503; unavailable).)".
Both queries declared a parameter their SQL never used, and the app's own query server refused them
with a sentence saying exactly that. The refusal happens in `Query.bind`, before the executor runs,
so the executor never recorded a reason and the turn fell back to the HTTP status — which reads as
"the warehouse is down" to a person and to the next session alike.
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


@pytest.fixture(autouse=True)
def _fresh_modules(build, monkeypatch):
    # After `build`: opening the project already asked the bare test template for `sage_queries.py`
    # and memoised "none", and `_app` copies the real one in afterwards.
    monkeypatch.setattr(builtapp, "_loaded", {})

TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"
UNUSED = "The query drift_bins declares region, which its statement never uses."


def _app(tmp_path: Path, project, sql: str) -> None:
    """The template's real `sage_queries.py`, and one catalog entry bound to one Data Source."""
    shutil.copy(TEMPLATE / "sage_queries.py", tmp_path / "template" / "sage_queries.py")
    project.workspace.update_bindings(lambda _: [
        {"kind": "data_source", "id": "ds-dwh", "name": "warehouse",
         "connector_type": "SnowflakeConfig"}])
    sage = project.workspace.path / ".sage"
    sage.mkdir(parents=True, exist_ok=True)
    (sage / "queries.json").write_text(json.dumps([
        {"name": "drift_bins", "binding": "ds-dwh", "sql": sql,
         "params": [{"name": "region", "type": "string"}]}]), encoding="utf-8")


def _page(orch, status: int, body: bytes = b"{}"):
    def report(event):
        orch.record_preview_ack(event["validationId"])
        context = orch.capture_preview_read(event["validationId"], "/api/queries/drift_bins",
                                            kind="query")
        orch.record_platform_read_failure(status, "/api/queries/drift_bins", context=context,
                                          body=body)
    return report


def _failed(events: list[dict]) -> str:
    return next(e for e in events if e["type"] == "data-source-failed")["message"]


@pytest.mark.parametrize("status", [503, 422])
def test_a_read_the_catalog_refused_ends_the_turn_with_the_catalog_sentence(build, tmp_path, status):
    orch, project, _ = build
    _app(tmp_path, project, "SELECT * FROM drift")

    events, done = run(orch, report=_page(orch, status, json.dumps({"error": UNUSED}).encode()))

    assert done["ok"] is False
    message = _failed(events)
    assert UNUSED in message
    assert "Read failed" not in message


def test_a_usable_query_that_failed_without_a_reason_keeps_the_status_fallback(build, tmp_path):
    # The discriminator: the catalog is consulted for a reason, not used to invent one. A query the
    # catalog holds no problem with, refused with nothing recorded, still says what the read saw.
    orch, project, _ = build
    _app(tmp_path, project, "SELECT * FROM drift WHERE region = :region")

    events, _ = run(orch, report=_page(orch, 503))

    assert "Read failed (503; unavailable)." in _failed(events)


def test_a_repair_turn_reports_the_query_fixed_only_after_the_preview_reads_it(build, tmp_path):
    orch, project, oc = build
    _app(tmp_path, project, "SELECT * FROM drift")
    _, first = run(orch, report=_page(orch, 422, json.dumps({"error": UNUSED}).encode()))
    assert first["ok"] is False

    # The agent fixes the catalog; a turn whose page never re-ran the query cannot call it read.
    _app(tmp_path, project, "SELECT * FROM drift WHERE region = :region")
    oc.turns.append(Turn(writes={"src/App.tsx": "// repaired\n"}))
    events, unread = run(orch, report=lambda ev: orch.record_preview_ack(ev["validationId"]))
    assert not [e for e in events if e["type"] == "data-source-failed"]
    assert unread["verification"]["stages"]["data"] == "unverified"

    # Only a turn whose preview read the query again, and got rows, reports its data as passed.
    oc.turns.append(Turn(writes={"src/App.tsx": "// repaired again\n"}))
    _, read = run(orch, report=_page(orch, 200, b'{"columns": [], "rows": [[1]]}'))
    assert read["ok"] is True
    assert read["verification"]["stages"]["data"] == "passed"
    assert [(r["path"], r["outcome"]) for r in read["verification"]["dataReads"]] == [
        ("/api/queries/drift_bins", "passed")]
