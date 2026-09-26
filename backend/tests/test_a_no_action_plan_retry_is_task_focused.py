"""The one clean no-action planning retry is a different, shorter request that keeps the task (#561).

MEASURED 2026-09-25 (Xiaomi, #557): two roughly 120-second reasoning-only plan calls and no plan.
The clean recovery re-sent the SAME composed prompt into a fresh session: the preamble, the voice,
the refusal way-out, the plan shape, the worked example, the person's request, the Chat background,
every note, and the request once more as the tail. A model that produced nothing on that input was
given exactly that input again.

The retry is now rebuilt from the inputs the first send was composed from (`PlanRetryInput`), never
by scraping the composed prompt or asking a model to summarise its own silence. It keeps every
identity and every constraint — the request in the person's words, the stack, the attached files
with their paths, the Resource identities, the plan contract — and drops what was a repetition or
background: the worked example (a second statement of the shape), the refusal way-out, the Chat
summary, the dropped-mention notes, and the second copy of the request. The correction says what
happened and what to do: return the plan directly.

Both planner entry points are covered: the gated turn in `build_stream` and the helper the Chat
handoff runs (`_run_sage_execution_plan` -> `_run_sage_plan`), which share one `PlanRecoveryBudget`.
The `done` row of the gated path carries the ADR-0069 fields, and `cause` is written only on a
confirmed-idle terminal; every other ending here is planted to prove it writes none.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from sage import build_diagnostics, timing
from sage.gateway.protocol import Protocol
from sage.orchestrator.service import (
    _PLAN_EXAMPLES,
    _PLAN_REQUEST_LABEL,
    _PLAN_RETRY_CORRECTION,
    PlanRetryInput,
)

from .fake_opencode import Turn, execution_plan
from .test_a_prompt_naming_no_app_asks_what_to_build import (  # noqa: F401  (_no_waiting: autouse)
    _build,
    _done,
    _no_waiting,
    _run,
)
from .test_native_model_controls import active, dispatch, running  # noqa: F401  (fixture)
from .test_phased_build import PROSE_PLAN

_ASK = "Build a table of lab samples with a late flag from the attached arrivals file."


def _no_action_on(oc, project, *sends: int, chunks: int = 8) -> None:
    """Make the Nth send_prompt end as the watchdog ends a reasoning-only call (deterministic)."""
    original = oc.send_prompt
    count = {"n": 0}

    def send(*args, **kwargs):
        count["n"] += 1
        original(*args, **kwargs)
        if count["n"] in sends:
            project.last_gateway_error = {
                "code": "model_no_action_timeout", "message": "safe",
                "call_id": f"call-{count['n']}", "turn_id": "turn",
                "elapsed_ms": 120_000, "chunk_count": chunks, "reasoning_only_chunks": chunks,
            }

    oc.send_prompt = send


def _of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e.get("type") == kind]


def _saved_done(project) -> dict:
    rows = [json.loads(line) for line in project.workspace.history_path.read_text().splitlines()
            if line.strip()]
    return [row for row in rows if row.get("type") == "done"][-1]


def _diagnostics(project) -> dict:
    return json.loads((project.record.path / ".sage" / "build-diagnostics.json").read_text())


# --- 1. the retry is different, shorter, and still the same task --------------------------------

def test_the_no_action_retry_is_a_different_shorter_request_that_keeps_the_task(tmp_path: Path):
    orch, oc = _build(tmp_path, [Turn(), Turn(text=execution_plan())])
    orch.upload_file("arrivals.csv", b"sample_id,site,due_date\n1,A,2026-01-01\n")
    project = orch.project(start_preview=False)
    _no_action_on(oc, project, 1)

    events = _run(orch, _ASK)

    assert _done(events)["decision"] == "awaiting approval"
    first, retry = oc.prompts
    assert retry["session"] != first["session"]
    # Same agent, same model handle: the retry changes the request, never the effort or the model.
    assert retry["agent"] == "sage-plan" and retry["model"] == first["model"]
    # Different, and shorter: the point of the retry.
    assert retry["text"] != first["text"]
    assert len(retry["text"]) < len(first["text"])
    assert retry["text"].startswith(_PLAN_RETRY_CORRECTION)
    # The duplicated blocks are gone: the worked example restates the shape, the way-out is for a
    # first send with nothing attached, and the request is said once, not twice.
    assert "An example of the SHAPE only" in first["text"]
    assert "An example of the SHAPE only" not in retry["text"]
    assert "NO APP DESCRIBED" not in retry["text"]
    # The constraints ride: the stack the first send's example was chosen for, named outright, and
    # every required plan section of the contract.
    stack = next(name for name, example in _PLAN_EXAMPLES.items() if example in first["text"])
    assert f"Stack: {stack}." in retry["text"]
    for section in ("'# '", "'## Problem & outcome'", "'## Who uses this'", "'## What it does'",
                    "'## Screens'", "'## Data'", "'## Done when'", "'## Plan'", "'### N. Label'"):
        assert section in retry["text"], section
    # The identities ride: the attached file with its path, and the request in the person's words,
    # once, LAST — after the attachment listing, which is where #537 wants it.
    assert [a["name"] for a in retry["attachments"]] == ["arrivals.csv"]
    assert retry["attachments"][0]["path"] == first["attachments"][0]["path"]
    assert retry["tail"] == _PLAN_REQUEST_LABEL + _ASK
    assert _ASK not in retry["text"]
    assert (retry["text"] + retry["tail"]).count(_ASK) == 1


def test_a_retry_with_nothing_attached_ends_on_the_request_and_carries_no_tail(tmp_path: Path):
    orch, oc = _build(tmp_path, [Turn(), Turn(text=execution_plan())])
    _no_action_on(oc, orch.project(start_preview=False), 1)

    events = _run(orch, _ASK)

    assert _done(events)["decision"] == "awaiting approval"
    retry = oc.prompts[1]
    assert retry["text"].endswith(_PLAN_REQUEST_LABEL + _ASK)
    assert retry["text"].count(_ASK) == 1
    assert retry["tail"] == "" and not retry["attachments"]
    assert "NO APP DESCRIBED" in oc.prompts[0]["text"]
    assert "NO APP DESCRIBED" not in retry["text"]


def test_the_retry_input_is_composed_from_inputs_and_carries_resource_identities():
    """The unit: identities and constraints in, correction first, request last, nothing scraped."""
    retry = PlanRetryInput(
        request="Show the daily usage per team.", stack="react-vite", voice="VOICE", shape="SHAPE",
        resource_note="Resources mentioned: Data Source `dwh` (binding ds_42), table `FCT_USAGE`.",
        source_note="Existing source paths: [\"src/App.tsx\"]",
        attachments=({"name": "a.csv", "path": "public/data/a.csv"},))

    prompt = retry.prompt()

    assert prompt.startswith(_PLAN_RETRY_CORRECTION)
    assert "Stack: react-vite." in prompt
    assert "binding ds_42" in prompt and "FCT_USAGE" in prompt
    assert "src/App.tsx" in prompt
    assert prompt.endswith(_PLAN_REQUEST_LABEL + "Show the daily usage per team.")
    assert "Show the daily usage per team." not in retry.body()


# --- 2 and 8. a valid retry is approvable; a second failure closes with the ADR-0069 fields -------

def test_a_second_no_action_closes_honestly_with_no_third_attempt_and_names_its_cause(
        tmp_path: Path):
    orch, oc = _build(tmp_path, [Turn(), Turn()])
    project = orch.project(start_preview=False)
    _no_action_on(oc, project, 1, 2)

    events = _run(orch, _ASK)

    done = _done(events)
    assert done["ok"] is False and done["decision"] == "model_no_action_timeout"
    assert done["cause"] == "model_no_action" and done["stage"] == "planning"
    assert done["recoveries"] == 1 and done["turnId"]
    assert not _of(events, "plan-proposed")
    assert len(oc.prompts) == 2 and len(oc.sessions) == 2
    # The terminal asked the old session to stop and it confirmed: one interrupt for the
    # recovery, one for the terminal.
    assert oc.interrupted == 2
    # The saved half is the same row.
    saved = _saved_done(project)
    assert {k: saved[k] for k in ("cause", "stage", "recoveries", "turnId", "decision")} == {
        k: done[k] for k in ("cause", "stage", "recoveries", "turnId", "decision")}
    assert "cause" not in _of(events, "error")[0]


def test_a_no_action_recover_then_an_invalid_plan_stop_keeps_both_decisions(tmp_path: Path):
    """The case ADR-0069 gives as the reason `planningRecoveries` exists: the slot keeps the stop."""
    orch, oc = _build(tmp_path, [Turn(), Turn(text=PROSE_PLAN)])
    project = orch.project(start_preview=False)
    _no_action_on(oc, project, 1)

    events = _run(orch, _ASK)

    done = _done(events)
    assert done["decision"] == "invalid execution plan" and done["ok"] is False
    # Not a `model_no_action` terminal, so no cause and no stage; the count and the id still ride.
    assert "cause" not in done and "stage" not in done
    assert done["recoveries"] == 1 and done["turnId"]
    assert len(oc.prompts) == 2 and not _of(events, "plan-proposed")
    record = _diagnostics(project)["records"][-1]
    assert record["planningRecoveries"] == [
        {"attempt": "initial", "trigger": "model_no_action", "action": "recover"},
        {"attempt": "recovery", "trigger": "invalid_execution_plan", "action": "stop"},
    ]
    assert record["planningRecovery"] == record["planningRecoveries"][-1]
    assert record["schemaVersion"] == build_diagnostics.SCHEMA_VERSION == 1


def test_a_successful_plan_after_one_recovery_says_so_on_its_done_row(tmp_path: Path):
    orch, oc = _build(tmp_path, [Turn(), Turn(text=execution_plan())])
    project = orch.project(start_preview=False)
    _no_action_on(oc, project, 1)

    done = _done(_run(orch, _ASK))

    assert done["ok"] is True and done["recoveries"] == 1 and done["turnId"]
    assert "cause" not in done
    assert _saved_done(project)["recoveries"] == 1


def test_a_clean_plan_carries_its_turn_id_and_no_count(tmp_path: Path):
    orch, _oc = _build(tmp_path, [Turn(text=execution_plan())])

    done = _done(_run(orch, _ASK))

    assert done["ok"] is True and done["turnId"]
    assert "recoveries" not in done and "cause" not in done


def test_the_recorder_keeps_every_decision_up_to_the_budget_plus_one():
    timing.start_turn("build", turn_id="turn")
    try:
        record = timing.current()
        timing.planning_recovery("model_no_action", "initial", "recover", record=record, limit=1)
        timing.planning_recovery("invalid_execution_plan", "recovery", "stop", record=record,
                                 limit=1)
        timing.planning_recovery("invalid_execution_plan", "recovery", "stop", record=record,
                                 limit=1)
        # A shape the normaliser refuses is dropped at the boundary, not copied.
        record.planning_recoveries.append({"attempt": "initial", "trigger": "PRIVATE", "action": "x"})
    finally:
        timing.finish_turn()

    exported = build_diagnostics.snapshot(
        record, {"turnId": "turn", "appId": "app", "conversationId": "c", "kind": "build"},
        terminal=True)

    assert exported["planningRecoveries"] == [
        {"attempt": "initial", "trigger": "model_no_action", "action": "recover"},
        {"attempt": "recovery", "trigger": "invalid_execution_plan", "action": "stop"},
    ]
    assert exported["planningRecovery"] == exported["planningRecoveries"][-1]
    assert "PRIVATE" not in json.dumps(exported)


# --- 3. the helper planner shares the one budget, in both orderings -------------------------------

@pytest.mark.parametrize("no_action_first", [True, False])
def test_the_helper_planner_shares_one_budget_in_both_orderings(tmp_path: Path, no_action_first):
    turns = [Turn(), Turn(text=PROSE_PLAN)] if no_action_first else [Turn(text=PROSE_PLAN), Turn()]
    orch, oc = _build(tmp_path, turns)
    project = orch.project(start_preview=False)
    _no_action_on(oc, project, 1 if no_action_first else 2)
    sid = oc.create_session(directory=str(project.workspace.path))
    retry = PlanRetryInput(request="write a plan", stack="fastapi-antd", voice="VOICE",
                           shape="SHAPE")

    with pytest.raises(ValueError, match=("also invalid" if no_action_first
                                          else "also produced no text")):
        orch._run_sage_execution_plan(project, "write a plan", sid, where="test",
                                      source_request_count=1, retry=retry)

    assert len(oc.prompts) == 2 and len(oc.sessions) == 2
    if no_action_first:
        assert oc.prompts[1]["text"] == retry.prompt()
    else:
        assert "required execution-plan structure" in oc.prompts[1]["text"]


# --- 4. endings that cannot become a plan, a second writer, or a `cause` -------------------------

def test_a_provider_terminal_error_is_not_retried_and_writes_no_cause(tmp_path: Path):
    orch, oc = _build(tmp_path, [Turn(), Turn(text=execution_plan())])
    project = orch.project(start_preview=False)
    original = oc.send_prompt

    def refused(*args, **kwargs):
        original(*args, **kwargs)
        project.last_gateway_error = {"message": "provider failed", "code": "provider_error"}

    oc.send_prompt = refused

    events = _run(orch, _ASK)

    done = _done(events)
    assert done["ok"] is False and "cause" not in done
    assert not _of(events, "plan-proposed") and not _of(events, "iterate")
    assert len(oc.prompts) == 1 and len(oc.sessions) == 1


def test_a_stop_during_clean_session_creation_writes_no_plan_and_no_cause(tmp_path: Path):
    orch, oc = _build(tmp_path, [Turn(), Turn(text=execution_plan())])
    project = orch.project(start_preview=False)
    _no_action_on(oc, project, 1)
    create = oc.create_session

    def stop_wins_during_creation(directory, **kwargs):
        sid = create(directory, **kwargs)
        if len(oc.sessions) == 2:
            project.stop_requested = True
        return sid

    oc.create_session = stop_wins_during_creation

    events = _run(orch, _ASK)

    assert _of(events, "stopped")
    assert not _of(events, "plan-proposed") and not _of(events, "iterate")
    assert len(oc.prompts) == 1
    assert all("cause" not in row for row in _of(events, "done"))
    assert project.stop_requested is False


def test_a_refused_stop_on_the_recovery_wedges_with_no_cause(tmp_path: Path, monkeypatch):
    orch, oc = _build(tmp_path, [Turn(), Turn(text=execution_plan())])
    project = orch.project(start_preview=False)
    _no_action_on(oc, project, 1)
    monkeypatch.setattr(orch, "_stop_wedged_session", lambda *_a, **_k: False)

    events = list(orch.build_stream(_ASK, None, None))

    assert _of(events, "build-stalled")
    assert _done(events)["decision"] == "wedged" and "cause" not in _done(events)
    assert len(oc.prompts) == 1 and len(oc.sessions) == 1


def test_a_refused_stop_on_the_terminal_no_action_writes_no_cause(tmp_path: Path, monkeypatch):
    """`cause` is the promise the old session is idle: an unconfirmed stop keeps the rows, drops
    the promise."""
    orch, oc = _build(tmp_path, [Turn(), Turn()])
    project = orch.project(start_preview=False)
    _no_action_on(oc, project, 1, 2)
    answers = iter([True, False])
    monkeypatch.setattr(orch, "_stop_wedged_session", lambda *_a, **_k: next(answers))

    events = _run(orch, _ASK)

    done = _done(events)
    assert done["decision"] == "model_no_action_timeout" and done["ok"] is False
    assert "cause" not in done and "stage" not in done
    assert done["recoveries"] == 1 and done["turnId"]
    assert len(oc.prompts) == 2


def test_a_retry_is_refused_when_the_turn_changed_owner(tmp_path: Path, monkeypatch):
    orch, oc = _build(tmp_path, [Turn(), Turn(text=execution_plan())])
    project = orch.project(start_preview=False)
    original = oc.send_prompt
    real_running = orch._turns.running
    readings = {"left": 2}

    def someone_else_twice():
        # Two readings follow the send: the poll loop's own `active_turn`, then the ownership
        # check before the retry. Every later reading (the turn lock's release among them) sees
        # the real owner again, so the lock is handed back.
        readings["left"] -= 1
        if readings["left"] == 0:
            orch._turns.running = real_running
        return SimpleNamespace(id="someone-else")

    def no_action_under_a_new_owner(*args, **kwargs):
        original(*args, **kwargs)
        project.last_gateway_error = {
            "code": "model_no_action_timeout", "message": "safe", "call_id": "call-1",
            "turn_id": "turn", "elapsed_ms": 120_000, "chunk_count": 8}
        monkeypatch.setattr(orch._turns, "running", someone_else_twice)

    oc.send_prompt = no_action_under_a_new_owner

    events = _run(orch, _ASK)

    done = _done(events)
    assert done["ok"] is False and done["decision"] == "planning session unavailable"
    assert "cause" not in done and done["turnId"] != "someone-else"
    assert not _of(events, "iterate") and not _of(events, "plan-proposed")
    assert len(oc.prompts) == 1 and len(oc.sessions) == 1


# --- 6. the watchdog on a fake clock: first action at 113 s wins, reasoning-only at 120 s ends --

def _sse(event: dict) -> bytes:
    return b"data: " + json.dumps(event).encode() + b"\n\n"


_REASONING = {"choices": [{"delta": {"reasoning_content": "PRIVATE"}}]}
_TEXT = {"choices": [{"delta": {"content": "# Plan"}}]}
_STOP = {"choices": [{"delta": {}, "finish_reason": "stop"}]}


@pytest.mark.parametrize(("frames", "ticks", "timed_out"), [
    # begin, started-race, then two reads per chunk: reasoning at 31 s, text at 113 s.
    ([_REASONING, _TEXT, _STOP],
     (0.0, 1.0, 31.0, 31.0, 113.0, 113.0, 128.0, 128.0, 129.0), False),
    # Reasoning, then another chunk after ≥120 s idle from lastChunkAt (race consumes one tick).
    ([_REASONING, _REASONING],
     (0.0, 1.0, 31.0, 50.0, 170.0, 170.0, 171.0, 172.0, 173.0), True),
], ids=["text_at_113_completes_at_128", "reasoning_idle_gap_at_120"])
def test_first_action_at_113_s_completes_while_reasoning_idle_gap_times_out(
        running, monkeypatch, frames, ticks, timed_out):  # noqa: F811
    from sage.gateway.client import FakeGatewayClient
    from sage.orchestrator import native_routes

    client, orch, _ = running
    assert client.post(
        "/api/project/model", json={"mode": "plan", "pick": "GLM 5.3 OR"}).status_code == 200
    orch._build_policy = replace(
        orch._build_policy, model_no_action_notice_seconds=30,
        model_no_action_timeout_seconds=120)
    clock = iter(ticks)
    monkeypatch.setattr(
        native_routes, "time", SimpleNamespace(monotonic=lambda: next(clock, ticks[-1])))

    class ScriptedGateway(FakeGatewayClient):
        def route(self, request, labels, *, protocol, cancel):
            for frame in frames:
                yield _sse(frame)

    orch._project.shim._gateway = ScriptedGateway()
    timing.start_turn("build", turn_id="turn")
    try:
        with active(orch) as headers:
            response = dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR")
    finally:
        record = timing.finish_turn()

    assert response.status_code == 200
    call = timing.as_dict(record)["calls"][0]
    error = orch._project.last_gateway_error
    if timed_out:
        assert error["code"] == "model_no_action_timeout"
        assert error["reasoning_only_chunks"] == 2 and error["chunk_count"] == 2
        assert call["outcome"] == "no_action_timeout"
        assert "sage_gateway_error" in response.text
    else:
        assert error is None
        assert call["outcome"] == "success" and call["firstActionKind"] == "text"
        assert "sage_gateway_error" not in response.text
    # The reasoning frames reach OpenCode as the model sent them (ADR-0066); what must not carry
    # them is Sage's own record of the call.
    assert "PRIVATE" not in json.dumps(call)
