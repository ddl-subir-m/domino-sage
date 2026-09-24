"""No-edit recovery is decided from one turn's real tree delta (#514 follow-up)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackError, FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode
from sage.workspace.stack import STACKS

from .fake_opencode import Turn
from .ledger import last_turn, needs_ledger
from .test_turn_path import _build


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    monkeypatch.setenv("SAGE_MAX_NUDGES", "3")
    monkeypatch.setenv("SAGE_IMPLEMENT_STRONG_FALLBACK", "1")


def _report(label: str) -> FeedbackReport:
    if label == "clean":
        return FeedbackReport(ok=True, errors=[], raw="")
    code = "TS2304" if label == "starter" else "TS2322"
    message = "starter error" if label == "starter" else "error introduced by this edit"
    return FeedbackReport(
        ok=False,
        errors=[FeedbackError("entry", 1, 1, code, message)],
        raw=message,
    )


class ScriptedFeedback:
    def __init__(self, labels: list[str]) -> None:
        self.reports = [_report(label) for label in labels]
        self.checked: list[Path] = []

    def check(self, path: Path) -> FeedbackReport:
        self.checked.append(path)
        if not self.reports:
            raise AssertionError("typecheck ran more times than the decision matrix allowed")
        return self.reports.pop(0)


@dataclass(frozen=True)
class DecisionCase:
    name: str
    effects: tuple[str, ...]
    reports: tuple[str, ...]
    prompt_kinds: tuple[str, ...]
    final_ok: bool


CASES = (
    DecisionCase(
        "red-starter-no-edit-then-success",
        ("none", "write"),
        ("starter", "clean"),
        ("request", "no_edit"),
        True,
    ),
    DecisionCase(
        "red-starter-no-edit-exhausted",
        ("none", "none", "none", "none"),
        ("starter", "starter", "starter", "starter"),
        ("request", "no_edit", "no_edit", "no_edit"),
        False,
    ),
    DecisionCase(
        "red-no-edit-does-not-spend-typecheck-breaker",
        ("none", "none", "opaque", "write"),
        ("starter", "starter", "starter", "clean"),
        ("request", "no_edit", "no_edit", "typecheck_repair"),
        True,
    ),
    DecisionCase(
        "real-edit-introduces-error",
        ("write", "write"),
        ("introduced", "clean"),
        ("request", "typecheck_repair"),
        True,
    ),
    DecisionCase(
        "opaque-shell-write-introduces-error",
        ("opaque", "write"),
        ("introduced", "clean"),
        ("request", "typecheck_repair"),
        True,
    ),
    DecisionCase(
        "clean-no-edit-then-success",
        ("none", "write"),
        ("clean", "clean"),
        ("request", "no_edit"),
        True,
    ),
    DecisionCase(
        "clean-real-edit",
        ("write",),
        ("clean",),
        ("request",),
        True,
    ),
    DecisionCase(
        "no-op-write-is-no-edit",
        ("noop", "write"),
        ("starter", "clean"),
        ("request", "no_edit"),
        True,
    ),
)


def _turns(case: DecisionCase, entry_file: str) -> list[Turn]:
    turns = [
        Turn(text="# Seed\n\n## Plan\n1. Seed the app."),
        Turn(text="Seeded.", writes={entry_file: "// seeded app\n"}),
    ]
    for n, effect in enumerate(case.effects):
        if effect == "write":
            turns.append(Turn(text="Changed it.",
                              writes={entry_file: f"// {case.name} change {n}\n"}))
        elif effect == "opaque":
            turns.append(Turn(text="Changed it through the shell.", tools=["bash"]))
        else:
            turns.append(Turn(text="Here is the plan I would follow."))
    return turns


def _prompt_kind(text: str) -> str:
    if "Now IMPLEMENT" in text:
        return "no_edit"
    if "found 1 error(s). Fix these:" in text:
        return "typecheck_repair"
    return "request"


def _wrote(effect: str) -> bool:
    return effect in {"write", "opaque"}


def _standing(state):
    return (state.mode, state.picked_model, state.picked_effort,
            state.read_only_turn, state.web_allowed)


def _run_case(tmp_path: Path, stack_name: str, mode: Mode, case: DecisionCase):
    stack = STACKS[stack_name]
    feedback = ScriptedFeedback(["clean", *case.reports])
    orch, oc, gateway = _build(tmp_path, _turns(case, stack.entry_file))
    orch._feedback = feedback
    orch.create_app(stack=stack_name)
    project = orch.project(start_preview=False)

    # Reach a real built-app turn. This keeps Auto's scope-classifier path in the test instead of
    # setting has_built by hand and accidentally testing the explicit Implement path twice.
    list(orch.build_stream("Seed this app."))
    seed = list(orch.approve_stream())
    assert next(e for e in seed if e["type"] == "done")["ok"] is True

    data = tmp_path / "sales.csv"
    data.write_text("region,sales\nNorth,7\n")
    attached = orch.attach_file("ds_sales_2026", "sales.csv", local_source=data)
    project.control.set_mode(mode)
    project.control.pick("a", "low")
    before = project.control.snapshot()
    scenario_start = len(oc.prompts)
    states = []
    send = oc.send_prompt

    def dispatch(session_id, text, model=None, agent=None, attachments=None, chat=False):
        scenario_index = len(oc.prompts) - scenario_start
        effect = case.effects[scenario_index]
        if effect == "noop":
            # Emit a completed write tool but put the same bytes back. The tree hash, not the tool
            # name, must decide that this turn made no real change.
            current = Path(oc._session_dir(session_id)) / stack.entry_file
            oc.turns[oc._next].writes[stack.entry_file] = current.read_text()
        states.append(project.control.snapshot())
        send(session_id, text, model, agent, attachments, chat)
        if effect == "opaque":
            # A shell/heredoc write has no edit/write part. Only the turn-start tree hash sees it.
            current = Path(oc._session_dir(session_id)) / stack.entry_file
            current.write_text(f"// opaque {case.name}\n")

    oc.send_prompt = dispatch
    events = list(orch.build_stream(
        "Add a region filter using the attached sales data.", [attached["path"]]
    ))
    prompts = oc.prompts[scenario_start:]
    return orch, gateway, feedback, before, states, events, prompts, attached


@needs_ledger
@pytest.mark.parametrize("stack_name", STACKS)
@pytest.mark.parametrize("mode", [Mode.AUTO, Mode.IMPLEMENT])
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_no_edit_and_typecheck_repair_decision_matrix(
    tmp_path: Path, stack_name: str, mode: Mode, case: DecisionCase
):
    orch, gateway, feedback, before, states, events, prompts, attached = _run_case(
        tmp_path, stack_name, mode, case
    )

    assert len(prompts) == len(case.effects)
    assert [_prompt_kind(prompt["text"]) for prompt in prompts] == list(case.prompt_kinds)
    assert len([event for event in events if event["type"] == "typecheck"]) == len(case.effects)
    done = next(event for event in events if event["type"] == "done")
    assert done["ok"] is case.final_ok
    assert feedback.reports == []

    # One classifier for a true built-app Auto turn. Implement and every internal retry add none.
    assert gateway.calls == (1 if mode is Mode.AUTO else 0)
    assert all(prompt["agent"] == "sage-implement" for prompt in prompts)
    assert all(prompt["session"] == prompts[0]["session"] for prompt in prompts)
    assert prompts[0]["attachments"][0]["path"] == attached["path"]
    assert all(prompt["attachments"] is None for prompt in prompts[1:])
    assert all(state.mode is Mode.IMPLEMENT for state in states)
    assert states[0].picked_model == "a"
    for n, kind in enumerate(case.prompt_kinds[1:], start=1):
        used_no_edit = "no_edit" in case.prompt_kinds[1:n + 1]
        assert states[n].picked_model == ("p" if used_no_edit else "a")
    after = orch.project(start_preview=False).control.snapshot()
    assert (after.mode, after.picked_model, after.picked_effort) == (
        before.mode, before.picked_model, before.picked_effort
    )

    spans = [span for span in last_turn().spans if span.name.startswith("agent-turn.")]
    assert len(spans) == len(case.effects)
    no_edit_attempt = 0
    for n, (span, effect, report) in enumerate(zip(spans, case.effects, case.reports)):
        wrote = _wrote(effect)
        assert span.fields["stack"] == stack_name
        assert span.fields["no_edit_attempt"] == no_edit_attempt
        assert span.fields["wrote_code"] is wrote
        if not wrote:
            assert span.fields["retry_reason"] == "no_edit"
            assert span.fields["retry_exhausted"] is (no_edit_attempt == 3)
            no_edit_attempt += 1
        elif report != "clean":
            assert span.fields["retry_reason"] == "typecheck_repair"
            assert span.fields["retry_exhausted"] is False
        else:
            assert "retry_reason" not in span.fields

        if n == 0:
            assert span.fields["why"] == "first send"
        elif case.prompt_kinds[n] == "typecheck_repair":
            assert span.fields["why"] == "iteration 1, errors remain"
        else:
            # A built-app Auto BUILD verdict pins the actual dispatch to Implement before the
            # first send. It is therefore already in Implement when recovery starts; saying the
            # retry switches modes would be false. The standing Auto choice is still restored.
            assert span.fields["why"].startswith("wrote no code — retrying")

    if not case.final_ok:
        assert all(kind != "typecheck_repair" for kind in case.prompt_kinds)


@pytest.mark.parametrize("stack_name", STACKS)
@pytest.mark.parametrize("read_only_kind", ["gate", "answer"])
def test_red_read_only_violation_reverts_before_any_repair(
    tmp_path: Path, stack_name: str, read_only_kind: str
):
    stack = STACKS[stack_name]
    turn = Turn(text="# Plan\n\n## Plan\n1. Do the work.",
                writes={stack.entry_file: "// forbidden write\n"})
    orch, oc, gateway = _build(tmp_path, [turn])
    orch._feedback = ScriptedFeedback(["introduced"])
    orch.create_app(stack=stack_name)
    project = orch.project(start_preview=False)
    project.control.pick("a", "low")
    entry = project.app_for_turn().path / stack.entry_file
    original = entry.read_text()
    prompt = "Build a sales dashboard." if read_only_kind == "gate" else "What does this app show?"
    if read_only_kind == "answer":
        project.control.set_mode(Mode.ASK)
    before = project.control.snapshot()

    events = list(orch.build_stream(prompt))

    done = next(event for event in events if event["type"] == "done")
    expected = "gate violated" if read_only_kind == "gate" else "answer only — edits discarded"
    assert done["ok"] is False and done["decision"] == expected
    assert done["readOnly"] == ("plan" if read_only_kind == "gate" else "ask")
    assert len(oc.prompts) == 1
    assert not any(event["type"] == "iterate" for event in events)
    assert len([event for event in events if event["type"] == "typecheck"]) == 1
    assert entry.read_text() == original
    assert _standing(project.control.snapshot()) == _standing(before)
    assert gateway.calls == 0


class StopAfterRedCheck(ScriptedFeedback):
    def __init__(self, labels: list[str]) -> None:
        super().__init__(labels)
        self.orch: Orchestrator | None = None

    def check(self, path: Path) -> FeedbackReport:
        report = super().check(path)
        if len(self.checked) == 2:
            assert self.orch is not None
            self.orch.project(start_preview=False).stop_requested = True
        return report


@pytest.mark.parametrize("stack_name", STACKS)
def test_stop_after_red_no_edit_check_preempts_both_recovery_paths(
    tmp_path: Path, stack_name: str
):
    stack = STACKS[stack_name]
    feedback = StopAfterRedCheck(["clean", "starter"])
    orch, oc, _gateway = _build(tmp_path, [
        Turn(text="# Seed\n\n## Plan\n1. Seed the app."),
        Turn(writes={stack.entry_file: "// seeded app\n"}),
        Turn(text="I would plan this change."),
    ])
    feedback.orch = orch
    orch._feedback = feedback
    orch.create_app(stack=stack_name)
    project = orch.project(start_preview=False)
    list(orch.build_stream("Seed this app."))
    list(orch.approve_stream())
    project.control.set_mode(Mode.IMPLEMENT)
    project.control.pick("a", "low")
    before = project.control.snapshot()
    entry = project.app_for_turn().path / stack.entry_file
    body = entry.read_text()
    prompt_count = len(oc.prompts)

    events = list(orch.build_stream("Add a region filter."))

    assert len(oc.prompts) == prompt_count + 1
    assert [event["type"] for event in events].count("typecheck") == 1
    assert any(event["type"] == "stopped" for event in events)
    assert not any(event["type"] in {"iterate", "done"} for event in events)
    assert entry.read_text() == body
    assert project.stop_requested is False
    assert _standing(project.control.snapshot()) == _standing(before)


def _approve(tmp_path: Path, stack_name: str, mode: Mode):
    stack = STACKS[stack_name]
    plan = "# Dashboard\n\n## Plan\n1. Show the selected sales data with a region filter."
    turns = [
        Turn(text=plan),
        Turn(text="I will add a table and a region filter."),
        Turn(writes={stack.entry_file: "// region filter implemented\n"}),
    ]
    orch, oc, gateway = _build(tmp_path, turns)
    orch._feedback = ScriptedFeedback(["starter", "clean"])
    orch.create_app(stack=stack_name)
    project = orch.project(start_preview=False)
    data = tmp_path / "sales.csv"
    data.write_text("region,sales\nNorth,7\n")
    attached = orch.attach_file("ds_sales_2026", "sales.csv", local_source=data)
    list(orch.build_stream("Build a sales dashboard with a region filter.", [attached["path"]]))
    project.control.set_mode(mode)
    project.control.pick("a", "low")
    before = project.control.snapshot()
    states = []
    send = oc.send_prompt

    def record_dispatch(*args, **kwargs):
        states.append(project.control.snapshot())
        return send(*args, **kwargs)

    oc.send_prompt = record_dispatch
    events = list(orch.approve_stream(answers="Keep the North region visible."))
    return orch, oc, gateway, before, states, events, attached


@pytest.mark.parametrize("stack_name", STACKS)
@pytest.mark.parametrize("mode", [Mode.AUTO, Mode.IMPLEMENT])
def test_red_approval_keeps_plan_request_attachment_and_control_context(
    tmp_path: Path, stack_name: str, mode: Mode
):
    orch, oc, gateway, before, states, events, attached = _approve(tmp_path, stack_name, mode)
    first, retry = oc.prompts[1:]

    assert oc.prompts[0]["attachments"][0]["path"] == attached["path"]
    assert first["attachments"] is retry["attachments"] is None
    assert retry["session"] == first["session"] == oc.prompts[0]["session"]
    assert "Build a sales dashboard with a region filter." in oc.prompts[0]["text"]
    assert "Show the selected sales data with a region filter." in first["text"]
    assert "Keep the North region visible." in first["text"]
    assert f"start with {STACKS[stack_name].entry_file}" in retry["text"]
    assert retry["agent"] == "sage-implement"
    assert all(state.mode is Mode.IMPLEMENT for state in states)
    assert states[0].picked_model == "a" and states[1].picked_model == "p"
    after = orch.project(start_preview=False).control.snapshot()
    assert (after.mode, after.picked_model, after.picked_effort) == (
        before.mode, before.picked_model, before.picked_effort
    )
    assert gateway.calls == 0
    assert next(event for event in events if event["type"] == "done")["ok"] is True


@pytest.mark.parametrize("stack_name", STACKS)
@pytest.mark.parametrize("mode", [Mode.AUTO, Mode.IMPLEMENT])
def test_red_approval_exhaustion_keeps_the_plan_for_try_again(
    tmp_path: Path, stack_name: str, mode: Mode
):
    stack = STACKS[stack_name]
    plan = "# Dashboard\n\n## Plan\n1. Show the selected sales data with a region filter."
    orch, oc, gateway = _build(tmp_path, [
        Turn(text=plan),
        *[Turn(text="I would plan this change.") for _ in range(4)],
    ])
    orch._feedback = ScriptedFeedback(["starter"] * 4)
    orch.create_app(stack=stack_name)
    project = orch.project(start_preview=False)
    data = tmp_path / "sales.csv"
    data.write_text("region,sales\nNorth,7\n")
    attached = orch.attach_file("ds_sales_2026", "sales.csv", local_source=data)
    request = "Build a sales dashboard with a region filter."
    list(orch.build_stream(request, [attached["path"]]))
    project.control.set_mode(mode)
    project.control.pick("a", "low")
    before = project.control.snapshot()
    states = []
    send = oc.send_prompt

    def record_dispatch(*args, **kwargs):
        states.append(project.control.snapshot())
        return send(*args, **kwargs)

    oc.send_prompt = record_dispatch
    events = list(orch.approve_stream(answers="Keep the North region visible."))
    approval_prompts = oc.prompts[1:]
    app = project.app_for_turn()

    assert len(approval_prompts) == 4
    assert [_prompt_kind(prompt["text"]) for prompt in approval_prompts] == [
        "request", "no_edit", "no_edit", "no_edit",
    ]
    assert all(prompt["session"] == approval_prompts[0]["session"] for prompt in approval_prompts)
    assert approval_prompts[0]["attachments"] is None
    assert all(prompt["attachments"] is None for prompt in approval_prompts[1:])
    assert "Show the selected sales data with a region filter." in approval_prompts[0]["text"]
    assert "Keep the North region visible." in approval_prompts[0]["text"]
    assert all(f"start with {stack.entry_file}" in prompt["text"]
               for prompt in approval_prompts[1:])
    assert all(prompt["agent"] == "sage-implement" for prompt in approval_prompts)
    assert all(state.mode is Mode.IMPLEMENT for state in states)
    assert states[0].picked_model == "a"
    assert all(state.picked_model == "p" for state in states[1:])

    done = next(event for event in events if event["type"] == "done")
    assert done == {
        "type": "done",
        "ok": False,
        "decision": "the model replied but didn't change any files — try rephrasing or a smaller step",
    }
    assert app.read_plan() == plan
    assert app.read_plan_retry_step() == 1
    assert app.has_built() is False
    assert project.attachments_for_turn()[0]["path"] == attached["path"]
    assert _standing(project.control.snapshot()) == _standing(before)
    assert gateway.calls == 0
