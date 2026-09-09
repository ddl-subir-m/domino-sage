"""Live read reaches the model as a CUSTOM tool, and a probe says whether MCP reaches it at all.

Measured live on the pinned 1.18.4, 2026-09-09: OpenCode held `sage-live-read` connected for the
very instance the turn ran in — confirmed by `GET /mcp?directory=` two seconds before the model
call — and handed the model ten built-in tools and none of ours. Config clean, port right, no
project config, no `tools` filter, the agent resolved. That is opencode #33027 and nothing on our
side of the boundary fixes it.

Custom tools go down the path that works: verified end to end on the same build, a tool in
`~/.config/opencode/tools/` appears in the model's own tool list AND executes, from any session
directory. `/experimental/tool` lists built-ins plus custom tools and never MCP tools, and a Chat
turn gets exactly that registry.

`sage-mcp-probe` is the control for the claim above, and is meant to be deleted once read.
"""
from __future__ import annotations

import json

import pytest

from sage import diag_mcp_probe as probe

CONFIG = {
    "model": "sage-gateway/gpt",
    "mcp": {
        "sage-live-read": {"type": "remote", "url": "http://localhost:8080/mcp/live-read",
                           "enabled": True},
        "sage-mcp-probe": {"type": "local",
                           "command": ["python3", "backend/sage/diag_mcp_probe.py"],
                           "enabled": True},
    },
    "provider": {"sage-gateway": {"options": {"baseURL": "http://localhost:8080/v1"}}},
    "agent": {"sage-chat": {"prompt": "hello"}},
}

TOOL_SRC = "backend/sage/liveread/tools"


@pytest.fixture()
def installed(tmp_path, monkeypatch):
    from sage.orchestrator.app import _install_opencode_config

    src_dir = tmp_path / "repo"
    (src_dir / TOOL_SRC).mkdir(parents=True)
    (src_dir / "opencode.json").write_text(json.dumps(CONFIG))
    (src_dir / TOOL_SRC / "live_read.ts").write_text("// the shim\n")
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    (tmp_path / "brand.json").write_text(json.dumps({"productName": "Acme"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(tmp_path / "brand.json"))

    _install_opencode_config(src_dir, 9999)
    return src_dir, home / ".config" / "opencode"


# --- the custom tools ----------------------------------------------------------------------------


def test_the_tool_lands_in_the_global_slot_opencode_reads(installed):
    """Global, because a Chat session runs under the workspace volume and the project slot is never
    ours to fill — the same reasoning that makes the global CONFIG the copy that does the work."""
    src_dir, global_dir = installed

    landed = global_dir / "tools" / "live_read.ts"
    assert landed.is_file()
    assert landed.read_text() == (src_dir / TOOL_SRC / "live_read.ts").read_text()


def test_the_filename_is_kept_because_it_names_the_tools(installed):
    """OpenCode names a multi-export custom tool `<file>_<export>`, so `live_read.ts` is what makes
    `live_read_table`. Copied under any other name, the prompts teach tools that do not exist."""
    _, global_dir = installed

    assert [p.name for p in (global_dir / "tools").glob("*.ts")] == ["live_read.ts"]


def test_a_copy_rather_than_a_link(installed):
    """A checkout that moves or a container that rebuilds would leave a dangling link, and a
    dangling link fails the way MCP already fails — silently, with the tools simply absent."""
    _, global_dir = installed

    assert not (global_dir / "tools" / "live_read.ts").is_symlink()


def test_a_missing_source_is_reported_and_does_not_stop_the_boot(tmp_path, monkeypatch, caplog):
    """Without the tools the agent falls back to Python, which answers but puts every row in the
    model's context — the thing ADR-0041 exists to avoid. Worth a loud line, not an exception."""
    import logging

    from sage.orchestrator.app import _install_opencode_config

    src_dir = tmp_path / "repo"
    src_dir.mkdir()
    (src_dir / "opencode.json").write_text(json.dumps(CONFIG))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    with caplog.at_level(logging.ERROR, logger="sage.orchestrator"):
        _install_opencode_config(src_dir, 9999)

    assert any("Live read tools" in r.getMessage() for r in caplog.records)


# --- the probe's command path --------------------------------------------------------------------


def test_a_local_servers_relative_command_is_made_absolute(installed):
    """OpenCode SPAWNS a local server, and from the session's directory — not from the checkout. A
    relative path resolves against the wrong place and the server dies silently, which reads exactly
    like the drop this whole file is about."""
    src_dir, global_dir = installed

    argv = json.loads((global_dir / "opencode.json").read_text())["mcp"]["sage-mcp-probe"]["command"]
    assert argv[0] == "python3", "the interpreter is not a path and must be left alone"
    assert argv[1] == str((src_dir / "backend" / "sage" / "diag_mcp_probe.py").resolve())


def test_a_bare_word_is_not_mistaken_for_a_path(installed):
    _, global_dir = installed

    argv = json.loads((global_dir / "opencode.json").read_text())["mcp"]["sage-mcp-probe"]["command"]
    assert not argv[0].startswith("/")


def test_an_absolute_command_is_left_as_it_is(tmp_path, monkeypatch):
    from sage.orchestrator.app import _install_opencode_config

    cfg = json.loads(json.dumps(CONFIG))
    cfg["mcp"]["sage-mcp-probe"]["command"] = ["/usr/bin/python3", "/opt/sage/probe.py"]
    src_dir = tmp_path / "repo"
    (src_dir / TOOL_SRC).mkdir(parents=True)
    (src_dir / "opencode.json").write_text(json.dumps(cfg))
    (src_dir / TOOL_SRC / "live_read.ts").write_text("// x\n")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    _install_opencode_config(src_dir, 9999)

    argv = json.loads((tmp_path / "home" / ".config" / "opencode" / "opencode.json").read_text())
    assert argv["mcp"]["sage-mcp-probe"]["command"] == ["/usr/bin/python3", "/opt/sage/probe.py"]


# --- the probe itself ----------------------------------------------------------------------------


def test_it_offers_one_tool_that_needs_no_arguments():
    """Unlike Live read in transport, process and shape, so that "both absent" means MCP and not
    something about our schema."""
    out = probe.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert [t["name"] for t in out["result"]["tools"]] == ["probe_ping"]
    assert out["result"]["tools"][0]["inputSchema"]["properties"] == {}


def test_it_negotiates_a_protocol_version():
    out = probe.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    assert out["result"]["protocolVersion"] == probe.PROTOCOL_VERSION
    assert out["result"]["serverInfo"]["name"] == "sage-mcp-probe"


def test_calling_it_says_the_thing_worth_knowing():
    out = probe.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "probe_ping"}})

    assert "MCP tools DO reach this turn" in out["result"]["content"][0]["text"]


def test_a_notification_gets_no_reply():
    assert probe.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_prompts_and_resources_are_answered_rather_than_refused():
    """A client that asks for these and gets a method error has been seen giving up on the whole
    server, which would make the probe report the failure it exists to measure."""
    for method in ("prompts/list", "resources/list"):
        out = probe.handle({"jsonrpc": "2.0", "id": 1, "method": method})
        assert out["result"] == {method.split("/")[0]: []}


def test_an_unknown_method_is_an_error_not_a_crash():
    out = probe.handle({"jsonrpc": "2.0", "id": 1, "method": "nope"})

    assert out["error"]["code"] == -32601
