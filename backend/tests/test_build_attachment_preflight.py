"""Selected inputs are usable before the first Build dispatch (#513)."""

import pytest

from sage.assets.provider import FakeAssetProvider

from .ledger import last_turn, needs_ledger
from .test_a_dropped_mention_reaches_the_agents_prompt import _orch


@pytest.fixture
def build(tmp_path, monkeypatch):
    import time

    from sage.orchestrator.service import Orchestrator

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    orch, oc = _orch(tmp_path)
    orch._assets = FakeAssetProvider()
    project = orch.project(start_preview=False)
    paths = [orch.upload_file(name, body)["path"] for name, body in (
        ("ADSL.csv", b"SUBJID,ARM\nprivate-subject,active\n"),
        ("ADAE.csv", b"SUBJID,AE\nprivate-subject,headache\n"),
        ("shell.txt", b"Table 14: Enrollment by arm\n"))]
    return orch, oc, project, paths


def test_first_build_repairs_selected_inputs_before_dispatch(build, monkeypatch):
    orch, oc, project, paths = build
    root = project.workspace.path
    (root / paths[0]).unlink()
    (root / paths[1]).unlink()
    (root / paths[1]).symlink_to(root / "absent")
    send = oc.send_prompt

    def checked(*args, **kwargs):
        assert all((root / path).is_file() for path in paths)
        assert {a["path"] for a in kwargs["attachments"]} == set(paths)
        assert "private-subject,active" not in str(kwargs["attachments"])
        assert "Sample rows" not in str(kwargs["attachments"])
        return send(*args, **kwargs)

    monkeypatch.setattr(oc, "send_prompt", checked)
    events = list(orch.build_stream("Build the enrollment table from ADSL, ADAE and shell"))
    assert oc.prompts
    assert not [e for e in events if e["type"] == "mentions-unresolved"]


def test_missing_selected_source_stops_before_model_dispatch(build):
    orch, oc, project, paths = build
    (project.workspace.path / paths[0]).resolve().unlink()
    events = list(orch.build_stream("Build the enrollment table"))
    assert not oc.prompts
    assert any(e["type"] == "mentions-unresolved" and "ADSL.csv" in e["message"]
               for e in events)
    assert events[-1]["type"] == "done" and events[-1]["ok"] is False


def test_explicit_selection_does_not_include_other_app_inputs(build):
    orch, oc, project, paths = build
    (project.workspace.path / paths[1]).resolve().unlink()
    list(orch.build_stream("Build the enrollment table", [paths[0]]))
    assert [a["path"] for a in oc.prompts[0]["attachments"]] == [paths[0]]


def test_intact_inputs_do_not_restore_or_download(build, monkeypatch):
    orch, oc, _, paths = build
    from sage.orchestrator import attachment_repair

    monkeypatch.setattr(attachment_repair, "repair", lambda *a, **k: pytest.fail("intact file repaired"))
    list(orch.build_stream("Build the enrollment table"))
    assert {a["path"] for a in oc.prompts[0]["attachments"]} == set(paths)


def test_removed_input_is_not_restored_or_reselected(build):
    orch, oc, project, paths = build
    orch.delete_file(paths[1])
    list(orch.build_stream("Build the enrollment table"))
    assert {a["path"] for a in oc.prompts[0]["attachments"]} == {paths[0], paths[2]}
    assert not (project.workspace.path / paths[1]).exists()


def test_build_without_selected_inputs_still_dispatches(tmp_path):
    orch, oc = _orch(tmp_path)
    list(orch.build_stream("Build an enrollment table"))
    assert oc.prompts[0]["attachments"] is None


def test_first_build_after_restart_restores_metadata_inputs(build, tmp_path):
    from sage.orchestrator.service import Orchestrator

    orch, oc, project, paths = build
    for path in paths:
        (project.workspace.path / path).unlink()
    restarted = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code", template=tmp_path / "template",
        gateway=orch._gateway, catalog=project.shim.catalog, project_id="Sage",
        assets=orch._assets, feedback=orch._feedback, opencode_client=oc)
    list(restarted.build_stream("Build the enrollment table"))
    assert {a["path"] for a in oc.prompts[0]["attachments"]} == set(paths)
    assert all((project.workspace.path / path).is_file() for path in paths)


def test_repair_timeout_cannot_publish_a_late_download(tmp_path):
    import threading
    from types import SimpleNamespace

    from sage.orchestrator import attachment_repair
    from sage.resources.provider import ResourceUnavailable

    release = threading.Event()
    finished = threading.Event()
    captured = []

    class Assets:
        def download_file(self, asset, rel, dest):
            captured.append(dest)
            try:
                release.wait(5)
                dest.write_text("late private bytes")
            finally:
                finished.set()

    dest = tmp_path / "absent.csv"
    try:
        with pytest.raises(ResourceUnavailable, match="timed out"):
            attachment_repair.repair({"file": "sample.csv"}, dest,
                                     lambda _: SimpleNamespace(mount_path=None), Assets(), .01)
        assert not dest.exists()
    finally:
        release.set()
    assert finished.wait(2)
    # Join the worker so the callback that removes its private stage has also run.
    for worker in threading.enumerate():
        if worker.name == "sage-attachment-repair":
            worker.join(2)
    assert captured and not captured[0].parent.exists()
    assert not dest.exists()


def test_download_failure_is_not_retried_during_cleanup(build, monkeypatch):
    from sage.orchestrator import attachment_repair
    from sage.resources.provider import ResourceUnavailable

    orch, oc, project, paths = build
    (project.workspace.path / paths[0]).unlink()
    attempted = []

    def failed(entry, *args):
        attempted.append(entry["path"])
        raise ResourceUnavailable("download timed out")

    monkeypatch.setattr(attachment_repair, "repair", failed)
    list(orch.build_stream("Build enrollment"))
    assert attempted == [paths[0]] and not oc.prompts


def test_remote_repair_publishes_only_a_complete_download(tmp_path):
    from types import SimpleNamespace

    from sage.orchestrator import attachment_repair

    class Assets:
        def download_file(self, asset, rel, dest):
            dest.write_bytes(b"subject,arm\n1,active\n")

    dest = tmp_path / "data" / "restored.csv"
    attachment_repair.repair({"file": "sample.csv"}, dest,
                             lambda _: SimpleNamespace(mount_path=None), Assets(), 1)
    assert dest.read_bytes() == b"subject,arm\n1,active\n"
    assert list(dest.parent.iterdir()) == [dest]


@needs_ledger
def test_preflight_diagnostics_report_counts_and_absence(build, caplog):
    import logging

    orch, _, project, paths = build
    (project.workspace.path / paths[0]).unlink()
    with caplog.at_level(logging.INFO, logger="sage.orchestrator"):
        list(orch.build_stream("Build enrollment"))
    assert "requested=0 eligible=3 resolved=3 unavailable=0" in caplog.text
    assert "turn deleted" not in caplog.text
    record = last_turn()
    assert record.counters["attachments.eligible"] == 3
    assert record.counters["attachments.restored.absent"] == 1
    assert record.observations["attachments.resolution_ms"][0] >= 0


def test_folder_mention_repairs_members_and_keeps_one_descriptor(build):
    orch, oc, project, paths = build
    (project.workspace.path / paths[0]).unlink()
    folder = paths[0].rsplit("/", 1)[0]
    list(orch.build_stream("Build enrollment", [folder]))
    assert [a["path"] for a in oc.prompts[0]["attachments"]] == [folder]
    assert (project.workspace.path / paths[0]).is_file()


def test_retry_after_repair_failure_rechecks_the_source(build, monkeypatch):
    from sage.orchestrator import attachment_repair
    from sage.resources.provider import ResourceUnavailable

    orch, oc, project, paths = build
    (project.workspace.path / paths[0]).unlink()
    repair = attachment_repair.repair

    def failed(*args):
        raise ResourceUnavailable("temporarily unavailable")

    monkeypatch.setattr(attachment_repair, "repair", failed)
    list(orch.build_stream("Build enrollment"))
    assert not oc.prompts
    monkeypatch.setattr(attachment_repair, "repair", repair)
    list(orch.build_stream("Build enrollment"))
    assert oc.prompts and (project.workspace.path / paths[0]).is_file()


@pytest.mark.parametrize("phased", [False, True])
def test_approval_with_missing_inputs_keeps_plan_and_stops(build, phased):
    from .test_phased_build import PHASED_PLAN

    orch, oc, project, paths = build
    project.workspace.write_plan(PHASED_PLAN)
    project.record.write_settings({"phased_build": phased})
    (project.workspace.path / paths[0]).resolve().unlink()
    events = list(orch.approve_stream())
    endings = [e for e in events if e["type"] == "done"]
    assert len(endings) == 1 and endings[0]["ok"] is False
    assert not oc.prompts
    assert project.workspace.read_plan()
    assert not [e for e in events if e.get("type") == "phase" and e.get("n", 0) > 1]
