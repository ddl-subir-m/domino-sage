"""A plan put away in Chat comes back out when its handoff is confirmed (#170).

#167 gave the plan document an `archived` flag with one refusal on the way in: a document
`.sage/plan.md` was written from right now cannot be hidden, because hiding it would leave an
Approve card pointing at a document the Plans group no longer lists. That guard is
one-directional — it stops a live plan becoming archived, and nothing stopped an archived plan
becoming live from the other side, where confirming a handoff writes `plan.md` straight from the
document.

Confirming unarchives rather than refuses. Pressing Confirm is an unambiguous act of wanting this
plan; refusing would only teach the person to press Unarchive and then Confirm again, reaching the
same state one click later.

`test_plan_origin.py` is the prior art for the harness.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator, PlanArchiveRefused
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word for any routed request: the Chat/Build classifier is its only caller."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


_DESK = ("A desk exposure dashboard.\n\n"
         "## Plan\n"
         "1. **A desk table** — Show it.\n\n"
         "## Open questions\n"
         "- None, ready to build.\n")
_NOTHING_EXTRA = {"resources": False, "artifacts": False, "transcript": False}


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


def _orch(tmp: Path):
    root = tmp / "mnt" / "code"
    oc = FakeOpenCode(root, [Turn(text="A dashboard, then."), Turn(text=_DESK)])
    orch = Orchestrator(workspace_dir=root, template=_template(tmp), gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    return orch, root


def _drafted(orch) -> tuple[str, str]:
    """A Chat-drafted plan document waiting on the sheet, with no app behind it yet."""
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "build me a desk exposure dashboard"))
    orch.draft_handoff_plan(tid)
    return tid, orch.get_thread(tid)["planId"]


def test_confirming_a_handoff_takes_the_archived_plan_back_out(tmp_path: Path):
    """The hole itself. Draft in Chat, put the document away, confirm — and the document the
    builder is now working from is one the Plans group lists."""
    orch, root = _orch(tmp_path)
    tid, plan_id = _drafted(orch)
    orch.archive_plan_doc(plan_id, True)
    assert orch.read_plan_doc(plan_id)["archived"] is True

    orch.confirm_handoff(tid, _NOTHING_EXTRA)

    assert orch.read_plan_doc(plan_id)["archived"] is False
    app_id = orch.project(start_preview=False).workspace.app_id
    assert (root / "apps" / app_id / ".sage" / "plan.md").read_text().startswith("A desk exposure")


def test_the_plan_the_builder_holds_is_still_the_one_edits_reach(tmp_path: Path):
    """Why the flag matters rather than the flag itself. An archived document drops out of the
    app's plan list, so while it was hidden the builder's copy was a plan no edit could reach —
    the person would fix the plan on screen and watch the build ignore it. Taken back out, the
    document is the app's again and the two stay one plan."""
    orch, root = _orch(tmp_path)
    tid, plan_id = _drafted(orch)
    orch.archive_plan_doc(plan_id, True)
    orch.confirm_handoff(tid, _NOTHING_EXTRA)
    app_id = orch.project(start_preview=False).workspace.app_id
    assert orch.project(start_preview=False).workspace.live_plan_doc_id() == plan_id

    orch.patch_plan_doc(plan_id, {"summary": "A desk exposure dashboard, sorted."})

    assert "sorted" in (root / "apps" / app_id / ".sage" / "plan.md").read_text()
    # And #167's refusal reads the same document, so the plan awaiting approval cannot be put
    # away again behind the card asking about it.
    with pytest.raises(PlanArchiveRefused):
        orch.archive_plan_doc(plan_id, True)


def test_a_plan_nobody_put_away_crosses_untouched(tmp_path: Path):
    """The ordinary handoff, which has no flag to clear and must not grow one."""
    orch, _root = _orch(tmp_path)
    tid, plan_id = _drafted(orch)

    orch.confirm_handoff(tid, _NOTHING_EXTRA)

    doc = orch.read_plan_doc(plan_id)
    assert doc["archived"] is False
    assert doc["appId"] == orch.project(start_preview=False).workspace.app_id
