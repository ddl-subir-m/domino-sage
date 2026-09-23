"""`sage.platform.auth.TokenSource`: one bearer per process, with the right wire shape per kind.

The `static` vs `sidecar` header/kwarg split isn't a style choice — it's what a real Domino
cluster actually answers, verified live 2026-09-23 (see the module docstring in `auth.py`): a
static account key sent as `Authorization: Bearer` is refused by `/api/users/v1/self`, accepted as
`X-Domino-Api-Key`. These tests pin that shape with a mock transport so a future edit can't drift
back to a bare Bearer header for a static key without a test catching it.
"""
from __future__ import annotations

import httpx
import pytest

from sage.platform.auth import TokenSource, build_token_source, gateway_bearer

USERS = {
    "static-key-1": {"id": "u-1", "userName": "alice"},
    "sidecar-jwt-1": {"id": "u-2", "userName": "bob"},
}


def _transport(calls: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/users/v1/self"
        calls.append(dict(request.headers))
        auth = request.headers.get("authorization", "")
        api_key = request.headers.get("x-domino-api-key", "")
        token = auth.removeprefix("Bearer ") or api_key
        if token not in USERS:
            return httpx.Response(403, json={"errors": ["Not authorized: No current user in request"]})
        return httpx.Response(200, json={"user": USERS[token]})

    return httpx.MockTransport(handler)


def test_a_static_source_authenticates_with_the_api_key_header_not_bearer():
    calls: list[dict] = []
    source = TokenSource.static("static-key-1", "https://d.example", transport=_transport(calls))
    who = source.whoami()
    assert who.name == "alice"
    assert "x-domino-api-key" in calls[0]
    assert "authorization" not in calls[0]


def test_a_sidecar_source_authenticates_with_bearer():
    calls: list[dict] = []
    source = TokenSource("sidecar", lambda: "sidecar-jwt-1", "https://d.example",
                          transport=_transport(calls))
    who = source.whoami()
    assert who.name == "bob"
    assert calls[0]["authorization"] == "Bearer sidecar-jwt-1"
    assert "x-domino-api-key" not in calls[0]


def test_whoami_is_cached_forever_not_keyed_on_the_token_value():
    """Unlike DominoControlPlane's per-viewer cache (kept for the old multi-viewer door), a
    TokenSource answers for one identity its whole life (decision #1) — even a token that changes
    under it (a rotating sidecar JWT) does not trigger a second lookup."""
    calls: list[dict] = []
    tokens = iter(["sidecar-jwt-1", "sidecar-jwt-1", "a-different-token-entirely"])
    source = TokenSource("sidecar", lambda: next(tokens), "https://d.example",
                          transport=_transport(calls))
    assert source.whoami().name == "bob"
    assert source.whoami().name == "bob"
    assert source.whoami().name == "bob"
    assert len(calls) == 1


def test_sdk_kwarg_matches_the_domino_data_constructor_shape():
    """Live-verified: DatasetClient(token=<static key>) fails; api_key=<static key> works.
    DatasetClient(token=<sidecar JWT>) works. Both share the identical constructor shape with
    DataSourceClient."""
    static = TokenSource.static("key-1", "https://d.example")
    sidecar = TokenSource("sidecar", lambda: "jwt-1", "https://d.example")
    assert static.sdk_kwarg() == {"api_key": "key-1"}
    assert sidecar.sdk_kwarg() == {"token": "jwt-1"}


def test_whoami_with_no_api_host_raises_rather_than_answering_garbage():
    source = TokenSource("sidecar", lambda: "tok", "")
    with pytest.raises(RuntimeError):
        source.whoami()


def test_build_token_source_is_none_with_no_domino_host():
    assert build_token_source("", "") is None


def test_build_token_source_prefers_a_static_key_over_the_sidecar():
    source = build_token_source("https://d.example", "a-key")
    assert source is not None
    assert source.kind == "static"


def test_build_token_source_falls_back_to_the_sidecar_with_no_key():
    source = build_token_source("https://d.example", "")
    assert source is not None
    assert source.kind == "sidecar"


def test_gateway_bearer_prefers_the_explicit_dgw_key():
    bearer = gateway_bearer("dgw_explicit", TokenSource.static("account-key", "https://d.example"))
    assert bearer() == "dgw_explicit"


def test_gateway_bearer_falls_back_to_the_shared_token_source():
    """Same object feeds the platform API and the LLM Gateway (§0) when no separate dgw_ key is
    configured — the gateway is proven-safe as a bare Bearer regardless of kind, so this is a
    plain passthrough, not a header-scheme choice."""
    source = TokenSource.static("account-key", "https://d.example")
    bearer = gateway_bearer("", source)
    assert bearer() == "account-key"


def test_gateway_bearer_falls_back_to_sidecar_with_nothing_configured():
    bearer = gateway_bearer("", None, env={"GATEWAY_TOKEN_URL": "http://localhost:8899/access-token"})
    assert callable(bearer)
