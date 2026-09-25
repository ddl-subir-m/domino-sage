"""Defensive prompt work for weaker plan and implement models (#538).

MEASURED 2026-09-24 (`build-turn_1a0d4c39b4a31ccec9596.json`, GLM 5.3 OR, effort Model default): a
TFL plan turn read a 44.8 KB system prompt and a 14 KB user message whose request was 0.4 KB. The
first plan call reasoned for 120 s and wrote nothing; the same request then acted in 3.5 s. #537
fixed the refusal. This file covers what a weaker model is given to copy, to read, and to be told.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator.plan_steps import validate_execution_contract
from sage.orchestrator.service import _PLAN_EXAMPLES, _PLAN_SHAPE

from .fake_opencode import Turn
from .test_a_prompt_naming_no_app_asks_what_to_build import (  # noqa: F401  (_no_waiting: autouse)
    _REFUSAL,
    _build,
    _no_waiting,
    _run,
)
from .test_native_model_controls import running  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parents[2]


# --- a worked example plan -----------------------------------------------------------------------

@pytest.mark.parametrize("stack", ["fastapi-antd", "react-vite"])
def test_the_example_plan_passes_the_contract_it_teaches(stack):
    """An example the contract refuses would teach the planner to write a refused plan."""
    example = _PLAN_EXAMPLES[stack]
    check = validate_execution_contract(example[example.index("# "):])
    assert check.valid, check
    assert check.step_count == 2


@pytest.mark.parametrize("stack", ["fastapi-antd", "react-vite"])
def test_the_example_names_only_files_its_stack_has(stack):
    """A path the template does not have is a path the planner will copy into a real plan."""
    example = _PLAN_EXAMPLES[stack]
    files = {f.strip() for line in example.splitlines() if line.startswith(("- Files — ",
                                                                             "- Don't touch — "))
             for f in line.split(" — ", 1)[1].split(",")}
    template = REPO / "template" / stack
    new_files = {"src/arrivals.ts"}  # the example's data step CREATES this one
    assert {f for f in files if not (template / f).is_file()} <= new_files


def test_a_gated_plan_turn_carries_the_example(tmp_path: Path):
    orch, oc = _build(tmp_path, [Turn(text=_REFUSAL)])

    _run(orch, "build me a table of lab samples")

    assert "An example of the SHAPE only" in oc.prompts[0]["text"]


def test_the_chat_handoff_shape_does_not_carry_the_example():
    """`_PLAN_SHAPE` is shared with the Chat handoff, which plans from a whole conversation."""
    assert "An example of the SHAPE only" not in _PLAN_SHAPE


# --- shorter documents on a plan turn ------------------------------------------------------------

_SPEC = ("# TFL shell\n\n" + "Programming note: N is the count of subjects in the arm. " * 150).encode()


def _detail(prompt: dict, name: str) -> str:
    return next(a["detail"] for a in prompt["attachments"] if a["path"].endswith(name))


def test_a_plan_turn_sends_a_long_document_shortened_with_its_path(tmp_path: Path):
    from sage.liveread.reference import PLAN_SELECTED_CHARS

    orch, oc = _build(tmp_path, [Turn(text=_REFUSAL)])
    path = orch.upload_file("shell.md", _SPEC)["path"]

    _run(orch, "build the table in the attached shell")

    detail = _detail(oc.prompts[0], "shell.md")
    body = detail.split("--- BEGIN PREPARED REFERENCE ---\n")[1].split("\n--- END")[0]
    assert len(body) == PLAN_SELECTED_CHARS
    assert f"shortened to {PLAN_SELECTED_CHARS:,} characters for planning. Read {path}" in detail


def test_the_approved_build_gets_the_document_whole(tmp_path: Path):
    """The cut is the plan turn's alone: `plan_record` keeps identity, and approval prepares again."""
    from sage.liveread.reference import MAX_SELECTED_CHARS

    from .fake_opencode import execution_plan

    orch, oc = _build(tmp_path, [
        Turn(text=execution_plan("Shell Table", "A table.", "Table", include_title=False)),
        Turn(text="Shell Table"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
    ])
    orch.upload_file("shell.md", _SPEC)
    _run(orch, "build the table in the attached shell")

    list(orch.approve_stream())

    detail = _detail(oc.prompts[-1], "shell.md")
    assert "for planning" not in detail
    body = detail.split("--- BEGIN PREPARED REFERENCE ---\n")[1].split("\n--- END")[0]
    assert len(body) == min(len(_SPEC.decode()), MAX_SELECTED_CHARS)


def test_only_a_longer_document_is_cut():
    from sage.liveread.reference import PLAN_SELECTED_CHARS, Prepared, for_planning

    def prepared(kind: str, n: int) -> Prepared:
        return Prepared(source="public/data/x", source_type=kind, text="x" * n,
                        requested_selector="", selected_selector="", source_bytes=n,
                        selected_characters=n, sent_characters=n, truncated=False,
                        status="prepared", source_sha256="", selected_sha256="")

    short = prepared("markdown", PLAN_SELECTED_CHARS)
    assert for_planning(short) is short
    table = prepared("table", 5000)
    assert for_planning(table) is table
    cut = for_planning(prepared("pdf", 5000))
    assert (len(cut.text), cut.sent_characters, cut.truncated) == (PLAN_SELECTED_CHARS,) * 2 + (True,)


# --- the effort hint when a planner stalls -------------------------------------------------------

def test_a_stalled_call_says_what_effort_it_ran_on(running, monkeypatch):  # noqa: F811
    """The hint reads these off the stall error itself, not the timing record, which can be off."""
    import json
    from dataclasses import replace
    from types import SimpleNamespace

    from sage import timing
    from sage.gateway.client import FakeGatewayClient
    from sage.gateway.protocol import Protocol
    from sage.orchestrator import native_routes

    from .test_native_model_controls import active, dispatch

    client, orch, _ = running
    client.post("/api/project/model", json={"mode": "plan", "pick": "GLM 5.3 OR"})
    orch._build_policy = replace(orch._build_policy, model_no_action_notice_seconds=30,
                                 model_no_action_timeout_seconds=120)
    ticks = iter((0.0, 1.0, 31.0, 31.0, 121.0, 121.0, 122.0, 123.0, 124.0))
    monkeypatch.setattr(native_routes, "time",
                        SimpleNamespace(monotonic=lambda: next(ticks, 124.0)))

    class Thinking(FakeGatewayClient):
        def route(self, request, labels, *, protocol, cancel):
            for _ in range(2):
                chunk = {"choices": [{"delta": {"reasoning_content": "thinking"}}]}
                yield b"data: " + json.dumps(chunk).encode() + b"\n\n"

    orch._project.shim._gateway = Thinking()
    timing.start_turn("build", turn_id="turn")
    try:
        with active(orch) as headers:
            dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR")
        record = timing.finish_turn()
    finally:
        if timing.current() is not None:
            timing.finish_turn()

    err = orch._project.last_gateway_error
    call = timing.as_dict(record)["calls"][0]
    assert err["code"] == "model_no_action_timeout"
    assert err["model"] == "GLM 5.3 OR"
    assert err["effort_source"] == call["effortSource"]
    assert err["effort"] == call["effectiveEffort"]  # what reached the WIRE, which is what the
    assert err["efforts"] == ["low", "high", "max"]  # hint keys on (#545); the measured row


def test_the_hint_names_the_absent_limit_and_the_levels_it_could_use():
    from sage.orchestrator.service import _effort_hint

    hint = _effort_hint({"effort": None, "effort_source": "provider_default",
                         "model": "GLM 5.3 OR", "efforts": ["low", "high", "max"]}, "Plan")
    assert "GLM 5.3 OR ran with no limit on its thinking" in hint
    assert "Setting its Plan effort (low, high, max)" in hint


def test_the_hint_names_the_stage_whose_row_would_fix_it():
    """Plan and Implement are two rows in the drawer, and a person reading an implement stall is
    being pointed at the one they can act on (#545)."""
    from sage.orchestrator.service import _effort_hint

    err = {"effort": None, "effort_source": "stage_default", "model": "gpt-5.4",
           "efforts": ["none"]}
    assert "Setting its Implement effort (none)" in _effort_hint(err, "Implement")
    assert "Setting its Plan effort (none)" in _effort_hint(err, "Plan")


def test_a_dropped_stage_level_still_gets_the_hint():
    """The case `effort_source` could not see. Since #545 an unset Build level resolves to the
    stage default, and gpt-5.4 keeps only `none` beside tools — so the field is dropped and the
    turn runs unlimited while carrying a source that is not `provider_default`. Keyed on the
    source, this turn stalled in silence."""
    from sage.orchestrator.service import _effort_hint

    hint = _effort_hint({"effort": None, "effort_source": "stage_default", "model": "gpt-5.4",
                         "efforts": ["none"]}, "Implement")
    assert "gpt-5.4 ran with no limit on its thinking" in hint


def test_the_implement_timeout_message_carries_the_hint(tmp_path: Path):
    """The site, not the sentence (#545). `_effort_hint` was written for #538 and wired only into
    the two PLAN no-action messages; the implement stall this issue reports ended with "Existing
    app changes were kept." and no way out named. This drives the real pre-edit path: one turn
    writes, which DISARMS the guard, and the next call produces nothing."""
    from .fake_opencode import execution_plan

    orch, oc = _build(tmp_path, [
        Turn(text=execution_plan("Shell Table", "A table.", "Table", include_title=False)),
        Turn(text="Shell Table"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
    ])
    _run(orch, "build me a table")
    project = orch.project(start_preview=False)
    send_prompt = oc.send_prompt

    def stall_after_the_write(*args, **kwargs):
        send_prompt(*args, **kwargs)
        project.last_gateway_error = {
            "code": "model_no_action_timeout", "message": "safe", "call_id": "call-1",
            "turn_id": "turn", "elapsed_ms": 120_000, "chunk_count": 20,
            "model": "GLM 5.3 OR", "effort": None, "effort_source": "stage_default",
            "efforts": ["low", "high", "max"],
        }

    oc.send_prompt = stall_after_the_write

    events = list(orch.approve_stream())

    message = next(e["message"] for e in events
                   if e.get("type") == "error" and "no next action" in e.get("message", ""))
    assert "GLM 5.3 OR ran with no limit on its thinking" in message
    assert "Setting its Implement effort (low, high, max)" in message


def test_no_hint_once_a_level_ran_or_where_the_model_takes_none():
    from sage.orchestrator.service import _effort_hint

    assert _effort_hint({"effort": "low", "effort_source": "user", "efforts": ["low"]}, "Plan") == ""
    assert _effort_hint(
        {"effort": "high", "effort_source": "stage_default", "efforts": ["high"]}, "Plan") == ""
    assert _effort_hint({"effort": None, "effort_source": "provider_default",
                         "efforts": []}, "Implement") == ""
    assert _effort_hint({}, "Plan") == ""


# --- what the final check rejects, first in the implement rules ----------------------------------

@pytest.mark.parametrize("stack", ["fastapi-antd", "react-vite"])
def test_the_implement_rules_open_on_what_the_check_rejects(stack):
    """A weaker model loses a rule that sits among forty; the ones that cost a repair turn go first.

    Read against `feedback/runner.py`, so the block cannot name a file the check does not."""
    runner = (REPO / "backend" / "sage" / "feedback" / "runner.py").read_text()
    text = (REPO / "template" / stack / "AGENTS.md").read_text()
    begin = text.index("<!-- sage:build-profile:v1:implement:begin -->")
    end = text.index("<!-- sage:build-profile:v1:implement:end -->")
    block = text[begin:end]
    first = block.index("## What the final check rejects")
    assert first < block.index("## Implementation turn")
    section = block[first:block.index("## Implementation turn")]
    assert len(section.encode()) <= 600
    placeholder = "static/app.js" if stack == "fastapi-antd" else "src/App.tsx"
    assert f'file="{placeholder}", line=1, col=1, code="SAGE001"' in runner
    assert f"SAGE001`: the starter placeholder is still the screen in `{placeholder}`" in section
