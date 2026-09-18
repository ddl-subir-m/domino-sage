"""Mode-specific input costs must not remove data access or change another mode's tools."""
import copy
import json
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog
from sage.shim.enforcement import EnforcementShim

from .fake_opencode import Turn
from .test_chat_turn import _orch


@pytest.mark.parametrize("model", ["sonnet", "gpt-5.4", "Gemma 4 31B", "domino/gemini-3.7-flash"])
def test_chat_keeps_data_skills_and_delegation_without_rewriting_history_or_the_model(model):
    control = ModelControl(mode=Mode.IMPLEMENT)
    catalog = ModelCatalog(model, model, model, model, model, model)
    gateway = FakeGatewayClient()
    shim = EnforcementShim(control, catalog, gateway)
    names = ["skill", "task", "todowrite", "read", "glob", "grep", "bash", "apply_patch",
             "sage-live-read_live_read_table", "sage-live-read_live_read_files"]
    request = {"model": model, "tools": [{"type": "function", "function": {"name": n}} for n in names],
               "messages": [{"role": "assistant", "tool_calls": [{"id": "old", "type": "function",
                             "function": {"name": "task", "arguments": "{}"}}]},
                            {"role": "tool", "tool_call_id": "old", "content": "prior result"}]}
    original = copy.deepcopy(request)
    token = control.arm_chat("thread")
    list(shim.handle(request, project="p"))
    sent = gateway.seen[-1][0]
    assert sent["model"] == model
    # `todowrite` is the one exception, and only since #400: a Chat turn answers and returns, so a
    # task list on it promises a build that cannot arrive. It does not weaken what this test is for.
    # The 2026-09-14 profile this pins was rejected for losing the skill catalogue and preventing
    # delegation — `skill` and `task`, which both still survive below. The task list is neither.
    assert {t["function"]["name"] for t in sent["tools"]} == set(names) - {"todowrite"}
    assert {"skill", "task"} <= {t["function"]["name"] for t in sent["tools"]}
    assert sent["messages"] == original["messages"]
    assert request == original
    control.disarm_chat(token)
    list(shim.handle(request, project="p"))
    assert gateway.seen[-1][0]["tools"] == original["tools"], "Chat's tool selection leaked into Build"


def test_chat_and_build_keep_skills_and_delegation():
    config = json.loads((Path(__file__).resolve().parents[2] / "opencode.json").read_text())
    for agent in ("sage-chat", "sage-implement"):
        for tool in ("skill", "task", "todowrite"):
            assert config["agent"][agent].get("tools", {}).get(tool, True)


def test_chat_accepts_general_questions_without_requiring_data(tmp_path):
    orch, _ = _orch(tmp_path)
    prompt = orch._chat_prompt("thread", "Explain how rainbows form.", {"items": []})
    assert "This turn answers a question about data" not in prompt
    assert "general questions" in prompt
    assert prompt.endswith("Explain how rainbows form.")


def test_build_gets_current_source_paths_without_file_contents(tmp_path, monkeypatch):
    from sage.orchestrator.service import Orchestrator

    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    orch, client = _orch(tmp_path, [Turn(text="Updated.", writes={"src/App.tsx": "export default () => null;"})])
    app = orch.project(start_preview=False).app_for_turn().path
    (app / "src" / "StatusPanel.tsx").write_text("PRIVATE_SOURCE_CONTENT\n")
    (app / "public").mkdir(exist_ok=True)
    (app / "public" / "private-data.csv").write_text("PRIVATE_DATA\n")
    list(orch._build_stream("Make the status panel blue.", mode=Mode.IMPLEMENT, is_approval=True))
    first = client.prompts[0]["text"]
    assert "src/StatusPanel.tsx" in first
    assert "PRIVATE_SOURCE_CONTENT" not in first
    assert "private-data.csv" not in first
    assert "Open the relevant files" in first


@pytest.mark.parametrize("broken", [False, True])
def test_source_listing_repeats_only_when_the_retry_has_a_new_session(tmp_path, monkeypatch, broken):
    from sage.orchestrator.service import Orchestrator

    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    orch, client = _orch(tmp_path, [Turn(text="Starting.", broken_write=broken),
                                  Turn(text="Updated.", writes={"src/App.tsx": "export default () => null;"})])
    list(orch._build_stream("Make the panel blue.", mode=Mode.IMPLEMENT, is_approval=True))
    assert len(client.prompts) == 2
    marker = "Existing source paths (JSON array"
    assert marker in client.prompts[0]["text"]
    assert (marker in client.prompts[1]["text"]) == broken


def test_source_paths_are_exact_json_strings_and_the_listing_is_bounded(tmp_path):
    from sage.orchestrator.service import Orchestrator

    assert Orchestrator._build_source_note(tmp_path) == ""
    src = tmp_path / "src"
    src.mkdir()
    name = 'A "panel"\n(1 lines).tsx'
    (src / name).write_text("Do not include source content")
    (src / ".hidden").mkdir()
    (src / ".hidden" / "secret.ts").touch()
    for i in range(65):
        (src / f"file{i:02}.ts").touch()
    note = Orchestrator._build_source_note(tmp_path)
    paths = json.loads(note.splitlines()[1])
    assert paths == ["src/" + name] + [f"src/file{i:02}.ts" for i in range(59)]
    assert "first 60 paths" in note
    assert "secret" not in note
    assert "Do not include source content" not in note
