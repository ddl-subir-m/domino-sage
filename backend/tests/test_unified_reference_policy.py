"""Documents, tables, and images share one explicit-reference policy (#519)."""

from __future__ import annotations

import base64
import copy
import json
from pathlib import Path

import pytest

from sage.assets.provider import FakeAssetProvider
from sage.driver.opencode import with_attachment_listing
from sage.liveread import reference
from sage.liveread.data_use import DataUse
from sage.orchestrator.service import Orchestrator
from sage.router.model_control import ModelControl
from sage.router.models import VISION_CAPABLE, Mode, Phase

from .fake_opencode import Turn
from .test_document_reference_preparation import _docx, _orch, _pdf
from .test_enforcement_shim import _shim, _vision_shim
from .test_plan_reference_persistence import PLAN, _orchestrator

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _outgoing(prompt: dict) -> str:
    return with_attachment_listing(prompt["text"], prompt["attachments"])


def test_opencode_declares_the_same_vision_models_as_the_router():
    config = json.loads((Path(__file__).resolve().parents[2] / "opencode.json").read_text())
    models = config["provider"]["sage-gateway"]["models"]
    declared = {
        name for name, row in models.items()
        if row.get("attachment") is True
        and row.get("modalities", {}).get("input") == ["text", "image"]
    }

    assert declared == VISION_CAPABLE


def test_one_mixed_prompt_uses_typed_carriers_and_excludes_the_neighbor(tmp_path: Path, caplog):
    orch, oc = _orch(tmp_path)
    markdown = orch.upload_file("requirements.md", b"# Rules\nMARKDOWN RULE\n")["path"]
    docx_file = _docx(tmp_path, "<w:p><w:r><w:t>DOCX RULE</w:t></w:r></w:p>")
    docx = orch.upload_file("requirements.docx", docx_file.read_bytes())["path"]
    pdf_file = _pdf(tmp_path, ["PDF RULE"])
    pdf = orch.upload_file("requirements.pdf", pdf_file.read_bytes())["path"]
    table = orch.upload_file(
        "shape.csv", b"subject,arm,score\n01,A,3\n02,B,4\n03,C,5\n04,D,6\n05,E,7\n"
        b"06,F,8\n07,G,9\n08,H,10\n09,I,11\n10,J,12\n11,K,13\n12,L,14\n13,M,15\n",
    )["path"]
    image = orch.upload_file("design.png", PNG)["path"]
    neighbor = orch.upload_file("private.csv", b"id,value\n1,PRIVATE NEIGHBOR SENTINEL\n")["path"]

    events = list(orch.build_stream(
        "Follow every explicitly attached reference", [markdown, docx, pdf, table, image]
    ))
    first = oc.prompts[0]
    outgoing = _outgoing(first)

    assert "MARKDOWN RULE" in outgoing
    assert "DOCX RULE" in outgoing
    assert "PDF RULE" in outgoing
    assert "BEGIN PREPARED TABLE STRUCTURE" in outgoing
    assert "subject: digits" in outgoing and "arm: string" in outgoing
    assert "01,A,3" not in outgoing and "13,M,15" not in outgoing
    assert "PRIVATE NEIGHBOR SENTINEL" not in outgoing
    assert neighbor not in outgoing
    assert next(item for item in first["attachments"] if item["path"] == image)["image_uri"].startswith(
        "data:image/png;base64,"
    )
    operations = {
        event["operation_id"]: event
        for row in events
        for event in row.get("dataUsed", [])
    }
    assert [event["operation"] for event in operations.values()] == [
        "document_reference", "document_reference", "document_reference",
        "table_reference", "image_reference",
    ]
    serialized = json.dumps(list(operations.values()))
    assert "PRIVATE NEIGHBOR SENTINEL" not in serialized
    assert "MARKDOWN RULE" not in serialized and "DOCX RULE" not in serialized
    assert "PDF RULE" not in serialized
    plan_text = orch.project(start_preview=False).app_for_turn().read_plan() or ""
    assert "PRIVATE NEIGHBOR SENTINEL" not in plan_text
    assert "PRIVATE NEIGHBOR SENTINEL" not in caplog.text
    diagnostics = orch.project(start_preview=False).app_for_turn().path / ".sage/build-diagnostics.json"
    if diagnostics.exists():
        assert "PRIVATE NEIGHBOR SENTINEL" not in diagnostics.read_text()
    assert not [part for part in oc.messages(first["session"])[0]["content"]
                if isinstance(part, dict) and part.get("type") == "tool"]


def _recorded_image_event(orch: Orchestrator) -> dict:
    events = [event for event, _reply, _persist
              in orch.project(start_preview=False).shim.data_use.operations.values()
              if event.get("operation") == "image_reference"]
    assert len(events) == 1
    return events[0]


def _record_image(shim, tmp_path: Path, *, turn_id: str = "turn-image") -> tuple[list[dict], str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "design.png"
    source.write_bytes(PNG)
    authorized = reference.authorize(tmp_path, [{"path": source.name}], source.name)
    assert authorized is not None
    prepared = reference.prepare(authorized, descriptor={
        "reference_kind": "image", "image_uri": "data:image/png;base64,AAAA",
    })
    assert prepared is not None
    event, reply = reference.data_use(prepared, purpose="Use the image")
    journal: list[dict] = []
    shim.data_use.record(event, reply, journal.append, turn_id)
    return journal, event["operation_id"], reply["selected"]


_IMAGE_MATRIX = [
    {"id": "capable-0-0", "kind": "shim", "capable": True, "markers": 0,
     "carriers": 0, "history": "none", "upstream": "complete"},
    {"id": "capable-1-1-delta", "kind": "shim", "capable": True, "markers": 1,
     "carriers": 1, "history": "none", "upstream": "delta"},
    {"id": "capable-2-2", "kind": "shim", "capable": True, "markers": 2,
     "carriers": 2, "history": "none", "upstream": "complete"},
    {"id": "mismatch-1-0", "kind": "shim", "capable": True, "markers": 1,
     "carriers": 0, "history": "none", "upstream": "complete"},
    {"id": "mismatch-2-1", "kind": "shim", "capable": True, "markers": 2,
     "carriers": 1, "history": "none", "upstream": "complete"},
    {"id": "mismatch-1-2", "kind": "shim", "capable": True, "markers": 1,
     "carriers": 2, "history": "none", "upstream": "complete"},
    {"id": "nonvision", "kind": "shim", "capable": False, "markers": 1,
     "carriers": 1, "history": "none", "upstream": "complete"},
    {"id": "sync-route-raise", "kind": "shim", "capable": True, "markers": 1,
     "carriers": 1, "history": "none", "upstream": "sync"},
    {"id": "lazy-next-raise", "kind": "shim", "capable": True, "markers": 1,
     "carriers": 1, "history": "none", "upstream": "lazy"},
    {"id": "empty-iterator", "kind": "shim", "capable": True, "markers": 1,
     "carriers": 1, "history": "none", "upstream": "empty"},
    {"id": "sse-error", "kind": "shim", "capable": True, "markers": 1,
     "carriers": 1, "history": "none", "upstream": "error"},
    {"id": "response-failed", "kind": "shim", "capable": True, "markers": 1,
     "carriers": 1, "history": "none", "upstream": "failed"},
    {"id": "response-incomplete", "kind": "shim", "capable": True, "markers": 1,
     "carriers": 1, "history": "none", "upstream": "incomplete"},
    {"id": "old-pending-history", "kind": "shim", "capable": True, "markers": 1,
     "carriers": 1, "history": "pending", "upstream": "complete"},
    {"id": "old-resolved-history", "kind": "shim", "capable": True, "markers": 1,
     "carriers": 1, "history": "resolved", "upstream": "complete"},
    {"id": "close-after-delta", "kind": "shim", "capable": True, "markers": 1,
     "carriers": 1, "history": "none", "upstream": "delta-close"},
    {"id": "close-after-error", "kind": "shim", "capable": True, "markers": 1,
     "carriers": 1, "history": "none", "upstream": "error-close"},
    {"id": "close-after-setup", "kind": "shim", "capable": True, "markers": 1,
     "carriers": 1, "history": "none", "upstream": "setup-close"},
    {"id": "close-before-first-pull", "kind": "shim", "capable": True, "markers": 1,
     "carriers": 1, "history": "none", "upstream": "unstarted-close"},
    {"id": "opencode-send-raise", "kind": "orchestrator", "exit": "send"},
    {"id": "stop-before-send", "kind": "orchestrator", "exit": "stop"},
    {"id": "terminal-done", "kind": "orchestrator", "exit": "done"},
    {"id": "restore-pending", "kind": "restore", "delivery": "pending", "failure": None},
    {"id": "restore-sent", "kind": "restore", "delivery": "sent", "failure": None},
    {"id": "restore-capability", "kind": "restore", "delivery": "not_sent",
     "failure": "capability"},
]


@pytest.mark.parametrize("case", _IMAGE_MATRIX, ids=[case["id"] for case in _IMAGE_MATRIX])
def test_image_delivery_state_matrix(tmp_path: Path, case: dict):
    if case["kind"] == "restore":
        event = {
            "operation_id": "image", "turn_id": "old", "operation": "image_reference",
            "source": "design.png", "delivery": case["delivery"], "failure": case["failure"],
            "coverage": {}, "requests": [],
        }
        writes: list[dict] = []
        data_use = DataUse()
        data_use.restore([{"dataUsed": [event]}], writes.append)
        restored = data_use.operations["image"][0]
        expected = ("not_sent", "no_request") if case["delivery"] == "pending" else (
            case["delivery"], case["failure"]
        )
        assert (restored["delivery"], restored["failure"]) == expected
        assert restored["requests"] == []
        assert len(writes) == (1 if case["delivery"] == "pending" else 0)
        return

    if case["kind"] == "orchestrator":
        orch, oc = _orch(tmp_path)
        image = orch.upload_file("design.png", PNG)["path"]
        data_use = orch.project(start_preview=False).shim.data_use
        writes: dict[str, int] = {}
        original_record = data_use.record

        def counting_record(event, reply, persist, turn_id):
            oid = event["operation_id"]

            def counted(row):
                writes[oid] = writes.get(oid, 0) + 1
                persist(row)

            original_record(event, reply, counted, turn_id)

        data_use.record = counting_record
        if case["exit"] == "send":
            oc.send_prompt = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("OpenCode send failed")
            )
            with pytest.raises(OSError, match="OpenCode send failed"):
                list(orch.build_stream("Match this image", [image]))
        else:
            if case["exit"] == "stop":
                orch.project(start_preview=False).stop_requested = True
            list(orch.build_stream("Match this image", [image]))
        image_event = _recorded_image_event(orch)
        assert (image_event["delivery"], image_event["failure"]) == ("not_sent", "no_request")
        assert image_event["requests"] == []
        assert writes == {image_event["operation_id"]: 2}
        assert not oc.prompts if case["exit"] == "stop" else True
        return

    seen: list[dict] = []

    class MatrixGateway:
        def route(self, request, _labels):
            seen.append(copy.deepcopy(request))
            upstream = case["upstream"]
            if upstream == "sync":
                raise OSError("sync route failure")
            if upstream == "lazy":
                def lazy_failure():
                    raise OSError("lazy next failure")
                    yield b"unreachable"
                return lazy_failure()
            frames = {
                "empty": [],
                "error": [{"error": {"type": "provider_error", "message": "failed"}}],
                "error-close": [{"error": {"type": "provider_error", "message": "failed"}}],
                "failed": [{"type": "response.failed"}],
                "incomplete": [{"type": "response.incomplete"}],
                "delta": [{"choices": [{"delta": {"content": "ok"}}]}],
                "delta-close": [{"choices": [{"delta": {"content": "ok"}}]}],
                "setup-close": [{"type": "response.created", "response": {"id": "r1"}}],
                "unstarted-close": [{"choices": [{"delta": {"content": "unread"}}]}],
                "complete": [{"choices": [{"finish_reason": "stop"}]}],
            }[upstream]
            return iter([("data: " + json.dumps(frame) + "\n\n").encode()
                         for frame in frames])

    gateway = MatrixGateway()
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    shim = _vision_shim(control, gateway) if case["capable"] else _shim(control, gateway)
    history: list[dict] = []
    old_event = None
    old_journal = None
    if case["history"] != "none":
        old_journal, old_operation, old_marker = _record_image(
            shim, tmp_path / "old", turn_id="old-turn"
        )
        if case["history"] == "resolved":
            shim.data_use.confirm_image_delivery((old_operation,), "sonnet")
        old_event = shim.data_use.operations[old_operation][0]
        history = [{"role": "user", "content": [
            {"type": "text", "text": old_marker},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,OLD"}},
        ]}, {"role": "assistant", "content": "old response"}]
    old_writes = len(old_journal or [])

    current = []
    current_journals: dict[str, list[dict]] = {}
    markers = []
    for index in range(case["markers"]):
        journal, operation, marker = _record_image(
            shim, tmp_path / f"current-{index}", turn_id="current-turn"
        )
        current.append(operation)
        current_journals[operation] = journal
        markers.append(marker)
    messages = [*history, {"role": "user", "content": [
        {"type": "text", "text": "\n".join(markers) or "no image reference"},
        *[{"type": "image_url", "image_url": {
            "url": f"data:image/png;base64,CURRENT{index}"}}
          for index in range(case["carriers"])],
    ]}]

    if case["upstream"] == "sync":
        with pytest.raises(OSError, match="sync route failure"):
            shim.handle({"messages": messages}, project="p")
    elif case["upstream"] == "lazy":
        with pytest.raises(OSError, match="lazy next failure"):
            list(shim.handle({"messages": messages}, project="p"))
    elif case["upstream"] == "unstarted-close":
        stream = shim.handle({"messages": messages}, project="p")
        stream.close()
    elif case["upstream"].endswith("-close"):
        stream = shim.handle({"messages": messages}, project="p")
        next(stream)
        stream.close()
    else:
        list(shim.handle({"messages": messages}, project="p"))

    history_carriers = 1 if case["history"] != "none" and case["capable"] else 0
    current_carriers = (case["carriers"] if case["capable"]
                        and case["markers"] == case["carriers"] else 0)
    assert sum(1 for message in seen[0]["messages"]
               for part in message.get("content", [])
               if isinstance(part, dict) and part.get("type") == "image_url") == (
                   history_carriers + current_carriers
               )
    if old_event is not None:
        expected_old = ("sent", None) if case["history"] == "resolved" else ("pending", None)
        assert (old_event["delivery"], old_event["failure"]) == expected_old
        assert old_event["requests"] == []
        assert len(old_journal) == old_writes

    if not current:
        return
    if not case["capable"]:
        expected = ("not_sent", "capability")
    elif case["markers"] != case["carriers"]:
        expected = ("not_sent", "carrier")
    elif case["upstream"] in ("sync", "lazy"):
        expected = ("not_sent", "gateway")
    elif case["upstream"] in ("empty", "setup-close", "unstarted-close"):
        expected = ("not_sent", "no_response")
    elif case["upstream"] in ("error", "error-close"):
        expected = ("not_sent", "provider")
    elif case["upstream"] in ("failed", "incomplete"):
        expected = ("not_sent", "incomplete")
    else:
        expected = ("sent", None)
    no_observation = case["upstream"] in ("sync", "unstarted-close")
    evidence_count = 0 if no_observation else 1
    write_delta = 1 if no_observation else 3
    for operation in current:
        event = shim.data_use.operations[operation][0]
        assert (event["delivery"], event["failure"]) == expected
        assert len(event["requests"]) == evidence_count
        assert len(current_journals[operation]) == 1 + write_delta


def test_table_and_image_plan_records_reauthorize_and_malformed_records_fail_closed(tmp_path: Path):
    table = tmp_path / "shape.csv"
    table.write_text("subject,arm\n01,A\n02,B\n")
    image = tmp_path / "design.png"
    image.write_bytes(PNG)
    manifest = [{"path": table.name}, {"path": image.name}]
    descriptors = {
        table.name: {"reference_kind": "tabular", "detail": (
            "2 columns, 2 data rows, delimiter ','.\nColumns:\n  subject: digits\n  arm: string"
        )},
        image.name: {"reference_kind": "image", "image_uri": "data:image/png;base64,AAAA"},
    }
    prepared = reference.prepare_explicit(
        tmp_path, manifest, [table.name, image.name], prompt="use both", descriptors=descriptors
    )
    saved = [reference.plan_record(item) for item in prepared]

    replayed = reference.prepare_plan_records(
        tmp_path, manifest, saved, descriptors=descriptors
    )
    assert [item.source_type for item in replayed] == ["table", "image"]
    assert replayed[0].selected_fields == ("subject", "arm")
    assert replayed[1].delivery == "pending"

    malformed = [{**saved[0], "sha256": ""}, {**saved[1], "handler": "table"}]
    refused = reference.prepare_plan_records(
        tmp_path, manifest, malformed, descriptors=descriptors
    )
    assert [item.status for item in refused] == ["saved_record_invalid", "saved_record_invalid"]
    assert all(item.delivery != "sent" for item in refused)


def test_data_use_restore_accepts_document_table_and_image_shapes():
    events = [
        {"operation_id": "doc", "turn_id": "turn", "operation": "document_reference",
         "source": "requirements.md", "coverage": {"sent_characters": 20}, "requests": []},
        {"operation_id": "table", "turn_id": "turn", "operation": "table_reference",
         "source": "shape.csv", "selected_fields": ["subject", "arm"],
         "coverage": {"total": 4, "processed": 4}, "requests": []},
        {"operation_id": "image", "turn_id": "turn", "operation": "image_reference",
         "source": "design.png", "delivery": "not_sent", "failure": "capability",
         "coverage": {}, "requests": []},
        {"operation_id": "pending-image", "turn_id": "old-turn",
         "operation": "image_reference", "source": "old-design.png",
         "delivery": "pending", "failure": None, "coverage": {}, "requests": []},
    ]
    data_use = DataUse()
    corrected: list[dict] = []

    data_use.restore([{"dataUsed": events}], corrected.append)

    assert [event["operation"] for event in data_use.events("turn")] == [
        "document_reference", "table_reference", "image_reference",
    ]
    assert data_use.operations["table"][1]["selected_fields"] == []
    assert "result_rows" not in data_use.operations["image"][1]
    restored = data_use.operations["pending-image"][0]
    assert restored["delivery"] == "not_sent" and restored["failure"] == "no_request"
    assert corrected == [{"type": "data_used", "dataUsed": [restored]}]


def test_approval_restart_reprepares_the_exact_table_and_image_references(tmp_path: Path):
    assets = FakeAssetProvider()
    table_source = assets.root / "sales_2026" / "shape.csv"
    table_source.write_text("subject,arm\n01,A\n02,B\n03,C\n")
    image_source = assets.root / "sales_2026" / "design.png"
    image_source.write_bytes(PNG)
    neighbor_source = assets.root / "sales_2026" / "private.csv"
    neighbor_source.write_text("id,value\n1,PRIVATE RESTART NEIGHBOR\n")
    first, _planner = _orchestrator(tmp_path, assets, [Turn(text=PLAN)])
    table = first.attach_file("ds_sales_2026", "shape.csv")["path"]
    image = first.attach_file("ds_sales_2026", "design.png")["path"]
    neighbor = first.attach_file("ds_sales_2026", "private.csv")["path"]
    events = list(first.build_stream("Use the attached table and image", [table, image]))
    plan_id = next(event["planId"] for event in events if event["type"] == "plan-proposed")
    saved = first.project(start_preview=False).record.read_plan_doc(plan_id)
    assert saved is not None
    assert [row["handler"] for row in saved["explicitReferences"]] == ["table", "image"]
    assert "PRIVATE RESTART NEIGHBOR" not in json.dumps(saved)

    restarted, builder = _orchestrator(
        tmp_path, assets, [Turn(writes={"src/App.tsx": "export default () => null\n"})]
    )
    approved = list(restarted.approve_stream(plan_id=plan_id))
    outgoing = _outgoing(builder.prompts[0])

    assert "BEGIN PREPARED TABLE STRUCTURE" in outgoing
    assert next(item for item in builder.prompts[0]["attachments"]
                if item["path"] == image)["image_uri"].startswith("data:image/png;base64,")
    assert neighbor not in outgoing and "PRIVATE RESTART NEIGHBOR" not in outgoing
    operations = {
        event["operation_id"]: event
        for row in approved
        for event in row.get("dataUsed", [])
    }
    assert [event["operation"] for event in operations.values()] == [
        "table_reference", "image_reference",
    ]


def test_folder_reference_never_expands_its_children(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    assets = FakeAssetProvider()
    orch._assets = assets
    nested = assets.root / "sales_2026" / "folder"
    nested.mkdir()
    (nested / "one.csv").write_text("id,value\n1,FIRST PRIVATE ROW\n")
    (nested / "two.csv").write_text("id,value\n2,SECOND PRIVATE ROW\n")
    first = orch.attach_file("ds_sales_2026", "folder/one.csv")["path"]
    second = orch.attach_file("ds_sales_2026", "folder/two.csv")["path"]
    folder = first.rsplit("/", 1)[0]

    events = list(orch.build_stream("Use the attached folder", [folder]))
    outgoing = _outgoing(oc.prompts[0])

    assert first not in outgoing and second not in outgoing
    assert "FIRST PRIVATE ROW" not in outgoing and "SECOND PRIVATE ROW" not in outgoing
    assert not [event for row in events for event in row.get("dataUsed", [])
                if event.get("operation", "").endswith("_reference")]
