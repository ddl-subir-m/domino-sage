"""ONE-APP-PLAN.md §2.3's spike addendum: `_write_project_opencode_config` fills the REAL
project-config slot for one project — a gitignored `opencode.json` at that project's own git root —
with the FULL voiced config (not a `{"provider":{"sage-gateway":{"options":{"baseURL":...}}}}` stub),
every port-bearing URL rewritten to this process's own control port AND that project's `/p/<slug>`
path prefix.
"""
from __future__ import annotations

import json

from sage.orchestrator.app import _write_project_opencode_config


def _seed_source(tmp_path, cfg):
    src_dir = tmp_path / "repo"
    src_dir.mkdir()
    (src_dir / "opencode.json").write_text(json.dumps(cfg))
    return src_dir


def test_writes_the_full_config_not_a_stub(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    src_dir = _seed_source(tmp_path, {
        "model": "x",
        "provider": {
            "sage-gateway": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Sage Enforcement Shim",
                "options": {"baseURL": "http://127.0.0.1:8080/v1", "apiKey": "local-shim-no-auth"},
                "models": {"gpt-5.4": {"name": "GPT-5.4"}},
            }
        },
        "agent": {"sage-chat": {"prompt": "You are Sage's chat agent."}},
    })
    dest = tmp_path / "projects" / "alpha" / "opencode.json"
    _write_project_opencode_config(src_dir, 9999, "alpha", dest)
    written = json.loads(dest.read_text())
    # The whole shape survives — not just the one field a stub would carry.
    assert written["provider"]["sage-gateway"]["npm"] == "@ai-sdk/openai-compatible"
    assert written["provider"]["sage-gateway"]["models"]["gpt-5.4"]["name"] == "GPT-5.4"
    assert written["provider"]["sage-gateway"]["options"]["apiKey"] == "local-shim-no-auth"


def test_the_gateway_baseurl_gets_this_ports_and_this_projects_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    src_dir = _seed_source(tmp_path, {
        "provider": {"sage-gateway": {"options": {"baseURL": "http://127.0.0.1:8080/v1"}}},
    })
    dest = tmp_path / "projects" / "alpha" / "opencode.json"
    _write_project_opencode_config(src_dir, 9999, "alpha", dest)
    written = json.loads(dest.read_text())
    assert written["provider"]["sage-gateway"]["options"]["baseURL"] == "http://127.0.0.1:9999/p/alpha/v1"


def test_an_mcp_server_url_gets_the_same_rewrite(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    src_dir = _seed_source(tmp_path, {
        "mcp": {"live-read": {"type": "remote", "url": "http://127.0.0.1:8080/mcp/live-read"}},
    })
    dest = tmp_path / "projects" / "beta" / "opencode.json"
    _write_project_opencode_config(src_dir, 9999, "beta", dest)
    written = json.loads(dest.read_text())
    assert written["mcp"]["live-read"]["url"] == "http://127.0.0.1:9999/p/beta/mcp/live-read"


def test_a_local_mcp_command_path_is_anchored_to_the_source_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    src_dir = _seed_source(tmp_path, {
        "mcp": {"tool": {"type": "local", "command": ["node", "backend/sage/driver/provider.mjs"]}},
    })
    dest = tmp_path / "projects" / "gamma" / "opencode.json"
    _write_project_opencode_config(src_dir, 9999, "gamma", dest)
    written = json.loads(dest.read_text())
    resolved = written["mcp"]["tool"]["command"][1]
    assert resolved == str((src_dir / "backend/sage/driver/provider.mjs").resolve())


def test_a_bare_word_command_argument_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    src_dir = _seed_source(tmp_path, {
        "mcp": {"tool": {"type": "local", "command": ["node", "--flag"]}},
    })
    dest = tmp_path / "projects" / "gamma" / "opencode.json"
    _write_project_opencode_config(src_dir, 9999, "gamma", dest)
    written = json.loads(dest.read_text())
    assert written["mcp"]["tool"]["command"] == ["node", "--flag"]


def test_the_config_is_voiced_in_the_packs_words(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "brand.json").write_text(json.dumps({"productName": "Acme"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(tmp_path / "brand.json"))
    src_dir = _seed_source(tmp_path, {
        "agent": {"sage-chat": {"prompt": "You are Sage's chat agent."}},
    })
    dest = tmp_path / "projects" / "alpha" / "opencode.json"
    _write_project_opencode_config(src_dir, 9999, "alpha", dest)
    written = json.loads(dest.read_text())
    assert written["agent"]["sage-chat"]["prompt"] == "You are Acme's chat agent."


def test_an_unreadable_source_is_logged_and_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    src_dir = tmp_path / "no-such-repo"
    dest = tmp_path / "projects" / "alpha" / "opencode.json"
    _write_project_opencode_config(src_dir, 9999, "alpha", dest)  # must not raise
    assert not dest.exists()


def test_creates_the_destination_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    src_dir = _seed_source(tmp_path, {"model": "x"})
    dest = tmp_path / "projects" / "deeply" / "nested" / "opencode.json"
    _write_project_opencode_config(src_dir, 9999, "deeply", dest)
    assert dest.exists()
