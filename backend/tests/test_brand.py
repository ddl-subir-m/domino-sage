"""Brand pack: Domino defaults, OEM overlay, voice substitution."""
from __future__ import annotations

import json
import logging
import re
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
    # The Title Case complaint is made once per process; each test is its own first time.
    monkeypatch.setattr("sage.orchestrator.brand._WARNED", set())


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
        "logoUrl": "./brand/acme-logo.svg",
        "logoAlt": "Acme",
        "colors": {"primary": "#112233"},
    }))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    pack = load()
    assert pack["productName"] == "Acme"
    assert pack["assistantName"] == "Acme"
    assert pack["pageTitle"] == "Acme Studio"
    assert pack["logoUrl"] == "./brand/acme-logo.svg"
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


def test_the_chat_template_carries_tokens_rather_than_names():
    """The Chat template is a prompt a person's answers come out of, so it re-brands like the Built
    App's (#114). Before #124 it hard-coded `Dataset` and `Data Source`, and under a partner's pack
    Chat said "Dataset" while the Resource panel beside it said the partner's word."""
    src = (Path(__file__).resolve().parents[2] / "template" / "chat" / "AGENTS.md").read_text()
    assert "{assistantName}" in src
    assert "{dataset}" in src and "{dataSource}" in src and "{dataSourcePlural}" in src
    assert re.search(r"\bSage\b", src) is None, "a bare product name survives in the template"


def test_the_chat_template_names_the_default_nouns_only_as_synonyms():
    """The one place a DEFAULT noun stays literal: the user types "dataset" whatever the pack says,
    and the agent has to recognise it to answer about the right thing."""
    src = (Path(__file__).resolve().parents[2] / "template" / "chat" / "AGENTS.md").read_text()
    assert "Recognise **Dataset** and **Data Source**" in src


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
    # The rules live in the sage-chat prompt, not here; this file is the stub that keeps an
    # AGENTS.md from a parent directory out of a Thread. It still speaks the pack's name.
    written = orch._chat_agents_md()
    assert "Acme's chat agent" in written
    assert "Sage" not in written
    # Proof it is a stub and not the body again: the rules are ~1,700 tokens.
    assert len(written) < 400


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


# --- platformName (#103) ---------------------------------------------------------------------
#
# One key for both parts the word plays — the platform as actor and the platform as destination —
# because they are one fact: is the platform rebranded? (ADR-0014)


def test_platform_name_defaults_to_domino():
    assert load()["platformName"] == "Domino"
    assert text("{platformName}") == "Domino"


def test_the_pack_renames_the_platform(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"platformName": "Acme Cloud"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    assert load()["platformName"] == "Acme Cloud"
    assert text("{platformName}") == "Acme Cloud"


def test_platform_name_does_not_follow_the_product_name(tmp_path, monkeypatch):
    """The fallback for a missing key is the built-in default, never another key a partner can
    edit. Renaming Sage is not evidence that the platform under it was renamed too, and a chain
    would put one fact in two places."""
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"productName": "Acme", "assistantName": "Ada"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    assert load()["platformName"] == "Domino"


def test_api_brand_carries_the_platform_name(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"platformName": "Acme Cloud"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    import sage.orchestrator.app as appmod

    r = TestClient(appmod.control_app).get("/api/brand")
    assert r.json()["platformName"] == "Acme Cloud"


def test_the_workbench_reads_the_platform_name():
    answers = _brand_js(
        [{"op": "platform"}, {"op": "text", "template": "Browse {platformName}…"}],
        pack={"platformName": "Acme Cloud"},
    )
    assert answers == ["Acme Cloud", "Browse Acme Cloud…"]


def test_an_actor_string_names_the_packs_platform(tmp_path, monkeypatch):
    """The platform did something, and the sentence saying so carries an API path in the same
    breath. The name moves; the path is a literal and does not."""
    import httpx

    from sage.assets.provider import DominoAssetProvider
    from sage.resources.provider import ResourceUnavailable

    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"platformName": "Acme Cloud"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(503))

    provider = DominoAssetProvider("https://acme.example", lambda: "tok", mount_roots=[])
    with pytest.raises(ResourceUnavailable) as e:
        provider.list_datasets(None)
    assert str(e.value) == "The Acme Cloud API answered 503 at /api/datasetrw/v2/datasets."


def test_a_destination_string_names_the_packs_platform(tmp_path, monkeypatch):
    """Copy that sends a person to a page Sage does not own has to say the partner's word, or it
    is a dead end. The git host in the same sentence is a literal and stays."""
    from sage.provision.domino import FakeControlPlane
    from sage.provision.github import FakeRepoProvider
    from sage.provision.service import ProvisionService

    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"platformName": "Acme Cloud"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))

    # No credential the create loop could use, so the sentence that sends the person to the
    # platform's own settings page is the one they read (ADR-0033 moved this text to the caller).
    svc = ProvisionService(
        FakeControlPlane(credentials=[]), FakeRepoProvider(), tmp_path / "work",
        seed=lambda *a, **k: None,
    )
    with pytest.raises(RuntimeError) as e:
        svc.create_app("X")
    assert "in your Acme Cloud account" in str(e.value)
    assert "github.com" in str(e.value)      # the literal in the same sentence
    assert "Domino" not in str(e.value)


# --- nouns (#104) -----------------------------------------------------------------------------
#
# The platform's own whitelabel renames its nouns and nothing exposes that vocabulary to a Sage
# Builder, so the pack carries a copy. Two forms per noun because the nouns are woven into
# sentences rather than confined to labels — hence no pluralisation engine, and no article engine
# either: copy needing `a`/`an` is reworded (ADR-0014).


def test_nouns_default_to_the_glossary_terms():
    nouns = load()["nouns"]
    assert nouns["dataset"] == {"singular": "Dataset", "plural": "Datasets"}
    assert nouns["dataSource"] == {"singular": "Data Source", "plural": "Data Sources"}
    assert nouns["builtApp"] == {"singular": "Built App", "plural": "Built Apps"}


def test_both_forms_of_a_noun_resolve_through_the_helper(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"nouns": {"dataset": {"singular": "Cube", "plural": "Cubes"}}}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    assert text("{dataset}") == "Cube"
    assert text("{datasetPlural}") == "Cubes"


def test_a_noun_woven_into_a_sentence_renders_in_both_forms(tmp_path, monkeypatch):
    """The strings this covers are the ones a person actually reads — the Resource Browser's
    heading and the empty state under a Dataset — not a bare label in isolation."""
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"nouns": {"dataset": {"singular": "Cube", "plural": "Cubes"}}}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    assert text("No files in this {dataset}.") == "No files in this Cube."
    assert text("{dataset} contents live under the {dataset}.") == (
        "Cube contents live under the Cube."
    )
    assert text("{datasetPlural}") == "Cubes"


def test_one_form_of_a_noun_leaves_the_other_at_its_default(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"nouns": {"dataset": {"plural": "Cubes"}}}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    assert text("{dataset}") == "Dataset"
    assert text("{datasetPlural}") == "Cubes"


def test_a_noun_the_pack_invents_is_ignored(tmp_path, monkeypatch):
    """A token Sage never emits is not a rename, and inventing one cannot make Sage say it."""
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"nouns": {"widget": {"singular": "Widget", "plural": "Widgets"}}}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    assert "widget" not in load()["nouns"]
    assert text("{widget}") == "{widget}"


def test_api_brand_carries_the_nouns(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"nouns": {"dataset": {"singular": "Cube", "plural": "Cubes"}}}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    import sage.orchestrator.app as appmod

    body = TestClient(appmod.control_app).get("/api/brand").json()
    assert body["nouns"]["dataset"] == {"singular": "Cube", "plural": "Cubes"}
    assert body["nouns"]["dataSource"]["plural"] == "Data Sources"


def test_a_noun_that_is_not_title_case_warns_and_is_used_anyway(tmp_path, monkeypatch, caplog):
    """`"No files in this xyz_dataset."` reads as a leaked code identifier rather than a product
    term. It is still used: a brand pack must never be able to stop the product booting."""
    path = tmp_path / "brand.json"
    path.write_text(json.dumps(
        {"nouns": {"dataset": {"singular": "xyz_dataset", "plural": "datasets"}}}
    ))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    with caplog.at_level(logging.WARNING, logger="sage.orchestrator.brand"):
        pack = load()
    assert pack["nouns"]["dataset"] == {"singular": "xyz_dataset", "plural": "datasets"}
    assert "xyz_dataset" in caplog.text          # the underscore
    assert "datasets" in caplog.text             # the lowercase start
    assert text("No files in this {dataset}.") == "No files in this xyz_dataset."


def test_the_title_case_warning_does_not_repeat_on_every_read(tmp_path, monkeypatch, caplog):
    """`load()` runs per request. One line per bad value, or a pack nobody is going to change
    fills the log."""
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"nouns": {"dataset": {"singular": "xyz_dataset"}}}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    with caplog.at_level(logging.WARNING, logger="sage.orchestrator.brand"):
        load()
        caplog.clear()
        load()
    assert caplog.text == ""


def test_the_workbench_weaves_a_renamed_noun_into_a_sentence():
    """The same two strings, through the accessor the Workbench actually calls."""
    answers = _brand_js(
        [{"op": "text", "template": "No files in this {dataset}."},
         {"op": "text", "template": "{datasetPlural}"}],
        pack={"nouns": {"dataset": {"singular": "Cube", "plural": "Cubes"}}},
    )
    assert answers == ["No files in this Cube.", "Cubes"]


def test_the_workbench_nouns_fall_back_before_the_pack_arrives():
    answers = _brand_js([{"op": "text", "template": "No files in this {dataset}."},
                         {"op": "text", "template": "{datasetPlural}"}])
    assert answers == ["No files in this Dataset.", "Datasets"]


# --- unknown keys (#118) ---------------------------------------------------------------------
#
# The pack has no `version` field and never will (ADR-0014). Forward compatibility is instead:
# every key optional with a documented default, unknown keys ignored but named in the log. So the
# log line IS the migration story, and a partner's typo is findable only through it.


def test_an_unknown_key_is_named_in_the_log_and_ignored(tmp_path, monkeypatch, caplog):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"prodcutName": "Acme", "productName": "Acme"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    with caplog.at_level(logging.WARNING, logger="sage.orchestrator.brand"):
        pack = load()
    assert "prodcutName" in caplog.text
    # Boots anyway: the typo is reported, the rest of the pack still applies, and nothing raises.
    assert pack["productName"] == "Acme"
    assert "prodcutName" not in pack


def test_a_recognised_key_logs_nothing(tmp_path, monkeypatch, caplog):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({
        "productName": "Acme", "assistantName": "Ada", "platformName": "Acme Cloud",
        "pageTitle": "Acme Studio", "logoUrl": "./img/acme.svg", "logoAlt": "Acme",
        "nouns": {"dataset": {"singular": "Cube", "plural": "Cubes"}},
        "colors": {"primary": "#112233"},
    }))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    with caplog.at_level(logging.WARNING, logger="sage.orchestrator.brand"):
        load()
    assert caplog.text == ""


def test_a_mistyped_noun_is_named_in_the_log_too(tmp_path, monkeypatch, caplog):
    """`nouns` is where a partner is most likely to typo, and the merge drops an unknown noun as
    silently as it drops an unknown top-level key (#124). A walk that stopped at the top left that
    one place unreported, which is the opposite of what the log line is for."""
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"nouns": {
        "datasets": {"singular": "Cube", "plural": "Cubes"},      # the typo
        "dataset": {"singular": "Cube", "plural": "Cubes"},
    }}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    with caplog.at_level(logging.WARNING, logger="sage.orchestrator.brand"):
        pack = load()
    assert "nouns.datasets" in caplog.text
    # Ignored, not refused, and the noun spelled correctly beside it still lands.
    assert "datasets" not in pack["nouns"]
    assert pack["nouns"]["dataset"] == {"singular": "Cube", "plural": "Cubes"}


def test_a_recognised_noun_logs_nothing(tmp_path, monkeypatch, caplog):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"nouns": {"dataSource": {"singular": "Warehouse"}}}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    with caplog.at_level(logging.WARNING, logger="sage.orchestrator.brand"):
        load()
    assert caplog.text == ""


def test_the_unknown_key_warning_does_not_repeat_on_every_read(tmp_path, monkeypatch, caplog):
    """`load()` runs per request. The complaint belongs to the pack the process booted with, so it
    is made once rather than on every call the Workbench makes."""
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"prodcutName": "Acme"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    with caplog.at_level(logging.WARNING, logger="sage.orchestrator.brand"):
        load()
        assert "prodcutName" in caplog.text
        caplog.clear()
        load()
        load()
    assert caplog.text == ""


def test_the_pack_has_no_version_field(tmp_path, monkeypatch, caplog):
    """`version` is not a key Sage knows, so it is reported like any other unknown one rather than
    quietly accepted — a pack that looks versioned would imply a migration story we do not have."""
    assert "version" not in DEFAULT
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"version": 2, "productName": "Acme"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    with caplog.at_level(logging.WARNING, logger="sage.orchestrator.brand"):
        pack = load()
    assert "version" in caplog.text
    assert "version" not in pack


def test_api_brand_still_answers_with_an_unknown_key_in_the_pack(tmp_path, monkeypatch):
    """A brand pack must never be able to stop the product booting."""
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"prodcutName": "Acme", "productName": "Acme"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    import sage.orchestrator.app as appmod

    r = TestClient(appmod.control_app).get("/api/brand")
    assert r.status_code == 200
    assert r.json()["productName"] == "Acme"


# --- peerProducts (#115) ---------------------------------------------------------------------
#
# A list, not a name. A switcher with one item is not a switcher — it offers a choice that does not
# exist — so a partner with no second product sets `[]` and the control collapses to a label.


def _switcher_js(pack: dict | None = None, click: str | None = None) -> dict:
    """Ask the shell's top bar what it drew for the product, given the pack /api/brand returned."""
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH (it is in the Sage image)")
    harness = Path(__file__).resolve().parent / "js" / "product_switcher_harness.mjs"
    out = subprocess.run(
        ["node", str(harness)],
        input=json.dumps({"pack": pack, "click": click}),
        check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_peer_products_defaults_to_the_domino_list():
    assert load()["peerProducts"] == [{"key": "studio", "label": "ML Studio"}]


def test_an_omitted_peer_products_key_keeps_the_default(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"productName": "Acme"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    assert load()["peerProducts"] == DEFAULT["peerProducts"]


def test_the_pack_replaces_the_peer_list(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"peerProducts": [
        {"key": "vision", "label": "Acme Vision"},
        {"key": "forge", "label": "Acme Forge"},
    ]}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    assert load()["peerProducts"] == [
        {"key": "vision", "label": "Acme Vision"},
        {"key": "forge", "label": "Acme Forge"},
    ]


def test_an_empty_peer_list_is_honoured_rather_than_read_as_unset(tmp_path, monkeypatch):
    """`[]` is the whole reason the key is a list: a partner saying there is nowhere else to go.
    Falling back to the default here would make that unsayable."""
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"peerProducts": []}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    assert load()["peerProducts"] == []


def test_a_peer_missing_a_key_or_a_label_is_dropped(tmp_path, monkeypatch):
    """A half-written entry would draw a menu row with nothing on it. Dropped, never fatal —
    a brand pack must not be able to stop the product booting."""
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"peerProducts": [
        {"key": "vision", "label": "Acme Vision"},
        {"key": "forge"},
        {"label": "Acme Anvil"},
        "studio",
    ]}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    assert load()["peerProducts"] == [{"key": "vision", "label": "Acme Vision"}]


def test_api_brand_carries_the_peer_products(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"peerProducts": [{"key": "vision", "label": "Acme Vision"}]}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    import sage.orchestrator.app as appmod

    r = TestClient(appmod.control_app).get("/api/brand")
    assert r.status_code == 200
    assert r.json()["peerProducts"] == [{"key": "vision", "label": "Acme Vision"}]


def test_the_shell_draws_the_switcher_from_the_pack():
    """The partner's own products, and nothing of ours left in the menu."""
    drawn = _switcher_js(pack={
        "productName": "Acme",
        "peerProducts": [{"key": "vision", "label": "Acme Vision"}],
    })
    assert drawn["switcher"] is True
    assert drawn["items"] == ["Acme", "Acme Vision"]


def test_an_empty_peer_list_collapses_the_switcher():
    """No switcher, no disabled control needing an explanation — a plain label with nothing to
    click."""
    drawn = _switcher_js(pack={"peerProducts": []})
    assert drawn["switcher"] is False
    assert drawn["control"] == "span"
    assert drawn["label"] == "AI Workbench"


def test_the_shell_offers_no_switcher_before_the_pack_arrives():
    """The shell paints before GET /api/brand answers. Erring towards the label is the safe half:
    it can only under-offer, never offer a product the partner does not have."""
    drawn = _switcher_js()
    assert drawn["switcher"] is False
    assert drawn["control"] == "span"


def test_choosing_a_peer_names_it_in_the_packs_words():
    drawn = _switcher_js(
        pack={
            "productName": "Acme", "platformName": "Acme Cloud",
            "peerProducts": [{"key": "vision", "label": "Acme Vision"}],
        },
        click="vision",
    )
    assert drawn["said"] == [
        "Acme Vision is another Acme Cloud product. Only Acme is built out here."
    ]
