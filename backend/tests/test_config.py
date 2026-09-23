"""`sage.config`: one Settings object per process, `$SAGE_HOME/settings.json` with env overriding.
"""
from __future__ import annotations

import json

from sage.config import Settings, derive_gateway_url, load, resolve_sage_home, save


def test_sage_home_honors_an_explicit_override():
    assert resolve_sage_home({"SAGE_HOME": "/tmp/explicit-home"}) == __import__("pathlib").Path(
        "/tmp/explicit-home")


def test_sage_home_is_ephemeral_scratch_on_domino_with_no_override():
    assert resolve_sage_home({"DOMINO_API_HOST": "http://nucleus-frontend.domino-platform"}) == \
        __import__("pathlib").Path("/tmp/sage-home")


def test_sage_home_is_a_dotfile_off_domino():
    home = resolve_sage_home({})
    assert home.name == ".sage"


def test_gateway_url_is_derived_from_the_internal_host():
    assert derive_gateway_url("http://nucleus-frontend.domino-platform:80") == (
        "https://apps.nucleus-frontend.domino-platform/apps/llm_gateway/v1"
    )


def test_derive_gateway_url_does_not_double_up_an_existing_apps_prefix():
    assert derive_gateway_url("apps.acme.domino.tech") == (
        "https://apps.acme.domino.tech/apps/llm_gateway/v1"
    )


def test_a_missing_settings_file_answers_defaults():
    settings = load(__import__("pathlib").Path("/tmp/does-not-exist-sage-home-xyz"), env={})
    assert settings == Settings()


def test_settings_round_trip_through_save_and_load(tmp_path):
    settings = Settings(domino_host="https://d.example", domino_token="tok-1")
    save(tmp_path, settings)
    assert json.loads((tmp_path / "settings.json").read_text())["domino_host"] == "https://d.example"
    assert load(tmp_path, env={}) == settings


def test_env_vars_override_a_saved_file():
    """The platform's own injected facts win over whatever a stale settings.json says."""
    settings = load(__import__("pathlib").Path("/tmp/does-not-exist-sage-home-xyz"), env={
        "DOMINO_API_HOST": "https://d.example",
        "DOMINO_USER_API_KEY": "key-123",
        "GATEWAY_BASE_URL": "https://gw.example/v1",
        "GATEWAY_API_KEY": "dgw_abc",
        "DOMINO_ENVIRONMENT_ID": "env-1",
        "DOMINO_HARDWARE_TIER_ID": "tier-1",
    })
    assert settings.domino_host == "https://d.example"
    assert settings.domino_token == "key-123"
    assert settings.gateway_base_url == "https://gw.example/v1"
    assert settings.gateway_api_key == "dgw_abc"
    assert settings.publish_environment_id == "env-1"
    assert settings.publish_hardware_tier_id == "tier-1"


def test_env_override_beats_a_saved_file_value(tmp_path):
    save(tmp_path, Settings(domino_host="https://saved.example"))
    settings = load(tmp_path, env={"DOMINO_API_HOST": "https://env.example"})
    assert settings.domino_host == "https://env.example"


def test_gateway_url_prefers_the_explicit_setting_over_deriving_one():
    settings = Settings(domino_host="https://d.example", gateway_base_url="https://gw.explicit/v1")
    assert settings.gateway_url() == "https://gw.explicit/v1"


def test_gateway_url_derives_when_nothing_is_set_explicitly():
    settings = Settings(domino_host="http://nucleus-frontend.domino-platform")
    assert settings.gateway_url() == (
        "https://apps.nucleus-frontend.domino-platform/apps/llm_gateway/v1"
    )


def test_gateway_url_is_empty_with_no_host_and_no_explicit_url():
    assert Settings().gateway_url() == ""


def test_redacted_hides_secrets_but_keeps_them_booleans():
    settings = Settings(domino_host="https://d.example", domino_token="secret",
                         gateway_api_key="dgw_secret")
    d = settings.redacted()
    assert d["domino_host"] == "https://d.example"
    assert d["domino_token"] is True
    assert d["gateway_api_key"] is True
    assert d["git_token"] is False
    assert "secret" not in json.dumps(d)


def test_an_unreadable_settings_file_is_treated_as_empty_not_an_error(tmp_path):
    (tmp_path / "settings.json").write_text("not json{{{")
    assert load(tmp_path, env={}) == Settings()
