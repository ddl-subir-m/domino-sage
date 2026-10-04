"""A Domino-hosted MCP server is reached through Sage, with a fresh Domino token on every call (#645).

A Domino App wants a current token on each request, and OpenCode reads a `{env:...}` header once, so
it would go stale within minutes. OpenCode is pointed at the control port's `/mcp/domino/<name>`
instead, which forwards each request to the real URL with a sidecar token fetched for it. The token
goes only to the Domino host or a subdomain of it, never to a browser, and never into a file.
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage import extension_mcp

from . import mcp_stub
from .test_no_edit_recovery_uses_the_active_stack import _no_waiting  # noqa: F401  (autouse)
from .test_turn_path import _build

HOST = "domino.example.com"
PORT = "8765"


class _Sidecar(BaseHTTPRequestHandler):
    """The workspace token sidecar: a different token on every fetch."""

    def do_GET(self):
        self.server.issued += 1
        body = f"Bearer tok-645-{self.server.issued}".encode()
        self.send_response(200)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _serve(handler, **attrs):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    for k, v in attrs.items():
        setattr(server, k, v)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def sidecar(monkeypatch):
    server = _serve(_Sidecar, issued=0)
    monkeypatch.setenv("GATEWAY_TOKEN_URL", f"http://127.0.0.1:{server.server_address[1]}/token")
    monkeypatch.setenv("DOMINO_API_HOST", f"https://{HOST}")
    monkeypatch.setenv("SAGE_CONTROL_PORT", PORT)
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def stub():
    server = _serve(mcp_stub.Handler, seen=[])
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def client(tmp_path, monkeypatch, sidecar):
    import sage.orchestrator.app as app_module

    orch, oc, _ = _build(tmp_path, [])
    monkeypatch.setattr(app_module, "orchestrator", orch)
    http = TestClient(app_module.control_app)
    http.orch, http.oc, http.sidecar = orch, oc, sidecar
    http.root = Path(orch.project(start_preview=False).record.path)
    return http


def _add(client, monkeypatch, **body):
    """Add a server without reaching for its (unreachable) real URL."""
    with monkeypatch.context() as m:
        m.setattr(extension_mcp, "read_tools", lambda config, env: [])
        return client.post("/api/project/mcp", json={"kind": "domino", **body})


def _point_at(root: Path, name: str, url: str) -> None:
    """A stub cannot serve the https Domino URL the door insists on, so the stored URL is moved to
    it afterwards."""
    registry = root / ".opencode" / "sage-mcp.json"
    body = json.loads(registry.read_text())
    body["servers"][name]["url"] = url
    registry.write_text(json.dumps(body))


def _stub_url(stub) -> str:
    return f"http://127.0.0.1:{stub.server_address[1]}/mcp"


def _rpc(method: str, id_: int, **params) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params}


# ---- the door ----------------------------------------------------------------------------------

@pytest.mark.parametrize(("url", "headers", "said"), [
    (f"http://{HOST}/mcp", None, "https://"),
    (f"https://{HOST}.evil.com/mcp", None, "nowhere else"),
    (f"https://evil{HOST}/mcp", None, "nowhere else"),
    (f"https://{HOST}@evil.com/mcp", None, "nowhere else"),
    (f"https://evil.com\\@{HOST}/mcp", None, "nowhere else"),
    ("https://crm.example.com/mcp", None, "nowhere else"),
    (f"https://apps.{HOST}/mcp", {"Authorization": "Bearer {env:TOK}"}, "Authorization"),
    (f"https://apps.{HOST}/mcp", {"authorization": "Bearer {env:TOK}"}, "Authorization"),
])
def test_a_domino_server_is_refused_off_the_domino_host_or_with_its_own_authorization(
        client, url, headers, said):
    r = client.post("/api/project/mcp", json={"name": "crm", "kind": "domino", "url": url,
                                              "headers": headers})
    assert r.status_code == 400 and said in r.json()["error"]
    assert not (client.root / ".opencode" / "opencode.json").exists()
    assert client.sidecar.issued == 0


def test_off_domino_the_kind_is_refused_with_a_reason(client, monkeypatch):
    monkeypatch.delenv("DOMINO_API_HOST")
    r = client.post("/api/project/mcp", json={"name": "crm", "kind": "domino",
                                              "url": f"https://apps.{HOST}/mcp"})
    assert r.status_code == 400 and "only when Sage runs on Domino" in r.json()["error"]


def test_an_unknown_kind_is_refused(client):
    r = client.post("/api/project/mcp", json={"name": "crm", "kind": "stdio", "url": "https://x/mcp"})
    assert r.status_code == 400 and "remote or domino" in r.json()["error"]


def test_opencode_is_pointed_at_the_control_port_and_never_at_the_real_url(client, monkeypatch):
    monkeypatch.setenv("REGION_645", "eu")
    real = f"https://apps.{HOST}/crm/mcp"
    r = _add(client, monkeypatch, name="crm", url=real,
             headers={"X-Region": "{env:REGION_645}", "X-Team": "sales"})
    assert r.status_code == 200
    row = r.json()
    assert (row["kind"], row["url"]) == ("domino", real)

    config = json.loads((client.root / ".opencode" / "opencode.json").read_text())
    assert config["mcp"]["crm"] == {
        "type": "remote", "url": f"http://127.0.0.1:{PORT}/mcp/domino/crm",
        "headers": {"X-Region": "{env:REGION_645}", "X-Team": "sales"}, "enabled": True}
    assert real not in (client.root / ".opencode" / "opencode.json").read_text()

    client.post("/api/project/mcp", json={"name": "docs", "url": "https://docs.example.com/mcp"})
    kinds = {s["name"]: (s["kind"], s["url"]) for s in client.get("/api/project/mcp").json()["servers"]}
    assert kinds == {"crm": ("domino", real), "docs": ("remote", "https://docs.example.com/mcp")}


def test_tools_are_listed_from_the_real_url_with_a_token_fetched_for_each_read(client, monkeypatch):
    asked: list[dict] = []
    monkeypatch.setattr(extension_mcp, "read_tools",
                        lambda config, env: (asked.append(config), ["echo"])[1])
    real = f"https://{HOST}/mcp"
    row = client.post("/api/project/mcp", json={"name": "crm", "kind": "domino", "url": real}).json()
    assert row["tools"] == ["echo"]
    client.post("/api/project/mcp/crm/tools")
    assert [(c["url"], c["headers"]) for c in asked] == [
        (real, {"Authorization": "Bearer tok-645-1"}), (real, {"Authorization": "Bearer tok-645-2"})]
    for path in (client.root / ".opencode").rglob("*"):
        if path.is_file():
            assert "tok-645" not in path.read_text(), path


def test_read_again_reaches_the_server_signed_in(client, monkeypatch, stub):
    _add(client, monkeypatch, name="crm", url=f"https://{HOST}/mcp")
    _point_at(client.root, "crm", _stub_url(stub))
    row = client.post("/api/project/mcp/crm/tools").json()
    assert row["tools"] == ["echo", "ping", "write_note"]
    assert extension_mcp.server(client.root, "crm")["warning"] is None
    assert {h["authorization"] for h in stub.seen} == {f"Bearer tok-645-{client.sidecar.issued}"}


# ---- the forwarding route ----------------------------------------------------------------------

def test_each_forwarded_request_carries_its_own_fresh_token_and_the_answer_streams_back(
        client, monkeypatch, stub, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("REGION_645", "eu")
    _add(client, monkeypatch, name="crm", url=f"https://{HOST}/mcp",
         headers={"X-Region": "{env:REGION_645}"})
    _point_at(client.root, "crm", _stub_url(stub))
    n = client.sidecar.issued
    sent = {"accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18",
            "x-region": "eu", "x-other": "not configured", "authorization": "Bearer caller"}

    first = client.post("/mcp/domino/crm", headers=sent, json=_rpc("initialize", 1))
    assert first.status_code == 200 and first.headers["mcp-session-id"] == "s-1"
    assert first.json()["result"]["serverInfo"]["name"] == "stub"

    listed = client.post("/mcp/domino/crm", headers={**sent, "mcp-session-id": "s-1"},
                         json=_rpc("tools/list", 2))
    assert listed.status_code == 200
    assert listed.headers["content-type"].startswith("text/event-stream")
    [data] = [line[5:] for line in listed.text.splitlines() if line.startswith("data:")]
    assert [t["name"] for t in json.loads(data)["result"]["tools"]] == ["echo", "write_note", "ping"]

    assert [h["authorization"] for h in stub.seen] == [f"Bearer tok-645-{n + 1}",
                                                       f"Bearer tok-645-{n + 2}"]
    for h in stub.seen:
        assert h["x-region"] == "eu" and h["accept"] == sent["accept"]
        assert h["mcp-protocol-version"] == "2025-06-18" and "x-other" not in h
    assert stub.seen[1]["mcp-session-id"] == "s-1"
    assert "tok-645" not in caplog.text + first.text + listed.text


@pytest.mark.parametrize("browser", [{"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Mode": "cors"}])
def test_a_browser_is_refused_before_anything_is_fetched(client, monkeypatch, stub, browser):
    _add(client, monkeypatch, name="crm", url=f"https://{HOST}/mcp")
    _point_at(client.root, "crm", _stub_url(stub))
    issued = client.sidecar.issued
    r = client.post("/mcp/domino/crm", headers=browser, json=_rpc("initialize", 1))
    assert r.status_code == 403
    assert stub.seen == [] and client.sidecar.issued == issued


def test_only_a_switched_on_domino_server_is_forwarded_to(client, monkeypatch, stub):
    _add(client, monkeypatch, name="crm", url=f"https://{HOST}/mcp")
    _point_at(client.root, "crm", _stub_url(stub))
    client.post("/api/project/mcp", json={"name": "docs", "url": _stub_url(stub)})
    stub.seen.clear()
    assert client.post("/mcp/domino/nope", json=_rpc("initialize", 1)).status_code == 404
    assert client.post("/mcp/domino/docs", json=_rpc("initialize", 1)).status_code == 404
    client.put("/api/project/mcp/crm/enabled", json={"enabled": False})
    assert client.post("/mcp/domino/crm", json=_rpc("initialize", 1)).status_code == 404
    assert stub.seen == []
