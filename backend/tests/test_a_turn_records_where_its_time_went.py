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
