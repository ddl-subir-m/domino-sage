"""Brand pack: Domino defaults, OEM overlay, voice substitution."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.orchestrator.brand import DEFAULT, apply_agent_voice, apply_voice, load, text
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog


@pytest.fixture(autouse=True)
def _isolate_brand(monkeypatch, tmp_path):
    monkeypatch.setattr("sage.orchestrator.brand._BAKED", tmp_path / "no-baked-brand.json")
    monkeypatch.delenv("SAGE_BRAND_FILE", raising=False)


def test_default_pack_keeps_the_workbench_sage_split():
    pack = load()
    assert pack["productName"] == "AI Workbench"
    assert pack["assistantName"] == "Sage"
    assert pack["pageTitle"] == "Sage Workspace"
    assert pack["logoAlt"] == "Domino"
    assert pack["colors"] == DEFAULT["colors"]


def test_brand_file_overrides_and_omitted_assistant_follows_product(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({
        "productName": "Acme",
        "pageTitle": "Acme Studio",
        "logoUrl": "https://acme.example/logo.svg",
        "logoAlt": "Acme",
        "colors": {"primary": "#112233"},
    }))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    pack = load()
    assert pack["productName"] == "Acme"
    assert pack["assistantName"] == "Acme"
    assert pack["pageTitle"] == "Acme Studio"
    assert pack["logoUrl"] == "https://acme.example/logo.svg"
    assert pack["colors"]["primary"] == "#112233"
    assert pack["colors"]["primaryDark"] == DEFAULT["colors"]["primaryDark"]


def test_explicit_assistant_name_keeps_a_split(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"productName": "Acme", "assistantName": "Ada"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    pack = load()
    assert pack["productName"] == "Acme"
    assert pack["assistantName"] == "Ada"


def test_colors_only_keeps_the_name_split(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"colors": {"primary": "#010203"}}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    pack = load()
    assert pack["productName"] == "AI Workbench"
    assert pack["assistantName"] == "Sage"
    assert pack["colors"]["primary"] == "#010203"


def test_env_file_wins_over_baked(tmp_path, monkeypatch):
    baked = tmp_path / "baked.json"
    baked.write_text(json.dumps({"productName": "Baked"}))
    extra = tmp_path / "extra.json"
    extra.write_text(json.dumps({"productName": "Env"}))
    monkeypatch.setattr("sage.orchestrator.brand._BAKED", baked)
    monkeypatch.setenv("SAGE_BRAND_FILE", str(extra))
    assert load()["productName"] == "Env"


def test_unreadable_brand_file_keeps_defaults(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text("{not json")
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    assert load()["assistantName"] == "Sage"


def test_missing_brand_file_keeps_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGE_BRAND_FILE", str(tmp_path / "nope.json"))
    assert load()["assistantName"] == "Sage"


def test_invalid_colors_are_ignored(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"colors": {"primary": "purple", "primaryDark": "#abc"}}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    pack = load()
    assert pack["colors"]["primary"] == DEFAULT["colors"]["primary"]
    assert pack["colors"]["primaryDark"] == "#abc"


def test_apply_voice_leaves_sage_prompts_alone():
    text = "You are Sage's chat agent."
    assert apply_voice(text, "Sage") == text


def test_apply_voice_rewrites_the_speaker():
    assert apply_voice("You are Sage's chat agent. Sage writes files.", "Acme") == (
        "You are Acme's chat agent. Acme writes files."
    )


def test_apply_agent_voice_does_not_touch_provider_keys():
    cfg = {
        "provider": {"sage-gateway": {"name": "Sage Enforcement Shim"}},
        "agent": {
            "sage-chat": {"prompt": "You are Sage's chat agent."},
            "sage-ask": {"prompt": "You are Sage's answering agent."},
        },
    }
    apply_agent_voice(cfg, "Acme")
    assert cfg["provider"]["sage-gateway"]["name"] == "Sage Enforcement Shim"
    assert cfg["agent"]["sage-chat"]["prompt"] == "You are Acme's chat agent."
    assert "sage-chat" in cfg["agent"]


def test_api_brand_returns_the_resolved_pack(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"productName": "Acme", "assistantName": "Ada"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    import sage.orchestrator.app as appmod

    r = TestClient(appmod.control_app).get("/api/brand")
    assert r.status_code == 200
    body = r.json()
    assert body["productName"] == "Acme"
    assert body["assistantName"] == "Ada"
    assert body["colors"]["primary"] == DEFAULT["colors"]["primary"]


def test_chat_agents_md_uses_assistant_name(tmp_path, monkeypatch):
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    agents = tmp_path / "chat" / "AGENTS.md"
    agents.parent.mkdir()
    agents.write_text("You are Sage's chat agent.\n")
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"productName": "Acme"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    orch = Orchestrator(
        workspace_dir=tmp_path / "ws",
        template=template,
        gateway=object(),
        catalog=ModelCatalog(
            sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
            plan="p", implement="i", ask="a",
        ),
        project_id="Sage",
    )
    assert orch._chat_agents_md() == "You are Acme's chat agent.\n"


def test_install_opencode_config_voices_the_global_copy_only(tmp_path, monkeypatch):
    from sage.orchestrator.app import _install_opencode_config

    src_dir = tmp_path / "repo"
    src_dir.mkdir()
    cfg = {
        "model": "x",
        "provider": {
            "sage-gateway": {
                "name": "Sage Enforcement Shim",
                "options": {"baseURL": "http://127.0.0.1:8080/v1"},
            }
        },
        "agent": {"sage-chat": {"prompt": "You are Sage's chat agent."}},
    }
    (src_dir / "opencode.json").write_text(json.dumps(cfg))
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    (tmp_path / "brand.json").write_text(json.dumps({"productName": "Acme"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(tmp_path / "brand.json"))
    _install_opencode_config(src_dir, 9999)
    src = json.loads((src_dir / "opencode.json").read_text())
    assert "Sage's" in src["agent"]["sage-chat"]["prompt"]
    assert ":9999" in src["provider"]["sage-gateway"]["options"]["baseURL"]
    global_cfg = json.loads((home / ".config" / "opencode" / "opencode.json").read_text())
    assert global_cfg["agent"]["sage-chat"]["prompt"] == "You are Acme's chat agent."
    assert global_cfg["provider"]["sage-gateway"]["name"] == "Sage Enforcement Shim"
    assert ":9999" in global_cfg["provider"]["sage-gateway"]["options"]["baseURL"]


# --- The author-time substitution helper (#102) ---------------------------------------------
#
# Every user-visible string is a template resolved when it is read, so a new string is branded
# because whoever wrote it wrote it that way (ADR-0014). These tests pin the helper's contract;
# no call site changes in this ticket.


def test_a_token_resolves_to_the_packs_value(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"productName": "Acme", "assistantName": "Ada"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    assert text("Ask {assistantName} in {productName}.") == "Ask Ada in Acme."


def test_a_token_resolves_to_the_domino_default_with_no_pack():
    assert text("Ask {assistantName} in {productName}.") == "Ask Sage in AI Workbench."


def test_an_unknown_token_is_left_alone():
    """A typo in a string must never stop the Workbench booting, and a passed-through error body
    can carry braces of its own."""
    assert text("{notABrandKey} said {\"ok\": 1}") == "{notABrandKey} said {\"ok\": 1}"


def test_a_string_without_a_token_comes_back_unchanged():
    assert text("Nothing to substitute here.") == "Nothing to substitute here."


def test_values_fill_the_rest_of_the_sentence(tmp_path, monkeypatch):
    """The whole sentence stays one literal, so the lint over marked positions can read it."""
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"productName": "Acme"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    assert text("{productName} answered {code}.", code=500) == "Acme answered 500."


def test_a_value_carrying_braces_is_not_resolved_again():
    """A Resource the user named after us arrives as a value, not as a template."""
    assert text("Missing {name}.", name="{productName}") == "Missing {productName}."


# --- The Workbench half of the same helper (#102) --------------------------------------------


def _brand_js(calls: list[dict], pack: dict | None = None) -> list:
    """Run SW.brand against a pack the way /api/brand delivers one."""
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH (it is in the Sage image)")
    harness = Path(__file__).resolve().parent / "js" / "brand_harness.mjs"
    out = subprocess.run(
        ["node", str(harness)],
        input=json.dumps({"pack": pack, "calls": calls}),
        check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_workbench_resolves_the_same_tokens_from_the_pack():
    """The accessor reads the pack GET /api/brand already returns, so both halves of the wire
    answer with one word."""
    answers = _brand_js(
        [{"op": "text", "template": "Ask {assistantName} in {productName}."}],
        pack={"productName": "Acme", "assistantName": "Ada"},
    )
    assert answers == ["Ask Ada in Acme."]


def test_the_workbench_falls_back_before_the_pack_arrives():
    """The shell paints before /api/brand answers. What it paints has to be the Domino default,
    not an unresolved token."""
    answers = _brand_js([{"op": "text", "template": "Ask {assistantName} in {productName}."}])
    assert answers == ["Ask Sage in AI Workbench."]


def test_the_workbench_leaves_an_unknown_token_alone():
    answers = _brand_js([{"op": "text", "template": "{notABrandKey} stays."}],
                        pack={"productName": "Acme"})
    assert answers == ["{notABrandKey} stays."]
