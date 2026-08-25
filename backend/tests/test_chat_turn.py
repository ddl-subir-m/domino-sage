from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def route(self, request, labels):
        yield b"data: [DONE]\n\n"


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="p", implement="i", ask="a")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _orch(tmp: Path, turns: list[Turn] | None = None):
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, turns or [])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc)
    orch.project(start_preview=False)
    return orch, oc


def test_chat_turn_uses_sage_chat_skips_plan_and_tsc(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="Rates is the largest desk.")])
    thread = orch.create_thread()
    events = list(orch.chat_stream(thread["id"], "what's our gross exposure by desk?"))

    assert oc.prompts[0]["agent"] == "sage-chat"
    kinds = {e.get("type") for e in events}
    assert "plan-proposed" not in kinds
    assert "typecheck" not in kinds
    assert next(e for e in events if e["type"] == "done")["decision"] == "answered"
    assert orch.project(start_preview=False).workspace.read_history() == []


def test_chat_turn_records_artifact_and_reverts_src(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    thread = orch.create_thread()
    tid = thread["id"]
    table = '{"title": "Desks", "columns": ["desk"], "rows": [["Rates"]]}'
    oc.turns = [Turn(
        text="Here is gross notional by desk.",
        writes={
            "src/App.tsx": "// hijack",
            f"examples/{tid}/exposure.table.json": table,
        },
    )]
    src = orch.project(start_preview=False).workspace.path / "src" / "App.tsx"
    original = src.read_text()
    events = list(orch.chat_stream(tid, "what's in this CSV?"))

    assert src.read_text() == original
    arts = next(e for e in events if e.get("type") == "artifacts")["items"]
    assert arts[0]["kind"] == "table"
    assert arts[0]["path"] == f"examples/{tid}/exposure.table.json"
    assert (orch.project(start_preview=False).workspace.path / arts[0]["path"]).read_text() == table
    hist = orch.thread_history(tid)
    assert hist[0]["type"] == "user"
    assert orch.project(start_preview=False).workspace.read_history() == []


def test_new_conversation_does_not_provision(tmp_path: Path, monkeypatch):
    orch, _ = _orch(tmp_path)
    calls: list[int] = []
    monkeypatch.setattr("sage.provision.service.HubService.create_app",
                        lambda *a, **k: calls.append(1))
    first = orch.create_thread()
    second = orch.create_thread()
    assert first["id"] != second["id"]
    assert calls == []
    assert len(orch.list_threads()) == 2


def test_get_patch_delete_thread(tmp_path: Path):
    orch, _ = _orch(tmp_path)
    row = orch.create_thread()
    got = orch.get_thread(row["id"])
    assert got["id"] == row["id"]
    assert got["history"] == []
    patched = orch.patch_thread(row["id"], {"title": "Rates", "pinned": True})
    assert patched["title"] == "Rates"
    assert patched["pinned"] is True
    orch.delete_thread(row["id"])
    assert orch.list_threads() == []
