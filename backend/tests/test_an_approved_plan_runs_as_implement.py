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

import json
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

PLAN = Turn(text="# Consumption Dashboard\n\n## Plan\n1. Add the table\n2. Wire up the data")
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


def _build(tmp: Path, turns: list[Turn]):
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
        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    return orch, oc


def _approved_from(tmp: Path, mode: Mode):
    """Plan first, then set the picker, then approve.

    The mode that matters is the one the picker is on when Approve is PRESSED, and that is also the
    only way to reach the Ask door at all: Ask answers rather than plans, so a plan approved from
    Ask was necessarily made somewhere else and the picker moved afterwards.
    """
    orch, oc = _build(tmp, [PLAN, BUILD])
    list(orch.build_stream("build me a consumption dashboard"))
    orch.project(start_preview=False).control.set_mode(mode)
    list(orch.approve_stream())
    return orch, oc


@pytest.mark.parametrize("mode", [Mode.AUTO, Mode.PLAN, Mode.ASK, Mode.IMPLEMENT])
def test_an_approval_builds_on_the_implement_agent_from_every_mode(tmp_path: Path, mode: Mode):
    """Auto is the case that was broken; the other three are what must not regress.

    Parametrised rather than written once for Auto, because the bug was precisely that three doors
    were fixed and the fourth was not — a test that only covers the door that bit is short by
    construction.
    """
    _orch, oc = _approved_from(tmp_path, mode)

    assert oc.prompts[-1]["agent"] == "sage-implement"
    assert "Add the table" in oc.prompts[-1]["text"]


@pytest.mark.parametrize("mode", [Mode.AUTO, Mode.PLAN, Mode.ASK, Mode.IMPLEMENT])
def test_an_approval_does_not_move_the_persons_picker(tmp_path: Path, mode: Mode):
    """The pin is turn-scoped (arm_turn_mode), not a `set_mode` the turn forgets to undo.

    This is the property the comment at the `run_as` site was written to protect: an earlier
    set_mode-then-restore moved the picker, so a mode the person changed while the build streamed
    was reverted underneath them when it finished. Pinning a fourth door must not reintroduce it.
    """
    orch, _oc = _approved_from(tmp_path, mode)

    assert orch.project(start_preview=False).control.selected_mode is mode


def test_the_agent_is_what_a_phase_seed_alone_would_have_missed(tmp_path: Path):
    """The rejected fix, pinned as a test so nobody re-derives it.

    Seeding the classifier's default phase to IMPLEMENT is smaller and keeps the rescue for free,
    and it would have left this assertion failing: `_agent_for_mode` reads the MODE, and
    `_MODE_AGENT` has no `Mode.AUTO` entry, so an Auto approval would still have run on OpenCode's
    default agent with no `sage-implement` prompt in sight.
    """
    _orch, oc = _approved_from(tmp_path, Mode.AUTO)

    approve = oc.prompts[-1]
    assert approve["agent"] is not None, "a phase seed would leave this None"
    assert approve["agent"] == "sage-implement"
