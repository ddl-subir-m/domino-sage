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
        list(client.route({"model": "not-in-catalog"}, CostLabels(project="p", phase="plan", model="x")))


def test_multi_provider_client_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("FAKE_VENDOR_KEY", raising=False)
    client = MultiProviderOpenAIClient(MODELS)
    with pytest.raises(GatewayUpstreamError, match="FAKE_VENDOR_KEY not set"):
        list(client.route({"model": "fake-model"}, CostLabels(project="p", phase="plan", model="fake-model")))
