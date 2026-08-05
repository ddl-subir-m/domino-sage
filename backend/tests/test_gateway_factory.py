"""Gateway mode resolution + openai-mode multi-provider routing (no network)."""
from __future__ import annotations

import pytest

from sage.gateway.client import CostLabels, GatewayUpstreamError, MultiProviderOpenAIClient
from sage.gateway.factory import build_gateway, resolve_mode
from sage.gateway.open_models import OpenModel


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ["SAGE_GATEWAY_MODE", "GATEWAY_BASE_URL", "GATEWAY_API_KEY", "DEEPSEEK_API_KEY"]:
        monkeypatch.delenv(var, raising=False)


def test_resolve_mode_explicit_openai_needs_no_base_url(monkeypatch):
    monkeypatch.setenv("SAGE_GATEWAY_MODE", "openai")
    assert resolve_mode() == "openai"


def test_resolve_mode_auto_with_no_base_url_is_fake():
    assert resolve_mode() == "fake"


def test_resolve_mode_auto_domino_like_base_url(monkeypatch):
    monkeypatch.setenv("GATEWAY_BASE_URL", "https://gw.domino.tech/v1")
    assert resolve_mode() == "domino"


def test_build_gateway_openai_mode_returns_multi_provider_client(monkeypatch):
    monkeypatch.setenv("SAGE_GATEWAY_MODE", "openai")
    client, mode = build_gateway()
    assert mode == "openai"
    assert isinstance(client, MultiProviderOpenAIClient)


MODELS = [OpenModel("fake-model", "FakeVendor", "https://fake.example/v1", "FAKE_VENDOR_KEY")]


def test_multi_provider_client_unknown_model_raises():
    client = MultiProviderOpenAIClient(MODELS)
    with pytest.raises(GatewayUpstreamError, match="unknown open-weight model"):
        list(client.route({"model": "not-in-catalog"}, CostLabels(phase="plan", mode="auto")))


def test_multi_provider_client_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("FAKE_VENDOR_KEY", raising=False)
    client = MultiProviderOpenAIClient(MODELS)
    with pytest.raises(GatewayUpstreamError, match="FAKE_VENDOR_KEY not set"):
        list(client.route({"model": "fake-model"}, CostLabels(phase="plan", mode="auto")))


def test_domino_mode_emits_namespaced_sage_tag_headers(monkeypatch):
    # Guards the ingest contract: the gateway drops any tag key in RESERVED_TAG_KEYS
    # (project/model/user/org/...). All Sage tags must be `sage-`-namespaced to survive AND to be
    # isolable via tag:sage-source=domino-sage. This test locks the exact header wire format.
    import httpx

    from sage.gateway.client import OpenAICompatibleClient, static_token

    captured: dict[str, str] = {}

    class _Resp:
        status_code = 200
        is_redirect = False

        def iter_bytes(self):
            yield b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url, json=None, headers=None):
            captured.update(headers or {})
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)

    client = OpenAICompatibleClient("https://gw.example/v1", static_token("t"), domino_tags=True)
    labels = CostLabels(phase="implement", mode="sovereign", component="builder",
                        session="ses_1", version="abc123", project_name="sub_user/Sage")
    list(client.route({"model": "m", "messages": []}, labels))

    assert captured["X-LLM-Tag-sage-source"] == "domino-sage"
    assert captured["X-LLM-Tag-sage-phase"] == "implement"
    assert captured["X-LLM-Tag-sage-mode"] == "sovereign"
    assert captured["X-LLM-Tag-sage-component"] == "builder"
    assert captured["X-LLM-Tag-sage-session"] == "ses_1"
    assert captured["X-LLM-Tag-sage-version"] == "abc123"
    # What makes one deployment's spend findable in the gateway dashboard. Namespaced, because the
    # bare key is reserved: sent as `project` it would be dropped at ingest with no error.
    assert captured["X-LLM-Tag-sage-project"] == "sub_user/Sage"
    # The bare reserved keys must never be sent — they'd be silently dropped, hiding Sage's cost.
    assert not any(k.lower() in ("x-llm-tag-project", "x-llm-tag-model") for k in captured)
