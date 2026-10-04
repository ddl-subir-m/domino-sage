"""A Built App's `secret()` reads a viewer's own key before the builder's (#644, ADR-0072).

What has to hold:

  - `secret(name)` is the viewer's value from their sealed cookie, else the environment, else the
    default — inside a route, per request;
  - the cookie opens only with this app's key: a tampered one, or one sealed for another app id,
    reads as empty;
  - the `/sage/keys` routes accept only the names in `sage_keys.json`, never answer a value, and
    refuse a cross-site request;
  - Sage writes `sage_keys.json` from the app's own `secret("NAME")` calls, both quote styles, at
    publish and when the preview starts;
  - `sage_mcp` speaks streamable HTTP to a server answering in JSON or in an event stream.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.preview.supervisor import UvicornSupervisor
from sage.workspace import viewer_keys
from sage.workspace.manager import WorkspaceManager

REPO = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO / "template" / "fastapi-antd"
# Not a real key: planted so a test can grep every answer for it.
PLANTED = "planted-viewer-value-0f3a9c"
APP_KEY = "test-app-key-not-a-real-secret-000000000000"
APP_CODE = '''
import sage_serve
from fastapi import FastAPI
from sage_secrets import secret

app = FastAPI()
sage_serve.mount(app)


@app.get("/api/which")
def which() -> dict:
    return {"crm": secret("CRM_TOKEN", "fallback"), "other": secret('OTHER_KEY')}
'''


def _seed(tmp_path: Path):
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=REPO / "template" / "react-vite")
    mgr.ensure("proj1")
    return mgr, mgr.create_app("proj1", stack="fastapi-antd")


def _load(app_dir: Path, name: str):
    for stale in [k for k in sys.modules if k in ("sage_queries", "sage_domino", "sage_secrets",
                                                    "sage_mcp", "sage_serve")]:
        del sys.modules[stale]
    sys.path.insert(0, str(app_dir))
    try:
        spec = importlib.util.spec_from_file_location(name, app_dir / "app.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(app_dir))
    return mod


@pytest.fixture
def app(tmp_path: Path, monkeypatch):
    """A seeded fastapi-antd app whose code reads two keys, served over https so the Secure cookie
    round-trips. Yields (workspace, client, sage_secrets module)."""
    monkeypatch.delenv("SAGE_PREVIEW", raising=False)
    monkeypatch.delenv("DOMINO_API_HOST", raising=False)
    monkeypatch.setenv("SAGE_APP_KEY", APP_KEY)
    monkeypatch.setenv("CRM_TOKEN", "builders-crm-token")
    monkeypatch.delenv("OTHER_KEY", raising=False)
    mgr, ws = _seed(tmp_path)
    (ws.path / "app.py").write_text(APP_CODE)
    project = ws.path.parent.parent
    (project / ".sage").mkdir(exist_ok=True)
    (project / ".sage" / "secrets.json").write_text(json.dumps({"CRM_TOKEN": {"note": "the CRM"}}))
    assert mgr.refresh_entry_script() is True, "publish writes sage_keys.json"
    mod = _load(ws.path, f"viewer_keys_app_{tmp_path.name}")
    return ws, TestClient(mod.app, base_url="https://testserver"), sys.modules["sage_secrets"]


def _everything(r) -> str:
    return r.text + json.dumps(dict(r.headers))


def test_secret_is_the_viewers_then_the_builders_then_the_default(app):
    _, client, _ = app
    assert client.get("/api/which").json() == {"crm": "builders-crm-token", "other": None}
    r = client.post("/sage/keys", json={"name": "CRM_TOKEN", "value": PLANTED, "base": ""})
    assert r.status_code == 200 and r.json() == {"name": "CRM_TOKEN", "note": "the CRM", "set": True}
    assert client.get("/api/which").json()["crm"] == PLANTED
    client.cookies.clear()
    assert client.get("/api/which").json()["crm"] == "builders-crm-token"


def test_secret_falls_back_to_the_default_with_no_env(app, monkeypatch):
    _, client, _ = app
    monkeypatch.delenv("CRM_TOKEN")
    assert client.get("/api/which").json()["crm"] == "fallback"


def test_a_sealed_map_round_trips_and_a_tampered_one_reads_empty(app):
    _, _, ss = app
    token = ss.seal({"CRM_TOKEN": PLANTED})
    assert PLANTED not in token
    assert ss.unseal(token) == {"CRM_TOKEN": PLANTED}
    flipped = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    assert ss.unseal(flipped) == {}
    assert ss.unseal("not-a-cookie") == {}
    assert ss.unseal("") == {}


def test_app_bs_server_cannot_open_app_as_cookie(app):
    _, _, ss = app
    token = ss.seal({"CRM_TOKEN": PLANTED}, app_id="app-a")
    assert ss.unseal(token, app_id="app-a") == {"CRM_TOKEN": PLANTED}
    assert ss.unseal(token, app_id="app-b") == {}


def test_the_routes_take_only_the_apps_own_names_and_never_answer_a_value(app):
    ws, client, ss = app
    assert json.loads((ws.path / "sage_keys.json").read_text()) == [
        {"name": "CRM_TOKEN", "note": "the CRM"}, {"name": "OTHER_KEY", "note": ""}]
    refused = client.post("/sage/keys", json={"name": "NOT_READ", "value": PLANTED, "base": ""})
    assert refused.status_code == 404 and PLANTED not in _everything(refused)
    saved = client.post("/sage/keys", json={"name": "CRM_TOKEN", "value": PLANTED,
                                            "base": f"/apps/{ws.path.name}/"})
    assert saved.status_code == 200 and PLANTED not in _everything(saved)
    cookie = saved.headers["set-cookie"]
    for flag in ("HttpOnly", "Secure", "SameSite=strict", f"Path=/apps/{ws.path.name}"):
        assert flag.lower() in cookie.lower(), flag
    client.cookies.set(ss.COOKIE, ss.seal({"CRM_TOKEN": PLANTED}))
    listed = client.get("/sage/keys")
    assert listed.json() == [{"name": "CRM_TOKEN", "note": "the CRM", "set": True},
                             {"name": "OTHER_KEY", "note": "", "set": False}]
    assert PLANTED not in _everything(listed)
    assert client.delete("/sage/keys/NOT_READ").status_code == 404
    cleared = client.delete("/sage/keys/CRM_TOKEN", params={"base": "/"})
    assert cleared.json() == {"ok": True} and 'Max-Age=0' in cleared.headers["set-cookie"]
    bad_base = client.post("/sage/keys", json={"name": "CRM_TOKEN", "value": "x",
                                               "base": "/a; Domain=evil"})
    assert bad_base.status_code == 400


def test_a_cross_site_request_is_refused(app):
    _, client, _ = app
    cross = {"Sec-Fetch-Site": "cross-site"}
    assert client.get("/sage/keys", headers=cross).status_code == 403
    assert client.post("/sage/keys", headers=cross,
                       json={"name": "CRM_TOKEN", "value": PLANTED, "base": ""}).status_code == 403
    assert client.delete("/sage/keys/CRM_TOKEN", headers=cross).status_code == 403
    assert client.get("/sage/keys", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_without_an_app_key_secret_reads_the_environment_and_the_page_says_why(app, monkeypatch):
    _, client, ss = app
    token = ss.seal({"CRM_TOKEN": PLANTED})
    monkeypatch.delenv("SAGE_APP_KEY")
    client.cookies.set(ss.COOKIE, token)
    assert client.get("/api/which").json()["crm"] == "builders-crm-token"
    r = client.get("/sage/keys")
    assert r.status_code == 503 and "SAGE_APP_KEY" in r.json()["error"]


def test_every_page_carries_the_your_keys_script(app):
    _, client, _ = app
    assert 'src="static/sage/keys.js"' in client.get("/").text
    assert client.get("/static/sage/keys.js").status_code == 200


def test_the_scan_finds_both_quote_styles_and_skips_sages_own(tmp_path: Path):
    (tmp_path / "routes").mkdir()
    (tmp_path / "app.py").write_text('secret("ONE")\nsecret( \'TWO\' )\nsecret(name)\n')
    (tmp_path / "routes" / "crm.py").write_text('x = secret("THREE", "d")\nsecret("SAGE_APP_KEY")\n')
    (tmp_path / "sage_secrets.py").write_text('secret("FROM_A_DOCSTRING")')
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text('secret("HIDDEN")')
    assert viewer_keys.scan(tmp_path) == ["ONE", "THREE", "TWO"]


def test_an_app_that_reads_no_key_gets_no_names_file(tmp_path: Path):
    mgr, ws = _seed(tmp_path)
    assert viewer_keys.write_names(ws.path) is False
    assert not (ws.path / "sage_keys.json").exists()
    assert mgr.refresh_entry_script() is False


def test_the_preview_writes_the_names_when_it_starts(tmp_path: Path, monkeypatch):
    _, ws = _seed(tmp_path)
    (ws.path / "app.py").write_text(APP_CODE)
    sup = UvicornSupervisor(ws.path)
    launched = []
    monkeypatch.setattr(sup, "_launch", lambda *a: launched.append(a))
    sup._spawn()
    assert launched
    assert [r["name"] for r in json.loads((ws.path / "sage_keys.json").read_text())] == \
        ["CRM_TOKEN", "OTHER_KEY"]


# --- sage_mcp --------------------------------------------------------------------------------------

def _load_mcp():
    spec = importlib.util.spec_from_file_location("sage_mcp_under_test", TEMPLATE_DIR / "sage_mcp.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@contextmanager
def _stub_mcp(stream: bool):
    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"message": message, "auth": self.headers.get("Authorization"),
                         "session": self.headers.get("mcp-session-id")})
            if "id" not in message:
                self.send_response(202)
                self.end_headers()
                return
            method = message["method"]
            if method == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {}}
            elif method == "tools/list":
                result = {"tools": [{"name": "find_account", "inputSchema": {"type": "object"}}]}
            elif method == "tools/call":
                result = {"content": [{"type": "text", "text": f"found {message['params']['arguments']['name']}"}],
                          "isError": False}
            else:
                reply = {"jsonrpc": "2.0", "id": message["id"], "error": {"message": "no such method"}}
                result = None
            if result is not None:
                reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
            body = (f"event: message\ndata: {json.dumps(reply)}\n\n" if stream else json.dumps(reply)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
            self.send_header("mcp-session-id", "sess-1")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/mcp", seen
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


@pytest.mark.parametrize("stream", [False, True])
def test_sage_mcp_lists_and_calls_tools_on_a_stub_server(stream: bool):
    mcp = _load_mcp()
    headers = {"Authorization": "Bearer stub-token"}
    with _stub_mcp(stream) as (url, seen):
        assert [t["name"] for t in mcp.list_tools(url, headers)] == ["find_account"]
        result = mcp.call_tool(url, "find_account", {"name": "Acme"}, headers)
    assert result["content"][0]["text"] == "found Acme"
    methods = [s["message"]["method"] for s in seen]
    assert methods == ["initialize", "notifications/initialized", "tools/list",
                       "initialize", "notifications/initialized", "tools/call"]
    assert all(s["auth"] == "Bearer stub-token" for s in seen)
    assert seen[1]["session"] == "sess-1", "the session id the server gave rides on what follows"


def test_sage_mcp_says_why_without_echoing_the_headers():
    mcp = _load_mcp()
    with pytest.raises(mcp.McpError) as e:
        mcp.list_tools("http://127.0.0.1:1/mcp", {"Authorization": "Bearer stub-token"}, timeout=2)
    assert "stub-token" not in str(e.value)
