"""An edit reaches `.sage/plan.md` from the document the plan IS live from (#177).

`patch_plan_doc` used to decide that by asking which document was NEWEST for the app. The app
records which one is live (`Workspace.write_plan`), and the two are a different question with a
different answer (#59) — so once they diverged the edit went to the wrong plan in both directions
at once: an edit to the plan being built never reached the builder, and an edit to a plan nobody
was building overwrote the one somebody was.

Reached here through the doors a person actually presses, not by writing `livePlanDocId` by hand.
A plan drafted in Chat becomes live only when its handoff is confirmed, which can be long after a
Build conversation wrote a newer document into the same app — the divergence `write_plan`'s
docstring has described from the start.

`test_a_confirmed_handoff_takes_its_plan_back_out.py` is the prior art for the harness; the
ordinary single-document cases live in `test_plan_doc.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """The scope classifier's only answer, switched by the test between its two conversations."""

    def __init__(self) -> None:
        self.word = "CHAT"

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.word}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


_CHAT_PLAN = ("A desk exposure dashboard.\n\n"
              "## Plan\n"
              "1. **A desk table** — Show notional by desk.\n\n"
              "## Open questions\n"
              "- None, ready to build.\n")
_BUILD_PLAN = ("A limits monitor.\n\n"
               "## Plan\n"
               "1. **A limits table** — Show desks over limit.\n")
_NOTHING_EXTRA = {"resources": False, "artifacts": False, "transcript": False}
BUILD_CONVERSATION = "conv_build"


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
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building this app\n\nSage's rules go here.\n")
    return t


def _diverged(tmp: Path):
    """One app, two documents, and the OLDER one is the live plan.

    Both halves are real doors. The Build conversation plans and builds, which leaves its document
    newest for the app and `plan.md` archived. The Chat plan was drafted before either, and only
    becomes live when its handoff is confirmed into that same app — so `live_plan_doc_id` names the
    older document while the document list still returns the newer one first.
    """
    root = tmp / "mnt" / "code"
    gateway = ScriptedGateway()
    oc = FakeOpenCode(root, [
        Turn(text="A dashboard, then."),                    # 1. the Chat reply
        Turn(text=_CHAT_PLAN),                              # 2. the plan drafted on the sheet
        Turn(text=_BUILD_PLAN),                             # 3. the Build gate's plan
        Turn(writes={"src/App.tsx": "// the limits table\n"}),   # 4. the approved build
    ])
    orch = Orchestrator(workspace_dir=root, template=_template(tmp), gateway=gateway,
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)

    thread = orch.create_thread()["id"]
    list(orch.chat_stream(thread, "build me a desk exposure dashboard"))
    orch.draft_handoff_plan(thread)
    chat_plan_id = orch.get_thread(thread)["planId"]

    gateway.word = "BUILD"
    list(orch.build_stream("build me a limits monitor", conversation=BUILD_CONVERSATION))
    list(orch.approve_stream(conversation=BUILD_CONVERSATION))
    app_id = orch.project(start_preview=False).workspace.app_id
    build_plan_id = next(d["id"] for d in orch.list_plan_docs() if d["id"] != chat_plan_id)

    # Into the app that already exists, which is the choice the sheet offers.
    orch.confirm_handoff(thread, _NOTHING_EXTRA, {"appId": app_id})
    return orch, root / "apps" / app_id / ".sage" / "plan.md", chat_plan_id, build_plan_id


def _live_and_newest_disagree(orch, live_id: str, newest_id: str) -> None:
    """The premise every assertion below rests on — worth failing on its own if it stops holding."""
    project = orch.project(start_preview=False)
    assert project.workspace.live_plan_doc_id() == live_id
    assert next(d["id"] for d in orch.list_plan_docs()) == newest_id
    assert live_id != newest_id


def test_an_edit_to_the_live_plan_reaches_the_builder_though_it_is_not_the_newest(tmp_path: Path):
    """The half that lost a person's words. The plan being built is the older document, and the
    edit has to reach the copy the builder reads — otherwise the build runs the plan as it was and
    the rail's pin goes on counting steps that are no longer there."""
    orch, plan_md, chat_plan_id, build_plan_id = _diverged(tmp_path)
    _live_and_newest_disagree(orch, live_id=chat_plan_id, newest_id=build_plan_id)

    orch.patch_plan_doc(chat_plan_id, {"summary": "A desk exposure dashboard, sorted by date."})

    assert "sorted by date" in plan_md.read_text()


def test_an_edit_to_the_newer_plan_leaves_the_one_being_built_alone(tmp_path: Path):
    """The other half, and the worse one. The newest document is not the live plan, so editing it
    must not replace the text of the plan the app is actually being built from."""
    orch, plan_md, chat_plan_id, build_plan_id = _diverged(tmp_path)
    _live_and_newest_disagree(orch, live_id=chat_plan_id, newest_id=build_plan_id)
    before = plan_md.read_text()

    orch.patch_plan_doc(build_plan_id, {"summary": "A limits monitor, watched hourly."})

    assert plan_md.read_text() == before
    assert "watched hourly" not in plan_md.read_text()
    # And the app still knows which plan it is holding: an edit is not a handoff.
    assert orch.project(start_preview=False).workspace.live_plan_doc_id() == chat_plan_id
