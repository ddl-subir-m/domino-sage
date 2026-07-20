"""Gateway provider-mode resolution (one place both apps use).

Modes:
  - fake    : no GATEWAY_BASE_URL -> in-process fake (offline).
  - domino  : the Domino LLM gateway. Token from a dgw_ PAT (GATEWAY_API_KEY) or the workspace
              sidecar; sends X-LLM-Tag-* for usage attribution.
  - openai  : a generic hosted OpenAI-compatible endpoint (local Mac E2E). Static Bearer key,
              no Domino tags.

Selection: SAGE_GATEWAY_MODE = auto (default) | domino | openai | fake. In auto, a dgw_ key,
a missing key (sidecar), or a domino-looking base URL -> domino; otherwise openai.

NOTE: openai mode tests the *mechanism* (switching, override, build loop), NOT the sovereign
guarantee. /healthz surfaces the mode so a local green run is never mistaken for the real thing.
"""
from __future__ import annotations

import os

from .client import (
    DEFAULT_SIDECAR_URL,
    FakeGatewayClient,
    GatewayClient,
    OpenAICompatibleClient,
    sidecar_token,
    static_token,
)


def resolve_mode() -> str:
    base = os.environ.get("GATEWAY_BASE_URL", "")
    if not base:
        return "fake"
    mode = os.environ.get("SAGE_GATEWAY_MODE", "auto").lower()
    if mode in {"domino", "openai", "fake"}:
        return mode
    # auto
    key = os.environ.get("GATEWAY_API_KEY", "")
    if key.startswith("dgw_") or not key or "domino" in base:
        return "domino"
    return "openai"


def build_gateway() -> tuple[GatewayClient, str]:
    """Return (client, mode). Mode is also what /healthz reports."""
    mode = resolve_mode()
    if mode == "fake":
        return FakeGatewayClient(), "fake"

    base_url = os.environ["GATEWAY_BASE_URL"]
    key = os.environ.get("GATEWAY_API_KEY", "")

    if mode == "domino":
        token = static_token(key) if key else sidecar_token(os.environ.get("GATEWAY_TOKEN_URL", DEFAULT_SIDECAR_URL))
        return OpenAICompatibleClient(base_url, token, domino_tags=True), "domino"

    # openai / generic
    if not key:
        raise RuntimeError("openai mode needs GATEWAY_API_KEY")
    return OpenAICompatibleClient(base_url, static_token(key), domino_tags=False), "openai"
