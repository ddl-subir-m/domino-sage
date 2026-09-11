"""A turn measures itself, or nobody can say why a build is slow.

Sage's latency only exists against a live gateway and a real workspace, so the loop for "builds take
too long" cannot be a local timing test — a laptop's numbers are not the numbers. What a local test
CAN hold is the half that keeps going wrong silently: whether the recorder is wired to the places
the time actually goes. An instrumented build that quietly stops recording its pre-turn gates looks
exactly like a build with no pre-turn cost.

So this asserts on STRUCTURE, never on duration: which spans a build opens, that a nudged build
opens one span per agent turn, and that the readout renders. The durations are read off
`/api/diag/timing` on a real deployment (see scripts/turn-timing.py).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage import timing
from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="p", implement="i", ask="a")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _orch(tmp_path: Path, turns: list[Turn]) -> Orchestrator:
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp_path / "mnt" / "code"
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=None, catalog=_catalog(),
                        project_id="Sage", feedback=OkFeedback(),
                        opencode_client=FakeOpenCode(ws, turns))
    project = orch.project(start_preview=False)
    project.control.set_mode(Mode.IMPLEMENT)
    # No plan gate: a gated turn proposes and stops, and the loop this measures is the one that
    # sends, polls, typechecks and decides whether to send again.
    project.record.write_settings({"skip_planning": True})
    return orch


class _ScriptedGateway:
    """Answers CHAT, so the post-turn classifier runs and stays quiet (as in test_chat_turn)."""

    def route(self, request, labels):
        import json as _json
        body = _json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _chat_orch(tmp_path: Path, turns: list[Turn]) -> Orchestrator:
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp_path / "mnt" / "code"
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=_ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=FakeOpenCode(ws, turns))
    orch.project(start_preview=False)
    return orch


def _names(rec) -> list[str]:
    return [s.name for s in rec.spans]


def test_a_build_turn_records_the_gates_it_paid_for(tmp_path: Path):
    orch = _orch(tmp_path, [Turn(text="Built it.", writes={"src/App.tsx": "export default () => null\n"})])
    list(orch.build_stream("add a chart"))

    rec = timing.recent(1)[0]
    assert rec.kind == "build"
    assert rec.t1 is not None, "the record was never closed — a turn that ends must close its span"
    names = _names(rec)
    # The serial pre-turn work, each named separately: a total that cannot be split says the turn
    # was slow before the model, which is where the question started, not where it ends.
    for gate in ("turn.acquire", "setup.seed", "setup.opencode", "setup.session",
                 "setup.agent_inputs", "gate.commit_before_turn", "typecheck"):
        assert gate in names, f"{gate} is not measured — {names}"
    # One span per pass of the send/poll/typecheck loop, which is the unit the retry budgets spend.
    assert "agent-turn.1" in names
    assert rec.counters.get("poll.iterations", 0) >= 1, "the poll loop counted no iterations"


def test_a_nudged_build_records_every_agent_turn_it_spent(tmp_path: Path):
    """MAX_NUDGES and friends are budgets nobody can see being spent. A turn that wrote nothing is
    re-sent, and each re-send is another full model turn — the readout has to show them separately
    or a three-nudge build reads as one slow one."""
    orch = _orch(tmp_path, [
        Turn(text="Here is my plan."),                                             # writes nothing
        Turn(text="Built it.", writes={"src/App.tsx": "export default () => null\n"}),
    ])
    list(orch.build_stream("add a chart"))

    rec = timing.recent(1)[0]
    names = _names(rec)
    turns = [n for n in names if n.startswith("agent-turn.")]
    assert turns == ["agent-turn.1", "agent-turn.2"], names
    whys = [s.fields.get("why") for s in rec.spans if s.name.startswith("agent-turn.")]
    assert whys[0] == "first send"
    assert whys[1] and whys[1] != "first send", f"the nudge did not record why it happened: {whys}"


def test_the_readout_renders_a_turn_that_is_still_running(tmp_path: Path):
    """The record someone most wants is the one they are waiting on, so an open turn has to render
    rather than raise on its half-finished spans."""
    timing.start_turn("build", "add a chart")
    open_span = timing.open_span("agent-turn.1", why="first send")
    call = timing.model_call()
    call.model("gpt-5.4", "plan")
    out = timing.render_all(1)
    assert "RUNNING" in out and "(open)" in out and "gpt-5.4" in out
    timing.close_span(open_span)
    timing.finish_turn(ok=True, decision="typecheck clean")


def test_a_chat_turn_records_where_it_went_too(tmp_path: Path):
    """Chat recorded `turn.acquire` and nothing else, which is not the same as costing nothing.

    A Chat turn that pays two seconds before its first inference and five after its last one used
    to render as one unnamed gap at each end, and `scripts/turn-timing.py` reported its polling
    bucket as a flat zero — not because the loop is free, but because no counter existed. The names
    below are the ones that readout already parses: `setup.*` counts as pre-inference, and
    `poll.read_ms`/`poll.sleep_ms` are summed by name.

    Structure only, like every other case in this file. The durations are read off a real
    deployment.
    """
    orch = _chat_orch(tmp_path, [Turn(text="the three biggest movers were …")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "what moved most this week?"))

    rec = timing.recent(1)[0]
    assert rec.kind == "chat"
    assert rec.t1 is not None
    names = _names(rec)
    for name in ("turn.acquire", "setup.opencode", "setup.session", "setup.snapshot",
                 "setup.baseline", "setup.mentions", "setup.prompt", "setup.dispatch"):
        assert name in names, f"{name} missing from {names}"
    # The other end of the turn: the stretch a person watches the turn bar for after reading the
    # answer. It was the whole reason the tail looked like unexplained time.
    for name in ("after.artifacts", "after.handoff", "after.compact", "after.save"):
        assert name in names, f"{name} missing from {names}"
    # A Chat record carried no decision at all until the wrapper recorded one: eight `done` sites,
    # none of them telling the ledger how the turn ended.
    assert rec.decision == "answered"
    assert rec.ok is True
    assert rec.counters["poll.iterations"] >= 1


def test_the_chat_classifier_is_on_the_ledger_like_the_scope_one(tmp_path: Path):
    """The post-turn classifier calls the gateway directly, so it bypassed the /v1 shim handler
    that fills the ledger — the same gap already closed for the scope classifier in scope.py.

    It runs after the answer is on screen, which is exactly the stretch that reads as time nothing
    can account for."""
    orch = _chat_orch(tmp_path, [Turn(text="here is the answer")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "which desk lost the most?"))

    rec = timing.recent(1)[0]
    handoff_calls = [c for c in rec.calls if c.phase == "handoff"]
    assert len(handoff_calls) == 1, [(c.model, c.phase) for c in rec.calls]
    assert handoff_calls[0].t1 is not None, "the entry was left open"
    assert handoff_calls[0].model == "a"   # catalog.ask, what the classifier routes to
