"""A plan remembers the Conversation that produced it, whichever way it was started (#54).

There are two ways a plan gets written: the Chat handoff drafts one before any app exists, and the
gate drafts one inside a Built App on its first turn. Both stamp the plan document, and the two
stamps used to be exactly inverted — the handoff recorded the Conversation and no app, the gate
recorded the app and no Conversation. So a plan born in Chat could link back to the discussion and
a plan born in Build could not, and the Conversation's own plan reference, which reads the origin,
was empty for every plan the gate ever wrote.

These tests hold both ends of the back-link: what each path records, and that the Conversation can
find its way back to the plan from it. The live plan file is a separate thing and stays separate —
one per Built App — which the last test here is about.
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
    """One scripted word for any routed request: the Chat/Build classifier is its only caller."""

    def __init__(self, verdict: str = "CHAT") -> None:
        self.verdict = verdict

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _plan(title: str, step: str) -> str:
    return (f"{title}\n\n"
            "## Plan\n"
            f"1. **{step}** — Show it.\n\n"
            "## Open questions\n"
            "- None, ready to build.\n")


_DESK = _plan("A desk exposure dashboard.", "Desk table")
_BURNDOWN = _plan("A burndown chart.", "Burndown")
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


def _orch(tmp: Path, turns: list[Turn] | None = None, *, verdict: str = "CHAT"):
    root = tmp / "mnt" / "code"
    oc = FakeOpenCode(root, turns or [])
    orch = Orchestrator(workspace_dir=root, template=_template(tmp), gateway=ScriptedGateway(verdict),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    return orch, oc, root


def _gate_in_build(orch, ask: str, conversation: str) -> list[dict]:
    """A first turn on a fresh Built App, driven from a Build conversation the way the Workbench
    drives it: typing in Build opens a Thread first and every turn names it."""
    return list(orch.build_stream(ask, conversation=conversation))


# ---- what each path records ------------------------------------------------------------------


def test_a_plan_the_gate_wrote_in_build_records_the_conversation_that_produced_it(tmp_path: Path):
    """Criterion 1, and the whole ticket. The gate has always known which app it was standing in;
    it also knows which Build conversation the turn was pinned to, and now says so."""
    orch, _oc, _root = _orch(tmp_path, [Turn(text=_DESK)])
    conversation = orch.create_thread()["id"]

    events = _gate_in_build(orch, "build me a desk exposure dashboard", conversation)

    assert next(e for e in events if e["type"] == "done")["decision"] == "awaiting approval"
    plan_id = next(e for e in events if e["type"] == "plan-proposed")["planId"]
    doc = orch.read_plan_doc(plan_id)
    assert doc["originThreadId"] == conversation
    # Criterion 5: the app half of the back-link is untouched by the conversation half arriving.
    assert doc["appId"] == orch.project(start_preview=False).workspace.app_id


def test_a_plan_the_chat_handoff_wrote_still_records_its_conversation(tmp_path: Path):
    """Criterion 2. The path that was already right stays right, and still records no app: the app
    does not exist until the handoff is confirmed, and confirming is what stamps it."""
    orch, _oc, _root = _orch(tmp_path, [Turn(text="A dashboard, then."), Turn(text=_DESK)])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "build me a desk exposure dashboard"))

    orch.draft_handoff_plan(tid)

    assert orch.read_plan_doc("001")["originThreadId"] == tid
    assert orch.read_plan_doc("001")["appId"] == ""
    orch.confirm_handoff(tid, _NOTHING_EXTRA)
    assert orch.read_plan_doc("001")["originThreadId"] == tid
    assert orch.read_plan_doc("001")["appId"] == orch.project(start_preview=False).workspace.app_id


def test_a_build_turn_with_no_conversation_behind_it_still_writes_its_plan(tmp_path: Path):
    """Criterion 8's other half. The Workbench always names a conversation, but the CLI path does
    not, and a plan with no origin to record is a plan without a way back rather than a failure."""
    orch, _oc, _root = _orch(tmp_path, [Turn(text=_DESK)])
    orch.project(start_preview=False)

    events = list(orch.build_stream("build me a desk exposure dashboard"))

    plan_id = next(e for e in events if e["type"] == "plan-proposed")["planId"]
    assert orch.read_plan_doc(plan_id)["originThreadId"] == ""


# ---- the Conversation's way back -------------------------------------------------------------


def test_the_conversation_finds_the_plan_the_gate_wrote_in_it(tmp_path: Path):
    """Criterion 3. This reference is read off the plan's recorded origin, so it answers for a plan
    that never went through a handoff — which is every plan the gate has ever written."""
    orch, _oc, _root = _orch(tmp_path, [Turn(text=_DESK)])
    conversation = orch.create_thread()["id"]
    assert orch.get_thread(conversation)["planId"] == ""

    _gate_in_build(orch, "build me a desk exposure dashboard", conversation)

    assert orch.get_thread(conversation)["planId"] == "001"
    # And it is the Conversation's own plan, not any plan: a second Conversation that produced
    # nothing points at nothing.
    assert orch.get_thread(orch.create_thread()["id"])["planId"] == ""


def test_a_conversation_points_at_the_newest_plan_it_produced(tmp_path: Path):
    """One Conversation may produce several — a Thread that hands off twice, a Build conversation
    the gate fires in again for a second app — and the plan card is about the newest."""
    orch, _oc, _root = _orch(tmp_path, [Turn(text=_DESK), Turn(text=_BURNDOWN)])
    conversation = orch.create_thread()["id"]
    _gate_in_build(orch, "build me a desk exposure dashboard", conversation)
    orch.create_app()

    _gate_in_build(orch, "now build me a burndown chart", conversation)

    assert [d["id"] for d in orch.list_plan_docs()] == ["002", "001"]
    assert orch.get_thread(conversation)["planId"] == "002"
    assert orch.read_plan_doc("002")["originThreadId"] == conversation


def test_a_plan_with_no_recorded_origin_still_loads(tmp_path: Path):
    """Criterion 8. The blank document the plan list hands you was written in no conversation at
    all. It reads back like any other; it simply has no way back to offer."""
    orch, _oc, _root = _orch(tmp_path)

    doc = orch.create_plan_doc({"title": "Something I typed out myself"})

    assert doc["originThreadId"] == ""
    assert doc["appId"] == ""
    assert orch.read_plan_doc(doc["id"])["title"] == "Something I typed out myself"


# ---- the live plan file is a different thing -------------------------------------------------


def test_the_live_plan_file_stays_one_per_built_app(tmp_path: Path):
    """Criterion 7, and the boundary this ticket must not cross. The document remembers a
    Conversation; the file the builder consumes belongs to the app it is building (#67). One
    Conversation driving two apps writes two files, one under each app, and none beside the
    Thread."""
    orch, _oc, root = _orch(tmp_path, [Turn(text=_DESK), Turn(text=_BURNDOWN)])
    conversation = orch.create_thread()["id"]
    _gate_in_build(orch, "build me a desk exposure dashboard", conversation)
    first = orch.project(start_preview=False).workspace.app_id
    second = orch.create_app()["id"]

    _gate_in_build(orch, "now build me a burndown chart", conversation)

    assert (root / "apps" / first / ".sage" / "plan.md").read_text().startswith("A desk exposure")
    assert (root / "apps" / second / ".sage" / "plan.md").read_text().startswith("A burndown")
    assert not (root / ".sage" / "threads" / conversation / "plan.md").exists()
    assert not (root / ".sage" / "plan.md").exists()
