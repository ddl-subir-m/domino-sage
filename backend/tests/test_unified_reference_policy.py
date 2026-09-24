"""Documents, tables, and images share one explicit-reference policy (#519)."""

from __future__ import annotations

import base64
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
from .test_enforcement_shim import FakeGatewayClient, _shim, _vision_shim
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


def test_stop_before_send_closes_the_image_without_changing_stop_semantics(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    image = orch.upload_file("design.png", PNG)["path"]
    orch.project(start_preview=False).stop_requested = True

    events = list(orch.build_stream("Match this image", [image]))

    assert any(event["type"] == "stopped" for event in events)
    assert not oc.prompts
    image_event = _recorded_image_event(orch)
    assert image_event["delivery"] == "not_sent"
    assert image_event["failure"] == "no_request"


def test_synchronous_opencode_send_failure_closes_the_image(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    image = orch.upload_file("design.png", PNG)["path"]

    def fail_send(*_args, **_kwargs):
        raise OSError("OpenCode send failed")

    oc.send_prompt = fail_send

    with pytest.raises(OSError, match="OpenCode send failed"):
        list(orch.build_stream("Match this image", [image]))

    image_event = _recorded_image_event(orch)
    assert image_event["delivery"] == "not_sent"
    assert image_event["failure"] == "no_request"


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


def test_image_delivery_records_the_actual_routed_model_capability(tmp_path: Path):
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "match this design"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]}]

    nonvision = _shim(ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT), FakeGatewayClient())
    journal, operation, marker = _record_image(nonvision, tmp_path / "nonvision")
    messages[0]["content"][0]["text"] += "\n" + marker
    list(nonvision.handle({"messages": messages}, project="p"))
    refused = nonvision.data_use.operations[operation][0]
    assert refused["delivery"] == "not_sent"
    assert refused["failure"] == "capability"
    assert refused["serving_model"] == "cheap-vendor"
    assert journal[-1]["dataUsed"][0]["failure"] == "capability"

    vision = _vision_shim(
        ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT), FakeGatewayClient()
    )
    journal, operation, marker = _record_image(vision, tmp_path / "vision")
    messages[0]["content"][0]["text"] += "\n" + marker
    list(vision.handle({"messages": messages}, project="p"))
    delivered = vision.data_use.operations[operation][0]
    assert delivered["delivery"] == "sent"
    assert delivered["failure"] is None
    assert delivered["serving_model"] == "sonnet"
    assert journal[-1]["dataUsed"][0]["delivery"] == "sent"


def test_image_delivery_updates_only_the_operation_in_this_request(tmp_path: Path):
    shim = _vision_shim(
        ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT), FakeGatewayClient()
    )
    old_journal, old_operation, old_marker = _record_image(
        shim, tmp_path / "old", turn_id="turn-old"
    )
    journal, operation, marker = _record_image(shim, tmp_path / "current", turn_id="turn-current")
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "old design\n" + old_marker},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,OLD"}},
        ]},
        {"role": "assistant", "content": "Earlier response"},
        {"role": "user", "content": [
            {"type": "text", "text": "current design\n" + marker},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,CURRENT"}},
        ]},
    ]

    list(shim.handle({"messages": messages}, project="p"))

    assert shim.data_use.operations[operation][0]["delivery"] == "sent"
    assert shim.data_use.operations[old_operation][0]["delivery"] == "pending"
    shim.data_use.finish_image_delivery("turn-old")
    assert shim.data_use.operations[old_operation][0]["delivery"] == "not_sent"
    assert shim.data_use.operations[old_operation][0]["failure"] == "no_request"
    assert old_journal[-1]["dataUsed"][0]["failure"] == "no_request"
    assert journal[-1]["dataUsed"][0]["delivery"] == "sent"


def test_image_carrier_count_mismatch_fails_every_current_operation_closed(tmp_path: Path):
    shim = _vision_shim(
        ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT), FakeGatewayClient()
    )
    _journal_a, operation_a, marker_a = _record_image(
        shim, tmp_path / "a", turn_id="turn-current"
    )
    _journal_b, operation_b, marker_b = _record_image(
        shim, tmp_path / "b", turn_id="turn-current"
    )
    messages = [{"role": "user", "content": [
        {"type": "text", "text": marker_a + "\n" + marker_b},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,ONLY_ONE"}},
    ]}]

    list(shim.handle({"messages": messages}, project="p"))

    for operation in (operation_a, operation_b):
        event = shim.data_use.operations[operation][0]
        assert event["delivery"] == "not_sent"
        assert event["failure"] == "carrier"


@pytest.mark.parametrize("failure_mode", ["synchronous", "lazy", "empty"])
def test_vision_image_is_not_sent_until_upstream_responds(tmp_path: Path, failure_mode: str):
    class FailingGateway:
        def route(self, _request, _labels):
            if failure_mode == "synchronous":
                raise OSError("gateway failed before returning an iterator")
            if failure_mode == "empty":
                return iter(())

            def lazy_failure():
                raise OSError("gateway failed on first iteration")
                yield b"unreachable"

            return lazy_failure()

    shim = _vision_shim(
        ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT), FailingGateway()
    )
    journal, operation, marker = _record_image(shim, tmp_path)
    messages = [{"role": "user", "content": [
        {"type": "text", "text": marker},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]}]

    if failure_mode == "synchronous":
        with pytest.raises(OSError, match="before returning"):
            shim.handle({"messages": messages}, project="p")
    elif failure_mode == "lazy":
        stream = shim.handle({"messages": messages}, project="p")
        assert shim.data_use.operations[operation][0]["delivery"] == "pending"
        with pytest.raises(OSError, match="first iteration"):
            list(stream)
    else:
        list(shim.handle({"messages": messages}, project="p"))

    event = shim.data_use.operations[operation][0]
    assert event["delivery"] == "not_sent"
    assert event["failure"] == ("no_response" if failure_mode == "empty" else "gateway")
    assert all(row["dataUsed"][0]["delivery"] != "sent" for row in journal)


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
