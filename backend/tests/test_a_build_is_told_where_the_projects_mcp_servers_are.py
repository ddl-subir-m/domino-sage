"""A Build is told where the Project's switched-on MCP servers are, and a plan names them (#744).

Live (#714, Signal Room 24): Tavily and the Deal Desk server were switched on and Chat had just
called both, yet prompt 7's plan named neither, and a later build wrote `tavily.example.com` and
`deal-desk.example.com` into the app. A Build model was told a server's URL only when the person's
last message carried `{mcp:name}`; a plan's `Uses` line, or a request in plain words, carried no
address, and the only one in sight was the template's example. The planner's list named the servers
but not that a step needing what their tools provide uses them, and a plan that copied the list line
("MCP server deal-desk") named nothing the reach check (#712) knew, so it was never judged.
"""
from __future__ import annotations

import re
from pathlib import Path

from sage import extension_mcp, project_secrets
from sage.gateway.client import FakeGatewayClient
from sage.orchestrator import plan_resources
from sage.orchestrator.plan_steps import parse_steps
from sage.provision.domino import FakeControlPlane
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase
from sage.shim.enforcement import EnforcementShim

from .fake_opencode import execution_plan

REPO = Path(__file__).resolve().parents[2]
NEWS_URL = "https://news.example.net/mcp/?apiKey={env:NEWS_API_KEY}"
DESK_URL = "https://desk.example.net/mcp"
_PLANTED = "planted-typed-key-5b2e81"


def _servers(root: Path) -> None:
    extension_mcp.add(root, "newsfeed", NEWS_URL)
    extension_mcp.set_tools(root, "newsfeed", ["search_news"], None)
    extension_mcp.add(root, "desk", DESK_URL, {"Authorization": "Bearer {env:DESK_TOKEN}"})
    extension_mcp.set_tools(root, "desk", ["approval_chain"], None)
    extension_mcp.add(root, "dormant", "https://dormant.example.net/mcp")
    extension_mcp.set_enabled(root, "dormant", False)


def _sent(root: Path, monkeypatch, text: str, control: ModelControl | None = None) -> str:
    monkeypatch.setattr(extension_mcp, "_root", root)
    catalog = ModelCatalog("sq", "sq", "sq", "gpt-5.4", "bedrock-qwen3-coder", "gpt-5.4")
    gw = FakeGatewayClient()
    shim = EnforcementShim(control or ModelControl(mode=Mode.AUTO, phase=Phase.PLAN), catalog, gw)
    list(shim.handle({"model": "x", "messages": [{"role": "user", "content": text}]}, project="p"))
    return gw.seen[-1][0]["messages"][0]["content"]


# --- the Build model is told every switched-on server's address -----------------------------------

def test_a_build_request_that_mentions_no_server_is_told_each_switched_on_servers_address(
        tmp_path, monkeypatch):
    _servers(tmp_path)
    sent = _sent(tmp_path, monkeypatch, "Get the news live and show the approval chain.")
    assert NEWS_URL in sent and "search_news" in sent
    assert DESK_URL in sent and "approval_chain" in sent and "Bearer {env:DESK_TOKEN}" in sent
    # A key in the URL is a secret too, not only one in a header.
    assert 'secret("NEWS_API_KEY")' in sent and 'secret("DESK_TOKEN")' in sent
    assert "no other address" in sent
    assert "not in this Project" in sent
    assert "dormant" not in sent


def test_a_mentioned_server_is_described_once(tmp_path, monkeypatch):
    _servers(tmp_path)
    sent = _sent(tmp_path, monkeypatch, "Show the chain from {mcp:desk}.")
    assert sent.count(DESK_URL) == 1
    assert "{mcp:desk} is the MCP server `desk`" in sent
    assert NEWS_URL in sent


def test_a_key_typed_into_a_servers_url_is_hidden_in_the_note(tmp_path, monkeypatch):
    extension_mcp.add(tmp_path, "typed", "https://typed.example.net/mcp/?apiKey=" + _PLANTED)
    plane = FakeControlPlane()
    plane.env_vars["p"] = {"TYPED_KEY": _PLANTED}
    monkeypatch.setattr(project_secrets, "_active", None)
    monkeypatch.setattr(project_secrets, "_reason", None)
    project_secrets.install(plane, "p", tmp_path / ".sage" / "secrets.json")
    project_secrets.active().refresh()
    sent = _sent(tmp_path, monkeypatch, "Build a page.")
    assert "https://typed.example.net/mcp/?apiKey={env:TYPED_KEY}" in sent
    assert _PLANTED not in sent


def test_a_project_with_no_switched_on_server_adds_nothing(tmp_path, monkeypatch):
    extension_mcp.add(tmp_path, "dormant", "https://dormant.example.net/mcp")
    extension_mcp.set_enabled(tmp_path, "dormant", False)
    assert _sent(tmp_path, monkeypatch, "Build a page.") == "Build a page."


def test_a_chat_request_is_not_told_the_addresses(tmp_path, monkeypatch):
    _servers(tmp_path)
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    control.arm_chat("thread-1")
    assert _sent(tmp_path, monkeypatch, "What is new?", control) == "What is new?"


# --- the planner is told a server is how a step gets what its tools provide -----------------------

def test_the_planner_is_told_a_step_needing_what_a_tool_provides_uses_its_server():
    note = plan_resources.planner_note(plan_resources.Resources(servers=(
        {"name": "desk", "url": DESK_URL, "tools": ["approval_chain"]},)))
    assert "even when the request does not name it" in note
    assert "name in backticks" in note


def test_a_uses_line_that_copies_the_list_lines_kind_still_names_the_resource():
    res = plan_resources.Resources(
        servers=({"name": "desk", "url": DESK_URL, "headers": {}},), secrets=("DESK_TOKEN",))
    plan = (execution_plan("App", "An app.", "Approvals", files="app.py")
            + "\n- Uses — MCP server desk, Secret DESK_TOKEN")
    found = plan_resources.unreached(parse_steps(plan), res, [("app.py", "# nothing yet")], [])
    assert [s.missing for s in found] == [("desk", "DESK_TOKEN")]


# --- the template the Build model reads -----------------------------------------------------------

def test_the_template_says_to_call_only_a_listed_server_at_its_listed_url():
    agents = (REPO / "template/fastapi-antd/AGENTS.md").read_text()
    assert "Call only a server {assistantName} lists for this Project" in agents
    assert "never write an address of your own" in agents
    # A Built App's AGENTS.md is re-branded at seed time (#114): only a token is.
    assert not re.search(r"\bSage\b", agents)
    # The helper's example is the only address the model saw; it must not be one to copy.
    assert "example.com" not in (REPO / "template/fastapi-antd/sage_mcp.py").read_text()
