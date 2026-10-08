"""A second app reads only the Data Source its own record holds (#704).

App A in a Project is bound to the warehouse through the source card's own click. App B is created
next and asked for a build over the same table, with B as the request's app (`X-Sage-App`). B
records no Data Source, so it either gets the card or reads nothing — whichever app is selected.

THE LEAK. OpenCode's live-read calls come back to `/mcp/live-read` as their own request, and that
request carries no `X-Sage-App`. A grant read off `project.workspace` there is the SELECTED app's,
so with A selected B's turn was handed A's store: the planner read the table, the app's
instructions (read on B's request) named no store, and B's queries named one it invented.
"""

from __future__ import annotations

import contextvars
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sage.orchestrator.app as appmod
from sage.orchestrator import brand, handoff
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_DATA_SOURCE
from sage.resources.provider import FakeResourceProvider, SampleRows
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

TABLE = "DWH.MARTS.FCT_USAGE_DAILY"
# Names the store, so the gate is asked "which Data Source" of B.
STORE_PROMPT = f"build me a dashboard of daily usage from Snowflake, using {TABLE}"
# Names the bare table and no store, which is a request the source gate does not read as one about
# a store (#185); the full `DB.SCHEMA.TABLE` name would be (#705). It reaches the agent, and what
# the agent may read is then the grant's question.
TABLE_PROMPT = "build me a dashboard of daily usage over FCT_USAGE_DAILY"


class Warehouse(FakeResourceProvider):
    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple] = []

    def sample_rows(self, source, database, schema, table, limit=5):
        self.asked.append((source.name, database, schema, table))
        return SampleRows(table, ["DAY"], [["2026-08-18"]][:limit])


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class ReadsMidTurn(FakeOpenCode):
    """Makes one live read while the turn is running, the way OpenCode does: as a separate
    request that names no app. A fresh context is that request — no view is bound in it."""

    orch: Orchestrator
    replies: list[str]

    def send_prompt(self, session_id, text, *args, **kwargs):
        out = super().send_prompt(session_id, text, *args, **kwargs)
        m = re.search(r"Read token: (lrt_[A-Za-z0-9_-]+)", text)
        if m:
            message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "live_read_table", "arguments": {
                    "token": m.group(1), "source": "Snowflake-Data-Warehouse",
                    "database": "DWH", "schema": "MARTS", "table": "FCT_USAGE_DAILY", "limit": 1}}}
            reply = contextvars.Context().run(self.orch.live_read_call, message)
            self.replies.append(reply["result"]["content"][0]["text"])
        return out


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text("{}\n")
    return t


def _project(tmp_path: Path, monkeypatch, selected: str):
    """A bound to the warehouse through the card's click; B created after it; `selected` on screen."""
    root = tmp_path / "mnt" / "code"
    resources = Warehouse()
    oc = ReadsMidTurn(root, [Turn(text="Built it.")])
    orch = Orchestrator(workspace_dir=root, template=_template(tmp_path), gateway=ScriptedGateway(),
                        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"), project_id="Sage",
                        feedback=OkFeedback(), opencode_client=oc, resources=resources)
    oc.orch, oc.replies = orch, []
    app_a = orch.project(start_preview=False).workspace.app_id
    app_b = orch.create_app()["id"]
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)
    bound = client.post("/api/bindings", headers={"X-Sage-App": app_a}, json={
        "kind": KIND_DATA_SOURCE, "id": "ds-dwh",
        "database": "DWH", "schema": "MARTS", "table": "FCT_USAGE_DAILY"})
    assert bound.status_code == 200, bound.text
    if selected == "A":
        orch.select_app(app_a)
    assert orch._wm.selected_app_id() == (app_a if selected == "A" else app_b)
    return orch, oc, resources, client, root, app_a, app_b


def _sources(root: Path, app_id: str) -> list[str]:
    path = root / "apps" / app_id / ".sage" / "bindings.json"
    rows = json.loads(path.read_text()) if path.is_file() else []
    return [r["id"] for r in rows if r.get("kind") == KIND_DATA_SOURCE]


def _frames(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def _build_on_b(client: TestClient, app_b: str, prompt: str, tid: str) -> list[dict]:
    return _frames(client.post("/api/project/build/stream", headers={"X-Sage-App": app_b},
                               json={"prompt": prompt, "conversation": tid}).text)


SELECTED = pytest.mark.parametrize("selected", ["A", "B"])


@SELECTED
def test_b_naming_the_store_gets_the_card(tmp_path: Path, monkeypatch, selected):
    orch, oc, _res, client, root, app_a, app_b = _project(tmp_path, monkeypatch, selected)
    tid = orch.create_thread()["id"]

    frames = _build_on_b(client, app_b, STORE_PROMPT, tid)

    assert [f for f in frames if f.get("type") == "source-candidates"], (
        f"B records no Data Source and got no card: {[f.get('type') for f in frames]}")
    assert oc.prompts == [], "the agent was asked to build B against a store B does not record"
    assert _sources(root, app_a) == ["ds-dwh"]
    assert _sources(root, app_b) == []


@SELECTED
def test_b_is_not_handed_a_store_only_a_records(tmp_path: Path, monkeypatch, selected):
    orch, oc, resources, client, root, _app_a, app_b = _project(tmp_path, monkeypatch, selected)
    tid = orch.create_thread()["id"]

    _build_on_b(client, app_b, TABLE_PROMPT, tid)

    assert oc.prompts, "the turn never reached the agent, so nothing here was checked"
    assert oc.replies, "the turn's prompt carried no read token"
    assert resources.asked == [], (
        f"B's turn read A's store with {selected} selected: {oc.replies[0][:200]}")
    assert all(brand.text("from {project} resources") in said for said in oc.replies)
    assert _sources(root, app_b) == []
