"""Gateway provider-mode resolution (one place both apps use).

Modes:
  - fake    : no GATEWAY_BASE_URL and no explicit SAGE_GATEWAY_MODE -> in-process fake (offline).
  - domino  : the Domino LLM gateway. Token from a dgw_ PAT (GATEWAY_API_KEY) or the workspace
              sidecar; sends X-LLM-Tag-* for usage attribution.
  - openai  : a static catalog of open-weight models (DeepSeek/Qwen/Kimi, see open_models.py),
              each routed to its own vendor base_url using a per-vendor API key env var. Does
              NOT use GATEWAY_BASE_URL/GATEWAY_API_KEY - those are domino-only.

Selection: SAGE_GATEWAY_MODE = auto (default) | domino | openai | fake. auto only ever resolves
to domino or fake (a dgw_ key, missing key, or domino-looking base URL -> domino; otherwise
fake) - openai must be selected explicitly since it has no base URL of its own to detect.

NOTE: openai mode tests the *mechanism* (switching, override, build loop), NOT the sovereign
guarantee. /healthz surfaces the mode so a local green run is never mistaken for the real thing.
"""
from __future__ import annotations

import os

from .client import (
    DEFAULT_SIDECAR_URL,
    FakeGatewayClient,
    GatewayClient,
    MultiProviderOpenAIClient,
    OpenAICompatibleClient,
    sidecar_token,
    static_token,
)
from .open_models import OPEN_WEIGHT_MODELS


def resolve_mode() -> str:
    mode = os.environ.get("SAGE_GATEWAY_MODE", "auto").lower()
    if mode in {"domino", "openai", "fake"}:
        return mode
    # auto: openai has no base URL of its own to auto-detect, so auto only ever yields domino/fake.
    base = os.environ.get("GATEWAY_BASE_URL", "")
    if not base:
        return "fake"
    key = os.environ.get("GATEWAY_API_KEY", "")
    if key.startswith("dgw_") or not key or "domino" in base:
        return "domino"
    return "fake"


def build_gateway() -> tuple[GatewayClient, str]:
    """Return (client, mode). Mode is also what /healthz reports."""
    mode = resolve_mode()
    if mode == "fake":
        return FakeGatewayClient(), "fake"

    if mode == "openai":
        return MultiProviderOpenAIClient(OPEN_WEIGHT_MODELS), "openai"

    # domino
    base_url = os.environ["GATEWAY_BASE_URL"]
    key = os.environ.get("GATEWAY_API_KEY", "")
    token = static_token(key) if key else sidecar_token(os.environ.get("GATEWAY_TOKEN_URL", DEFAULT_SIDECAR_URL))
    return OpenAICompatibleClient(base_url, token, domino_tags=True), "domino"
