"""A Dataset file pick supplies the selected document to the continued Chat question."""

from pathlib import Path

from sage.driver.opencode import with_attachment_listing
from sage.liveread import run
from sage.liveread.held import HeldRead

from .fake_opencode import Turn
from .test_chat_turn import _orch


def _picked(tmp_path):
    orch, oc = _orch(tmp_path, [Turn(text="The selected card was used.")])
    asset = orch._find_asset("ds_sales_2026")
    source = Path(asset.mount_path) / "battlecards.md"
    source.write_text("# Example competitor\nBATTLECARD EVIDENCE: compare the full platform.\n")
    (source.parent / "neighbor.md").write_text("UNSELECTED NEIGHBOR")
    tid = orch.create_thread()["id"]
    picked = orch.confirm_thread_dataset_file(tid, asset.id, source.name)
    return orch, oc, tid, picked, source


def test_dataset_file_pick_reaches_the_replayed_chat_prompt(tmp_path):
    orch, oc, tid, picked, _source = _picked(tmp_path)
    try:
        list(orch.chat_stream(tid, "Use the battlecards in @sales_2026 to compare competitors."))
        sent = with_attachment_listing(oc.prompts[0]["text"], oc.prompts[0]["attachments"])
        assert "BATTLECARD EVIDENCE" in sent
        assert "UNSELECTED NEIGHBOR" not in sent
        assert "reference material" in sent
        operations = [event for row in orch.thread_history(tid)
                      for event in row.get("dataUsed", [])
                      if event.get("operation") == "document_reference"]
        assert operations and operations[0]["source"] == picked["path"]
        assert "BATTLECARD EVIDENCE" not in str(operations)
    finally:
        orch.shutdown()


def test_document_tool_reads_the_exact_picked_mounted_file(tmp_path):
    orch, _oc, tid, picked, _source = _picked(tmp_path)
    project = orch.project(start_preview=False)
    pin = project.control.arm_chat(tid)
    try:
        turn = orch._live_read_turn_for(tid, include_app_bindings=False)
        reply = run._document({"dataset": "upload", "path": picked["path"]}, turn)
        assert "BATTLECARD EVIDENCE" in reply
        assert turn.reference_for(str(Path(picked["path"]).with_name("neighbor.md"))) is None
    finally:
        project.control.disarm_chat(pin)
        orch.shutdown()


def test_retargeted_dataset_link_does_not_transfer_another_file(tmp_path):
    orch, _oc, tid, picked, source = _picked(tmp_path)
    project = orch.project(start_preview=False)
    path = project.record.path / picked["path"]
    path.unlink()
    path.symlink_to(source.with_name("neighbor.md"))
    pin = project.control.arm_chat(tid)
    try:
        turn = orch._live_read_turn_for(tid, include_app_bindings=False)
        assert turn.reference_for(picked["path"]) is None
    finally:
        project.control.disarm_chat(pin)
        orch.shutdown()


def test_selected_document_numbers_survive_a_turn_that_also_read_a_table(tmp_path):
    orch, _oc, tid, _picked_file, source = _picked(tmp_path)
    source.write_text("# Example competitor\nThe card reports a 37% improvement.\n")
    try:
        list(orch.chat_stream(tid, "Use the battlecards in @sales_2026 to compare competitors."))
        orch._hold_read(tid, HeldRead("Calls", "calls", ["Competitor", "Calls"],
                                      [["Example", 81]], [["Example", 81]]))
        assert orch._unbacked_numbers(tid, "The card reports a 37% improvement.", "") == []
        assert orch._unbacked_numbers(tid, "The card reports a 92% improvement.", "") == ["92%"]
        kept = orch._numbers_left_out(tid, "The card reports a 37% improvement.\n"
                                      "The table reports 999 calls.", "")
        assert "37%" in kept and "999" not in kept
    finally:
        orch.shutdown()


def test_long_selected_document_names_sections_beyond_its_excerpt(tmp_path):
    orch, oc, tid, picked, source = _picked(tmp_path)
    source.write_text("# First competitor\n" + "Evidence. " * 950
                      + "\n# Last competitor\nLATE BATTLECARD EVIDENCE\n")
    project = orch.project(start_preview=False)
    try:
        list(orch.chat_stream(tid, "Use the battlecards in @sales_2026 to compare competitors."))
        sent = with_attachment_listing(oc.prompts[0]["text"], oc.prompts[0]["attachments"])
        assert "LATE BATTLECARD EVIDENCE" not in sent
        assert "Last competitor" in sent
        pin = project.control.arm_chat(tid)
        try:
            turn = orch._live_read_turn_for(tid, include_app_bindings=False)
            reply = run._document({"dataset": "upload", "path": picked["path"],
                                   "heading": "Last competitor"}, turn)
            assert "LATE BATTLECARD EVIDENCE" in reply
        finally:
            project.control.disarm_chat(pin)
    finally:
        orch.shutdown()
