"""A Build @mention names an MCP server as `{mcp:name}`, and the model is told its record.

The chip is the name. The URL, kind, tools and header references ride the model request, not the
text the person sent. Chat does not offer the server and does not receive the note: a connected
server's tools are already on that turn.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage import extension_mcp, project_secrets
from sage.gateway.client import FakeGatewayClient
from sage.provision.domino import FakeControlPlane
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase
from sage.shim.enforcement import EnforcementShim

_HARNESS = Path(__file__).resolve().parent / "js" / "mention_secret_harness.mjs"
_URL = "https://apps.example.test/apps-internal/acme/mcp"
_PLANTED = "planted-github-token-7f3a9c"


def _run(mode: str, query: str, **kw) -> dict:
    payload = {"mode": mode, "query": query, "available": True, **kw}
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(payload), capture_output=True,
                         text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _servers(**over) -> list[dict]:
    row = {"name": "acme", "kind": "domino", "url": _URL, "headers": {},
           "enabled": True, "tools": ["list_customers", "lookup_customer"]}
    row.update(over)
    return [row]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)")
def test_build_offers_the_server_and_inserts_its_name():
    got = _run("build", "acm", pick="acme", mcp=[{
        "name": "acme", "url": _URL, "kind": "domino", "tools": ["list_customers"]}])
    assert "acme" in [r["name"] for r in got["rows"]]
    assert got["inserted"] == "Call it with {mcp:acme}"
    assert _URL not in got["inserted"]
    assert got["posts"] == []
    assert got["field"] == "sw-composer-field has-mcp-refs"
    assert got["mirror"]["chips"] == ["acme"]
    assert got["mirror"]["text"] == "Call it with {mcp:acme}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)")
def test_chat_does_not_offer_an_mcp_server():
    got = _run("chat", "acm", mcp=[{"name": "acme", "url": _URL, "kind": "domino"}])
    assert "acme" not in [r["name"] for r in got["rows"]]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)")
def test_a_sent_message_shows_the_server_by_name():
    got = _run("build", "nothing-matches-this",
               markdown="Use {mcp:acme} here. Literally: `{mcp:RAW}`")
    assert got["message"]["chips"] == ["acme"]
    assert "{mcp:acme}" not in got["message"]["text"]
    assert got["message"]["code"] == ["{mcp:RAW}"]


def test_the_note_names_a_domino_server_without_inventing_a_missing_one():
    messages = [{"role": "user", "content": "List them with {mcp:acme} and {mcp:nope}"}]
    noted = extension_mcp.note_mentions(messages, _servers(
        headers={"X-Region": "{env:REGION}"}))
    note = noted[0]["content"]
    assert messages[0]["content"] == "List them with {mcp:acme} and {mcp:nope}"
    assert _URL in note and "Domino-hosted" in note
    assert "list_customers" in note and "lookup_customer" in note
    assert "{env:REGION}" in note and 'secret("NAME")' in note
    assert "sage_domino.token()" in note
    assert "names no MCP server" in note and "nope" in note
    assert note.count(_URL) == 1


def test_the_note_for_a_remote_server_keeps_the_header_reference():
    noted = extension_mcp.note_mentions(
        [{"role": "user", "content": "{mcp:github}"}],
        [{"name": "github", "kind": "remote", "url": "https://api.githubcopilot.com/mcp/",
          "headers": {"Authorization": "Bearer {env:GITHUB_TOKEN}"}, "enabled": False,
          "tools": ["list_repos"]}])
    note = noted[0]["content"]
    assert "remote" in note and "list_repos" in note
    assert "Bearer {env:GITHUB_TOKEN}" in note
    assert 'secret("NAME")' in note and "sage_domino.token()" not in note
    assert "switched off" in note
    assert _PLANTED not in note


def _shim(gw: FakeGatewayClient, control: ModelControl | None = None) -> EnforcementShim:
    catalog = ModelCatalog("sq", "sq", "sq", "gpt-5.4", "bedrock-qwen3-coder", "gpt-5.4")
    return EnforcementShim(control or ModelControl(mode=Mode.AUTO, phase=Phase.PLAN), catalog, gw)


def test_a_build_request_is_told_the_record_and_the_stored_message_is_not(tmp_path, monkeypatch):
    extension_mcp.add(tmp_path, "github", "https://api.githubcopilot.com/mcp/",
                      {"Authorization": "Bearer {env:GITHUB_TOKEN}"})
    extension_mcp.set_tools(tmp_path, "github", ["list_repos"], None)
    monkeypatch.setattr(extension_mcp, "_root", tmp_path)
    plane = FakeControlPlane()
    plane.env_vars["p"] = {"GITHUB_TOKEN": _PLANTED}
    monkeypatch.setattr(project_secrets, "_active", None)
    project_secrets.install(plane, "p", tmp_path / ".sage" / "secrets.json")
    project_secrets.active().refresh()

    original = "List my repos with {mcp:github}"
    messages = [{"role": "user", "content": original}]
    gw = FakeGatewayClient()
    list(_shim(gw).handle({"model": "x", "messages": messages}, project="p"))

    assert messages[0]["content"] == original
    sent = gw.seen[-1][0]["messages"][0]["content"]
    assert "https://api.githubcopilot.com/mcp/" in sent
    assert "list_repos" in sent and "Bearer {env:GITHUB_TOKEN}" in sent
    assert 'secret("GITHUB_TOKEN")' in sent
    assert _PLANTED not in sent


def test_a_chat_request_is_not_told_the_url(tmp_path, monkeypatch):
    extension_mcp.add(tmp_path, "github", "https://api.githubcopilot.com/mcp/",
                      {"Authorization": "Bearer {env:GITHUB_TOKEN}"})
    monkeypatch.setattr(extension_mcp, "_root", tmp_path)
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    control.arm_chat("thread-1")
    messages = [{"role": "user", "content": "List my repos with {mcp:github}"}]
    gw = FakeGatewayClient()
    list(_shim(gw, control).handle({"model": "x", "messages": messages}, project="p"))
    assert "githubcopilot" not in gw.seen[-1][0]["messages"][0]["content"]
