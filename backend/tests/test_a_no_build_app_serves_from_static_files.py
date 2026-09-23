"""A fastapi-antd app is seeded, served, checked and published with no build step (#490).

The one stack Sage seeds: FastAPI serving a page that loads React, Ant Design, Day.js and
Highcharts as plain scripts — the stack the Workbench itself is built on. It gets nothing from
Node or a bundler; everything comes from files on disk and the interpreter running Sage. What has
to hold:

  - seeding it produces an app with no `package.json`, no `node_modules` and a record naming it;
  - its own server (`sage_serve.py`, mounted by `app.py`) serves the page with the mount-prefix
    shim, the static tree with `no-cache`, and the named queries through `sage_queries.py`;
  - the platform relay (#489) is on it, fenced, with no host needed to prove the fence;
  - the page asks no CDN for anything (ADR-0014, #19);
  - the generated model configs are plain-script globals, not module exports.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sage.resources.app_helpers import TEMPLATE
from sage.resources.bindings import Binding
from sage.resources.model_api_credentials import Credential
from sage.resources.pinned_model import render_config as render_llm_config
from sage.resources.pinned_model_api import render_config as render_model_api_config
from sage.workspace.manager import WorkspaceManager
from sage.workspace.stack import FASTAPI_ANTD

REPO = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO / "template" / "fastapi-antd"


def _seed(tmp_path: Path):
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=TEMPLATE_DIR)
    return mgr, mgr.ensure("proj1")


def _load(app_dir: Path, name: str):
    """`sage_serve.py` out of the SEEDED app, by path — so `ROOT` is the app, the way a published
    app runs it. Registered in sys.modules before exec, as every by-path load here is."""
    spec = importlib.util.spec_from_file_location(name, app_dir / "sage_serve.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def served(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SAGE_PREVIEW", raising=False)
    monkeypatch.delenv("DOMINO_API_HOST", raising=False)
    _, ws = _seed(tmp_path)
    # Each test gets its own module object: `sage_serve` and `sage_queries` are cached by name in
    # sys.modules, and a second app dir would otherwise read the first one's ROOT.
    for stale in [k for k in sys.modules if k in ("sage_queries", "sage_domino")]:
        del sys.modules[stale]
    serve = _load(ws.path, f"fastapi_antd_serve_{tmp_path.name}")
    app = FastAPI()
    serve.mount(app)
    return ws, serve, TestClient(app)


def test_a_bare_ensure_seeds_the_no_build_stack_by_default(tmp_path: Path, monkeypatch):
    """The production default: a caller that names no stack — an older Workbench, a handoff —
    gets a fastapi-antd app, on the real template with no override at all."""
    monkeypatch.delenv("SAGE_DEFAULT_STACK", raising=False)
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=TEMPLATE_DIR)
    ws = mgr.ensure("proj1")
    assert ws.stack_name == "fastapi-antd"
    assert (ws.path / "app.py").is_file() and not (ws.path / "package.json").exists()


def test_seeding_the_stack_leaves_no_node_behind(tmp_path: Path):
    mgr, ws = _seed(tmp_path)
    assert ws.stack_name == "fastapi-antd" and mgr.stack == FASTAPI_ANTD
    assert (ws.path / "app.py").is_file() and (ws.path / "static" / "index.html").is_file()
    assert not (ws.path / "package.json").exists()
    assert not (ws.path / "node_modules").exists()
    assert not (ws.path / "vite.config.ts").exists()
    assert (ws.path / "app.sh").stat().st_mode & 0o111, "publish runs it; copy2 keeps +x"
    for rel in FASTAPI_ANTD.deploy_files + FASTAPI_ANTD.owned_sources:
        assert (ws.path / rel).is_file(), rel
    assert ws.app_entry == ws.path / "static" / "app.js"
    assert ws.helpers is TEMPLATE
    assert mgr.template == TEMPLATE_DIR
    # Nothing to refresh: the app was just seeded from these very files.
    assert mgr.refresh_entry_script() is False
    assert mgr.refresh_owned_sources() is False


def test_the_page_is_served_with_the_mount_prefix_shim(served):
    _ws, _serve, client = served
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"
    assert "__SAGE_BASE__" in r.text and "<base href=" in r.text
    assert "__SAGE_PREVIEW__=false" in r.text
    assert r.text.index("sage: recover the mount prefix") < r.text.index("<link"), \
        "the shim writes the <base> before any URL is resolved"
    # The shim carries the path this server RECEIVED, which the browser subtracts from its own.
    assert json.dumps("/") in r.text


def test_the_static_tree_and_the_attachments_are_served_and_revalidated(served):
    ws, _serve, client = served
    r = client.get("/static/app.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert "sage-placeholder" in r.text
    assert client.get("/static/vendor/antd.min.js").status_code == 200
    assert client.get("/static/sage/appQuery.js").status_code == 200
    assert client.get("/static/nope.js").status_code == 404
    (ws.path / "public" / "data" / "slug").mkdir(parents=True)
    (ws.path / "public" / "data" / "slug" / "rows.csv").write_text("a,b\n1,2\n")
    r = client.get("/data/slug/rows.csv")
    assert r.status_code == 200 and r.text.startswith("a,b")


def test_named_queries_are_answered_by_the_shared_module(served):
    _ws, _serve, client = served
    r = client.post("/api/queries/revenue", json={"params": {}})
    assert r.status_code == 404
    assert r.json() == {"error": "This app has no query called revenue."}
    assert client.get("/api/queries/revenue").status_code == 405
    assert client.post("/api/queries/revenue", content=b"{not json").status_code == 400
    assert client.post("/api/queries/revenue", content=b"x" * (64 * 1024 + 1)).status_code == 413


def test_the_platform_relay_is_fenced_before_it_needs_a_host(served):
    _ws, _serve, client = served
    assert client.get("/api/domino/etc/passwd").status_code == 403
    assert client.get("/api/domino/v4/jobs").status_code == 403
    # Allowed family, no host: the relay says the platform is out of reach, never a traceback.
    r = client.get("/api/domino/api/users/v1/self")
    assert r.status_code == 503 and "error" in r.json()


def test_the_preview_variable_reaches_the_page(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SAGE_PREVIEW", "1")
    _, ws = _seed(tmp_path)
    for stale in [k for k in sys.modules if k in ("sage_queries", "sage_domino")]:
        del sys.modules[stale]
    serve = _load(ws.path, f"fastapi_antd_preview_{tmp_path.name}")
    app = FastAPI()
    serve.mount(app)
    assert "__SAGE_PREVIEW__=true" in TestClient(app).get("/").text


def test_the_page_asks_no_cdn_for_anything():
    """ADR-0014 refuses remote assets and #19 is the same rule for fonts: on a locked-down tenant a
    CDN request fails quietly and the app is blank. Every URL the page loads is relative."""
    html = (TEMPLATE_DIR / "static" / "index.html").read_text()
    for host in ("//unpkg.com", "//cdn.jsdelivr.net", "//code.highcharts.com",
                 "//fonts.googleapis.com", "//cdnjs.cloudflare.com"):
        assert host not in html, host
    for url in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert not url.startswith(("http:", "https:", "//", "/")), url


def test_the_generated_configs_are_plain_script_globals():
    llm = render_llm_config([Binding("llm_alias", "llm:1", "gpt", "GPT")],
                            "https://apps.host/gateway", "proj", names=TEMPLATE)
    assert "window.appLlmConfig = {" in llm and "export const" not in llm
    assert "./appLlm.js" in llm
    api = render_model_api_config([Binding("model_api", "m:1", "scorer", "Scorer")],
                                  {"m:1": Credential("https://host/models/1", "t")}, names=TEMPLATE)
    assert "window.appModelApiConfig = {" in api and "export const" not in api


def test_the_no_build_instructions_carry_the_numeric_stdout_guard():
    """Every rule the guard test is held to by name lives in this stack's AGENTS.md too, proved
    against the SAME probes that test uses rather than a second list that would drift."""
    from .test_build_agent_numeric_stdout_guard import PROBES

    ours = (TEMPLATE_DIR / "AGENTS.md").read_text()
    for probe in PROBES:
        assert probe in ours, probe
    # The pack's tokens, never a bare product or platform name (ADR-0014).
    assert "{assistantName}" in ours and "{platformName}" in ours
    assert re.search(r"\bSage\b", ours) is None, "a bare product name survives in the template"
    assert re.search(r"\bDomino\b", ours) is None, "a bare platform name survives in the template"
    for noun in ("Dataset", "Data Source", "Model API", "LLM Alias", "Built App", "Gallery"):
        assert noun in ours, f"{noun} is not offered as a synonym"
    # The live-read tools are named, so an agent on this stack is told to call them too.
    assert "live_read_table" in ours and "live_read_files" in ours


def test_the_stacks_shape_is_internally_consistent():
    assert FASTAPI_ANTD.preview_config is None and FASTAPI_ANTD.server_script is None
    assert FASTAPI_ANTD.deploy_files[-1] == "app.sh"
    assert FASTAPI_ANTD.deploy_files.index("sage_queries.py") < FASTAPI_ANTD.deploy_files.index("sage_serve.py")
    assert FASTAPI_ANTD.owned_sources.index("static/sage/reportRuntimeError.js") < \
        FASTAPI_ANTD.owned_sources.index("static/sage/errorBoundary.js")
