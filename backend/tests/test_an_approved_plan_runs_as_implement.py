"""An approved plan is built, not planned again — whatever mode it was approved from (#498).

The live shape, measured 2026-09-21 on `sage_rev e1a5222`: a plan approved with the picker on Auto
spent its first several model calls re-planning on the plan model at plan effort. One of them ran
5m35s and emitted a two-item todo list. The ratio across the session was ~34 plan calls to ~9
implement, on turns whose own prompt opens *"The user approved the plan. Build the app it describes
now — implement it, don't re-plan"* with the whole approved plan inlined underneath.

Two separate things were unpinned, and only one of them had ever been named:

  * the MODEL and its effort, because the shim's per-step classifier restarts every turn in PLAN
    until that turn's first write, and an approve prompt is a user message that resets the window;
  * the AGENT, because `_MODE_AGENT` has no `Mode.AUTO` entry — deliberately, since an Auto turn may
    be a question, a plan or an edit — so an Auto approval ran on OpenCode's default agent and never
    saw the `sage-implement` prompt that says "a turn in which you touched no files is a failed turn".

Approving from Plan or Ask already pinned Implement. Auto was the one door left open, and Auto is
the default. The fix pins the mode for the turn, which fixes both halves at once; seeding the phase
would have fixed only the first, because the agent is chosen by `_agent_for_mode(mode)` and never by
phase. That is the test at the bottom of this file.

The phase half of the guarantee is pinned in `test_enforcement_shim.py`, which can see the shim.
The fake agent here never reaches the shim, so the agent name is what this layer can observe.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from sage.build_policy import BuildPolicy
from sage.feedback.runner import FeedbackReport
from sage.orchestrator import native_routes
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

PLAN = Turn(text="""# Consumption Dashboard

A dashboard for reviewing consumption.

## Problem & outcome
Consumption is hard to review; the app makes it visible.

## Who uses this
The operations analyst.

## What it does
- Shows consumption in a table.

## Screens
- **Consumption table** — Shows usage rows.

## Done when
- The preview shows the consumption table.

## Plan

### 1. Add the table
- Files — src/App.tsx
- Do — Add the consumption table and wire up the data.
- Done when — The preview shows the table.
""")
BUILD = Turn(writes={"src/App.tsx": "// the table\n"})


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word per routed request — the scope classifier is the only caller here."""

    def __init__(self, verdict: str = "BUILD") -> None:
        self.verdict = verdict

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The same two waits test_turn_path strips: a scripted turn can only spend them."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _build(tmp: Path, turns: list[Turn], *, build_policy: BuildPolicy | None = None):
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")

    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, turns)
    orch = Orchestrator(
        workspace_dir=ws, template=template, gateway=ScriptedGateway(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage", feedback=OkFeedback(), opencode_client=oc,
        build_policy=build_policy)
    return orch, oc


def _approved_from(tmp: Path, mode: Mode):
    """Plan first, then set the picker, then approve.

    The mode that matters is the one the picker is on when Approve is PRESSED, and that is also the
    only way to reach the Ask door at all: Ask answers rather than plans, so a plan approved from
    Ask was necessarily made somewhere else and the picker moved afterwards.
    """
    orch, oc = _build(tmp, [PLAN, BUILD])
    list(orch.build_stream("build me a consumption dashboard"))
    project = orch.project(start_preview=False)
    project.control.set_mode(mode)
    intents = []
    send_prompt = oc.send_prompt

    def capture(*args, **kwargs):
        intents.append(project.active_build_intent)
        return send_prompt(*args, **kwargs)

    oc.send_prompt = capture
    list(orch.approve_stream())
    return orch, oc, intents


@pytest.mark.parametrize("mode", [Mode.AUTO, Mode.PLAN, Mode.ASK, Mode.IMPLEMENT])
def test_an_approval_builds_on_the_implement_agent_from_every_mode(tmp_path: Path, mode: Mode):
    """Auto is the case that was broken; the other three are what must not regress.

    Parametrised rather than written once for Auto, because the bug was precisely that three doors
    were fixed and the fourth was not — a test that only covers the door that bit is short by
    construction.
    """
    orch, oc, intents = _approved_from(tmp_path, mode)

    assert oc.prompts[-1]["agent"] == "sage-implement"
    assert "Add the table" not in oc.prompts[-1]["text"]
    assert intents[-1].kind == "approved_plan"
    assert intents[-1].source_requests == ("build me a consumption dashboard",)
    assert "Add the table" in intents[-1].authoritative_plan
    assert orch.project(start_preview=False).active_build_intent is None


@pytest.mark.parametrize("mode", [Mode.AUTO, Mode.PLAN, Mode.ASK, Mode.IMPLEMENT])
def test_an_approval_does_not_move_the_persons_picker(tmp_path: Path, mode: Mode):
    """The pin is turn-scoped (arm_turn_mode), not a `set_mode` the turn forgets to undo.

    This is the property the comment at the `run_as` site was written to protect: an earlier
    set_mode-then-restore moved the picker, so a mode the person changed while the build streamed
    was reverted underneath them when it finished. Pinning a fourth door must not reintroduce it.
    """
    orch, _oc, _intents = _approved_from(tmp_path, mode)

    assert orch.project(start_preview=False).control.selected_mode is mode


def test_the_agent_is_what_a_phase_seed_alone_would_have_missed(tmp_path: Path):
    """The rejected fix, pinned as a test so nobody re-derives it.

    Seeding the classifier's default phase to IMPLEMENT is smaller and keeps the rescue for free,
    and it would have left this assertion failing: `_agent_for_mode` reads the MODE, and
    `_MODE_AGENT` has no `Mode.AUTO` entry, so an Auto approval would still have run on OpenCode's
    default agent with no `sage-implement` prompt in sight.
    """
    _orch, oc, _intents = _approved_from(tmp_path, Mode.AUTO)

    approve = oc.prompts[-1]
    assert approve["agent"] is not None, "a phase seed would leave this None"
    assert approve["agent"] == "sage-implement"


def test_a_direct_build_owns_one_exact_intent_until_turn_cleanup(tmp_path: Path):
    request = "Build the exact dashboard — Ω."
    orch, oc = _build(tmp_path, [BUILD])
    project = orch.project(start_preview=False)
    project.record.write_settings({"skip_planning": True})
    project.control.set_mode(Mode.IMPLEMENT)
    intents = []
    send_prompt = oc.send_prompt

    def capture(*args, **kwargs):
        intents.append(project.active_build_intent)
        return send_prompt(*args, **kwargs)

    oc.send_prompt = capture
    list(orch.build_stream(request))

    assert len(intents) == 1
    assert intents[0].kind == "direct_build"
    assert intents[0].source_requests == (request,)
    assert request not in oc.prompts[0]["text"]
    assert "SAGE_BUILD_INTENT" not in project.workspace.history_path.read_text()
    assert project.active_build_intent is None


def test_planning_and_ask_dispatch_with_no_build_intent(tmp_path: Path):
    orch, oc = _build(tmp_path, [PLAN, Turn(text="It shows consumption.")])
    project = orch.project(start_preview=False)
    intents = []
    send_prompt = oc.send_prompt

    def capture(*args, **kwargs):
        intents.append(project.active_build_intent)
        return send_prompt(*args, **kwargs)

    oc.send_prompt = capture
    list(orch.build_stream("plan a consumption dashboard"))
    project.control.set_mode(Mode.ASK)
    list(orch.build_stream("what does this plan show?"))

    assert intents == [None, None]
    assert project.active_build_intent is None


def test_ten_no_edit_sends_reuse_one_intent_without_storing_the_request(tmp_path: Path):
    request = "Build the one canonical dashboard."
    turns = [Turn(text="I will think about it.") for _ in range(9)] + [BUILD]
    orch, oc = _build(tmp_path, turns, build_policy=BuildPolicy(no_edit_nudge_limit=9))
    project = orch.project(start_preview=False)
    project.record.write_settings({"skip_planning": True})
    project.control.set_mode(Mode.IMPLEMENT)
    intents = []
    send_prompt = oc.send_prompt

    def capture(*args, **kwargs):
        intents.append(project.active_build_intent)
        return send_prompt(*args, **kwargs)

    oc.send_prompt = capture
    list(orch.build_stream(request))

    assert len(intents) == 10
    assert all(intent is intents[0] for intent in intents)
    assert intents[0].source_requests == (request,)
    assert all(request not in prompt["text"] for prompt in oc.prompts)
    assert project.active_build_intent is None


def test_runtime_repair_reuses_the_direct_build_intent(tmp_path: Path, monkeypatch):
    orch, oc = _build(tmp_path, [
        Turn(writes={"src/App.tsx": "// first attempt\n"}),
        Turn(writes={"src/App.tsx": "// repaired\n"}),
    ])
    project = orch.project(start_preview=False)
    project.record.write_settings({"skip_planning": True})
    project.control.set_mode(Mode.IMPLEMENT)
    crashes = iter([{"message": "render failed", "stack": "stack"}, None])
    monkeypatch.setattr(orch, "_await_runtime_error", lambda *args, **kwargs: next(crashes))
    intents = []
    send_prompt = oc.send_prompt

    def capture(*args, **kwargs):
        intents.append(project.active_build_intent)
        return send_prompt(*args, **kwargs)

    oc.send_prompt = capture
    events = list(orch.build_stream("build a runtime-safe app"))

    assert len(intents) == 2 and intents[0] is intents[1]
    assert any(event.get("reason", "").startswith("app crashed at runtime")
               for event in events if event["type"] == "iterate")
    assert project.active_build_intent is None


def test_sequential_direct_turns_cannot_reuse_each_others_intent(tmp_path: Path):
    orch, oc = _build(tmp_path, [
        Turn(writes={"src/App.tsx": "// first\n"}),
        Turn(writes={"src/App.tsx": "// second\n"}),
    ])
    project = orch.project(start_preview=False)
    project.record.write_settings({"skip_planning": True})
    project.control.set_mode(Mode.IMPLEMENT)
    intents = []
    send_prompt = oc.send_prompt

    def capture(*args, **kwargs):
        intents.append(project.active_build_intent)
        return send_prompt(*args, **kwargs)

    oc.send_prompt = capture
    list(orch.build_stream("first request"))
    list(orch.build_stream("second request"))

    assert [intent.source_requests for intent in intents] == [
        ("first request",), ("second request",),
    ]
    assert intents[0] is not intents[1]
    assert project.active_build_intent is None


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_exception_and_cancellation_clear_the_intent(tmp_path: Path, failure):
    orch, oc = _build(tmp_path, [BUILD])
    project = orch.project(start_preview=False)
    project.record.write_settings({"skip_planning": True})
    project.control.set_mode(Mode.IMPLEMENT)
    seen = []

    def fail(*args, **kwargs):
        seen.append(project.active_build_intent)
        raise failure("stopped")

    oc.send_prompt = fail
    with pytest.raises(failure):
        list(orch.build_stream("build it"))

    assert seen[0].source_requests == ("build it",)
    assert project.active_build_intent is None


def test_stop_and_client_disconnect_clear_the_intent(tmp_path: Path):
    orch, oc = _build(tmp_path, [BUILD, BUILD])
    project = orch.project(start_preview=False)
    project.record.write_settings({"skip_planning": True})
    project.control.set_mode(Mode.IMPLEMENT)
    send_prompt = oc.send_prompt

    def stop_after_send(*args, **kwargs):
        send_prompt(*args, **kwargs)
        project.stop_requested = True

    oc.send_prompt = stop_after_send
    events = list(orch.build_stream("stop this build"))
    assert any(event["type"] == "stopped" for event in events)
    assert project.active_build_intent is None

    project.stop_requested = False
    oc.send_prompt = send_prompt
    stream = orch.build_stream("disconnect this build")
    while next(stream)["type"] != "turn":
        pass
    assert project.active_build_intent is not None
    stream.close()
    assert project.active_build_intent is None


def test_integrity_refusal_emits_one_failure_keeps_plan_and_skips_nudge(tmp_path: Path):
    orch, oc = _build(tmp_path, [PLAN, Turn()])
    list(orch.build_stream("build me a consumption dashboard"))
    project = orch.project(start_preview=False)
    send_prompt = oc.send_prompt

    def refuse(*args, **kwargs):
        send_prompt(*args, **kwargs)
        project.last_gateway_error = {"message": native_routes._BUILD_INTENT_ERROR}

    oc.send_prompt = refuse
    events = list(orch.approve_stream())

    assert len([event for event in events if event["type"] == "error"]) == 1
    done = [event for event in events if event["type"] == "done"]
    assert len(done) == 1 and done[0]["ok"] is False
    assert len(oc.prompts) == 2
    assert project.workspace.read_plan()
    assert project.active_build_intent is None
