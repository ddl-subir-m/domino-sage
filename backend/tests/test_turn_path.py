"""End-to-end turn dispatch, driven through a fake OpenCode.

The three gate changes before this one — the explicit plan request, the scope classifier, the
failure-triggered replan — were each tested as predicates and then wired in by hand, with the wiring
verified by reading. Predicates that pass in isolation and a turn that behaves correctly are not the
same claim. These tests make the second one: prompt in, event stream out, nothing stubbed between the
dispatch decision and the events the UI renders.

What is still faked is deliberate and narrow: the agent (scripted, see fake_opencode), the typecheck
(no tsc in the suite), and the gateway (scripted verdicts, so a test asserts what the ORCHESTRATOR
does with an answer, never what a model would say).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.feedback.runner import FeedbackReport
from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    """Typecheck always passes. Type errors have their own tests; here they would only add a second
    reason for a turn to end badly and blur which one a failing assertion meant."""

    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """Answers every routed request with one scripted word. The only caller on this path is the scope
    classifier — the fake agent never reaches the shim — so this controls exactly one decision."""

    def __init__(self, verdict: str = "BUILD") -> None:
        self.verdict = verdict
        self.calls = 0

    def route(self, request, labels):
        self.calls += 1
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="p", implement="i", ask="a")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """Strip the two waits a scripted turn can only ever spend, never use.

    The poll loop sleeps a second between polls — real latency against a real agent, dead wall-clock
    against a fake one. Patched on the stdlib module rather than on `service`, which imports `time`
    function-locally.

    `_await_runtime_error` then polls for four seconds for the preview to report a crash. These tests
    run with `start_preview=False`, so there is no preview, nothing can ever set `project.runtime_error`,
    and the poll is four guaranteed-fruitless seconds per successful build. Worse with sleep patched
    out: the wait stops sleeping and starts spinning, same wall-clock, 43M clock reads. Skipping it
    removes a wait for an event that cannot occur, not a behaviour under test."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _build(tmp: Path, turns: list[Turn], *, verdict: str = "BUILD"):
    """An orchestrator wired to fakes, plus the fake agent and gateway for assertions."""
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")

    ws = tmp / "mnt" / "code"
    gateway = ScriptedGateway(verdict)
    oc = FakeOpenCode(ws, turns)
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=gateway, catalog=_catalog(),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False)
    return orch, oc, gateway


def _run(orch, prompt: str, mode: Mode = Mode.AUTO) -> list[dict]:
    orch.project(start_preview=False).control.set_mode(mode)
    return list(orch.build_stream(prompt))


def _get_built(orch) -> None:
    """Take the project through the real first-build flow, consuming the first two scripted turns.

    There is no shortcut worth taking here: the first-build gate fires in every mode including
    Implement, so a turn that "just builds" on turn one doesn't exist, and `has_built` — which every
    later gate decision keys on — is set by a build that actually completed, not by a flag a test
    could plant. Plan, approve, build."""
    list(orch.build_stream("build me a dashboard"))
    list(orch.approve_stream())


def _done(events: list[dict]) -> dict:
    return next(e for e in events if e.get("type") == "done")


def _kinds(events: list[dict]) -> set[str]:
    return {e.get("type") for e in events}


# --- the answer-only path ------------------------------------------------------------------------

def test_a_question_is_answered_without_building(tmp_path: Path):
    orch, oc, gateway = _build(tmp_path, [Turn(text="It uses a bar chart from Highcharts.")])
    events = _run(orch, "what charting library does this use?")

    assert _done(events)["decision"] == "answered"
    assert "plan-proposed" not in _kinds(events)
    # The typecheck is skipped entirely: nothing changed, so running tsc would be dead time and its
    # "passed" line would read as though a build had been verified.
    assert "typecheck" not in _kinds(events)
    # A question never reaches the scope classifier — it's decided for free, before anything paid.
    assert gateway.calls == 0
    assert oc.prompts[0]["agent"] == "sage-ask"


# --- the plan gate -------------------------------------------------------------------------------

def test_a_first_build_gates_and_proposes_a_plan(tmp_path: Path):
    orch, oc, _ = _build(tmp_path, [Turn(text="1. Add a table\n2. Wire up the data")])
    events = _run(orch, "build me a dashboard")

    assert _done(events)["decision"] == "awaiting approval"
    plan = next(e for e in events if e["type"] == "plan-proposed")
    assert "Add a table" in plan["plan"]
    assert plan["kind"] == "plan"
    # Written where approve_stream will look for it, not just streamed and lost.
    assert (orch.project(start_preview=False).workspace.path / ".sage" / "plan.md").exists()


def test_the_scope_classifier_gates_a_substantial_change_on_a_built_app(tmp_path: Path):
    orch, oc, gateway = _build(tmp_path, [
        Turn(text="1. A table\n2. A chart"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text="1. Add an auth provider\n2. Add an orgs page"),
    ], verdict="PLAN")
    _get_built(orch)
    events = _run(orch, "add auth, orgs and a billing page")

    assert gateway.calls == 1  # the classifier ran, and only once
    assert _done(events)["decision"] == "awaiting approval"
    assert "plan-proposed" in _kinds(events)


def test_a_small_change_on_a_built_app_just_builds(tmp_path: Path):
    orch, oc, gateway = _build(tmp_path, [
        Turn(text="1. A table\n2. A chart"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text="Done.", writes={"src/App.tsx": "// v2, sortable\n"}),
    ], verdict="BUILD")
    _get_built(orch)
    events = _run(orch, "make the table sortable")

    assert gateway.calls == 1
    assert "plan-proposed" not in _kinds(events)
    assert _done(events)["ok"] is True
    assert (orch.project(start_preview=False).workspace.path / "src" / "App.tsx").read_text() == "// v2, sortable\n"


# --- failure-triggered replan --------------------------------------------------------------------

def test_a_failed_turn_makes_the_next_one_plan_first(tmp_path: Path):
    # The failure here is a gated turn that produced no plan text — a real, reachable failure ("no
    # plan text" is usually "no inference reached us") rather than an exception injected to force one.
    orch, oc, gateway = _build(tmp_path, [
        Turn(text="1. A table\n2. A chart"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text=""),                       # fails: gated, wrote nothing, said nothing
        Turn(text="1. Check the data source\n2. Then retry"),
    ], verdict="BUILD")
    _get_built(orch)

    failed = _run(orch, "plan the retraining work", Mode.PLAN)
    assert _done(failed)["ok"] is False
    ws = orch.project(start_preview=False).workspace
    assert ws.read_last_turn_failed() is True

    # The retry would be an ungated build turn on a built project; the failure gates it instead.
    retry = _run(orch, "try that again")
    assert _done(retry)["decision"] == "awaiting approval"
    # And the signal is spent: the classifier's BUILD verdict is what decides the turn after.
    assert ws.read_last_turn_failed() is False


def test_a_question_after_a_failure_does_not_spend_the_gate(tmp_path: Path):
    orch, oc, gateway = _build(tmp_path, [
        Turn(text="1. A table\n2. A chart"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text=""),                       # fails
        Turn(text="Because the data source was empty."),
        Turn(text="1. Fix the data source"),
    ], verdict="BUILD")
    _get_built(orch)
    _run(orch, "plan the retraining work", Mode.PLAN)

    ws = orch.project(start_preview=False).workspace
    _run(orch, "why did that fail?")
    assert ws.read_last_turn_failed() is True  # asking about the failure must not consume it

    assert _done(_run(orch, "try that again"))["decision"] == "awaiting approval"
