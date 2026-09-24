"""One `TokenSource` per process — the bearer Sage acts with for every Domino call
(ONE-APP-PLAN.md decision #1, confirmed 2026-09-22: one person per Sage process).

Two kinds, matching `sage.gateway.client.static_token`/`sidecar_token`:

- `static`  — a long-lived Domino account API key (laptop `settings.domino.token`, or the
  `DOMINO_USER_API_KEY` Domino itself injects into an App/workspace).
- `sidecar` — a short-lived JWT re-fetched per call from the workspace/App sidecar at
  `localhost:8899` (README: workspace JWTs expire quickly).

THE TWO ARE NOT INTERCHANGEABLE ON THE WIRE. Verified live against a real Domino cluster,
2026-09-23, from inside this sandbox (which is itself a Domino workspace with a real
`DOMINO_USER_API_KEY` and a reachable sidecar):

- `GET /api/users/v1/self` and `GET /api/projects/beta/projects`: a static key sent as
  `Authorization: Bearer <key>` is REFUSED (403, "Not authorized: No current user in request"
  from `/api/users/v1/self`); the same key sent as `X-Domino-Api-Key: <key>` is accepted (200).
  A sidecar JWT sent as `Authorization: Bearer <jwt>` is accepted (200) — the header style
  `sage.provision.domino.DominoControlPlane` already uses everywhere, unchanged here.
- `GET /api/datasetrw/v2/datasets` (and the `/v4/datasetrw/*` family
  `sage.assets.provider.DominoAssetProvider` calls) tolerated a static key as `Bearer` too in this
  same test — so that provider's existing raw-Bearer calls are left alone; only the STRICT
  endpoints above are known to require the `X-Domino-Api-Key` header, and `.headers()` below
  always uses it for a static key so nothing has to guess which endpoints are strict.
- The `domino_data` SDK mirrors the split at its own constructor: `DatasetClient(token=<static
  key>)` fails ("Anonymous principals are not supported"); `DatasetClient(api_key=<static key>)`
  and `DatasetClient(token=<sidecar JWT>)` both work (live-verified against a real Dataset).
  `DataSourceClient` shares the identical `api_key`/`token` constructor shape — not independently
  live-verified, but `.sdk_kwarg()` below applies the same rule to both rather than each caller
  re-deriving it.

So a caller never assumes one bearer string works everywhere: it asks `.headers()` for a raw REST
call, or `.sdk_kwarg()` for a `domino_data` client constructor, and gets the right shape for
whichever kind this TokenSource actually is.

A `static` credential is itself one of TWO kinds, and nothing in the string says which. The legacy
account API key above (`DOMINO_USER_API_KEY`) needs `X-Domino-Api-Key`. A Domino Personal Access
Token — what a person mints for a laptop — is the opposite: live-checked 2026-09-24 from the
product owner's laptop against cloud-dogfood, a PAT as `Authorization: Bearer` answered 200 on both
`/api/users/v1/self` and `/api/projects/beta/projects`, and the same PAT as `X-Domino-Api-Key`
answered 403 on both. So a static source finds out once, by asking `/api/users/v1/self` with each
header, and remembers the one Domino accepted (`static(..., scheme=...)` skips the probe for a
caller that already knows).
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable

import httpx

from ..gateway.client import (
    DEFAULT_SIDECAR_URL,
)
from ..gateway.client import (
    sidecar_token as _sidecar_token,
)
from ..gateway.client import (
    static_token as _static_token,
)
from ..provision.domino import UserRef


class TokenSource:
    """One identity, for its whole life (decision #1) — unlike `DominoControlPlane.whoami()`,
    which is deliberately kept keyed on the live token value because ONE of its instances used to
    serve MANY viewers behind the old multi-viewer door (ADR-0004, `test_whoami_follows_the_token
    .py`). This pivot removes that door; a `TokenSource` here is built once per process (or once
    per settings save) and answers for one person the whole time, so caching `whoami()` forever is
    correct rather than stale.
    """

    def __init__(
        self,
        kind: str,
        bearer_fn: Callable[[], str],
        api_host: str,
        *,
        scheme: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        if kind not in ("static", "sidecar"):
            raise ValueError(f"unknown TokenSource kind {kind!r}")
        if scheme not in (None, "bearer", "api_key"):
            raise ValueError(f"unknown TokenSource scheme {scheme!r}")
        self.kind = kind
        self._bearer_fn = bearer_fn
        self._api_host = api_host.rstrip("/")
        self._transport = transport
        self._timeout_s = timeout_s
        self._me: UserRef | None = None
        self._lock = threading.Lock()
        # Only meaningful for `static` (a sidecar JWT is always Bearer). None = not probed yet.
        self._scheme: str | None = "bearer" if kind == "sidecar" else scheme
        self._scheme_lock = threading.Lock()

    @classmethod
    def static(cls, key: str, api_host: str, **kw) -> TokenSource:
        return cls("static", _static_token(key), api_host, **kw)

    @staticmethod
    def _shaped(scheme: str, token: str) -> dict[str, str]:
        if scheme == "api_key":
            return {"X-Domino-Api-Key": token}
        return {"Authorization": f"Bearer {token}"}

    def scheme(self) -> str:
        """`bearer` or `api_key` — which header Domino accepts this credential in.

        Probed once for a static credential: `/api/users/v1/self` with Bearer first (a PAT, the
        laptop case), then `X-Domino-Api-Key` (a legacy account key). Only a 200 decides it; a
        refusal on both, or a network failure, leaves it undecided so the next call asks again,
        and answers `api_key` meanwhile — the shape this class always sent before PATs were known
        to need the other one.
        """
        if self._scheme is not None:
            return self._scheme
        with self._scheme_lock:
            if self._scheme is not None:
                return self._scheme
            if not self._api_host:
                return "api_key"
            token = self.bearer()
            try:
                with httpx.Client(transport=self._transport, timeout=self._timeout_s) as c:
                    for candidate in ("bearer", "api_key"):
                        r = c.get(f"{self._api_host}/api/users/v1/self",
                                  headers={**self._shaped(candidate, token),
                                           "Accept": "application/json"})
                        if r.status_code == 200:
                            self._scheme = candidate
                            return candidate
            except httpx.HTTPError:
                pass
            return "api_key"

    @classmethod
    def sidecar(cls, api_host: str, url: str = DEFAULT_SIDECAR_URL, **kw) -> TokenSource:
        return cls("sidecar", _sidecar_token(url), api_host, **kw)

    def bearer(self) -> str:
        """The raw token string. Prefer `.headers()`/`.sdk_kwarg()` — this exists for the one or
        two call sites (the LLM Gateway) that are already proven to take a bare Bearer regardless
        of kind, not as a general-purpose accessor."""
        return self._bearer_fn()

    def headers(self) -> dict[str, str]:
        """Auth header for a raw REST call to the Domino API, correct for either kind (see the
        module docstring for what was actually tried)."""
        return self._shaped(self.scheme(), self.bearer())

    def sdk_kwarg(self) -> dict[str, str]:
        """The `domino_data` constructor kwarg this token authenticates with (`DatasetClient`,
        `DataSourceClient` — both share the `api_key=`/`token=` shape). Follows `scheme()`: a
        Bearer-shaped credential goes in as `token=` the way a sidecar JWT does. Live-verified for
        the legacy key and the sidecar JWT; NOT yet for a PAT as `token=`."""
        return {"api_key": self.bearer()} if self.scheme() == "api_key" else {"token": self.bearer()}

    def whoami(self) -> UserRef:
        with self._lock:
            if self._me is not None:
                return self._me
            if not self._api_host:
                raise RuntimeError("TokenSource.whoami() needs a Domino API host")
            with httpx.Client(transport=self._transport, timeout=self._timeout_s) as c:
                r = c.get(f"{self._api_host}/api/users/v1/self",
                          headers={**self.headers(), "Accept": "application/json"})
                r.raise_for_status()
            u = (r.json() or {}).get("user") or {}
            self._me = UserRef(
                id=str(u.get("id") or ""),
                name=str(u.get("userName") or u.get("loginId") or u.get("id") or ""),
                full_name=str(u.get("fullName") or ""),
                email=str(u.get("email") or ""),
            )
            return self._me


def build_token_source(domino_host: str, domino_token: str, env: dict[str, str] | None = None,
                        **kw) -> TokenSource | None:
    """The one bearer Sage acts with for every Domino call (§0's "Identity/tokens" row): a static
    account key when one is configured, else the workspace/App sidecar. `None` when this process
    has no Domino host at all — a from-scratch laptop run before Settings names one.
    """
    env = env if env is not None else os.environ
    if not domino_host:
        return None
    if domino_token:
        return TokenSource.static(domino_token, domino_host, **kw)
    return TokenSource.sidecar(domino_host, env.get("GATEWAY_TOKEN_URL", DEFAULT_SIDECAR_URL), **kw)


def gateway_bearer(gateway_api_key: str, token_source: TokenSource | None,
                    env: dict[str, str] | None = None) -> Callable[[], str] | None:
    """What the LLM Gateway is called with: `gateway_api_key` (a `dgw_` key) when the gateway does
    not accept the shared token (risk #2 in ONE-APP-PLAN.md §6); otherwise `token_source.bearer` —
    the SAME object every other Domino call uses (§0). Both shapes are proven-safe as a bare
    `Authorization: Bearer` today (`gateway/client.py`'s `OpenAICompatibleClient`), so this needs
    no `.headers()`-style branching the way the Domino API calls above do.
    """
    if gateway_api_key:
        return _static_token(gateway_api_key)
    if token_source is not None:
        return token_source.bearer
    env = env if env is not None else os.environ
    return _sidecar_token(env.get("GATEWAY_TOKEN_URL", DEFAULT_SIDECAR_URL))
