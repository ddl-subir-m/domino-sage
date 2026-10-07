"""A Chat-to-Build handoff always leaves the person a way forward (#661).

Four dead ends, measured in one dogfood conversation:

1. A build that failed before it changed the app archived the plan anyway, so Approve & build
   answered "That plan was already built" for an app that had never been built.
2. A drafted plan was served from the handoff row for good. Cancel on the sheet reset nothing, the
   "Write a plan" offer was gone, and writing the plan again returned the same plan in 0.09 s.
3. The sheet named the new app after the plan's heading with no way to say otherwise.
4. Every handoff from the conversation reused the first plan document, `planId: "001"`, even when
   each went into a different Built App.

Asserted on the public surface: the orchestrator's doors, the stored handoff rows, and the apps the
rail lists.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn, execution_plan


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word for any routed request: the Chat/Build classifier is its only caller."""

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


_BOARD = execution_plan("Pipeline Signal Board", "A pipeline signal board.", "Signal table")
_ROOM = execution_plan("Signal Room", "A signal room.", "Signal feed")
_NOTHING_EXTRA = {"resources": False, "artifacts": False, "transcript": False}


def _orch(tmp: Path, turns: list[Turn]):
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building this app\n\nSage's rules go here.\n")
    root = tmp / "mnt" / "code"
    oc = FakeOpenCode(root, [Turn(text="A dashboard, then."), *turns])
    orch = Orchestrator(workspace_dir=root, template=t, gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "an app called Signal Room for the pipeline signals"))
    return orch, root, tid


def _handoffs(orch: Orchestrator, tid: str) -> list[dict]:
    from sage.workspace.threads import ThreadStore
    return ThreadStore(orch._chat_project().record.path).read_handoffs(tid)


# ---- 1. a build that fails before it changes the app leaves the plan approvable ----------------


def test_a_build_that_drops_before_it_changes_the_app_can_be_approved_again(tmp_path: Path):
    """The stream ends mid-turn — the tab closed, the proxy cut it, the reply could not be read
    and the browser gave up — after the implement prompt went out and before any file changed.
    Nothing was built, so Approve & build has to build, not say the plan was already built."""
    orch, root, tid = _orch(tmp_path, [
        Turn(text=_BOARD),
        Turn(text="Reading the plan first."),
        Turn(writes={"src/App.tsx": "// the signal table\n"}),
    ])
    plan_id = orch.draft_handoff_plan(tid)["handoff"]["planId"]
    orch.confirm_handoff(tid, _NOTHING_EXTRA)
    app_id = orch.project(start_preview=False).workspace.app_id

    stream = orch.approve_stream(conversation=tid, plan_id=plan_id)
    for event in stream:
        if event.get("type") == "agent":
            stream.close()
            break

    events = list(orch.approve_stream(conversation=tid, plan_id=plan_id))
    done = next(e for e in reversed(events) if e["type"] == "done")
    assert done["decision"] != "no plan to approve"
    assert done["ok"] is True
    assert (root / "apps" / app_id / "src" / "App.tsx").read_text() == "// the signal table\n"


# ---- 2. Cancel keeps the offer, and the next plan is a new one ---------------------------------


def test_cancelling_the_sheet_puts_the_write_a_plan_offer_back(tmp_path: Path):
    """`suggested` is the state whose callout carries Write a plan (handoff.md §6). A plan the
    person cancelled is not the handoff's plan any more, so the row stops naming it."""
    orch, _root, tid = _orch(tmp_path, [Turn(text=_BOARD)])
    orch.draft_handoff_plan(tid)

    orch.patch_thread(tid, {"handoff": "cancel"})

    row = orch.get_thread(tid)["handoff"]
    assert row["status"] == "suggested"
    assert "planId" not in row


def test_writing_the_plan_again_after_cancel_drafts_a_new_plan(tmp_path: Path):
    orch, _root, tid = _orch(tmp_path, [Turn(text=_BOARD), Turn(text=_ROOM)])
    first = orch.draft_handoff_plan(tid)
    orch.patch_thread(tid, {"handoff": "cancel"})

    second = orch.draft_handoff_plan(tid)

    assert second["plan"].startswith("# Signal Room")
    assert second["handoff"]["planId"] != first["handoff"]["planId"]


def test_an_explicit_redraft_regenerates_the_plan(tmp_path: Path):
    orch, _root, tid = _orch(tmp_path, [Turn(text=_BOARD), Turn(text=_ROOM)])
    first = orch.draft_handoff_plan(tid)

    second = orch.draft_handoff_plan(tid, redraft=True)

    assert second["plan"].startswith("# Signal Room")
    assert second["handoff"]["planId"] != first["handoff"]["planId"]


def test_the_plan_route_carries_the_redraft(tmp_path: Path, monkeypatch):
    """The wire between the sheet's button and the rule. A flag the route dropped would serve the
    cached plan and look exactly like a redraft that happened to write the same thing."""
    from fastapi.testclient import TestClient

    from sage.orchestrator import app as routes

    orch, _root, tid = _orch(tmp_path, [Turn(text=_BOARD), Turn(text=_ROOM)])
    monkeypatch.setattr(routes, "orchestrator", orch)
    with TestClient(routes.control_app) as client:
        client.post(f"/api/threads/{tid}/handoff/plan")
        again = client.post(f"/api/threads/{tid}/handoff/plan", json={"redraft": True})

    assert again.json()["plan"].startswith("# Signal Room")


# ---- 3. the person names the new app -----------------------------------------------------------


def test_the_sheet_offers_the_plans_own_heading_as_the_name(tmp_path: Path):
    orch, _root, tid = _orch(tmp_path, [Turn(text=_BOARD)])

    assert orch.draft_handoff_plan(tid)["appName"] == "Pipeline Signal Board"


def test_the_confirm_names_the_new_app_what_the_sheet_says(tmp_path: Path):
    orch, _root, tid = _orch(tmp_path, [Turn(text=_BOARD)])
    orch.draft_handoff_plan(tid)

    result = orch.confirm_handoff(tid, _NOTHING_EXTRA, {"name": "Signal Room"})

    names = {row["id"]: row["name"] for row in orch.list_apps()}
    assert names[result["handoff"]["appId"]] == "Signal Room"


def test_the_confirm_renames_the_plan_to_what_the_sheet_says(tmp_path: Path):
    """#670: the app took the sheet's name and the plan kept the planner's, so the two disagreed."""
    orch, _root, tid = _orch(tmp_path, [Turn(text=_BOARD)])
    plan_id = orch.draft_handoff_plan(tid)["handoff"]["planId"]

    orch.confirm_handoff(tid, _NOTHING_EXTRA, {"name": "Signal Room"})

    doc = orch.read_plan_doc(plan_id)
    assert doc["title"] == "Signal Room"
    assert doc["markdown"].startswith("# Signal Room\n")
    assert "# Pipeline Signal Board" not in doc["markdown"]


def test_the_confirm_gives_a_plan_with_no_heading_the_sheets_name(tmp_path: Path):
    orch, _root, tid = _orch(tmp_path, [Turn(text=execution_plan(
        "Pipeline Signal Board", "A pipeline signal board.", "Signal table", include_title=False))])
    plan_id = orch.draft_handoff_plan(tid)["handoff"]["planId"]

    orch.confirm_handoff(tid, _NOTHING_EXTRA, {"name": "Signal Room"})

    doc = orch.read_plan_doc(plan_id)
    assert doc["title"] == "Signal Room"
    assert doc["markdown"].startswith("# Signal Room\n\nA pipeline signal board.")


def test_a_plan_built_into_an_existing_app_keeps_its_own_name(tmp_path: Path):
    """The sheet names only a NEW app; an existing one keeps its name, so the plan keeps its own."""
    orch, _root, tid = _orch(tmp_path, [Turn(text=_BOARD), Turn(text=_ROOM)])
    orch.draft_handoff_plan(tid)
    first = orch.confirm_handoff(tid, _NOTHING_EXTRA)["handoff"]["appId"]
    plan_id = orch.draft_handoff_plan(tid)["handoff"]["planId"]

    orch.confirm_handoff(tid, _NOTHING_EXTRA, {"appId": first, "name": "Something Else"})

    doc = orch.read_plan_doc(plan_id)
    assert doc["title"] == "Signal Room"
    assert doc["markdown"].startswith("# Signal Room\n")


# ---- 4. each handoff has its own plan ----------------------------------------------------------


def test_each_handoff_from_one_conversation_drafts_its_own_plan(tmp_path: Path):
    """A bound handoff is finished; the next one is a new handoff with a new plan document, not the
    last app's plan offered again."""
    orch, _root, tid = _orch(tmp_path, [Turn(text=_BOARD), Turn(text=_ROOM)])
    orch.draft_handoff_plan(tid)
    orch.confirm_handoff(tid, _NOTHING_EXTRA)

    second = orch.draft_handoff_plan(tid)
    orch.confirm_handoff(tid, _NOTHING_EXTRA)

    assert second["plan"].startswith("# Signal Room")
    plan_ids = [row.get("planId") for row in _handoffs(orch, tid)]
    assert len(plan_ids) == 2 and plan_ids[0] != plan_ids[1]
