"""Gateway provider-mode resolution (one place the orchestrator uses).

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
from typing import TYPE_CHECKING

from .client import FakeGatewayClient, GatewayClient, MultiProviderOpenAIClient, OpenAICompatibleClient
from .open_models import OPEN_WEIGHT_MODELS

if TYPE_CHECKING:
    from ..platform.auth import TokenSource


def resolve_mode(base_url: str | None = None) -> str:
    """`base_url` overrides the raw `GATEWAY_BASE_URL` env read — `orchestrator/app.py` passes
    `Settings.gateway_url()` there so a gateway configured only through Settings (no env vars at
    all, the laptop/Settings-UI story Phase 1 adds) still resolves to `domino` rather than always
    reading as `fake` because the env var it derived from was never set."""
    mode = os.environ.get("SAGE_GATEWAY_MODE", "auto").lower()
    if mode in {"domino", "openai", "fake"}:
        return mode
    # auto: openai has no base URL of its own to auto-detect, so auto only ever yields domino/fake.
    base = base_url if base_url is not None else os.environ.get("GATEWAY_BASE_URL", "")
    if not base:
        return "fake"
    key = os.environ.get("GATEWAY_API_KEY", "")
    if key.startswith("dgw_") or not key or "domino" in base:
        return "domino"
    return "fake"


def build_gateway(token_source: TokenSource | None = None,
                   gateway_api_key: str | None = None,
                   base_url: str | None = None) -> tuple[GatewayClient, str]:
    """Return (client, mode). Mode is also what /healthz reports.

    Every argument is optional so every existing bare `build_gateway()` call (shim/app.py,
    tools/probe.py, this module's own tests) keeps reading `GATEWAY_BASE_URL`/`GATEWAY_API_KEY`/
    the sidecar directly from the environment, unchanged — only `orchestrator/app.py` passes the
    process's shared `Settings`/`TokenSource` (§0: "one TokenSource ... feeds ... the LLM Gateway
    listing"), via `platform.auth.gateway_bearer`.
    """
    mode = resolve_mode(base_url)
    if mode == "fake":
        return FakeGatewayClient(), "fake"

    if mode == "openai":
        return MultiProviderOpenAIClient(OPEN_WEIGHT_MODELS), "openai"

    # domino
    from ..platform.auth import gateway_bearer

    base = base_url if base_url is not None else os.environ["GATEWAY_BASE_URL"]
    key = gateway_api_key if gateway_api_key is not None else os.environ.get("GATEWAY_API_KEY", "")
    token = gateway_bearer(key, token_source)
    return OpenAICompatibleClient(base, token, domino_tags=True), "domino"
