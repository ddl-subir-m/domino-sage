"""A Chat handoff plan names the files of the stack the app has (#676).

A plan written in Build is shown a worked example in the app's own stack (`_plan_example_for`). The
handoff planner was not: `handoff.plan_prompt` took no stack and carried no example, so with no stack
named the planner picked one, and Sonnet picked React. On a fastapi-antd app every step then named
`src/*.tsx`, the build correctly wrote `static/app.js`, and the plan check (#662, #671) reported that
the build had built none of the plan.

Two guards. The handoff prompt names the stack and carries that stack's example, and the contract
the handoff plan is checked against refuses a file outside the stack's `source_globs`, so a plan
that names another stack's files gets the clean planning retry instead of the approval card.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator.plan_steps import validate_execution_contract
from sage.orchestrator.service import _PLAN_EXAMPLES
from sage.workspace.stack import FASTAPI_ANTD, REACT_VITE

from .fake_opencode import Turn
from .test_model_calls_answers_this_turn_on_chat import (
    CountingOpenCode,
    _chat_orch,
    _no_real_waiting,  # noqa: F401
)


def _plan(stack: str) -> str:
    """The stack's own worked example, which is a plan the contract accepts."""
    example = _PLAN_EXAMPLES[stack]
    return example[example.index("# "):]


def _door(tmp_path: Path, stack: str, plan_texts: tuple[str, ...]):
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text=t) for t in plan_texts])
    orch = _chat_orch(tmp_path, oc)
    settings = orch._chat_project().app_for_turn().path / ".sage" / "settings.json"
    record = json.loads(settings.read_text()) if settings.is_file() else {}
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({**record, "stack": stack}))
    return orch, orch.create_thread()["id"], oc


def _plan_prompts(oc: CountingOpenCode) -> list[str]:
    return [p["text"] for p in oc.prompts if p["agent"] == "sage-plan"]


# ---- the handoff prompt -------------------------------------------------------------------------


@pytest.mark.parametrize("stack, own, other", [
    ("fastapi-antd", "static/components/MainScreen.js", "src/screens/MainScreen.tsx"),
    ("react-vite", "src/screens/MainScreen.tsx", "static/components/MainScreen.js"),
])
def test_the_handoff_prompt_names_the_apps_stack_and_carries_its_example(
        tmp_path: Path, stack: str, own: str, other: str):
    orch, tid, oc = _door(tmp_path, stack, (_plan(stack),))

    orch.draft_handoff_plan(tid)

    [prompt] = _plan_prompts(oc)
    assert stack in prompt
    assert _PLAN_EXAMPLES[stack] in prompt
    assert own in prompt
    assert other not in prompt


def test_a_handoff_plan_naming_another_stacks_files_is_planned_again(tmp_path: Path):
    """The React plan the report saw, on a fastapi-antd app: refused, then retried clean."""
    orch, tid, oc = _door(tmp_path, "fastapi-antd",
                          (_plan("react-vite"), _plan("fastapi-antd")))

    payload = orch.draft_handoff_plan(tid)

    prompts = _plan_prompts(oc)
    assert len(prompts) == 2
    assert "static/**/*" in prompts[1]
    assert "static/components/MainScreen.js" in payload["plan"]


# ---- the contract -------------------------------------------------------------------------------


def _with_files(files: str, dont_touch: str = "") -> str:
    plan = _plan("fastapi-antd")
    plan = plan.replace("- Files — static/components/MainScreen.js, static/app.css", f"- Files — {files}")
    if dont_touch:
        plan = plan.replace("- Don't touch — app.py", f"- Don't touch — {dont_touch}")
    return plan


def test_a_fastapi_antd_plan_naming_a_react_file_is_invalid():
    check = validate_execution_contract(_with_files("src/App.tsx"), stack=FASTAPI_ANTD)
    assert not check.valid
    assert check.invalid_file_fields >= 1


def test_a_react_file_under_dont_touch_is_invalid_too():
    check = validate_execution_contract(
        _with_files("static/app.js", dont_touch="src/App.tsx"), stack=FASTAPI_ANTD)
    assert check.invalid_file_fields >= 1


def test_a_fastapi_antd_plan_naming_its_own_files_and_the_queries_file_is_valid():
    check = validate_execution_contract(
        _with_files("static/app.js, .sage/queries.json"), stack=FASTAPI_ANTD)
    assert check.valid, check


def test_the_react_vite_example_is_valid_for_react_vite_and_not_for_fastapi_antd():
    assert validate_execution_contract(_plan("react-vite"), stack=REACT_VITE).valid
    assert not validate_execution_contract(_plan("react-vite"), stack=FASTAPI_ANTD).valid


def test_without_a_stack_the_contract_is_unchanged():
    """Build-mode plans pass no stack (#676 is handoff-only)."""
    assert validate_execution_contract(_with_files("src/App.tsx")).valid
