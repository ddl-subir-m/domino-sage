"""`runQuery` hands an app its rows by column name as well as by position (#662).

Live (2026-10-06): a dashboard read `row.OPEN_PIPELINE` off `sage.runQuery`'s `rows`, which are
positional arrays. Every read came back undefined, so every page drew $0, 0 and "No data" over
queries that had answered. `records` is the same rows as objects keyed by the column names the
store returned, so the read an agent reaches for first is a read that works.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.resources.app_helpers import FASTAPI
from sage.resources.app_helpers import TEMPLATE as TEMPLATE_NAMES
from sage.resources.bindings import KIND_DATA_SOURCE, Binding
from sage.resources.bound_schema import BoundSource, agents_block

HARNESS = Path(__file__).parent / "js" / "app_query_records_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

ANSWER = {"columns": ["OWNER", "OPEN_PIPELINE"], "rows": [["Ana", 1200.5], ["Bo", None]],
          "truncated": False}


def _harness(template: str, reads: list[str]) -> dict:
    out = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps({"template": template, "body": ANSWER, "reads": reads}),
        capture_output=True, text=True, timeout=30, check=True,
    )
    return json.loads(out.stdout)


def _run(template: str) -> dict:
    return _harness(template, [])["result"]


def _reports(template: str, reads: list[str]) -> list[str]:
    return _harness(template, reads)["reports"]


@pytest.mark.parametrize("template", ["fastapi-antd", "react-vite"])
def test_each_row_is_also_an_object_keyed_by_its_columns(template: str):
    got = _run(template)
    assert got["records"] == [{"OWNER": "Ana", "OPEN_PIPELINE": 1200.5},
                              {"OWNER": "Bo", "OPEN_PIPELINE": None}]


@pytest.mark.parametrize("template", ["fastapi-antd", "react-vite"])
def test_the_positional_rows_are_unchanged(template: str):
    got = _run(template)
    assert got["columns"] == ANSWER["columns"]
    assert got["rows"] == ANSWER["rows"]


@pytest.mark.parametrize("template", ["fastapi-antd", "react-vite"])
def test_reading_a_column_the_query_does_not_return_is_reported(template: str):
    """#673: a page read `'Change %'` off rows keyed `ChangePct`, got undefined on every row and drew
    zeros. The report goes where a crash goes, so the build that wrote the read is told to fix it —
    once per name, not once per row."""
    assert _reports(template, ["Open pipeline"]) == [
        "query q has no column 'Open pipeline'; columns are OWNER, OPEN_PIPELINE"]


@pytest.mark.parametrize("template", ["fastapi-antd", "react-vite"])
def test_reading_a_returned_column_reports_nothing(template: str):
    assert _reports(template, ["OWNER", "OPEN_PIPELINE"]) == []


@pytest.mark.parametrize("template", ["fastapi-antd", "react-vite"])
@pytest.mark.parametrize("probe", ["Symbol.iterator", "hasOwnProperty", "constructor", "toJSON",
                                   "then", "key", "children"])
def test_a_probe_that_is_not_a_column_read_reports_nothing(template: str, probe: str):
    """Symbols and Object.prototype names are the language's own reads; `toJSON` is
    JSON.stringify's, `then` is await's, and `key` and `children` are what antd's Table reads off
    every row it is given (rowKey "key", childrenColumnName "children")."""
    assert _reports(template, [probe]) == []


@pytest.mark.parametrize("names", [FASTAPI, TEMPLATE_NAMES], ids=["fastapi-antd", "react-vite"])
def test_the_instructions_an_agent_reads_show_the_by_name_read(names):
    block = _block(names)
    assert "const { columns, rows, records } = await" in block
    assert "`records`" in block and "positional" in block


def _block(names) -> str:
    binding = Binding(KIND_DATA_SOURCE, "ds-dwh", "warehouse", "warehouse",
                      "DWH", "MARTS", None, "SnowflakeConfig")
    return agents_block([BoundSource(binding, [], [], None)], [], 5000, names=names)


# ---- useQuery (#681): the loading/error/empty/ready machine every screen used to hand-write --------

TWO = {"body": ANSWER}
NONE = {"body": {"columns": ["OWNER"], "rows": [], "truncated": False}}
FAILED = {"status": 403, "body": {"error": "You cannot read this data."}}


def _hook(template: str, steps: list, answers: list) -> dict:
    out = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps({"template": template, "steps": steps, "answers": answers}),
        capture_output=True, text=True, timeout=30, check=True,
    )
    return json.loads(out.stdout)


TEMPLATES = ["fastapi-antd", "react-vite"]


@pytest.mark.parametrize("template", TEMPLATES)
def test_use_query_loads_then_is_ready_with_the_records_and_their_evidence(template: str):
    got = _hook(template, [{"mount": ["q", {"since": "2026-01-01"}]}, "settle"], [TWO])
    loading, ready = got["snapshots"]
    assert (loading["status"], loading["records"]) == ("loading", None)
    assert ready["status"] == "ready" and ready["error"] is None and ready["refreshing"] is False
    assert ready["records"] == [{"OWNER": "Ana", "OPEN_PIPELINE": 1200.5},
                                {"OWNER": "Bo", "OPEN_PIPELINE": None}]
    assert ready["query"] == "q"            # dataUsed passes through
    assert got["requests"] == [{"query": "q", "params": {"since": "2026-01-01"}, "aborted": False}]


@pytest.mark.parametrize("template", TEMPLATES)
def test_use_query_is_empty_only_for_zero_records_and_a_failure_is_an_error(template: str):
    empty = _hook(template, [{"mount": ["q"]}, "settle"], [NONE])["snapshots"][-1]
    assert (empty["status"], empty["records"]) == ("empty", [])
    failed = _hook(template, [{"mount": ["q"]}, "settle"], [FAILED])["snapshots"][-1]
    assert failed["status"] == "error"
    assert failed["error"] == "You cannot read this data."   # the viewer's sentence, unchanged
    assert failed["records"] is None


@pytest.mark.parametrize("template", TEMPLATES)
def test_use_query_answers_a_second_mount_from_the_page_cache(template: str):
    got = _hook(template, [{"mount": ["q", {"a": 1, "b": 2}]}, "settle", "unmount",
                           {"mount": ["q", {"b": 2, "a": 1}]}], [TWO])
    assert got["snapshots"][-1]["status"] == "ready"         # on its first render, no spinner
    assert len(got["requests"]) == 1


@pytest.mark.parametrize("template", TEMPLATES)
def test_refresh_refetches_and_keeps_the_old_answer_on_screen_meanwhile(template: str):
    got = _hook(template, [{"mount": ["q"]}, "settle", "refresh", "settle"], [TWO, NONE])
    _, ready, refreshing, after = got["snapshots"]
    assert refreshing["status"] == "ready" and refreshing["refreshing"] is True
    assert refreshing["records"] == ready["records"]
    assert (after["status"], after["refreshing"]) == ("empty", False)
    assert len(got["requests"]) == 2


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_superseded_request_is_aborted_and_its_abort_is_not_an_error(template: str):
    got = _hook(template, [{"mount": ["q", {"r": "east"}]}, {"props": ["q", {"r": "west"}]}, "settle"],
                [TWO, NONE])
    assert [r["aborted"] for r in got["requests"]] == [True, False]
    assert got["snapshots"][-1]["status"] == "empty"


@pytest.mark.parametrize("template", TEMPLATES)
def test_refresh_during_a_request_is_not_reported_as_a_failure(template: str):
    # The same key both times, so only the AbortError guard keeps the aborted first request's
    # rejection off the screen.
    got = _hook(template, [{"mount": ["q"]}, "refresh", "tick", "settle"], [TWO, TWO])
    assert got["snapshots"][2]["status"] == "loading" and got["snapshots"][2]["error"] is None
    assert got["snapshots"][-1]["status"] == "ready"
    assert [r["aborted"] for r in got["requests"]] == [True, False]


@pytest.mark.parametrize("template", TEMPLATES)
def test_unmounting_aborts_the_request_in_flight(template: str):
    got = _hook(template, [{"mount": ["q"]}, "unmount"], [TWO])
    assert got["requests"][0]["aborted"] is True


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_disabled_query_sends_nothing_until_it_is_enabled(template: str):
    got = _hook(template, [{"mount": ["q", {}, {"enabled": False}]},
                           {"props": ["q", {}, {"enabled": True}]}, "settle"], [TWO])
    held, _, ready = got["snapshots"]
    assert held["status"] == "loading"
    assert ready["status"] == "ready"
    assert len(got["requests"]) == 1


@pytest.mark.parametrize("names", [FASTAPI, TEMPLATE_NAMES], ids=["fastapi-antd", "react-vite"])
def test_the_instructions_an_agent_reads_offer_use_query(names):
    block = _block(names)
    assert "useQuery(" in block
    assert "refresh" in block and '"empty"' in block
