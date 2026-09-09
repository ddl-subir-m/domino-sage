"""/api/diag reports the MCP servers OpenCode was told about, and whether each one answers.

OpenCode drops an MCP server it cannot reach SILENTLY — the tools are absent from the list the model
is offered and nothing is logged. So a Live read that never arrived (ADR-0041) read on screen exactly
like a model that chose not to call it: the assistant says it cannot see the person's data, and every
surface that could have contradicted it agreed. `agents` already reported what OpenCode resolved out
of the same config; this is the half that had nothing.

Run against a real socket rather than a stubbed `httpx.post`: the thing being checked IS the round
trip, and a stub would pass with the port wrong, which is the bug that started this.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.orchestrator import app as app_module


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler's own spelling
        self.rfile.read(int(self.headers.get("content-length") or 0))
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": [
            {"name": "live_read_table"}, {"name": "live_read_files"}]}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # keep the test output readable
        pass


@pytest.fixture
def served() -> int:
    """A live MCP server on a real port, answering `tools/list` the way `liveread.mcp` does."""
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_port
    finally:
        srv.shutdown()
        srv.server_close()


def _diag(tmp_path: Path, monkeypatch, mcp: dict | None, *, control_port: int) -> dict:
    """/api/diag with `mcp` as the config OpenCode loads — the GLOBAL copy, which is the slot
    `_install_opencode_config` explains is the one that does the work."""
    cfg = tmp_path / ".config" / "opencode"
    cfg.mkdir(parents=True)
    if mcp is not None:
        (cfg / "opencode.json").write_text(json.dumps({"mcp": mcp}))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SAGE_CONTROL_PORT", str(control_port))
    r = TestClient(app_module.control_app).get("/api/diag")
    assert r.status_code == 200
    return r.json()["mcp"]


def test_a_server_that_answers_names_the_tools_it_offers(tmp_path, monkeypatch, served):
    """The names, not just a status code. OpenCode namespaces each one by the config key before it
    reaches the model, so a server answering with a renamed tool is a prompt naming a call nothing
    will execute — which fails identically to no server at all."""
    out = _diag(tmp_path, monkeypatch, {"sage-live-read": {
        "type": "remote", "enabled": True,
        "url": f"http://127.0.0.1:{served}/mcp/live-read"}}, control_port=served)

    row = out["servers"][0]
    assert row["name"] == "sage-live-read"
    assert row["on_control_port"] is True
    assert row["reachable"]["ok"] is True
    assert row["reachable"]["tools"] == ["live_read_table", "live_read_files"]


def test_a_server_nothing_is_listening_on_says_so_instead_of_nothing(tmp_path, monkeypatch, served):
    """The silent drop, made loud. Port 1 is not being served by anything, which is the shape of the
    failure: the config named a port, the control plane is on another, and OpenCode said nothing."""
    out = _diag(tmp_path, monkeypatch, {"sage-live-read": {
        "type": "remote", "enabled": True,
        "url": "http://127.0.0.1:1/mcp/live-read"}}, control_port=served)

    assert out["servers"][0]["reachable"]["ok"] is False
    assert out["servers"][0]["reachable"]["error"]


def test_a_url_on_the_wrong_port_is_flagged_even_when_something_answers(tmp_path, monkeypatch,
                                                                       served):
    """The rewrite in `_install_opencode_config` puts this URL on the port this process serves. A
    mismatch means it did not run and the image's checked-in `:8080` was read straight through —
    worth naming on its own, because on a busy host that port may well answer as something else."""
    out = _diag(tmp_path, monkeypatch, {"sage-live-read": {
        "type": "remote", "enabled": True,
        "url": f"http://127.0.0.1:{served}/mcp/live-read"}}, control_port=served + 1)

    assert out["servers"][0]["on_control_port"] is False


def test_a_project_config_that_shadows_the_mcp_block_is_named(tmp_path, monkeypatch):
    """The slot that actually did it. OpenCode resolves project config off the git root of the
    SESSION directory, which is the Project volume — so it outranks OPENCODE_CONFIG, it is not ours
    to fill, and deleting the workspace does not clear it.

    Everything else can look right while this is wrong: `agents` still lists all five, because they
    come from the global copy loaded earlier, and `mcp` still reports a healthy server, because it
    reads the file WE wrote. Only this says which files OpenCode read.
    """
    workspace = tmp_path / "mnt" / "code"
    (workspace / ".opencode").mkdir(parents=True)
    (workspace / ".opencode" / "opencode.json").write_text(json.dumps({"mcp": {}}))
    monkeypatch.setattr(app_module.orchestrator, "_wm",
                        type("W", (), {"_dir": workspace})(), raising=False)
    monkeypatch.setattr(app_module.orchestrator, "_opencode_log_tail", lambda n=30: [], raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config" / "opencode").mkdir(parents=True)

    out = TestClient(app_module.control_app).get("/api/diag").json()["opencode_config"]

    assert out["shadowing_mcp"] == [str(workspace / ".opencode" / "opencode.json")]
    row = next(r for r in out["slots"] if r["path"].endswith(".opencode/opencode.json"))
    assert row["exists"] is True and row["ours"] is False and row["declares_mcp"] is True


def test_a_slot_opencode_only_probed_is_not_read_as_a_file_it_found(tmp_path, monkeypatch):
    """OpenCode logs `loading path=...` for every slot it TRIES, existing or not — it logs one for
    `config.json`, which Sage never writes. Absence is the common case, so reporting it is what
    stops a probed path being read as a found one."""
    monkeypatch.setattr(app_module.orchestrator, "_opencode_log_tail",
                        lambda n=30: ["message=loading path=/nowhere/opencode.json"], raising=False)
    monkeypatch.setattr(app_module.orchestrator, "_wm", None, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config" / "opencode").mkdir(parents=True)

    out = TestClient(app_module.control_app).get("/api/diag").json()["opencode_config"]

    row = next(r for r in out["slots"] if r["path"] == "/nowhere/opencode.json")
    assert row["exists"] is False
    assert out["shadowing_mcp"] == []


def test_the_config_report_never_carries_a_value(tmp_path, monkeypatch):
    """A config holds the gateway's credentials. Keys say which slot spoke about what; values are
    never the question being asked here."""
    workspace = tmp_path / "mnt" / "code"
    (workspace / ".opencode").mkdir(parents=True)
    (workspace / ".opencode" / "opencode.json").write_text(
        json.dumps({"provider": {"sage-gateway": {"options": {"apiKey": "sk-do-not-leak"}}}}))
    monkeypatch.setattr(app_module.orchestrator, "_wm",
                        type("W", (), {"_dir": workspace})(), raising=False)
    monkeypatch.setattr(app_module.orchestrator, "_opencode_log_tail", lambda n=30: [], raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config" / "opencode").mkdir(parents=True)

    out = TestClient(app_module.control_app).get("/api/diag").json()["opencode_config"]

    assert "sk-do-not-leak" not in json.dumps(out)
    row = next(r for r in out["slots"] if r["path"].endswith(".opencode/opencode.json"))
    assert row["keys"] == ["provider"]


def test_a_config_that_cannot_be_read_does_not_break_the_page(tmp_path, monkeypatch):
    """A diagnostic must never be the thing that breaks the diagnostics page — and a missing global
    config is itself a finding, since it is what OpenCode reads."""
    out = _diag(tmp_path, monkeypatch, None, control_port=8080)

    assert out["error"]
    assert "opencode.json" in out["config"]
