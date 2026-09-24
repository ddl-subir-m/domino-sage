"""The actual second dispatch must name this app's entry file (#514)."""
from pathlib import Path

import pytest

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


def _approve(tmp_path: Path, stack_name: str, mode: Mode, *, edits: bool):
    stack = STACKS[stack_name]
    plan = "# Dashboard\n\n## Plan\n1. Show the selected sales data with a region filter."
    turns = [Turn(text=plan), Turn(text="I will add a table and a region filter.")]
    if edits:
        turns.append(Turn(writes={stack.entry_file: "// region filter implemented\n"}))
    orch, oc, gateway = _build(tmp_path, turns)
    orch.create_app(stack=stack_name)
    project = orch.project(start_preview=False)
    data = tmp_path / "sales.csv"
    data.write_text("region,sales\nNorth,7\n")
    attached = orch.attach_file("ds_sales_2026", "sales.csv", local_source=data)
    list(orch.build_stream("Build a sales dashboard with a region filter.", [attached["path"]]))
    project.control.set_mode(mode)
    project.control.pick("a", "low")
    before = project.control.snapshot()
    dispatch_states = []
    send = oc.send_prompt

    def record_dispatch(*args, **kwargs):
        dispatch_states.append(project.control.snapshot())
        return send(*args, **kwargs)

    oc.send_prompt = record_dispatch
    events = list(orch.approve_stream(answers="Keep the North region visible."))
    return orch, oc, gateway, before, dispatch_states, events, attached


@pytest.mark.parametrize("stack_name", STACKS)
@pytest.mark.parametrize("mode", [Mode.AUTO, Mode.IMPLEMENT])
@pytest.mark.parametrize("edits", [True, False], ids=["retry-edits", "retry-exhausted"])
def test_recovery_dispatch_and_bound(tmp_path: Path, stack_name: str, mode: Mode, edits: bool):
    orch, oc, gateway, before, states, events, attached = _approve(
        tmp_path, stack_name, mode, edits=edits)
    first, *retries = oc.prompts[1:]
    assert len(retries) == (1 if edits else 3)
    assert oc.prompts[0]["attachments"][0]["path"] == attached["path"]
    # The user did not name a structured reference, so approval and its retries keep the
    # active session context without resending the broad planning attachment list.
    assert first["attachments"] is None
    for retry in retries:
        assert f"start with {STACKS[stack_name].entry_file}" in retry["text"]
        assert retry["agent"] == "sage-implement"
        # OpenCode retains the original request and approved plan in this session.
        assert retry["session"] == first["session"] == oc.prompts[0]["session"]
        assert retry["attachments"] is None
    assert "Build a sales dashboard with a region filter." in oc.prompts[0]["text"]
    assert "Show the selected sales data with a region filter." in first["text"]
    assert "Keep the North region visible." in first["text"]
    assert all(state.mode is Mode.IMPLEMENT for state in states)
    assert states[0].picked_model == "a"
    assert all(state.picked_model == "p" for state in states[1:])
    after = orch.project(start_preview=False).control.snapshot()
    assert (after.mode, after.picked_model, after.picked_effort) == (
        before.mode, before.picked_model, before.picked_effort)
    assert gateway.calls == 0, "the approved retry must not add a classifier call"
    done = next(e for e in events if e["type"] == "done")
    assert done["ok"] is edits
    assert "plan-proposed" not in {e["type"] for e in events}
    if not edits:
        assert not orch.project(start_preview=False).workspace.has_built()


@needs_ledger
@pytest.mark.parametrize("stack_name", STACKS)
@pytest.mark.parametrize("edits", [True, False])
def test_recovery_records_bounded_metadata(tmp_path: Path, stack_name: str, edits: bool):
    _approve(tmp_path, stack_name, Mode.AUTO, edits=edits)
    spans = [s for s in last_turn().spans if s.name.startswith("agent-turn.")]
    assert len(spans) == (2 if edits else 4)
    for attempt, span in enumerate(spans):
        assert span.fields["stack"] == stack_name
        assert span.fields["no_edit_attempt"] == attempt
        wrote = edits and attempt == 1
        assert span.fields["wrote_code"] is wrote
        if not wrote:
            assert span.fields["retry_reason"] == "no_edit"
            assert span.fields["retry_exhausted"] is (attempt == 3)
