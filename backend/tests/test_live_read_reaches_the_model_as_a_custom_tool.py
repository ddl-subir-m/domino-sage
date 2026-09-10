"""Live read reaches the model as a CUSTOM tool — the one copy of it that the model is offered.

The long version of why is in `driver/opencode.py`: OpenCode 1.18.4 serves two APIs, and turns used
to run on v2, whose prompt path sends the model no custom tools AND no MCP tools. Everything that
reported the tools healthy read the REGISTRY, which was right the whole time. Moving the turn to v1
(`7b9209e`) delivered both at once — measured in production, `all 13:` including `live_read_table`
and `sage-live-read_live_read_table`, and the model then called one.

So MCP was never broken, and the choice between the two is now yes-or-no rather than either-or.
Sage keeps the CUSTOM tool and declares no MCP server at all: offering both put two tools with the
same description and the same Python route in front of the model and made it guess. The custom tool
wins because it needs no server, no handshake and no port — the `.ts` posts to the route this
process already serves.

`sage-mcp-probe` was the control that proved "no MCP tool of any kind arrives", and is gone now that
the answer is known.
"""
from __future__ import annotations

import json

import pytest

CONFIG = {
    "model": "sage-gateway/gpt",
    "mcp": {
        "sage-live-read": {"type": "remote", "url": "http://localhost:8080/mcp/live-read",
                           "enabled": True},
        "a-local-one": {"type": "local",
                        "command": ["python3", "backend/sage/some_server.py"],
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

    argv = json.loads((global_dir / "opencode.json").read_text())["mcp"]["a-local-one"]["command"]
    assert argv[0] == "python3", "the interpreter is not a path and must be left alone"
    assert argv[1] == str((src_dir / "backend" / "sage" / "some_server.py").resolve())


def test_a_bare_word_is_not_mistaken_for_a_path(installed):
    _, global_dir = installed

    argv = json.loads((global_dir / "opencode.json").read_text())["mcp"]["a-local-one"]["command"]
    assert not argv[0].startswith("/")


def test_an_absolute_command_is_left_as_it_is(tmp_path, monkeypatch):
    from sage.orchestrator.app import _install_opencode_config

    cfg = json.loads(json.dumps(CONFIG))
    cfg["mcp"]["a-local-one"]["command"] = ["/usr/bin/python3", "/opt/sage/probe.py"]
    src_dir = tmp_path / "repo"
    (src_dir / TOOL_SRC).mkdir(parents=True)
    (src_dir / "opencode.json").write_text(json.dumps(cfg))
    (src_dir / TOOL_SRC / "live_read.ts").write_text("// x\n")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    _install_opencode_config(src_dir, 9999)

    argv = json.loads((tmp_path / "home" / ".config" / "opencode" / "opencode.json").read_text())
    assert argv["mcp"]["a-local-one"]["command"] == ["/usr/bin/python3", "/opt/sage/probe.py"]


# --- what is actually on disk ---------------------------------------------------------------------
#
# The wiring log says the file was WRITTEN. It cannot say it is still there, is the right size, or
# is the only thing in that directory. On 2026-09-09 `live_read.ts` was written at boot and its
# tools were still absent from every turn, on a build where the identical file registers from this
# same slot on a bench — and the page had nothing to say about the gap.


def _diag(tmp_path, monkeypatch, files: dict[str, str] | None):
    from sage.orchestrator.app import _custom_tools_diag

    monkeypatch.setenv("HOME", str(tmp_path))
    if files is not None:
        d = tmp_path / ".config" / "opencode" / "tools"
        d.mkdir(parents=True)
        for name, body in files.items():
            (d / name).write_text(body)
    return _custom_tools_diag()


def test_it_names_the_files_and_their_sizes(tmp_path, monkeypatch):
    """Bytes because "written" is not "landed whole", and a truncated file registers nothing."""
    body = "export const table = tool({})\n"
    out = _diag(tmp_path, monkeypatch, {"live_read.ts": body})

    assert out["exists"] is True
    assert out["files"] == [{"name": "live_read.ts", "bytes": len(body)}]


def test_it_works_out_the_names_opencode_will_build(tmp_path, monkeypatch):
    """`<file>_<export>` is the rule, and nobody remembers it at the hour they need this page."""
    out = _diag(tmp_path, monkeypatch,
                {"live_read.ts": "export const table = tool({})\nexport const files = tool({})\n"})

    assert out["tools_it_should_make"] == ["live_read_files", "live_read_table"]


def test_a_default_export_is_named_for_its_file(tmp_path, monkeypatch):
    out = _diag(tmp_path, monkeypatch, {"probe.ts": "export default tool({})\n"})

    assert out["tools_it_should_make"] == ["probe"]


def test_a_directory_that_is_not_there_says_so_rather_than_raising(tmp_path, monkeypatch):
    """The finding, in the case that matters most: the install did not land at all."""
    out = _diag(tmp_path, monkeypatch, None)

    assert out["exists"] is False
    assert out["error"]
    assert "tools" in out["dir"]


def test_it_never_puts_the_file_contents_on_the_page(tmp_path, monkeypatch):
    """A diagnostic, not a listing. Bytes answer "did it land whole" without printing a program."""
    out = _diag(tmp_path, monkeypatch, {"live_read.ts": "export const table = tool({}) // SECRET\n"})

    assert "SECRET" not in json.dumps(out)


# --- and whether the thing it imports is there ----------------------------------------------------
#
# `tool()` is the identity function and `tool.schema` is zod, so the import buys nothing but types —
# and costs everything if it cannot be resolved. On 2026-09-09 the file landed whole in the global
# slot (5099 bytes, byte for byte) and its tools were still absent. The bench that "proved" this
# path works had `@opencode-ai/plugin` sitting in `~/.config/opencode/node_modules` since July,
# installed by OpenCode itself. A fresh workspace has no such luck, and the failure is silent.

IMPORTING = 'import { tool } from "@opencode-ai/plugin"\nexport const table = tool({})\n'


def test_it_says_where_an_imported_package_was_found(tmp_path, monkeypatch):
    (tmp_path / ".config" / "opencode" / "node_modules" / "@opencode-ai" / "plugin").mkdir(
        parents=True)

    out = _diag(tmp_path, monkeypatch, {"live_read.ts": IMPORTING})

    assert out["imports"] == [{"package": "@opencode-ai/plugin", "found_at": str(
        tmp_path / ".config" / "opencode" / "node_modules" / "@opencode-ai" / "plugin")}]


def test_an_import_that_resolves_nowhere_is_the_finding(tmp_path, monkeypatch):
    """The whole module fails to load and the tool is dropped in silence, which reads on every
    other surface exactly like the MCP drop — connected, installed, absent."""
    out = _diag(tmp_path, monkeypatch, {"live_read.ts": IMPORTING})

    assert out["imports"] == [{"package": "@opencode-ai/plugin", "found_at": None}]


def test_it_looks_up_the_chain_the_way_a_js_runtime_does(tmp_path, monkeypatch):
    """`node_modules` beside the tools directory, above it, or anywhere further up all count."""
    (tmp_path / "node_modules" / "zod").mkdir(parents=True)

    out = _diag(tmp_path, monkeypatch, {"live_read.ts": 'import { z } from "zod"\n'})

    assert out["imports"][0]["found_at"] == str(tmp_path / "node_modules" / "zod")


def test_a_relative_import_is_not_a_package(tmp_path, monkeypatch):
    """`./helper` is a file beside the tool, not something to install, and listing it as missing
    would send whoever reads this page after a package that does not exist."""
    out = _diag(tmp_path, monkeypatch, {"live_read.ts": 'import { x } from "./helper"\n'})

    assert out["imports"] == []


# --- and what OPENCODE made of it ------------------------------------------------------------------
#
# The third reading, and the one whose absence cost the day. `custom_tools.files` says what is on
# DISK; the shim's line says what reached the GATEWAY. Between them sits OpenCode, and until this
# was asked, "never installed", "installed but the module never loaded" and "loaded but never
# offered" were the same blank. `GET /experimental/tool` lists built-ins plus custom tools and never
# MCP tools — useless for that question, exactly right for this one.

import contextlib
import json as _json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from sage.orchestrator import app as app_module


class _Handle:
    def __init__(self, url):
        self._url = url

    def url(self):
        return self._url


@contextlib.contextmanager
def _opencode_saying(tools):
    class _H(BaseHTTPRequestHandler):
        def do_GET(self):  # BaseHTTPRequestHandler's own spelling
            _H.asked = self.path
            body = _json.dumps([{"id": t} for t in tools]).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_port, _H
    finally:
        srv.shutdown()
        srv.server_close()


def _asked(tmp_path, monkeypatch, model="sage-gateway/gpt-5.4", tools=(), directory=None):
    cfg = tmp_path / ".config" / "opencode"
    cfg.mkdir(parents=True)
    (cfg / "opencode.json").write_text(_json.dumps({"model": model}))
    monkeypatch.setenv("HOME", str(tmp_path))
    with _opencode_saying(list(tools)) as (port, handler):
        monkeypatch.setattr(app_module.orchestrator, "_oc_server",
                            _Handle(f"http://127.0.0.1:{port}"), raising=False)
        out = app_module.orchestrator.opencode_tool_registry(directory)
    return out, getattr(handler, "asked", "")


def test_it_says_which_tools_opencode_holds_and_which_are_ours(tmp_path, monkeypatch):
    out, _ = _asked(tmp_path, monkeypatch, tools=["bash", "live_read_table", "live_read_files"])

    assert out["ok"] is True
    assert out["tools"] == ["bash", "live_read_files", "live_read_table"]
    assert out["ours"] == ["live_read_files", "live_read_table"]


def test_opencode_holding_none_of_ours_is_visible_rather_than_implied(tmp_path, monkeypatch):
    """The finding: the file is on disk and OpenCode never made tools of it."""
    out, _ = _asked(tmp_path, monkeypatch, tools=["bash", "read", "write"])

    assert out["ok"] is True
    assert out["ours"] == []


def test_it_asks_about_the_directory_the_turns_run_in(tmp_path, monkeypatch):
    """Instances are per-directory. Asking about the wrong one is how `opencode_says` read healthy
    through three turns that had no Live read in them."""
    _, asked = _asked(tmp_path, monkeypatch, tools=["bash"], directory="/mnt/code/.sage/chat-work")

    assert "directory=%2Fmnt%2Fcode%2F.sage%2Fchat-work" in asked


def test_the_provider_and_model_come_from_the_config_we_installed(tmp_path, monkeypatch):
    """`/experimental/tool` refuses without them, and the registry genuinely differs by model —
    production offers `apply_patch` where a bench offers `task`. A guess would answer about a
    model no turn uses."""
    _, asked = _asked(tmp_path, monkeypatch, model="sage-gateway/gpt-5.4", tools=["bash"])

    assert "provider=sage-gateway" in asked
    assert "model=gpt-5.4" in asked
    assert "agent=sage-chat" in asked


def test_a_model_that_is_not_provider_slash_name_says_so(tmp_path, monkeypatch):
    out, _ = _asked(tmp_path, monkeypatch, model="gpt-5.4", tools=["bash"])

    assert out["asked"] is False
    assert "provider/name" in out["why"]


def test_no_server_is_not_an_error_page(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(app_module.orchestrator, "_oc_server", None, raising=False)

    assert app_module.orchestrator.opencode_tool_registry()["asked"] is False


def test_a_timeout_names_the_npm_install_rather_than_blaming_the_tools(tmp_path, monkeypatch):
    """OpenCode installs `@opencode-ai/plugin` the first time it reads that directory, whether or
    not anything imports it, and blocks this call until npm gives up — 132 s measured with the
    registry black-holed. A bare "timed out" would send the reader after the wrong thing."""
    cfg = tmp_path / ".config" / "opencode"
    cfg.mkdir(parents=True)
    (cfg / "opencode.json").write_text(_json.dumps({"model": "sage-gateway/gpt"}))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(app_module.orchestrator, "_oc_server", _Handle("http://127.0.0.1:1"),
                        raising=False)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(
        httpx.TimeoutException("slow")))

    out = app_module.orchestrator.opencode_tool_registry()

    assert out["error"] == "timed out"
    assert "npm" in out["probably"]


def test_the_page_carries_all_three_readings(tmp_path, monkeypatch):
    """Disk, OpenCode, gateway. Any one alone is the blank this whole file exists to fill."""
    from fastapi.testclient import TestClient

    (tmp_path / ".config" / "opencode" / "tools").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(app_module.orchestrator, "_oc_server", None, raising=False)

    body = TestClient(app_module.control_app).get("/api/diag").json()["custom_tools"]

    assert "files" in body and "opencode_holds" in body and "opencode_log" in body


# --- the reason, if OpenCode logged one -----------------------------------------------------------


def test_the_tool_lines_of_the_log_survive_being_scrolled_past(tmp_path, monkeypatch):
    """A tool that fails to load says so at boot, and `_opencode_log_tail` shows only the last few
    lines — long gone by the time anyone opens the page."""
    log = tmp_path / "opencode.log"
    log.write_text("boot\n" + "tool failed to load: live_read.ts\n" + "chatter\n" * 200)
    monkeypatch.setattr(app_module.orchestrator, "_oc_log_path", str(log), raising=False)

    assert any("live_read.ts" in ln for ln in app_module.orchestrator.opencode_log_about_tools())


def test_no_log_is_an_empty_list_not_a_crash(monkeypatch):
    monkeypatch.setattr(app_module.orchestrator, "_oc_log_path", None, raising=False)

    assert app_module.orchestrator.opencode_log_about_tools() == []


# --- which OpenCode is actually running -----------------------------------------------------------
#
# A tool list is a version fingerprint. Production hands the model `edit`, `write`, `question` AND
# `apply_patch` together and no `task`; on a bench, 1.18.4 and 1.18.30 both swap `apply_patch` IN
# PLACE OF `edit`/`write` and both include `task` — with the real 24 KB config, the real agent, a
# nested git session directory and the real request path, where the custom tools DO arrive. The
# driver spawns `npx opencode serve`, so the pin in the image says what was installed, not what npx
# resolved. That had gone unasked for a day.


class _Cli:
    def __init__(self, said="opencode 1.18.4"):
        self.said = said
        self.asked = None

    def url(self):
        return "http://127.0.0.1:1"

    def cli(self, args, cwd=None, timeout_s=30.0):
        self.asked = args
        return self.said


def test_it_asks_the_binary_rather_than_trusting_the_pin(monkeypatch):
    cli = _Cli()
    monkeypatch.setattr(app_module.orchestrator, "_oc_server", cli, raising=False)

    out = app_module.orchestrator.opencode_version()

    assert cli.asked == ["--version"]
    assert out["says"] == "opencode 1.18.4"
    assert out["matches_pin"] is True


def test_a_version_that_is_not_the_pin_says_so_plainly(monkeypatch):
    """The whole point. `matches_pin: false` is the finding, and it must not need arithmetic."""
    monkeypatch.setattr(app_module.orchestrator, "_oc_server", _Cli("opencode 1.19.7"),
                        raising=False)

    out = app_module.orchestrator.opencode_version()

    assert out["matches_pin"] is False
    assert out["pinned"] == "1.18.4"


def test_it_names_the_binary_npx_resolved(monkeypatch):
    """`npx opencode` prefers a local `node_modules/.bin` over the globally installed pin, so the
    path is half the answer whenever the version is a surprise."""
    monkeypatch.setattr(app_module.orchestrator, "_oc_server", _Cli(), raising=False)
    monkeypatch.setattr("sage.orchestrator.service.shutil.which", lambda _n: "/usr/local/bin/opencode")

    assert app_module.orchestrator.opencode_version()["binary"] == "/usr/local/bin/opencode"


def test_a_binary_that_will_not_answer_is_not_an_error_page(monkeypatch):
    class _Boom(_Cli):
        def cli(self, *a, **k):
            raise RuntimeError("no such file")

    monkeypatch.setattr(app_module.orchestrator, "_oc_server", _Boom(), raising=False)

    assert app_module.orchestrator.opencode_version()["ok"] is False


def test_no_server_is_not_an_error_page_either(monkeypatch):
    monkeypatch.setattr(app_module.orchestrator, "_oc_server", None, raising=False)

    assert app_module.orchestrator.opencode_version()["asked"] is False
