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
    binding = Binding(KIND_DATA_SOURCE, "ds-dwh", "warehouse", "warehouse",
                      "DWH", "MARTS", None, "SnowflakeConfig")
    block = agents_block([BoundSource(binding, [], [], None)], [], 5000, names=names)
    assert "const { columns, rows, records } = await" in block
    assert "`records`" in block and "positional" in block
