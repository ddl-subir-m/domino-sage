"""A handoff that binds no Data Source says so, on the sheet and to the build agent (#669).

From a live run. The person cleared the conversation's chips and handed it to Build. A Binding is a
person's pick (ADR-0010), so the conversation's reads did not cross as Bindings and the app was born
with none. Nothing on the sheet said that; it listed `.sage/bindings.json` anyway. The handoff note
named the sources the conversation read with no id to copy, the app's data region was empty because
nothing was reaching yet, and the first build turn wrote every query against a made-up `snowflake`.
Each was then refused with "no longer recorded as using", which claims a binding that never existed.

Warnings only. Read sources are not bound for the person.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.orchestrator import handoff
from sage.resources.builtapp import catalog_problems

from .fake_opencode import Turn
from .test_handoff_receipt import _DESK, ALL_ON, _no_waiting, _orch, _store  # noqa: F401

_SHEET_HARNESS = Path(__file__).resolve().parent / "js" / "handoff_sheet_harness.mjs"
REPO = Path(__file__).resolve().parents[2]
_ON = {"resources": True, "artifacts": True, "transcript": False}
_READ = {"type": "done", "dataUsed": [{
    "operation_id": "du_1", "operation": "table_read", "source": "SFDC_OPPORTUNITY",
    "turn_id": "turn_1", "columns": ["stage"],
}]}
_CHIP = {"id": "ctx_1", "kind": "data_source", "name": "trades",
         "bindingKey": ["data_source", "ds-1"]}

node = pytest.mark.skipif(shutil.which("node") is None,
                          reason="node is not on PATH (it is in the Sage image)")


def _render_sheet(cases: list[dict]) -> list[dict]:
    out = subprocess.run(["node", str(_SHEET_HARNESS)], input=json.dumps(cases), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _optional_rows(rendered: dict) -> list[str]:
    return rendered["sections"][1]["rows"]


# ---- the sheet ---------------------------------------------------------------------------------


@node
def test_the_sheet_warns_when_no_data_source_will_cross_and_names_what_was_read():
    [sheet] = _render_sheet([{**_ON, "draft": {
        "bindingKinds": [], "dataReads": ["SFDC_OPPORTUNITY", "GONG_CALLS"]}}])

    [alert] = sheet["alerts"]
    assert "No Data Source will carry over" in alert
    assert "SFDC_OPPORTUNITY" in alert and "GONG_CALLS" in alert


@node
def test_the_sheet_does_not_list_bindings_when_nothing_binds():
    [sheet] = _render_sheet([{**_ON, "draft": {"bindingKinds": [], "dataReads": []}}])

    assert ".sage/bindings.json" not in _optional_rows(sheet)
    assert sheet["alerts"]


@node
def test_a_bound_data_source_draws_no_warning():
    [sheet] = _render_sheet([{**_ON, "draft": {"bindingKinds": ["data_source"],
                                               "dataReads": ["SFDC_OPPORTUNITY"]}}])

    assert sheet["alerts"] == []
    assert ".sage/bindings.json" in _optional_rows(sheet)


@node
def test_a_dataset_alone_still_lists_bindings():
    [sheet] = _render_sheet([{**_ON, "draft": {"bindingKinds": ["dataset"], "dataReads": []}}])

    assert ".sage/bindings.json" in _optional_rows(sheet)


@node
def test_resources_left_out_draw_no_warning():
    [sheet] = _render_sheet([{"resources": False, "artifacts": True, "transcript": False,
                              "draft": {"bindingKinds": [], "dataReads": ["SFDC_OPPORTUNITY"]}}])

    assert sheet["alerts"] == []


# ---- the sheet payload -------------------------------------------------------------------------


def _drafted(tmp_path: Path, chips: list[dict], history: list[dict]):
    orch, _oc, root = _orch(tmp_path, [Turn(text="A dashboard, then."), Turn(text=_DESK)])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "build me a pipeline dashboard"))
    store = _store(orch)
    for row in history:
        store.append_history(tid, row)
    store.write_context(tid, {"items": chips})
    return orch, root, tid, orch.draft_handoff_plan(tid)


def test_the_payload_says_what_the_chips_bind_and_what_the_conversation_read(tmp_path: Path):
    *_, draft = _drafted(tmp_path, [], [_READ])

    assert draft["bindingKinds"] == []
    assert draft["dataReads"] == ["SFDC_OPPORTUNITY"]


def test_the_payload_names_a_chip_that_binds(tmp_path: Path):
    *_, draft = _drafted(tmp_path, [_CHIP], [])

    assert draft["bindingKinds"] == ["data_source"]
    assert draft["dataReads"] == []


# ---- the build agent ---------------------------------------------------------------------------


def _agents_after_confirm(tmp_path: Path, history: list[dict]) -> str:
    orch, root, tid, _draft = _drafted(tmp_path, [], history)
    orch.confirm_handoff(tid, ALL_ON)
    app_id = orch.project(start_preview=False).workspace.app_id
    return (root / "apps" / app_id / "AGENTS.md").read_text()


def test_a_handoff_that_read_data_and_binds_none_warns_before_the_first_turn(tmp_path: Path):
    agents = _agents_after_confirm(tmp_path, [_READ])

    assert "Stop calling `runQuery`" in agents


def test_a_handoff_that_read_no_data_is_told_none_of_it(tmp_path: Path):
    agents = _agents_after_confirm(tmp_path, [])

    assert "Stop calling `runQuery`" not in agents


def test_the_note_marks_data_used_as_background_when_nothing_binds():
    lines = ["table read from SFDC_OPPORTUNITY."]
    digest = handoff.confirm_digest("Background.", artifacts=[], context=[],
                                    include_artifacts=False, include_resources=True,
                                    data_used=lines)

    assert "table read from SFDC_OPPORTUNITY." in digest
    assert "the app cannot query" in digest


def test_the_note_does_not_mark_data_used_when_a_data_source_binds():
    lines = ["table read from SFDC_OPPORTUNITY."]
    digest = handoff.confirm_digest("Background.", artifacts=[], context=[_CHIP],
                                    include_artifacts=False, include_resources=True,
                                    data_used=lines)

    assert "table read from SFDC_OPPORTUNITY." in digest
    assert "the app cannot query" not in digest


# ---- the app's own refusal ---------------------------------------------------------------------


@pytest.mark.parametrize("stack", ["react-vite", "fastapi-antd"])
def test_a_query_on_a_source_never_bound_does_not_say_no_longer(tmp_path: Path, stack):
    (tmp_path / ".sage").mkdir()
    (tmp_path / ".sage" / "queries.json").write_text(json.dumps(
        [{"name": "pipeline", "binding": "snowflake", "sql": "SELECT 1"}]))

    [problem] = catalog_problems(REPO / "template" / stack, tmp_path)

    assert "snowflake" in problem
    assert "no longer" not in problem
    assert "not recorded as using" in problem
