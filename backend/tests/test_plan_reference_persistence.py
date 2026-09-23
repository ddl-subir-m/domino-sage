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

PLAN = "# Requirements App\n\n## Plan\n1. Build the required table\n"
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


def _planned(tmp_path: Path):
    assets = FakeAssetProvider()
    (assets.root / "sales_2026" / "README.md").write_text(
        "# TFL Layout\nWide table\n\n## Programming Notes\n" + RULE + "\n"
    )
    (assets.root / "sales_2026" / "train.csv").write_text(
        "USUBJID,ARM\n" + NEIGHBOR + ",Active\n"
    )
    orchestrator, client = _orchestrator(tmp_path, assets, [Turn(text=PLAN)])
    shell = orchestrator.attach_file("ds_sales_2026", "README.md")["path"]
    neighbor = orchestrator.attach_file("ds_sales_2026", "train.csv")["path"]
    events = list(orchestrator.build_stream(
        "Build the table. Follow the Programming Notes in the attached document.", [shell]
    ))
    plan_id = next(event["planId"] for event in events if event["type"] == "plan-proposed")
    return orchestrator, client, assets, shell, neighbor, plan_id


def _outgoing(client: FakeOpenCode) -> str:
    prompt = client.prompts[0]
    return with_attachment_listing(prompt["text"], prompt["attachments"])


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
    }]

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


def test_changed_reference_hash_fails_closed_without_sending_new_text(tmp_path: Path):
    _first, _planner, assets, _shell, _neighbor, plan_id = _planned(tmp_path)
    changed = "A REVISED RULE THAT WAS NEVER PLANNED"
    (assets.root / "sales_2026" / "README.md").write_text("# Shell\n" + changed + "\n")
    restarted, builder = _orchestrator(tmp_path, assets, [Turn()])

    list(restarted.approve_stream(plan_id=plan_id))
    outgoing = _outgoing(builder)

    assert "changed after the plan was prepared" in outgoing
    assert changed not in outgoing and RULE not in outgoing


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
