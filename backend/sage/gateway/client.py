"""GatewayClient — the upstream adapter at the enforcement seam (DESIGN.md Seam 2).

The Domino AI Gateway already exists and is OpenAI-compatible, so route() is a thin OpenAI
client pointed at the gateway URL. costs()/guardrail_events() are the unconfirmed surface
(gateway-questions.md Q1/Q4) and stay behind this adapter so the fake covers them until Step 2.3.

Two adapters make the seam real:
  - FakeGatewayClient   — deterministic, for tests (no network)
  - DominoGatewayClient — real; fill in once we have the gateway URL + auth (Q7)
"""
from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from .open_models import OpenModel

# Domino workspace sidecar that mints a short-lived access token.
DEFAULT_SIDECAR_URL = "http://localhost:8899/access-token"


def static_token(token: str) -> Callable[[], str]:
    """Token provider for a long-lived dgw_ PAT (off-Domino / laptop use)."""
    return lambda: token


def sidecar_token(url: str = DEFAULT_SIDECAR_URL) -> Callable[[], str]:
    """Token provider that fetches a fresh short-lived token from the Domino sidecar.

    Only reachable inside a Domino workspace/job. Fetched per request because the
    token is short-lived (README: workspace JWTs expire quickly).
    """
    import urllib.request

    def _fetch() -> str:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - fixed localhost URL
            tok = resp.read().decode().strip()
        return tok[len("Bearer ") :] if tok.startswith("Bearer ") else tok  # avoid double-Bearer

    return _fetch


@dataclass(frozen=True)
class CostLabels:
    """Sage's cost-attribution tags, sent to the Domino gateway as X-LLM-Tag-sage-* headers and
    stored on each UsageLog row (queryable via group_by=tag:sage-*).

    Only Sage-specific dimensions live here. The gateway already captures project, model, user and
    org as first-class columns from the caller's Domino context, so tagging those is both redundant
    AND silently dropped — they're in the gateway's RESERVED_TAG_KEYS (services/tags.py). Everything
    below is namespaced `sage-` so all Sage traffic is one filter (tag:sage-source=domino-sage) on
    the shared gateway, and so no key collides with that reserved set.
    """

    phase: str                  # plan | implement | ask   — build phase
    mode: str                   # auto | ask | sovereign   — routing mode (sovereign = asset lock)
    component: str = "builder"  # builder | probe          — which Sage process made the call
    session: str | None = None  # OpenCode session id      — per-build cost rollup
    version: str | None = None  # Sage git rev             — cost/quality across Sage releases


@dataclass
class CostRecord:
    model: str
    user: str | None
    tokens: int
    cost_usd: float
    latency_ms: int
    status: int


@dataclass
class GuardrailEvent:
    detected: str
    asset: str | None
    blocked: bool  # True = blocked before the model; False = post-hoc detection
    severity: str


class GatewayClient(Protocol):
    def route(self, request: dict[str, Any], labels: CostLabels) -> Iterator[bytes]:
        """Forward an OpenAI-compatible request to the gateway; stream the response back."""
        ...

    def costs(self, window: str) -> list[CostRecord]:
        """Per-request cost/usage. Surface TBD (Q1) — may be API or UI-only."""
        ...

    def guardrail_events(self) -> Iterator[GuardrailEvent]:
        """Guardrail/leak events. Surface TBD (Q4) — may not be exposed to callers."""
        ...


@dataclass
class FakeGatewayClient:
    """Deterministic double for integration/E2E tests. No network."""

    scripted_costs: list[CostRecord] = field(default_factory=list)
    scripted_events: list[GuardrailEvent] = field(default_factory=list)
    seen: list[tuple[dict[str, Any], CostLabels]] = field(default_factory=list)

    def route(self, request: dict[str, Any], labels: CostLabels) -> Iterator[bytes]:
        self.seen.append((request, labels))
        # Echo the (possibly overridden) model so tests can assert the shim set it.
        yield f'{{"model": "{request.get("model")}", "phase": "{labels.phase}"}}'.encode()

    def costs(self, window: str) -> list[CostRecord]:
        return list(self.scripted_costs)

    def guardrail_events(self) -> Iterator[GuardrailEvent]:
        yield from self.scripted_events


class OpenAICompatibleClient:
    """Client for any OpenAI-compatible endpoint behind a Bearer token.

    Two modes, one code path:
      - domino_tags=True  -> the Domino LLM gateway: also sends X-LLM-Tag-* (usage attribution).
      - domino_tags=False -> a generic hosted OpenAI-compatible endpoint (local Mac E2E), no
        Domino-specific headers.
    """

    def __init__(
        self,
        base_url: str,
        token_provider: Callable[[], str],
        *,
        domino_tags: bool = False,
        timeout_s: float = 60.0,
    ) -> None:
        # base_url is the OpenAI base ending in /v1, e.g.
        #   https://apps.cloud-dogfood.domino.tech/apps/llm_gateway/v1  (domino)
        #   https://api.some-host.com/v1                                 (generic)
        self._base_url = base_url.rstrip("/")
        self._token_provider = token_provider
        self._domino_tags = domino_tags
        self._timeout_s = timeout_s

    def route(self, request: dict[str, Any], labels: CostLabels) -> Iterator[bytes]:
        import httpx  # local import so tests that never hit the network don't need it

        headers = {"Authorization": f"Bearer {self._token_provider()}"}
        if self._domino_tags:
            # Stored in the gateway's usage `tags` column, queryable as group_by=tag:sage-*.
            # Namespaced `sage-` to (a) isolate Sage traffic on the shared gateway and (b) dodge
            # the gateway's RESERVED_TAG_KEYS — project/model/user/org are captured first-class
            # there, so bare `project`/`model` tags would be silently dropped at ingest.
            headers |= {
                "X-LLM-Tag-sage-source": "domino-sage",
                "X-LLM-Tag-sage-phase": labels.phase,
                "X-LLM-Tag-sage-mode": labels.mode,
                "X-LLM-Tag-sage-component": labels.component,
            }
            if labels.session:
                headers["X-LLM-Tag-sage-session"] = labels.session
            if labels.version:
                headers["X-LLM-Tag-sage-version"] = labels.version
        url = f"{self._base_url}/chat/completions"  # base already ends in /v1
        with httpx.Client(timeout=self._timeout_s, follow_redirects=False) as client:
            with client.stream("POST", url, json=request, headers=headers) as resp:
                # Surface upstream errors BEFORE streaming so the caller gets a clean message
                # instead of a mid-stream reset. A 3xx here means auth bounced to a login page.
                if resp.status_code >= 400 or resp.is_redirect:
                    body = resp.read().decode(errors="replace")[:800]
                    raise GatewayUpstreamError(resp.status_code, url, body)
                yield from resp.iter_bytes()


class MultiProviderOpenAIClient:
    """openai gateway mode: each model routes to its own vendor base_url/key.

    Unlike OpenAICompatibleClient (one fixed base_url), there's no shared gateway here — the
    catalog entry matching request["model"] decides which vendor endpoint and API key to use.
    """

    def __init__(self, models: list[OpenModel], *, timeout_s: float = 60.0) -> None:
        self._by_id = {m.id: m for m in models}
        self._timeout_s = timeout_s

    def route(self, request: dict[str, Any], labels: CostLabels) -> Iterator[bytes]:
        import httpx  # local import so tests that never hit the network don't need it

        model_id = request.get("model")
        model = self._by_id.get(model_id)
        if model is None:
            known = ", ".join(sorted(self._by_id))
            raise GatewayUpstreamError(400, "", f"unknown open-weight model {model_id!r}; known: {known}")

        key = os.environ.get(model.api_key_env, "")
        if not key:
            raise GatewayUpstreamError(400, "", f"{model.api_key_env} not set for model {model_id!r}")

        headers = {"Authorization": f"Bearer {key}"}
        url = f"{model.base_url.rstrip('/')}/chat/completions"
        with httpx.Client(timeout=self._timeout_s, follow_redirects=False) as client:
            with client.stream("POST", url, json=request, headers=headers) as resp:
                if resp.status_code >= 400 or resp.is_redirect:
                    body = resp.read().decode(errors="replace")[:800]
                    raise GatewayUpstreamError(resp.status_code, url, body)
                yield from resp.iter_bytes()

    def costs(self, window: str) -> list[CostRecord]:
        raise NotImplementedError("openai mode has no unified cost API across providers")

    def guardrail_events(self) -> Iterator[GuardrailEvent]:
        raise NotImplementedError("openai mode has no guardrail surface")


class GatewayUpstreamError(RuntimeError):
    def __init__(self, status: int, url: str, body: str) -> None:
        self.status = status
        self.url = url
        self.body = body
        super().__init__(f"gateway returned {status} for {url}: {body}")

    def costs(self, window: str) -> list[CostRecord]:
        raise NotImplementedError("Step 2.3: depends on whether cost is API-exposed (Q1)")

    def guardrail_events(self) -> Iterator[GuardrailEvent]:
        raise NotImplementedError("Step 2.3: depends on guardrail event exposure (Q4)")
