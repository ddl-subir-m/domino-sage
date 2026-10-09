"""The Chat turn prompt names the Project's MCP servers and their tools (#711).

MEASURED. A Haiku turn asked for the latest news on some accounts refused, while the Tavily tools
were in its tool list. The turn prompt named the language models it could call and nothing else,
and `template/chat/AGENTS.md` says to use what this turn's context lists and otherwise stop. A
stronger model connects "news" to `tavily_tavily_search`; a weaker one follows the rule.

THROUGH THE REAL TURN, like the models sentence beside it
(`test_the_prompt_names_the_models_the_conversation_can_call.py`): the servers are written with
`extension_mcp`, the same door the Resources panel uses, and the prompt read back is the one the
agent was sent.
"""

from __future__ import annotations

from pathlib import Path

from sage import extension_mcp
from sage.orchestrator import service

from .test_the_prompt_names_the_models_the_conversation_can_call import _orch, _turn

MARK = "connected tools"


def _server(root: Path, name: str, tools: list[str]) -> None:
    extension_mcp.add(root, name, f"https://{name}.example/mcp")
    extension_mcp.set_tools(root, name, tools, None)


def _tools_line(text: str) -> str:
    return next((ln for ln in text.splitlines() if MARK in ln), "")


def test_a_chat_turn_names_the_projects_search_tool(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    root = orch._chat_project().record.path
    _server(root, "tavily", ["tavily_search", "tavily_extract"])
    _server(root, "deal-desk", ["lookup_account"])
    tid = orch.create_thread()["id"]

    line = _tools_line(_turn(orch, oc, tid))
    # The names OpenCode offers the model: `<server>_<tool>`, as `_project_mcp_server` reads back.
    for probe in ("`tavily`", "tavily_tavily_search", "tavily_tavily_extract",
                  "`deal-desk`", "deal-desk_lookup_account"):
        assert probe in line, f"{probe!r} missing from the tools line: {line!r}"


def test_a_tool_name_is_spelled_the_way_opencode_offers_it(tmp_path: Path):
    """OpenCode replaces anything outside `[a-zA-Z0-9_-]` in a tool's name with `_`."""
    orch, oc = _orch(tmp_path)
    _server(orch._chat_project().record.path, "news", ["search.latest"])
    tid = orch.create_thread()["id"]

    line = _tools_line(_turn(orch, oc, tid))
    assert "news_search_latest" in line, line
    assert "search.latest" not in line, line


def test_a_switched_off_server_is_not_named(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    root = orch._chat_project().record.path
    _server(root, "tavily", ["tavily_search"])
    _server(root, "deal-desk", ["lookup_account"])
    extension_mcp.set_enabled(root, "deal-desk", False)
    tid = orch.create_thread()["id"]

    text = _turn(orch, oc, tid)
    assert "tavily_tavily_search" in _tools_line(text)
    assert "deal-desk" not in text, "a switched-off server is not offered, so it is not named"


def test_a_server_with_no_listed_tools_is_not_named(tmp_path: Path):
    """Naming a server with no tool names invites the model to invent one."""
    orch, oc = _orch(tmp_path)
    root = orch._chat_project().record.path
    _server(root, "tavily", ["tavily_search"])
    extension_mcp.add(root, "crm", "https://crm.example/mcp")
    tid = orch.create_thread()["id"]

    text = _turn(orch, oc, tid)
    assert "tavily_tavily_search" in _tools_line(text)
    assert "`crm`" not in text


def test_a_project_with_no_servers_adds_nothing_to_the_prompt(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]

    lines = _turn(orch, oc, tid).splitlines()
    assert not any(MARK in ln for ln in lines)
    # Not even an empty line where the sentence would have gone.
    at = next(i for i, ln in enumerate(lines) if "delegated_model_call" in ln)
    assert lines[at + 1].strip(), "the prompt grew by a blank line for a Project with no servers"


def test_a_server_with_many_tools_names_only_a_few(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    root = orch._chat_project().record.path
    _server(root, "big", [f"tool{i:02d}" for i in range(20)])
    tid = orch.create_thread()["id"]

    line = _tools_line(_turn(orch, oc, tid))
    named = line.count("big_tool")
    assert named == service._PROJECT_TOOLS_SHOWN, line
    assert f"and {20 - named} more" in line, line


def test_an_unreadable_server_config_does_not_stop_the_turn(tmp_path: Path, monkeypatch):
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]

    def boom(root):
        raise extension_mcp.ExtensionError("not valid JSON")

    monkeypatch.setattr(extension_mcp, "list_servers", boom)
    text = _turn(orch, oc, tid)
    assert "delegated_model_call" in text
    assert MARK not in text


def test_the_tools_line_sits_beside_the_models_sentence(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    _server(orch._chat_project().record.path, "tavily", ["tavily_search"])
    tid = orch.create_thread()["id"]

    lines = _turn(orch, oc, tid).splitlines()
    at = next(i for i, ln in enumerate(lines) if "delegated_model_call" in ln)
    assert MARK in lines[at + 1]
