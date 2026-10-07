"""A handoff that binds no Data Source says so, on the sheet and to the build agent (#669).

From a live run. The person cleared the conversation's chips and handed it to Build. A Binding is a
person's pick (ADR-0010), so the conversation's reads did not cross as Bindings and the app was born
with none. Nothing on the sheet said that; it listed `.sage/bindings.json` anyway. The handoff note
named the sources the conversation read with no id to copy, the app's data region was empty because
nothing was reaching yet, and the first build turn wrote every query against a made-up `snowflake`.
Each was then refused with "no longer recorded as using", which claims a binding that never existed.

Warnings only. Read sources are not bound for the person. And only for a Data Source: a conversation
that read nothing but files (a Dataset file, an Upload) carries them across as files, so telling it
the app "will not be able to query them" would be false. A read recorded before `source_kind`
existed counts as a Data Source.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.liveread import reference, run
from sage.orchestrator import handoff
from sage.resources.builtapp import catalog_problems

from .fake_opencode import Turn
from .test_csv_calculation_data_used import args as calc_args
from .test_csv_calculation_data_used import setup_turn as calc_turn
from .test_csv_calculation_data_used import table_args
from .test_csv_text_analysis_data_used import analysis_args
from .test_csv_text_analysis_data_used import setup_turn as text_turn
from .test_handoff_receipt import _DESK, ALL_ON, _no_waiting, _orch, _store  # noqa: F401

_SHEET_HARNESS = Path(__file__).resolve().parent / "js" / "handoff_sheet_harness.mjs"
REPO = Path(__file__).resolve().parents[2]
_ON = {"resources": True, "artifacts": True, "transcript": False}
_READ = {"type": "done", "dataUsed": [{
    "operation_id": "du_1", "source": "SFDC_OPPORTUNITY", "source_kind": "data_source",
    "turn_id": "turn_1", "columns": ["stage"],
}]}
_FILE_READ = {"type": "done", "dataUsed": [{
    "operation_id": "du_2", "source": "support.csv", "source_kind": "file",
    "turn_id": "turn_1", "columns": ["body"],
}]}
_LEGACY_READ = {"type": "done", "dataUsed": [{
    "operation_id": "du_3", "source": "GONG_CALLS", "turn_id": "turn_1", "columns": ["id"],
}]}
_UNBOUND = "can be queried from the app"
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
    [sheet] = _render_sheet([{**_ON, "draft": {"bindingKinds": [],
                                               "dataReads": ["SFDC_OPPORTUNITY"]}}])

    assert ".sage/bindings.json" not in _optional_rows(sheet)


@node
def test_a_conversation_that_read_no_data_source_draws_no_warning():
    [sheet] = _render_sheet([{**_ON, "draft": {"bindingKinds": [], "dataReads": []}}])

    assert sheet["alerts"] == []
    assert ".sage/bindings.json" not in _optional_rows(sheet)


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


def _into(app: dict | None) -> dict:
    return {**_ON, "appId": app["id"] if app else "",
            "draft": {"bindingKinds": [], "dataReads": ["SFDC_OPPORTUNITY"],
                      "apps": [app] if app else []}}


def _none_crosses(sheet: dict) -> bool:
    return any("No Data Source will carry over" in a for a in sheet["alerts"])


@node
def test_an_app_that_already_binds_a_data_source_draws_no_warning():
    [sheet] = _render_sheet([_into({"id": "app_1", "name": "Desk", "boundDataSource": True})])

    assert not _none_crosses(sheet)


@node
def test_an_app_that_binds_no_data_source_still_warns():
    [sheet] = _render_sheet([_into({"id": "app_1", "name": "Desk", "boundDataSource": False})])

    assert _none_crosses(sheet)


@node
def test_a_new_app_still_warns_beside_an_app_that_binds_one():
    case = _into({"id": "app_1", "name": "Desk", "boundDataSource": True})
    [sheet] = _render_sheet([{**case, "appId": ""}])

    assert _none_crosses(sheet)


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


def test_the_payload_names_only_the_data_sources_read(tmp_path: Path):
    *_, draft = _drafted(tmp_path, [], [_FILE_READ, _READ])

    assert draft["dataReads"] == ["SFDC_OPPORTUNITY"]


def test_a_file_only_conversation_reads_no_data_source(tmp_path: Path):
    *_, draft = _drafted(tmp_path, [], [_FILE_READ])

    assert draft["dataReads"] == []


def test_a_read_recorded_before_source_kind_counts_as_a_data_source(tmp_path: Path):
    *_, draft = _drafted(tmp_path, [], [_LEGACY_READ])

    assert draft["dataReads"] == ["GONG_CALLS"]


def test_the_payload_names_a_chip_that_binds(tmp_path: Path):
    *_, draft = _drafted(tmp_path, [_CHIP], [])

    assert draft["bindingKinds"] == ["data_source"]
    assert draft["dataReads"] == []


def _app_binding(orch, root: Path, binding: dict | None) -> str:
    app_id = orch.create_app()["id"]
    if binding is not None:
        (root / "apps" / app_id / ".sage" / "bindings.json").write_text(json.dumps([binding]))
    return app_id


def test_the_payload_says_which_apps_already_bind_a_data_source(tmp_path: Path):
    orch, root, tid, _draft = _drafted(tmp_path, [], [_READ])
    bound = _app_binding(orch, root, {"kind": "data_source", "id": "ds-1", "name": "trades"})
    dataset = _app_binding(orch, root, {"kind": "dataset", "id": "dset-1", "name": "sales"})
    bare = _app_binding(orch, root, None)

    apps = {a["id"]: a["boundDataSource"] for a in orch.draft_handoff_plan(tid)["apps"]}

    assert {k: apps[k] for k in (bound, dataset, bare)} == {bound: True, dataset: False,
                                                            bare: False}


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


def test_a_handoff_that_read_only_files_is_told_none_of_it(tmp_path: Path):
    agents = _agents_after_confirm(tmp_path, [_FILE_READ])

    assert "Stop calling `runQuery`" not in agents


def _note_after_confirm_into(tmp_path: Path, binding: dict | None) -> str:
    orch, root, tid, _draft = _drafted(tmp_path, [], [_READ])
    app_id = _app_binding(orch, root, binding)
    orch.confirm_handoff(tid, ALL_ON, {"appId": app_id})
    return (root / "apps" / app_id / ".sage" / "handoff.md").read_text()


def test_a_handoff_into_an_app_that_binds_a_data_source_does_not_say_none_crosses(
        tmp_path: Path):
    note = _note_after_confirm_into(tmp_path, {"kind": "data_source", "id": "ds-1",
                                               "name": "trades"})

    assert "SFDC_OPPORTUNITY" in note
    assert not handoff.note_reads_unbound_data(note)


def test_a_handoff_into_an_app_that_binds_no_data_source_still_says_none_crosses(
        tmp_path: Path):
    note = _note_after_confirm_into(tmp_path, {"kind": "dataset", "id": "dset-1", "name": "sales"})

    assert handoff.note_reads_unbound_data(note)


def test_a_handoff_into_a_new_app_still_says_none_crosses(tmp_path: Path):
    orch, root, tid, _draft = _drafted(tmp_path, [], [_READ])
    orch.confirm_handoff(tid, ALL_ON)
    app_id = orch.project(start_preview=False).workspace.app_id

    assert handoff.note_reads_unbound_data(
        (root / "apps" / app_id / ".sage" / "handoff.md").read_text())


def _digest(context: list[dict], read_a_data_source: bool) -> str:
    return handoff.confirm_digest("Background.", artifacts=[], context=context,
                                  include_artifacts=False, include_resources=True,
                                  data_used=["table read from SFDC_OPPORTUNITY."],
                                  read_a_data_source=read_a_data_source)


def test_the_note_marks_data_used_as_background_when_nothing_binds():
    digest = _digest([], True)

    assert "table read from SFDC_OPPORTUNITY." in digest
    assert _UNBOUND in digest
    assert handoff.note_reads_unbound_data(digest)


def test_the_note_does_not_mark_data_used_when_a_data_source_binds():
    digest = _digest([_CHIP], True)

    assert "table read from SFDC_OPPORTUNITY." in digest
    assert _UNBOUND not in digest


def test_the_note_does_not_mark_data_used_when_only_files_were_read():
    digest = _digest([], False)

    assert "table read from SFDC_OPPORTUNITY." in digest
    assert not handoff.note_reads_unbound_data(digest)


# ---- each read says what kind of source it read ------------------------------------------------


def test_a_csv_calculation_records_a_file_read(tmp_path: Path):
    turn, data, _ = calc_turn(tmp_path)
    run.perform("live_read_files", calc_args(), turn)

    assert data.events("turn1")[0]["source_kind"] == "file"


def test_a_dataset_file_calculation_records_a_file_read(tmp_path: Path):
    root = tmp_path / "mounts" / "sales"
    root.mkdir(parents=True)
    (root / "sales.csv").write_text("region,revenue\nNorth,1\n")
    turn, data, _ = calc_turn(tmp_path, bound={"dataset": ("sales",)},
                              dataset_root=lambda name: root if name == "sales" else None,
                              upload_for=lambda _p: None)
    run.perform("live_read_files", calc_args(dataset="sales", path="sales.csv"), turn)

    assert data.events("turn1")[0]["source_kind"] == "file"


def test_a_bound_table_calculation_records_a_data_source_read(tmp_path: Path):
    from .test_csv_calculation_data_used import SALES, Rows
    source = object()
    turn, data, _ = calc_turn(
        tmp_path, bound={"datasource": ("Snowflake-Data-Warehouse",)},
        source_for=lambda name: source if name == "Snowflake-Data-Warehouse" else None,
        sample_rows=lambda s, db, sc, t, lim: Rows(
            ["region", "revenue", "email"],
            [line.split(",") for line in SALES.splitlines()[1:]][:lim]),
        upload_for=lambda _p: None)
    run.perform("live_read_table", table_args(), turn)

    assert data.events("turn1")[0]["source_kind"] == "data_source"


def test_a_csv_text_analysis_records_a_file_read(tmp_path: Path):
    turn, data, _journal, _source = text_turn(tmp_path)
    run.perform("live_read_files", analysis_args(), turn)

    assert data.events("turn1")[0]["source_kind"] == "file"


def test_an_attachment_reference_records_a_file_read(tmp_path: Path):
    (tmp_path / "notes.md").write_text("# Notes\nBuild it")
    prepared = reference.prepare(reference.authorize(tmp_path, [{"path": "notes.md"}], "notes.md"))
    event, _reply = reference.data_use(prepared, purpose="Use an attached file")

    assert event["source_kind"] == "file"


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
