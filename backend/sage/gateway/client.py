"""GatewayClient — the upstream adapter at the enforcement seam (DESIGN.md Seam 2).

The Domino AI Gateway already exists and is OpenAI-compatible, so route() is a thin OpenAI
client pointed at the gateway URL. costs()/guardrail_events() are the unconfirmed surface
(gateway-questions.md Q1/Q4) and stay behind this adapter so the fake covers them until Step 2.3.

Two adapters make the seam real:
  - FakeGatewayClient   — deterministic, for tests (no network)
  - DominoGatewayClient — real; fill in once we have the gateway URL + auth (Q7)
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

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
    """Tags attached to every request so the gateway attributes cost correctly.

    Untagged requests land in the gateway's 'unknown' bucket (observed in its UI), which
    breaks per-phase attribution. project + phase are mandatory (SPEC.md C4/C7).
    """

    project: str
    phase: str
    model: str


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


class DominoGatewayClient:
    """Real client for the OpenAI-compatible Domino gateway.

    route() is implemented; needs live verification once we have base_url + key (Step 1.1).
    costs()/guardrail_events() await the Step 2.3 answers (Q1/Q4).
    """

    def __init__(self, base_url: str, token_provider: Callable[[], str], timeout_s: float = 60.0) -> None:
        # base_url is the OpenAI base ending in /v1, e.g.
        #   https://apps.cloud-dogfood.domino.tech/apps/llm_gateway/v1
        self._base_url = base_url.rstrip("/")
        self._token_provider = token_provider
        self._timeout_s = timeout_s

    def route(self, request: dict[str, Any], labels: CostLabels) -> Iterator[bytes]:
        import httpx  # local import so tests that never hit the network don't need it

        # Auth + tags per the LLM_gateway repo:
        #   Authorization: Bearer <token>  (sidecar JWT in-workspace, or a dgw_ PAT off-Domino)
        #   X-LLM-Tag-*  -> stored in the gateway's usage `tags` column (project from Domino
        #   context; we add phase + model so per-phase cost is queryable).
        headers = {
            "Authorization": f"Bearer {self._token_provider()}",
            "X-LLM-Tag-project": labels.project,
            "X-LLM-Tag-phase": labels.phase,
            "X-LLM-Tag-model": labels.model,
        }
        url = f"{self._base_url}/chat/completions"  # base already ends in /v1
        with httpx.Client(timeout=self._timeout_s, follow_redirects=False) as client:
            with client.stream("POST", url, json=request, headers=headers) as resp:
                # Surface upstream errors BEFORE streaming so the caller gets a clean
                # message instead of a mid-stream connection reset. A 3xx here means auth
                # bounced us to a login page (bad/expired token).
                if resp.status_code >= 400 or resp.is_redirect:
                    body = resp.read().decode(errors="replace")[:800]
                    raise GatewayUpstreamError(resp.status_code, url, body)
                yield from resp.iter_bytes()


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
