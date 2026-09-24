"""`sage.platform.auth.TokenSource`: one bearer per process, with the right wire shape per kind.

The header split isn't a style choice — it's what a real Domino cluster actually answers (see the
module docstring in `auth.py`): a legacy account key is refused as `Authorization: Bearer` and
accepted as `X-Domino-Api-Key`, and a Personal Access Token is the exact opposite. Nothing in a
static string says which it is, so a static source probes once. These tests pin both shapes with a
mock transport that accepts each credential only in its own header.
"""
from __future__ import annotations

import httpx
import pytest

from sage.platform.auth import TokenSource, build_token_source, gateway_bearer

# Each credential is only accepted in the header its kind needs — what a real cluster answers:
# a legacy account key (`DOMINO_USER_API_KEY`) only as X-Domino-Api-Key (sandbox, 2026-09-23), a
# Personal Access Token only as Bearer (product owner's laptop vs cloud-dogfood, 2026-09-24).
USERS = {
    "static-key-1": ("api_key", {"id": "u-1", "userName": "alice", "fullName": "Alice Anderson",
                                 "email": "alice@example.com"}),
    "pat-1": ("bearer", {"id": "u-3", "userName": "carol"}),
    "sidecar-jwt-1": ("bearer", {"id": "u-2", "userName": "bob"}),
}


def _transport(calls: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/users/v1/self"
        calls.append(dict(request.headers))
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            scheme, token = "bearer", auth.removeprefix("Bearer ")
        else:
            scheme, token = "api_key", request.headers.get("x-domino-api-key", "")
        accepted, user = USERS.get(token, (None, None))
        if accepted != scheme:
            return httpx.Response(403, json={"errors": ["Not authorized: No current user in request"]})
        return httpx.Response(200, json={"user": user})

    return httpx.MockTransport(handler)


def test_a_legacy_api_key_ends_up_on_the_api_key_header():
    calls: list[dict] = []
    source = TokenSource.static("static-key-1", "https://d.example", transport=_transport(calls))
    assert source.whoami().name == "alice"
    assert source.scheme() == "api_key"
    assert "x-domino-api-key" in calls[-1]
    assert "authorization" not in calls[-1]


def test_a_personal_access_token_ends_up_on_bearer():
    """The laptop bug: a PAT sent as X-Domino-Api-Key is refused (403 'No current user')."""
    calls: list[dict] = []
    source = TokenSource.static("pat-1", "https://d.example", transport=_transport(calls))
    assert source.whoami().name == "carol"
    assert source.scheme() == "bearer"
    assert source.headers() == {"Authorization": "Bearer pat-1"}
    assert source.sdk_kwarg() == {"token": "pat-1"}


def test_the_scheme_is_probed_once_and_remembered():
    calls: list[dict] = []
    source = TokenSource.static("static-key-1", "https://d.example", transport=_transport(calls))
    source.headers()
    probes = len(calls)
    source.headers()
    source.headers()
    assert len(calls) == probes  # no further probing
    assert probes == 2  # Bearer tried and refused, then the API-key header accepted


def test_a_credential_neither_header_accepts_stays_undecided():
    """A refusal on both is not an answer: nothing is cached, so a corrected key (or a network
    that comes back) is probed again next call. Meanwhile the old API-key shape is sent."""
    calls: list[dict] = []
    source = TokenSource.static("not-a-real-key", "https://d.example", transport=_transport(calls))
    assert source.scheme() == "api_key"
    first = len(calls)
    source.scheme()
    assert len(calls) == 2 * first


def test_an_explicit_scheme_skips_the_probe():
    calls: list[dict] = []
    source = TokenSource.static("pat-1", "https://d.example", scheme="bearer",
                                transport=_transport(calls))
    assert source.headers() == {"Authorization": "Bearer pat-1"}
    assert calls == []


def test_a_sidecar_source_authenticates_with_bearer():
    calls: list[dict] = []
    source = TokenSource("sidecar", lambda: "sidecar-jwt-1", "https://d.example",
                          transport=_transport(calls))
    who = source.whoami()
    assert who.name == "bob"
    assert calls[0]["authorization"] == "Bearer sidecar-jwt-1"
    assert "x-domino-api-key" not in calls[0]


def test_whoami_carries_the_full_name_and_email_when_the_api_has_them():
    """The git-identity resolver (`Orchestrator._git_identity`) needs a real name/email, not just
    the username `.name` already carried — confirmed live against a real Domino cluster (see
    `platform/auth.py`'s module docstring)."""
    source = TokenSource.static("static-key-1", "https://d.example", transport=_transport([]))
    who = source.whoami()
    assert who.full_name == "Alice Anderson"
    assert who.email == "alice@example.com"


def test_whoami_full_name_and_email_are_blank_when_the_api_omits_them():
    source = TokenSource("sidecar", lambda: "sidecar-jwt-1", "https://d.example",
                          transport=_transport([]))
    who = source.whoami()
    assert who.full_name == ""
    assert who.email == ""


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
    static = TokenSource.static("key-1", "https://d.example", scheme="api_key")
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
