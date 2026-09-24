"""An approved Build receives the exact references that its planning turn received (#517)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sage.assets.provider import FakeAssetProvider
from sage.driver.opencode import with_attachment_listing
from sage.liveread import reference
from sage.orchestrator.recall import WITHHELD
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace.manager import ProjectRecord

from .fake_opencode import FakeOpenCode, Turn
from .test_a_dropped_mention_reaches_the_agents_prompt import OkFeedback, ScriptedGateway

PLAN = """# Requirements App

An app that follows the saved requirements.

## Problem & outcome
The requirements are hard to use; the app makes them visible.

## Who uses this
The requirements analyst.

## What it does
- Shows the required table

## Screens
- **Requirements table** — Shows the required fields.

## Done when
- The preview shows the required table.

## Plan
### 1. Requirements table
- Files — src/App.tsx
- Do — Build the required table from the saved reference.
- Done when — The preview shows the required table.
"""
RULE = "UNIQUE PLAN REFERENCE RULE"
NEIGHBOR = "PRIVATE NEIGHBOR SENTINEL"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _orchestrator(tmp_path: Path, assets: FakeAssetProvider, turns: list[Turn]):
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text(
        "export default function App() { return null }\n"
    )
    (template / "package.json").write_text("{}")
    workspace = tmp_path / "mnt" / "code"
    client = FakeOpenCode(workspace, turns)
    orchestrator = Orchestrator(
        workspace_dir=workspace,
        template=template,
        gateway=ScriptedGateway(),
        catalog=ModelCatalog(
            sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
            plan="p", implement="i", ask="a",
        ),
        project_id="Sage",
        feedback=OkFeedback(),
        assets=assets,
        opencode_client=client,
    )
    orchestrator.project(start_preview=False)
    return orchestrator, client


def _planned(tmp_path: Path, *, document: str | None = None, oversized: bool = False,
             prompt: str = "Build the table. Follow the Programming Notes in the attached document."):
    assets = FakeAssetProvider()
    source = assets.root / "sales_2026" / "README.md"
    if oversized:
        with source.open("wb") as handle:
            handle.truncate(reference.MAX_SOURCE_BYTES + 1)
    else:
        source.write_text(document or (
            "# TFL Layout\nWide table\n\n## Programming Notes\n" + RULE + "\n"
        ))
    (assets.root / "sales_2026" / "train.csv").write_text(
        "USUBJID,ARM\n" + NEIGHBOR + ",Active\n"
    )
    orchestrator, client = _orchestrator(tmp_path, assets, [Turn(text=PLAN)])
    shell = orchestrator.attach_file("ds_sales_2026", "README.md")["path"]
    neighbor = orchestrator.attach_file("ds_sales_2026", "train.csv")["path"]
    events = list(orchestrator.build_stream(prompt, [shell]))
    plan_id = next(event["planId"] for event in events if event["type"] == "plan-proposed")
    return orchestrator, client, assets, shell, neighbor, plan_id


def _outgoing(client: FakeOpenCode) -> str:
    prompt = client.prompts[0]
    return with_attachment_listing(prompt["text"], prompt["attachments"])


def test_a_malformed_generated_build_plan_creates_no_document_or_card(tmp_path: Path):
    assets = FakeAssetProvider()
    malformed = "# Table App\n\nA small table.\n\n## Plan\n1. Build the table.\n"
    orch, _ = _orchestrator(tmp_path, assets, [Turn(text=malformed)])
    private = "PRIVATE SOURCE REQUEST SENTINEL"

    events = list(orch.build_stream(f"Build a table. {private}"))

    assert not any(event.get("type") == "plan-proposed" for event in events)
    record = orch.project(start_preview=False).record
    assert record.list_plan_docs() == []
    assert any(event.get("decision") == "invalid execution plan" for event in events)
    diagnostics = json.loads((record.path / ".sage" / "build-diagnostics.json").read_text())
    contract = diagnostics["records"][-1]["planContract"]
    assert contract == {
        "executionContractVersion": 1,
        "sourceRequestMessagesVersion": 1,
        "sourceRequestCount": 1,
        "valid": False,
        "stepCount": 0,
        "malformedStepCount": 0,
        "invalidFileCount": 0,
        "missingSections": ["problem", "users", "outcomes", "screens", "acceptance"],
    }
    assert private not in json.dumps(diagnostics)


def _pdf(path: Path, texts: list[str]) -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        font = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }))
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
        page.replace_contents(stream)
    with path.open("wb") as handle:
        writer.write(handle)


def test_plan_restart_approve_reprepares_only_the_saved_reference(tmp_path: Path):
    first, _planner, assets, shell, neighbor, plan_id = _planned(tmp_path)
    doc = first.project(start_preview=False).record.read_plan_doc(plan_id)
    assert doc is not None
    assert doc["explicitReferencesVersion"] == 1
    assert doc["explicitReferences"] == [{
        "source": shell,
        "handler": "markdown",
        "selector": "Programming Notes",
        "sha256": hashlib.sha256(
            (assets.root / "sales_2026" / "README.md").read_bytes()
        ).hexdigest(),
        "status": "prepared",
    }]
    assert doc["executionContractVersion"] == 1
    assert doc["sourceRequestMessagesVersion"] == 1
    assert doc["sourceRequestMessages"] == [
        "Build the table. Follow the Programming Notes in the attached document."
    ]
    assert doc["sourceRequestMessages"][0] not in doc["markdown"]

    persisted = json.dumps(doc["explicitReferences"])
    assert RULE not in persisted and NEIGHBOR not in persisted
    app = first.project(start_preview=False).workspace
    assert RULE not in app.history_path.read_text()
    diagnostics = first.project(start_preview=False).record.path / ".sage" / "build-diagnostics.json"
    if diagnostics.exists():
        assert RULE not in diagnostics.read_text()

    restarted, builder = _orchestrator(
        tmp_path, assets, [Turn(writes={"src/App.tsx": "export default () => null\n"})]
    )
    events = list(restarted.approve_stream(plan_id=plan_id))
    outgoing = _outgoing(builder)

    assert RULE in outgoing
    assert NEIGHBOR not in outgoing
    assert shell in outgoing and neighbor not in outgoing
    assert len(builder.prompts[0]["attachments"]) == 1
    assert not [part for part in builder.messages(builder.prompts[0]["session"])[0]["content"]
                if isinstance(part, dict) and part.get("type") == "tool"
                and part.get("tool") in {"read", "grep", "bash"}]
    document_events = [event for row in events for event in row.get("dataUsed", [])
                       if event.get("operation") == "document_reference"]
    assert len(document_events) == 1
    assert RULE not in json.dumps(document_events)


def test_an_invalid_edit_to_a_v1_plan_stops_before_a_model_call(tmp_path: Path):
    assets = FakeAssetProvider()
    orch, builder = _orchestrator(tmp_path, assets, [Turn()])
    project = orch.project(start_preview=False)
    doc = project.record.create_plan_doc(
        PLAN,
        title="Requirements App",
        app_id=project.workspace.app_id,
        execution_contract_version=1,
        source_request_messages_version=1,
        source_request_messages=("Build the requirements app.",),
        explicit_references=[],
    )
    project.workspace.write_plan(PLAN, doc["id"])
    invalid = PLAN.replace("- Files — src/App.tsx\n", "")

    events = list(orch.approve_stream(plan_id=doc["id"], plan_edits=invalid))

    assert builder.prompts == []
    assert any("Files fields" in event.get("message", "") for event in events)
    assert project.workspace.read_plan() == invalid
    saved = project.record.read_plan_doc(doc["id"])
    assert saved["version"] == 2
    assert saved["markdown"] == invalid


def test_fixing_an_invalid_v1_plan_allows_the_build(tmp_path: Path):
    assets = FakeAssetProvider()
    orch, builder = _orchestrator(
        tmp_path, assets, [Turn(writes={"src/App.tsx": "export default () => null\n"})]
    )
    project = orch.project(start_preview=False)
    doc = project.record.create_plan_doc(
        PLAN.replace("- Files — src/App.tsx\n", ""),
        title="Requirements App",
        app_id=project.workspace.app_id,
        execution_contract_version=1,
        source_request_messages_version=1,
        source_request_messages=("Build the requirements app.",),
        explicit_references=[],
    )
    project.workspace.write_plan(doc["markdown"], doc["id"])

    list(orch.approve_stream(plan_id=doc["id"], plan_edits=PLAN))

    assert len(builder.prompts) == 1


def test_malformed_source_request_metadata_stops_before_approval(tmp_path: Path):
    assets = FakeAssetProvider()
    orch, builder = _orchestrator(tmp_path, assets, [Turn()])
    project = orch.project(start_preview=False)
    doc = project.record.create_plan_doc(
        PLAN,
        title="Requirements App",
        app_id=project.workspace.app_id,
        execution_contract_version=1,
        source_request_messages_version=1,
        source_request_messages=("Build it.",),
    )
    project.workspace.write_plan(PLAN, doc["id"])
    meta_path = project.record.plan_docs_dir / doc["id"] / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["sourceRequestMessages"] = ["Build it.", 7]
    meta_path.write_text(json.dumps(meta))

    events = list(orch.approve_stream(plan_id=doc["id"]))

    assert builder.prompts == []
    assert any("original-request metadata" in event.get("message", "") for event in events)
    assert project.workspace.read_plan() == PLAN


def test_an_unknown_execution_contract_version_fails_closed(tmp_path: Path):
    assets = FakeAssetProvider()
    orch, builder = _orchestrator(tmp_path, assets, [Turn()])
    project = orch.project(start_preview=False)
    doc = project.record.create_plan_doc(
        PLAN,
        title="Requirements App",
        app_id=project.workspace.app_id,
        execution_contract_version=1,
        source_request_messages_version=1,
        source_request_messages=("Build it.",),
    )
    project.workspace.write_plan(PLAN, doc["id"])
    meta_path = project.record.plan_docs_dir / doc["id"] / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["executionContractVersion"] = 2
    meta_path.write_text(json.dumps(meta))

    events = list(orch.approve_stream(plan_id=doc["id"]))

    assert builder.prompts == []
    assert any("execution contract version" in event.get("message", "") for event in events)


def test_a_legacy_plan_approves_with_no_invented_source_request(tmp_path: Path):
    assets = FakeAssetProvider()
    orch, builder = _orchestrator(
        tmp_path, assets, [Turn(writes={"src/App.tsx": "export default () => null\n"})]
    )
    project = orch.project(start_preview=False)
    legacy = project.record.create_plan_doc(
        "# Legacy\n\n## Plan\n1. Add the table.\n",
        title="Legacy",
        app_id=project.workspace.app_id,
    )
    project.workspace.write_plan(legacy["markdown"], legacy["id"])

    list(orch.approve_stream(plan_id=legacy["id"]))

    assert len(builder.prompts) == 1
    assert legacy["executionContractVersion"] == 0
    assert legacy["sourceRequestMessagesVersion"] == 0
    assert legacy["sourceRequestMessages"] == []


def test_changed_reference_hash_fails_closed_without_sending_new_text(tmp_path: Path):
    _first, _planner, assets, _shell, _neighbor, plan_id = _planned(tmp_path)
    changed = "A REVISED RULE THAT WAS NEVER PLANNED"
    (assets.root / "sales_2026" / "README.md").write_text("# Shell\n" + changed + "\n")
    restarted, builder = _orchestrator(tmp_path, assets, [Turn()])

    list(restarted.approve_stream(plan_id=plan_id))
    outgoing = _outgoing(builder)

    assert "changed after the plan was prepared" in outgoing
    assert changed not in outgoing and RULE not in outgoing


def test_pdf_page_selection_survives_restart_approval(tmp_path: Path):
    assets = FakeAssetProvider()
    source = assets.root / "sales_2026" / "requirements.pdf"
    _pdf(source, ["PAGE ONE SECRET", "PAGE TWO SECRET", "PAGE THREE RULE"])
    first, _planner = _orchestrator(tmp_path, assets, [Turn()])
    shell = first.attach_file("ds_sales_2026", "requirements.pdf")["path"]
    project = first.project(start_preview=False)
    doc = project.record.create_plan_doc(
        PLAN,
        title="PDF requirements",
        app_id=project.workspace.app_id,
        explicit_references=[{
            "source": shell,
            "handler": "pdf",
            "selector": "",
            "pages": [3],
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "status": "prepared",
        }],
    )
    project.workspace.write_plan(PLAN, doc["id"])
    restarted, builder = _orchestrator(
        tmp_path, assets, [Turn(writes={"src/App.tsx": "export default () => null\n"})]
    )

    list(restarted.approve_stream(plan_id=doc["id"]))
    outgoing = _outgoing(builder)

    assert "PAGE THREE RULE" in outgoing
    assert "PAGE ONE SECRET" not in outgoing
    assert "PAGE TWO SECRET" not in outgoing


@pytest.mark.parametrize("digest", [None, "", "not-a-sha256"])
def test_incomplete_success_digest_cannot_transfer_current_bytes_after_restart(
    tmp_path: Path, digest: str | None,
):
    first, _planner, assets, _shell, _neighbor, plan_id = _planned(tmp_path)
    record = first.project(start_preview=False).record
    meta_path = record.plan_docs_dir / plan_id / "meta.json"
    meta = json.loads(meta_path.read_text())
    if digest is None:
        meta["explicitReferences"][0].pop("sha256")
    else:
        meta["explicitReferences"][0]["sha256"] = digest
    meta_path.write_text(json.dumps(meta))
    restarted, builder = _orchestrator(tmp_path, assets, [Turn()])

    list(restarted.approve_stream(plan_id=plan_id))
    outgoing = _outgoing(builder)

    assert "saved reference record is incomplete or invalid" in outgoing
    assert RULE not in outgoing


def test_duplicate_heading_failure_keeps_its_attempted_selector_after_restart(tmp_path: Path):
    first, _planner, assets, _shell, _neighbor, plan_id = _planned(
        tmp_path,
        document=("# TFL Layout\n\n## Programming Notes\nFIRST DUPLICATE SECRET\n\n"
                  "## Programming Notes\nSECOND DUPLICATE SECRET\n"),
    )
    doc = first.project(start_preview=False).record.read_plan_doc(plan_id)
    assert doc is not None
    assert doc["explicitReferences"][0]["selector"] == "Programming Notes"
    assert doc["explicitReferences"][0]["status"] == "heading_not_unique"
    restarted, builder = _orchestrator(tmp_path, assets, [Turn()])

    list(restarted.approve_stream(plan_id=plan_id))
    outgoing = _outgoing(builder)

    assert "heading is missing or is not unique" in outgoing
    assert "FIRST DUPLICATE SECRET" not in outgoing
    assert "SECOND DUPLICATE SECRET" not in outgoing


def test_over_limit_planning_failure_cannot_become_a_transfer_after_replacement(tmp_path: Path):
    first, _planner, assets, _shell, _neighbor, plan_id = _planned(tmp_path, oversized=True)
    doc = first.project(start_preview=False).record.read_plan_doc(plan_id)
    assert doc is not None
    assert doc["explicitReferences"][0]["status"] == "source_too_large"
    assert doc["explicitReferences"][0]["sha256"] == ""
    replacement = "SMALL REPLACEMENT THAT PLANNING NEVER SAW"
    (assets.root / "sales_2026" / "README.md").write_text(replacement)
    restarted, builder = _orchestrator(tmp_path, assets, [Turn()])

    list(restarted.approve_stream(plan_id=plan_id))
    outgoing = _outgoing(builder)

    assert "exceeds the 8 MiB source limit" in outgoing
    assert replacement not in outgoing


def test_deleted_reference_stops_before_the_implementation_request(tmp_path: Path):
    first, _planner, assets, shell, _neighbor, plan_id = _planned(tmp_path)
    (assets.root / "sales_2026" / "README.md").unlink()
    (first.project(start_preview=False).workspace.path / shell).unlink()
    restarted, builder = _orchestrator(tmp_path, assets, [Turn()])

    events = list(restarted.approve_stream(plan_id=plan_id))

    assert builder.prompts == []
    assert any(event.get("type") == "mentions-unresolved" for event in events)
    assert next(event for event in events if event.get("type") == "done")["ok"] is False


def test_withholding_is_applied_again_on_approval(tmp_path: Path):
    first, _planner, assets, shell, _neighbor, plan_id = _planned(tmp_path)
    first.project(start_preview=False).workspace.append_history(
        {"type": WITHHELD, "keys": ["file:" + shell]}
    )
    restarted, builder = _orchestrator(tmp_path, assets, [Turn()])

    list(restarted.approve_stream(plan_id=plan_id))
    outgoing = _outgoing(builder)

    assert "withheld this document" in outgoing
    assert RULE not in outgoing


def test_saved_record_reauthorization_refuses_an_unauthorized_sibling(tmp_path: Path):
    (tmp_path / "allowed.md").write_text("allowed")
    (tmp_path / "private.md").write_text(NEIGHBOR)
    record = {"source": "private.md", "handler": "markdown", "selector": "",
              "sha256": "a" * 64}

    prepared = reference.prepare_plan_records(
        tmp_path, [{"path": "allowed.md"}], [record]
    )

    assert len(prepared) == 1 and prepared[0].status == "not_authorized"
    assert NEIGHBOR not in prepared[0].prompt_block()


def test_old_plan_metadata_remains_readable_with_no_saved_references(tmp_path: Path):
    record = ProjectRecord("Sage", tmp_path)
    doc = record.create_plan_doc("# Old plan\n", title="Old")
    meta_path = record.plan_docs_dir / doc["id"] / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta.pop("explicitReferences", None)
    meta.pop("explicitReferencesVersion", None)
    meta_path.write_text(json.dumps(meta))

    reopened = record.read_plan_doc(doc["id"])
    assert reopened is not None
    assert reopened["explicitReferencesVersion"] == 0
    assert reopened["explicitReferences"] == []


def test_old_plan_without_declared_reference_metadata_keeps_legacy_attachments(tmp_path: Path):
    first, _planner, assets, shell, neighbor, plan_id = _planned(tmp_path)
    record = first.project(start_preview=False).record
    meta_path = record.plan_docs_dir / plan_id / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta.pop("explicitReferencesVersion")
    meta.pop("explicitReferences")
    meta_path.write_text(json.dumps(meta))
    restarted, builder = _orchestrator(tmp_path, assets, [Turn()])

    list(restarted.approve_stream(plan_id=plan_id))

    assert {item["path"] for item in builder.prompts[0]["attachments"]} == {shell, neighbor}


@pytest.mark.parametrize("version", [2, "1", True, None])
def test_declared_unsupported_reference_metadata_stops_approval(
    tmp_path: Path, version: object,
):
    first, _planner, assets, _shell, _neighbor, plan_id = _planned(tmp_path)
    record = first.project(start_preview=False).record
    meta_path = record.plan_docs_dir / plan_id / "meta.json"
    meta = json.loads(meta_path.read_text())
    if version is None:
        meta.pop("explicitReferencesVersion")
    else:
        meta["explicitReferencesVersion"] = version
    meta_path.write_text(json.dumps(meta))
    reopened = record.read_plan_doc(plan_id)
    assert reopened is not None
    assert reopened["explicitReferencesVersion"] == -1
    restarted, builder = _orchestrator(tmp_path, assets, [Turn()])

    events = list(restarted.approve_stream(plan_id=plan_id))

    assert builder.prompts == []
    assert any(event.get("decision") == "invalid plan reference metadata" for event in events)
    assert next(event for event in events if event.get("type") == "done")["ok"] is False
