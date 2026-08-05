"""GatewayClient — the upstream adapter at the enforcement seam (DESIGN.md Seam 2).

The Domino AI Gateway already exists and is OpenAI-compatible, so route() is a thin OpenAI
client pointed at the gateway URL. guardrail_events() is the one still-unconfirmed surface
(gateway-questions.md Q4) and stays behind this adapter so the fake covers it until Step 2.3.

There is deliberately no cost API here (Q1 is settled): Sage tags its calls (CostLabels) and links
out to the gateway's own Usage & Cost dashboard rather than re-deriving spend. Only the gateway can
price a call correctly — it honours per-alias custom rates from its own DB (routes/gateway.py
_compute_cost) that no client can see.

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
        with urllib.request.urlopen(url, timeout=5) as resp:
            tok = resp.read().decode().strip()
        return tok.removeprefix("Bearer ")  # avoid double-Bearer

    return _fetch


@dataclass(frozen=True)
class CostLabels:
    """Sage's cost-attribution tags, sent to the Domino gateway as X-LLM-Tag-sage-* headers and
    stored on each UsageLog row (queryable via group_by=tag:sage-*).

    Everything below is namespaced `sage-` for two reasons. It makes all Sage traffic one filter
    (tag:sage-source=domino-sage) on a shared gateway, and it dodges the gateway's RESERVED_TAG_KEYS
    (services/tags.py), which silently DROPS bare `project`/`project-id`/`project-name`/`model`/
    `user`/`org` at ingest — no error, just a tag that never arrives.

    `project_name` is tagged even though the gateway has first-class project columns, because those
    columns are populated only from the X-Domino-Project-Id / X-Domino-Project request headers
    (routes/gateway.py `_resolve_caller`) and Sage doesn't send them — they're blank for all Sage
    traffic. The dashboard also has no "By Project" grouping (static/js/views/usage.js
    GROUP_BY_OPTIONS_BASE), whereas tag keys are discovered and appended to that dropdown
    automatically, so `sage-project` shows up as "By Tag: sage-project" with no gateway change.
    """

    phase: str                  # plan | implement | ask   — build phase
    mode: str                   # auto | ask | sovereign   — routing mode (sovereign = asset lock)
    component: str = "builder"  # builder | probe          — which Sage process made the call
    session: str | None = None  # OpenCode session id      — per-build cost rollup
    version: str | None = None  # Sage git rev             — cost/quality across Sage releases
    project_name: str | None = None  # "<owner>/<project>"  — which Sage deployment spent this


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

    def guardrail_events(self) -> Iterator[GuardrailEvent]:
        """Guardrail/leak events. Surface TBD (Q4) — may not be exposed to callers."""
        ...


@dataclass
class FakeGatewayClient:
    """Deterministic double for integration/E2E tests. No network."""

    scripted_events: list[GuardrailEvent] = field(default_factory=list)
    seen: list[tuple[dict[str, Any], CostLabels]] = field(default_factory=list)

    def route(self, request: dict[str, Any], labels: CostLabels) -> Iterator[bytes]:
        self.seen.append((request, labels))
        # Echo the (possibly overridden) model so tests can assert the shim set it.
        yield f'{{"model": "{request.get("model")}", "phase": "{labels.phase}"}}'.encode()

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
        read_timeout_s: float = 300.0,
    ) -> None:
        # base_url is the OpenAI base ending in /v1, e.g.
        #   https://apps.cloud-dogfood.domino.tech/apps/llm_gateway/v1  (domino)
        #   https://api.some-host.com/v1                                 (generic)
        self._base_url = base_url.rstrip("/")
        self._token_provider = token_provider
        self._domino_tags = domino_tags
        self._timeout_s = timeout_s
        self._read_timeout_s = read_timeout_s

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
            if labels.project_name:
                headers["X-LLM-Tag-sage-project"] = labels.project_name
        url = f"{self._base_url}/chat/completions"  # base already ends in /v1
        # read_timeout_s (default 300s) is the inter-chunk read timeout: httpx applies the read
        # timeout to the GAP between streamed chunks. The original scalar 60s was too SHORT — LLM
        # turns routinely pause >60s mid-stream (extended thinking) -> ReadTimeout -> the stream to
        # OpenCode severs -> "TypeError: network error". But read=None (unbounded) is WRONG too: a
        # gateway that stops sending would hang the turn forever. A large FINITE value tolerates real
        # thinking gaps yet still surfaces a dead stream as a clean error (the shim wraps it into a
        # readable message). connect/write/pool stay bounded via _timeout_s.
        timeout = httpx.Timeout(self._timeout_s, read=self._read_timeout_s)
        with (
            httpx.Client(timeout=timeout, follow_redirects=False) as client,
            client.stream("POST", url, json=request, headers=headers) as resp,
        ):
            # Surface upstream errors BEFORE streaming so the caller gets a clean message
            # instead of a mid-stream reset. A 3xx here means auth bounced to a login page.
            if resp.status_code >= 400 or resp.is_redirect:
                body = resp.read().decode(errors="replace")[:800]
                raise GatewayUpstreamError(resp.status_code, url, body)
            yield from resp.iter_bytes()

    def guardrail_events(self) -> Iterator[GuardrailEvent]:
        raise NotImplementedError("Step 2.3: depends on guardrail event exposure (Q4)")


class MultiProviderOpenAIClient:
    """openai gateway mode: each model routes to its own vendor base_url/key.

    Unlike OpenAICompatibleClient (one fixed base_url), there's no shared gateway here — the
    catalog entry matching request["model"] decides which vendor endpoint and API key to use.
    """

    def __init__(self, models: list[OpenModel], *, timeout_s: float = 60.0, read_timeout_s: float = 300.0) -> None:
        self._by_id = {m.id: m for m in models}
        self._timeout_s = timeout_s
        self._read_timeout_s = read_timeout_s

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
        timeout = httpx.Timeout(self._timeout_s, read=self._read_timeout_s)  # large FINITE inter-chunk read (see route above)
        with (
            httpx.Client(timeout=timeout, follow_redirects=False) as client,
            client.stream("POST", url, json=request, headers=headers) as resp,
        ):
            if resp.status_code >= 400 or resp.is_redirect:
                body = resp.read().decode(errors="replace")[:800]
                raise GatewayUpstreamError(resp.status_code, url, body)
            yield from resp.iter_bytes()

    def guardrail_events(self) -> Iterator[GuardrailEvent]:
        raise NotImplementedError("openai mode has no guardrail surface")


class GatewayUpstreamError(RuntimeError):
    def __init__(self, status: int, url: str, body: str) -> None:
        self.status = status
        self.url = url
        self.body = body
        super().__init__(f"gateway returned {status} for {url}: {body}")
