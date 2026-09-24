"""Approved plans cross into one clean, durable implementation session (#526)."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from sage import build_diagnostics
from sage.build_policy import BuildPolicy
from sage.feedback.runner import FeedbackReport
from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

DRAFT = """# Orders Dashboard

A dashboard for reviewing orders.

## Problem & outcome
Orders are hard to review; the app makes them visible.

## Who uses this
The operations analyst.

## What it does
- Shows orders in a table.

## Screens
- **Orders table** — Shows the order rows.

## Done when
- The preview shows the orders table.

## Plan

### 1. Add the table
- Files — src/App.tsx
- Do — Add the draft orders table.
- Done when — The preview shows the table.
"""
EDITED = DRAFT.replace("draft orders table", "edited approved orders table")
REQUEST = "Build an orders dashboard from the exact source request."
CONVERSATION = "thr_fresh_526"


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def __init__(self) -> None:
        self.word = "BUILD"

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.word}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class RecordingOpenCode(FakeOpenCode):
    def __init__(self, workspace: Path, turns: list[Turn]) -> None:
        super().__init__(workspace, turns)
        self.orch: Orchestrator | None = None
        self.active_at_dispatch: list[str | None] = []
        self.intents = []
        self.interrupted_sessions: list[str] = []

    def send_prompt(self, session_id: str, text: str, model: dict | None = None,
                    agent: str | None = None, attachments: list[dict] | None = None,
                    chat: bool = False) -> None:
        if self.orch is not None:
            project = self.orch.project(start_preview=False)
            self.active_at_dispatch.append(project.active_session_id)
            self.intents.append(project.active_build_intent)
        super().send_prompt(session_id, text, model, agent, attachments, chat)

    def interrupt(self, session_id: str) -> None:
        self.interrupted_sessions.append(session_id)
        super().interrupt(session_id)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def _build(
    tmp: Path,
    turns: list[Turn],
    *,
    build_policy: BuildPolicy | None = None,
) -> tuple[Orchestrator, RecordingOpenCode]:
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text(
        "export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    (template / "AGENTS.md").write_text("# Build this app\n")
    workspace = tmp / "mnt" / "code"
    opencode = RecordingOpenCode(workspace, turns)
    orch = Orchestrator(
        workspace_dir=workspace, template=template, gateway=ScriptedGateway(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage", feedback=OkFeedback(), opencode_client=opencode,
        build_policy=build_policy,
    )
    opencode.orch = orch
    return orch, opencode


def _plan(orch: Orchestrator) -> None:
    list(orch.build_stream(REQUEST, conversation=CONVERSATION))


def _done(events: list[dict]) -> dict:
    return next(event for event in reversed(events) if event["type"] == "done")


def test_approval_replaces_planning_once_and_reuses_the_new_session_for_recovery_and_followup(
        tmp_path: Path):
    orch, opencode = _build(tmp_path, [
        Turn(text=DRAFT),
        Turn(text="I only inspected the app."),
        Turn(writes={"src/App.tsx": "// built from the edited plan\n"}),
        Turn(writes={"src/App.tsx": "// direct follow-up\n"}),
    ])
    _plan(orch)
    project = orch.project(start_preview=False)
    planning_session = opencode.prompts[0]["session"]

    events = list(orch.approve_stream(
        conversation=CONVERSATION, plan_edits=EDITED, answers="Use compact rows."))

    assert _done(events)["ok"] is True
    implementation_session = opencode.sessions[-1]["id"]
    assert implementation_session != planning_session
    assert len(opencode.sessions) == 2
    approval_prompts = opencode.prompts[1:3]
    assert {prompt["session"] for prompt in approval_prompts} == {implementation_session}
    assert planning_session not in {prompt["session"] for prompt in approval_prompts}
    assert opencode.active_at_dispatch[1:3] == [implementation_session, implementation_session]
    assert project.session_id == implementation_session
    assert project.record.read_session_id(
        CONVERSATION, project.workspace.app_id) == implementation_session
    first_intent = opencode.intents[1]
    assert first_intent is opencode.intents[2]
    assert first_intent.source_requests == (REQUEST,)
    assert "edited approved orders table" in first_intent.authoritative_plan
    assert "draft orders table" not in first_intent.authoritative_plan
    assert first_intent.answers == "Use compact rows."

    project.control.set_mode(Mode.IMPLEMENT)
    list(orch.build_stream("Add a direct follow-up.", conversation=CONVERSATION))
    assert len(opencode.sessions) == 2
    assert opencode.prompts[-1]["session"] == implementation_session


def test_retry_of_a_live_approved_plan_gets_another_clean_session(tmp_path: Path):
    orch, opencode = _build(tmp_path, [
        Turn(text=DRAFT), Turn(text="No edit."),
        Turn(writes={"src/App.tsx": "// retry succeeded\n"}),
    ], build_policy=BuildPolicy(no_edit_nudge_limit=0))
    _plan(orch)
    first = list(orch.approve_stream(conversation=CONVERSATION))
    assert _done(first)["ok"] is False
    assert orch.project(start_preview=False).workspace.read_plan()
    first_clean = opencode.prompts[1]["session"]

    second = list(orch.build_stream("try again", conversation=CONVERSATION))

    assert _done(second)["ok"] is True
    assert len(opencode.sessions) == 3
    assert opencode.prompts[-1]["session"] not in {opencode.prompts[0]["session"], first_clean}


def test_confirmed_chat_handoff_approval_starts_one_clean_build_session(tmp_path: Path):
    chat_request = "Help me reason about an orders dashboard from this Chat request."
    orch, opencode = _build(tmp_path, [
        Turn(text="I can prepare that."), Turn(text=DRAFT),
        Turn(writes={"src/App.tsx": "// built from Chat\n"}),
    ])
    thread = orch.create_thread()["id"]
    orch._chat_project().shim.gateway.word = "CHAT"
    list(orch.chat_stream(thread, chat_request))
    orch.draft_handoff_plan(thread)
    orch.confirm_handoff(
        thread, {"resources": False, "artifacts": False, "transcript": True})
    sessions_before = len(opencode.sessions)
    prompts_before = len(opencode.prompts)

    events = list(orch.approve_stream(conversation=thread))

    assert _done(events)["ok"] is True
    assert len(opencode.sessions) == sessions_before + 1
    assert {prompt["session"] for prompt in opencode.prompts[prompts_before:]} == {
        opencode.sessions[-1]["id"]
    }
    intent = opencode.intents[-1]
    assert intent.kind == "approved_plan"
    assert intent.source_requests == (chat_request,)
    assert chat_request in intent.handoff_note


@pytest.mark.parametrize("failure", ["create", "persist"])
def test_session_setup_failure_sends_nothing_keeps_the_old_selection_and_plan(
        tmp_path: Path, monkeypatch, failure: str):
    orch, opencode = _build(tmp_path, [Turn(text=DRAFT)])
    _plan(orch)
    project = orch.project(start_preview=False)
    old_id = project.session_id
    assert project.record.read_session_id(CONVERSATION, project.workspace.app_id) == old_id

    if failure == "create":
        monkeypatch.setattr(opencode, "create_session",
                            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("create failed")))
    else:
        record_type = type(project.record)
        write_session_id = record_type.write_session_id

        def fail_this_record(record, *args, **kwargs):
            if record is project.record:
                raise OSError("write failed")
            return write_session_id(record, *args, **kwargs)

        monkeypatch.setattr(record_type, "write_session_id", fail_this_record)

    events = list(orch.approve_stream(conversation=CONVERSATION))

    assert [prompt["session"] for prompt in opencode.prompts] == [old_id]
    assert project.session_id == old_id
    assert project.record.read_session_id(CONVERSATION, project.workspace.app_id) == old_id
    assert project.workspace.read_plan()
    assert _done(events) == {"type": "done", "ok": False,
                             "decision": "implementation session unavailable"}
    errors = [event for event in events if event["type"] == "error"]
    assert [event["message"] for event in errors] == [
        ("Sage could not start a clean implementation session. "
         "The approved plan is still here. Try again.")
    ]


def test_disconnect_after_persistence_keeps_the_new_selection_and_live_plan(tmp_path: Path):
    orch, opencode = _build(tmp_path, [Turn(text=DRAFT)])
    _plan(orch)
    planning_session = opencode.prompts[0]["session"]
    stream = orch.approve_stream(conversation=CONVERSATION)
    for event in stream:
        if event["type"] == "turn":
            break
    project = orch.project(start_preview=False)
    new_id = project.session_id
    assert new_id != planning_session
    assert project.active_session_id == new_id
    stream.close()

    assert [prompt["session"] for prompt in opencode.prompts] == [planning_session]
    assert project.session_id == new_id
    assert project.active_session_id is None
    assert project.record.read_session_id(CONVERSATION, project.workspace.app_id) == new_id
    assert project.workspace.read_plan()


def test_restart_recovers_the_clean_session_for_a_direct_followup(tmp_path: Path):
    orch, opencode = _build(tmp_path, [
        Turn(text=DRAFT), Turn(writes={"src/App.tsx": "// approved build\n"}),
    ])
    _plan(orch)
    list(orch.approve_stream(conversation=CONVERSATION))
    implementation_session = opencode.prompts[-1]["session"]
    sessions_before = len(opencode.sessions)
    opencode.turns.append(Turn(writes={"src/App.tsx": "// after restart\n"}))

    restarted = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code", template=tmp_path / "template",
        gateway=ScriptedGateway(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage", feedback=OkFeedback(), opencode_client=opencode,
    )
    opencode.orch = restarted
    restarted.project(start_preview=False).control.set_mode(Mode.IMPLEMENT)
    list(restarted.build_stream("Add the restart follow-up.", conversation=CONVERSATION))

    assert len(opencode.sessions) == sessions_before
    assert opencode.prompts[-1]["session"] == implementation_session


def test_stop_targets_the_new_implementation_session(tmp_path: Path):
    orch, opencode = _build(tmp_path, [Turn(text=DRAFT), Turn()])
    _plan(orch)
    planning_session = opencode.prompts[0]["session"]
    send_prompt = opencode.send_prompt

    def stop_after_send(*args, **kwargs):
        send_prompt(*args, **kwargs)
        assert orch.stop_build() is True

    opencode.send_prompt = stop_after_send
    list(orch.approve_stream(conversation=CONVERSATION))

    implementation_session = opencode.prompts[1]["session"]
    assert implementation_session != planning_session
    assert opencode.interrupted_sessions
    assert set(opencode.interrupted_sessions) == {implementation_session}
    assert orch.project(start_preview=False).session_id == implementation_session


def test_diagnostics_expose_only_safe_session_state(tmp_path: Path, caplog):
    private = "PRIVATE_SESSION_SENTINEL_526"
    orch, _opencode = _build(tmp_path, [
        Turn(text=DRAFT.replace("orders", private)),
        Turn(writes={"src/App.tsx": "// built\n"}),
    ])
    list(orch.build_stream(REQUEST.replace("orders", private), conversation=CONVERSATION))
    list(orch.approve_stream(conversation=CONVERSATION, answers=private))

    project = orch.project(start_preview=False)
    store = build_diagnostics.Store(project.record.path)
    summary = next(row for row in reversed(store.list(project.workspace.app_id))
                   if row["turn"]["kind"] == "approve")
    approval = store.get(summary["turn"]["turnId"], project.workspace.app_id, CONVERSATION)
    assert approval["implementationSession"] == {
        "fresh": True, "reason": "approved_plan", "created": True,
        "persisted": True, "dispatchStarted": True,
    }
    encoded = json.dumps(approval)
    assert private not in encoded
    assert private not in caplog.text


def test_stale_persisted_session_reports_a_fresh_replacement_on_the_standard_reused_path(
    tmp_path: Path,
):
    orch, opencode = _build(tmp_path, [
        Turn(writes={"src/App.tsx": "// direct build after stale recovery\n"}),
    ])
    project = orch.project(start_preview=False)
    project.workspace.mark_built()
    project.control.set_mode(Mode.IMPLEMENT)
    stale_id = "stale-persisted-session"
    project.record.write_session_id(stale_id, CONVERSATION, project.workspace.app_id)
    messages = opencode.messages

    def reject_stale(session_id: str, *, limit: int | None = None):
        if session_id == stale_id:
            request = httpx.Request("GET", "http://127.0.0.1/session/stale/message")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("404", request=request, response=response)
        return messages(session_id, limit=limit)

    opencode.messages = reject_stale

    list(orch.build_stream("Add the direct change.", conversation=CONVERSATION))

    assert len(opencode.sessions) == 1
    assert project.session_id == opencode.sessions[0]["id"]
    assert project.record.read_session_id(
        CONVERSATION, project.workspace.app_id) == opencode.sessions[0]["id"]
    store = build_diagnostics.Store(project.record.path)
    summary = next(row for row in store.list(project.workspace.app_id)
                   if row["turn"]["kind"] == "build")
    record = store.get(summary["turn"]["turnId"], project.workspace.app_id, CONVERSATION)
    # `reason` names the standard direct path. The booleans say that validation replaced its stale
    # selected ID, so `reused` plus fresh/created/persisted is deliberate.
    assert record["implementationSession"] == {
        "fresh": True, "reason": "reused", "created": True,
        "persisted": True, "dispatchStarted": True,
    }


@pytest.mark.parametrize(
    ("mode", "prompt", "answer"),
    [
        (Mode.PLAN, "Plan an orders dashboard.", DRAFT),
        (Mode.ASK, "What should an orders dashboard show?", "It should show orders."),
        (Mode.AUTO, "Give me an architecture for an orders dashboard.",
         ("A design.\n\n## Diagram\n```mermaid\nflowchart TD\n  A --> B\n```\n\n"
          "## Components\n- **Table** — holds rows.\n")),
    ],
)
def test_non_implementation_turns_omit_implementation_session_diagnostics(
    tmp_path: Path, mode: Mode, prompt: str, answer: str,
):
    orch, _opencode = _build(tmp_path, [Turn(text=answer)])
    project = orch.project(start_preview=False)
    project.control.set_mode(mode)

    list(orch.build_stream(prompt, conversation=CONVERSATION))

    store = build_diagnostics.Store(project.record.path)
    summary = next(row for row in store.list(project.workspace.app_id)
                   if row["turn"]["kind"] == "build")
    record = store.get(summary["turn"]["turnId"], project.workspace.app_id, CONVERSATION)
    assert "implementationSession" not in record
