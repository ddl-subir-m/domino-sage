"""An app with nothing bound is never told to call a helper nobody explained to it (#596).

The usage of `runQuery`, `askModel`, `checkModel` and `callModelApi` lives in the AGENTS.md regions
spliced in when the matching Resource is bound. The static template used to name them anyway — a
"Carry Data used" rule, the fastapi-antd toolbox row, "Call `sage.askModel`" — and to say "keep the
seeded preview reporter loaded", which nothing explained. Live, with only Datasets bound, the
implement model spent its whole response grepping the app for exactly those names.

The helper FILES stay named: they are on disk in every app and the do-not-edit rule has to cover
them (`test_agents_md_forbids_editing_every_file_that_gets_refreshed`). None of the checked names
is a substring of a file name, so the file list does not trip the check.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.implementation_request import IMPLEMENT_SECTIONS, apply_instruction_profile
from sage.orchestrator.service import Orchestrator
from sage.resources import bound_schema, pinned_model, pinned_model_api
from sage.resources.bindings import KIND_DATA_SOURCE, Binding
from sage.resources.model_api_credentials import Credential
from sage.workspace.manager import WorkspaceManager
from sage.workspace.stack import FASTAPI_ANTD, REACT_VITE

REPO = Path(__file__).resolve().parents[2]
STACKS = (REACT_VITE, FASTAPI_ANTD)
HELPERS = ("runQuery", "askModel", "checkModel", "callModelApi", "onOutcome", "dataUsed")
REPORTER = {
    "react-vite": "import './reportRuntimeError.ts'",
    "fastapi-antd": "static/sage/reportRuntimeError.js",
}
SOURCE = Binding(KIND_DATA_SOURCE, "ds-dwh", "warehouse", "warehouse",
                 "DWH", "MARTS", None, "SnowflakeConfig")
ALIAS = Binding("llm_alias", "id-sonnet", "sonnet", "Claude Sonnet 4.6")


def _seeded_agents(tmp_path: Path, stack) -> str:
    """The AGENTS.md a freshly seeded app of `stack` carries — voiced, as the model reads it."""
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=REPO / "template" / "react-vite")
    ws = mgr.ensure("proj1", stack=stack.name)
    assert ws.stack_name == stack.name
    return (ws.path / "AGENTS.md").read_text()


def _implement_prompt(agents: str) -> str:
    """What an implement turn sends: the app's AGENTS.md as the system message, every optional
    section kept — the most the model can be handed."""
    request = {"model": "m", "messages": [{"role": "system", "content": agents},
                                          {"role": "user", "content": "build it"}]}
    after, report = apply_instruction_profile(request, "implement", sections=IMPLEMENT_SECTIONS)
    assert report["status"] == "valid"
    return json.dumps(after, ensure_ascii=False)


def _spliced(agents: str, begin: str, end: str, block: str) -> str:
    return agents.rstrip() + f"\n\n{begin}\n{block}\n{end}\n"


@pytest.mark.parametrize("stack", STACKS, ids=lambda s: s.name)
def test_with_nothing_bound_the_implement_prompt_names_no_helper(tmp_path: Path, stack):
    # Nothing bound renders every Resource region empty, so the seeded file IS the whole prompt.
    assert bound_schema.agents_block([], None, 5000, names=stack.helpers) == ""
    assert pinned_model.agents_block([], [], stack.helpers) == ""
    assert pinned_model_api.agents_block([], {}, stack.helpers) == ""

    prompt = _implement_prompt(_seeded_agents(tmp_path, stack))

    for name in HELPERS:
        assert name not in prompt, name
    assert "seeded preview reporter" not in prompt
    assert REPORTER[stack.name] in prompt


@pytest.mark.parametrize("stack", STACKS, ids=lambda s: s.name)
def test_a_bound_data_source_carries_the_data_used_guidance(tmp_path: Path, stack):
    block = bound_schema.agents_block(
        [bound_schema.BoundSource(SOURCE, [], [], None)], [], 5000, names=stack.helpers)
    prompt = _implement_prompt(_spliced(_seeded_agents(tmp_path, stack), Orchestrator._DATA_BEGIN,
                                        Orchestrator._DATA_END, block))

    assert "runQuery" in prompt
    for phrase in ("result.dataUsed", "dataUsed.modelView", "row coverage", "truncated state"):
        assert phrase in block, phrase
        assert phrase in prompt, phrase


@pytest.mark.parametrize("stack", STACKS, ids=lambda s: s.name)
def test_a_bound_alias_carries_the_model_outcome_guidance(tmp_path: Path, stack):
    block = pinned_model.agents_block([ALIAS], [], stack.helpers)
    prompt = _implement_prompt(_spliced(_seeded_agents(tmp_path, stack), Orchestrator._MODEL_BEGIN,
                                        Orchestrator._MODEL_END, block))

    for phrase in ("askModel", "checkModel", "onOutcome", "show that model state separately",
                   "never turn it into proof of policy coverage",
                   "do not present the partial text as complete"):
        assert phrase in block, phrase
        assert phrase in prompt, phrase


API = Binding("model_api", "id-fraud", "fraud-scorer", "Fraud Scorer")


@pytest.mark.parametrize("stack", STACKS, ids=lambda s: s.name)
def test_the_model_examples_are_written_in_the_stacks_own_language(stack):
    # A plain-script page has no module loader: `import` there is a syntax error, and the helpers
    # are on `window.sage` already. The Data Source example already says so; these two did not.
    blocks = {"askModel": pinned_model.agents_block([ALIAS], [], stack.helpers),
              "callModelApi": pinned_model_api.agents_block(
                  [API], {API.id: Credential("https://m", "t")}, stack.helpers)}
    for helper, block in blocks.items():
        assert block, helper
        if stack is FASTAPI_ANTD:
            assert "import {" not in block, helper
            assert f"await sage.{helper}(" in block, helper
        else:
            assert f"import {{ {helper}" in block, helper
