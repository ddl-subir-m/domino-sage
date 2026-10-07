"""An approved Chat handoff attaches a bound single-file Dataset before it builds (#687).

The single-file attach lived only in `build_stream`'s Dataset gate. A plan drafted in Build passed
that gate on its plan turn; a plan drafted by the Chat handoff never did, so its first build was the
approve, and the build searched the app tree for a file nobody had attached.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.assets.provider import Asset, FakeAssetProvider
from sage.feedback.runner import FeedbackReport
from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import ResourceUnavailable
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn, execution_plan


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


_PLAN = execution_plan("Sales Playbook Board", "A sales playbook board.", "Playbook table")
_NOTHING_EXTRA = {"resources": False, "artifacts": False, "transcript": False}


def _approved_handoff(tmp: Path, files: dict[str, str],
                      also: dict[str, dict[str, str]] | None = None):
    """A confirmed handoff whose app binds `sales-playbooks` holding `files`, then each Dataset in
    `also` (name to files, id `ds_<name>`), in that order, with nothing attached."""
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    assets = FakeAssetProvider()
    datasets = {"ds_playbooks": ("sales-playbooks", files)}
    datasets.update({f"ds_{name}": (name, held) for name, held in (also or {}).items()})
    for dataset_id, (name, held) in datasets.items():
        where = tmp / "datasets" / name
        for rel, body in held.items():
            (where / rel).parent.mkdir(parents=True, exist_ok=True)
            (where / rel).write_text(body)
        assets.assets.append(Asset(dataset_id, name, project="Sage", mount_path=str(where)))
    root = tmp / "mnt" / "code"
    oc = FakeOpenCode(root, [Turn(text="A board, then."), Turn(text=_PLAN),
                             Turn(writes={"src/App.tsx": "// the playbook table\n"})])
    orch = Orchestrator(workspace_dir=root, template=t, gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", assets=assets, feedback=OkFeedback(),
                        opencode_client=oc)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "a board for the sales playbooks"))
    plan_id = orch.draft_handoff_plan(tid)["handoff"]["planId"]
    orch.confirm_handoff(tid, _NOTHING_EXTRA)
    for dataset_id in datasets:
        orch.bind_dataset(dataset_id)
    return orch, oc, tid, plan_id


def _attached(orch) -> list[dict]:
    return list(orch.project(start_preview=False).attached)


def _spy_attach(orch, monkeypatch, oc) -> list[tuple]:
    """Each attach_file call, with how many prompts had gone out when it was made."""
    calls = []
    real = orch.attach_file

    def spy(dataset_id, file_path, **kw):
        calls.append((dataset_id, file_path, len(oc.prompts)))
        return real(dataset_id, file_path, **kw)

    monkeypatch.setattr(orch, "attach_file", spy)
    return calls


def test_approving_a_handoff_attaches_the_only_file_before_the_build(tmp_path, monkeypatch):
    orch, oc, tid, plan_id = _approved_handoff(tmp_path, {"playbook.md": "# Playbook\n"})
    calls = _spy_attach(orch, monkeypatch, oc)
    before = len(oc.prompts)

    events = list(orch.approve_stream(conversation=tid, plan_id=plan_id))

    assert calls == [("ds_playbooks", "playbook.md", before)], "not attached before the build"
    [entry] = _attached(orch)
    assert entry["file"] == "playbook.md"
    assert entry["path"] in {a["path"] for a in oc.prompts[-1]["attachments"]}
    notes = [e["message"] for e in events if e["type"] == "dataset-attached"]
    assert notes == ["Using playbook.md from sales-playbooks."]
    assert next(e for e in reversed(events) if e["type"] == "done")["ok"] is True


def test_a_phased_approve_attaches_the_only_file_and_says_so(tmp_path, monkeypatch):
    orch, oc, tid, plan_id = _approved_handoff(tmp_path, {"playbook.md": "# Playbook\n"})
    orch.project(start_preview=False).record.write_settings({"phased_build": True})
    calls = _spy_attach(orch, monkeypatch, oc)
    before = len(oc.prompts)

    events = list(orch.approve_stream(conversation=tid, plan_id=plan_id))

    assert calls == [("ds_playbooks", "playbook.md", before)]
    assert [e["message"] for e in events if e["type"] == "dataset-attached"] == [
        "Using playbook.md from sales-playbooks."]
    project = orch.project(start_preview=False)
    history = project.workspace.read_history(project.build_conversation)
    assert [e["type"] for e in history if e["type"] in ("user", "dataset-attached")][-2:] == [
        "user", "dataset-attached"]


def test_an_already_attached_dataset_is_not_attached_again(tmp_path, monkeypatch):
    orch, oc, tid, plan_id = _approved_handoff(tmp_path, {"playbook.md": "# Playbook\n"})
    orch.attach_file("ds_playbooks", "playbook.md")
    calls = _spy_attach(orch, monkeypatch, oc)

    events = list(orch.approve_stream(conversation=tid, plan_id=plan_id))

    assert calls == []
    assert len(_attached(orch)) == 1
    assert [e for e in events if e["type"] == "dataset-attached"] == []


def test_a_dataset_that_cannot_be_listed_still_builds(tmp_path, monkeypatch):
    orch, oc, tid, plan_id = _approved_handoff(tmp_path, {"playbook.md": "# Playbook\n"})
    calls = _spy_attach(orch, monkeypatch, oc)

    def unavailable(*a, **k):
        raise ResourceUnavailable("the platform is down")

    monkeypatch.setattr(orch, "_dataset_candidates", unavailable)
    before = len(oc.prompts)

    events = list(orch.approve_stream(conversation=tid, plan_id=plan_id))

    assert calls == []
    assert len(oc.prompts) > before, "the build did not start"
    assert next(e for e in reversed(events) if e["type"] == "done")["ok"] is True


def test_a_dataset_that_needs_a_choice_builds_without_a_card(tmp_path, monkeypatch):
    """Today's behaviour on the approve path, kept: no card, nothing attached, the build runs.
    A single-file Dataset bound after it is still attached."""
    orch, oc, tid, plan_id = _approved_handoff(
        tmp_path, {"playbook.md": "# Playbook\n", "old_playbook.md": "# Old\n"},
        also={"regions": {"regions.csv": "region\nEMEA\n"}})
    calls = _spy_attach(orch, monkeypatch, oc)

    events = list(orch.approve_stream(conversation=tid, plan_id=plan_id))

    assert [c[:2] for c in calls] == [("ds_regions", "regions.csv")]
    assert [e for e in events if e["type"] == "dataset-files"] == []
    assert next(e for e in reversed(events) if e["type"] == "done")["ok"] is True
