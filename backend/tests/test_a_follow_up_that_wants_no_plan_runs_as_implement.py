"""A follow-up the scope classifier says wants no plan runs as Implement (#498, phase B).

Phase A fixed the approve turn. It did not fix the rest of the measured session: on 2026-09-21 a
TYPED follow-up on an already-built app restarted in PLAN like every other turn, because the shim's
per-step classifier biases to PLAN until the turn's first write and a user message resets its
window. `continue` cost 4.6 min and `fix this error cardTitle is not defined` cost 0.9 min, each
paying a fresh ramp on the plan model for work that was not planning.

The signal was already there and already paid for. `_scope_gate_applies` spends one model call per
Auto turn on a built project asking `scope.wants_a_plan`, and until now a BUILD verdict was a call
that had cost a round trip to agree with the default. It is now also the instruction "run this as
Implement" — the same instruction as picking Implement, inferred instead of typed.

What this file pins, at the layer that can see it:

  * a BUILD verdict dispatches the turn as `mode=implement` AND on `sage-implement` — two
    assertions, because a phase seed would have delivered the first and not the second;
  * a PLAN verdict still dispatches as `mode=auto` and gates onto `sage-plan` — widening one gate
    has broken its neighbour in this repo before;
  * the pin is turn-scoped: the person's picker does not move;
  * an explicit mode is never second-guessed, because the classifier never runs there at all.

The mode assertions read the orchestrator's own `turn:` log line rather than an agent name, and
that is not a stylistic choice — see `_dispatched_mode`. The agent chain tests `gate` before it
reaches `_agent_for_mode`, so on a gated turn the agent name is right no matter what the mode is
pinned to, and a plant proved that an agent-only version of this file stayed green while the pin
fired on exactly the turn it must not fire on.

The phase half of the guarantee lives in `test_enforcement_shim.py`, which can see the shim; the
fake agent here never reaches it.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn, execution_plan

PLAN = Turn(text=execution_plan("Consumption Dashboard", "A consumption dashboard.",
                                "Add the table", work="Add the table and wire up its data."))
BUILD = Turn(writes={"src/App.tsx": "// the table\n"})
FOLLOW_UP = Turn(writes={"src/App.tsx": "// the table, filtered\n"})
FOLLOW_UP_PLAN = Turn(text=execution_plan("Filtering", "A filter for the dashboard.",
                                          "Add a filter control"))

# Names no file of this app, is not a question, does not ask for a plan or an architecture — so
# every deterministic signal declines and the scope classifier is the one that decides.
FOLLOW_UP_PROMPT = "add a filter above the table and keep the totals in step with it"


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word per routed request — the scope classifier is the only caller here."""

    def __init__(self, verdict: str = "BUILD") -> None:
        self.verdict = verdict
        self.calls = 0

    def route(self, request, labels):
        self.calls += 1
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The same two waits test_turn_path strips: a scripted turn can only spend them."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _built(tmp: Path, verdict: str, follow_up: Turn):
    """Build an app the ordinary way, then leave it one turn from a follow-up.

    The app has to be REAL — `_scope_gate_applies` declines while `has_built()` is False, because the
    first-build gate has that turn. So this plans, approves and builds before the turn under test.
    """
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")

    ws = tmp / "mnt" / "code"
    gateway = ScriptedGateway(verdict)
    oc = FakeOpenCode(ws, [PLAN, BUILD, follow_up])
    orch = Orchestrator(
        workspace_dir=ws, template=template, gateway=gateway,
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    list(orch.build_stream("build me a consumption dashboard"))
    list(orch.approve_stream())
    return orch, oc, gateway


def _dispatched_mode(caplog) -> str:
    """The mode the LAST turn actually dispatched under, read off the `turn:` line the orchestrator
    logs beside its agent choice.

    The agent name alone cannot answer this. `agent` is chosen by a chain that tests `gate` before it
    ever reaches `_agent_for_mode`, so a gated turn reads `sage-plan` whatever the mode is pinned to
    — and the mode is what picks the MODEL. A plant proved exactly that: forcing the pin to fire on a
    gated turn left every agent assertion in this file green. This reads the pin where it bites.
    """
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("turn: ")]
    assert lines, "no turn was dispatched"
    return lines[-1].rsplit("mode=", 1)[1].strip()


def test_a_build_verdict_runs_the_follow_up_on_the_implement_agent(tmp_path: Path, caplog):
    """The fix. A verdict of BUILD is an instruction, not just a decision not to gate.

    Both halves are asserted, because they break for different reasons and a phase seed would
    deliver only one of them. The MODE is what `_resolve_build` reads to reach branch 4 and the
    implement slot. The AGENT comes from `_MODE_AGENT`, which has no `Mode.AUTO` entry — so an Auto
    turn resolves to OpenCode's default agent and never sees the `sage-implement` prompt, the one
    that says "a turn in which you touched no files is a failed turn". A phase seed moves the model
    and leaves the agent None.
    """
    orch, oc, gateway = _built(tmp_path, "BUILD", FOLLOW_UP)

    with caplog.at_level(logging.INFO, logger="sage.orchestrator"):
        list(orch.build_stream(FOLLOW_UP_PROMPT))

    assert gateway.calls, "the scope classifier never ran — the turn under test never happened"
    assert _dispatched_mode(caplog) == "implement"
    assert oc.prompts[-1]["agent"] is not None, "a phase seed would leave this None"
    assert oc.prompts[-1]["agent"] == "sage-implement"


def test_a_plan_verdict_still_gates_and_is_not_pinned_to_implement(tmp_path: Path, caplog):
    """The neighbour. Pinning on one verdict must not disturb the other one.

    The mode is the assertion that does the work here, and the agent name is the one that looks like
    it does. A gated turn is pinned to `sage-plan` by a branch that runs BEFORE `_agent_for_mode`,
    so the agent stays right even if the mode is wrong — while the mode is what routes the MODEL.
    Pinning a gated turn to Implement would leave it planning, correctly, on the cheap coder at the
    coder's effort, with every write tool stripped by the shim and the plan card no better for it.
    """
    orch, oc, _gateway = _built(tmp_path, "PLAN", FOLLOW_UP_PLAN)

    with caplog.at_level(logging.INFO, logger="sage.orchestrator"):
        list(orch.build_stream(FOLLOW_UP_PROMPT))

    assert _dispatched_mode(caplog) == "auto"
    assert oc.prompts[-1]["agent"] == "sage-plan"


def test_the_pin_does_not_move_the_persons_picker(tmp_path: Path):
    """Turn-scoped, via `set_turn_mode`, which re-pins the running turn and never touches
    `selected_mode`. The person chose Auto; their next turn is still Auto."""
    orch, _oc, _gateway = _built(tmp_path, "BUILD", FOLLOW_UP)

    list(orch.build_stream(FOLLOW_UP_PROMPT))

    assert orch.project(start_preview=False).control.selected_mode is Mode.AUTO


@pytest.mark.parametrize("mode,agent", [(Mode.PLAN, "sage-plan"), (Mode.IMPLEMENT, "sage-implement")])
def test_an_explicit_mode_is_never_second_guessed(tmp_path: Path, mode: Mode, agent: str):
    """The classifier does not run outside Auto, so there is no verdict to act on and nothing here
    changes. Asserted at this layer rather than on the predicate alone: `test_scope.py` already pins
    that `_scope_gate_applies` declines, and this pins that declining it still dispatches the agent
    the person's own pick asked for."""
    orch, oc, gateway = _built(tmp_path, "BUILD", FOLLOW_UP)
    orch.project(start_preview=False).control.set_mode(mode)

    list(orch.build_stream(FOLLOW_UP_PROMPT))

    assert gateway.calls == 0, "the scope classifier ran outside Auto"
    assert oc.prompts[-1]["agent"] == agent


def test_ask_mode_still_refuses_a_change_request_before_any_inference(tmp_path: Path):
    """Ask is the fourth door and it is not in the parametrize above, because it never reaches an
    agent at all: `_ask_mode_refusal` turns a change request back before any inference, so there is
    no prompt to assert an agent on. Pinned here rather than left out, because it is the neighbour
    the scope verdict now sits closest to and 'nothing changed for Ask' is the claim being made."""
    orch, oc, gateway = _built(tmp_path, "BUILD", FOLLOW_UP)
    orch.project(start_preview=False).control.set_mode(Mode.ASK)
    before = len(oc.prompts)

    events = list(orch.build_stream(FOLLOW_UP_PROMPT))

    assert gateway.calls == 0
    assert len(oc.prompts) == before, "Ask dispatched a turn it should have refused"
    assert events[-1].pop("turnId")  # ADR-0069 (#565): every Build `done` names its turn; the rest is unchanged
    assert events[-1] == {"type": "done", "ok": False, "decision": "ask mode (read-only)"}
