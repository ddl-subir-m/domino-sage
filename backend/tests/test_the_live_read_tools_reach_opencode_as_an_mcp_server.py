"""ADR-0041 — the transport half. OpenCode is given a tool by `mcp` in `opencode.json`.

The shim cannot do this job. It sits on the LLM request, so a tool injected there would be one the
model could call and OpenCode could not execute. `mcp` takes a URL, and the orchestrator already
serves HTTP, so the tools arrive that way instead.

One OpenCode server hosts many Conversations, so the URL cannot say which turn is calling. The
token does, and it comes from the turn's own prompt: an assistant can relay the one it was handed
and has no way to name another Conversation's.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from sage.liveread import mcp


def call(method, *, mid=1, params=None, run=None):
    return mcp.handle(
        {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}},
        run=run or (lambda name, args: "ok"),
    )


def test_initialize_answers_with_a_tools_capability():
    r = call("initialize")["result"]
    assert r["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert "tools" in r["capabilities"]
    assert r["serverInfo"]["name"] == "sage-live-read"


def test_both_tools_are_listed_and_each_one_demands_the_turns_token():
    tools = call("tools/list")["result"]["tools"]

    assert {t["name"] for t in tools} == {"live_read_table", "live_read_files"}
    for t in tools:
        assert "token" in t["inputSchema"]["required"], f"{t['name']} must say which turn it is"


def test_a_notification_takes_no_reply():
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, run=None) is None


def test_a_call_reaches_run_and_comes_back_as_text_the_assistant_reads():
    seen = {}

    def run(name, args):
        seen.update(name=name, args=args)
        return "500 of 12,431 rows — examples/thr_a/sample.table.json"

    r = call("tools/call", params={
        "name": "live_read_table",
        "arguments": {"token": "tok_1", "source": "DWH", "table": "GONG__CALLS"},
    }, run=run)["result"]

    assert seen["name"] == "live_read_table"
    assert seen["args"]["token"] == "tok_1"
    assert r["content"][0]["text"].startswith("500 of 12,431 rows")
    assert not r.get("isError")


def test_a_refusal_is_ordinary_text_and_not_a_protocol_error():
    # A refusal is a sentence the person is owed. Returned as an error it becomes something nobody
    # words, and the assistant falls back on inventing why it could not answer.
    r = call("tools/call", params={"name": "live_read_files", "arguments": {"token": "t", "dataset": "d"}},
             run=lambda n, a: "Sage cannot reach that from this conversation.")["result"]
    assert not r.get("isError")
    assert "cannot reach" in r["content"][0]["text"]


def test_a_read_that_failed_reaches_the_assistant_rather_than_escaping_as_a_crash():
    r = call("tools/call", params={"name": "live_read_table", "arguments": {"token": "t", "source": "s", "table": "t"}},
             run=lambda n, a: (_ for _ in ()).throw(RuntimeError("DWH did not answer: timed out")))["result"]

    assert r["isError"] is True
    assert "timed out" in r["content"][0]["text"]


def test_a_tool_nobody_defined_is_refused_by_the_protocol():
    assert call("tools/call", params={"name": "rm_rf", "arguments": {}})["error"]["code"] == -32602


def test_arguments_that_are_not_an_object_are_refused():
    assert call("tools/call", params={"name": "live_read_table", "arguments": []})["error"]["code"] == -32602


def test_an_unknown_method_says_so():
    assert call("resources/subscribe")["error"]["code"] == -32601


@pytest.mark.parametrize("method", ["prompts/list", "resources/list"])
def test_the_lists_opencode_probes_for_answer_empty_rather_than_erroring(method):
    # A client that probes these and gets -32601 logs a fault on every connect. There is nothing
    # here to serve, so say that plainly instead.
    assert call(method)["result"] == {method.split("/")[0]: []}


def test_the_name_an_instruction_gives_is_the_name_opencode_offers():
    """OpenCode namespaces an MCP tool by its `opencode.json` key, then strips it again to call.

    Verified live against the pinned 1.18.4 with a stub provider in front of it: the model was
    offered `sage-live-read_live_read_table`, and the `tools/call` that reached the server named
    `live_read_table`. Both halves matter and they point opposite ways — the handler keys on the
    bare name, and every sentence that tells an agent which tool to call must use the prefixed one.

    They drift the instant somebody renames the key, and the failure is quiet: the agent calls a
    tool that does not exist, and falls back on telling the person it cannot see their data, which
    is the transcript this whole feature exists to stop. So they are pinned to each other here.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    servers = json.loads((root / "opencode.json").read_text())["mcp"]
    assert len(servers) == 1, "the prefix below assumes one server; name the new one here too"
    key = next(iter(servers))

    told = "\n".join(p.read_text() for p in (
        root / "template" / "react-vite" / "AGENTS.md",
        root / "template" / "chat" / "AGENTS.md",
        root / "backend" / "sage" / "orchestrator" / "service.py",
    ))
    for tool in mcp.TOOLS:
        assert f"{key}_{tool['name']}" in told, f"nothing tells an agent to call {tool['name']}"
        # And the bare name is what the server answers to, which is the other half of the pair.
        assert mcp.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": tool["name"], "arguments": {}}},
            run=lambda n, a: "ok",
        )["result"]["content"][0]["text"] == "ok"
