"""`whoami()` answers for the token it was asked with, not for the first one it ever saw.

`_headers` calls `self._token_provider()` on every request, and `DominoControlPlane` is a
process-wide singleton. On the published Workbench App — a door serving many viewers (ADR-0004) —
`_me` cached once and forever meant the first viewer's name was handed to everyone after them.
`Door.ensure_default` builds the Default Project name out of it, so viewer B landed in viewer A's
Project and A's Sage Builder.

The cache still earns its place: attach polls ask who the viewer is every few seconds while a
builder boots. It is keyed on the token now, so the poll still costs one request and a different
token is a different answer.
"""
from __future__ import annotations

import httpx

from sage.provision.domino import DominoControlPlane

USERS = {
    "tok-alice": {"id": "u-alice", "userName": "alice"},
    "tok-bob": {"id": "u-bob", "userName": "bob"},
}


def _cp(token_provider, calls):
    def handler(request):
        assert request.url.path == "/api/users/v1/self"
        calls.append(request.headers["authorization"])
        token = request.headers["authorization"].removeprefix("Bearer ")
        return httpx.Response(200, json={"user": USERS[token], "metadata": {}})

    return DominoControlPlane(
        "https://domino.example.com",
        token_provider,
        environment_id="env-1",
        hardware_tier_id="tier-1",
        transport=httpx.MockTransport(handler),
    )


def test_a_second_viewers_token_gets_a_second_viewers_identity():
    """The leak. One client, two tokens, because the token is fetched per request and the client
    is not per viewer."""
    token = {"v": "tok-alice"}
    calls: list[str] = []
    cp = _cp(lambda: token["v"], calls)

    assert cp.whoami().name == "alice"
    token["v"] = "tok-bob"
    assert cp.whoami().name == "bob"
    assert cp.whoami().id == "u-bob"


def test_the_same_token_is_still_asked_once():
    """What the cache was for: the door polls `whoami` every few seconds while a builder boots, and
    without a cache that is one request per poll."""
    calls: list[str] = []
    cp = _cp(lambda: "tok-alice", calls)

    for _ in range(5):
        assert cp.whoami().name == "alice"
    assert len(calls) == 1


def test_going_back_to_the_first_token_asks_again_rather_than_answering_from_a_stale_slot():
    """One slot, keyed on the token — so a switch back is a miss, not a stale hit. Two viewers
    alternating cost one request each rather than one of them getting the other's name."""
    token = {"v": "tok-alice"}
    calls: list[str] = []
    cp = _cp(lambda: token["v"], calls)

    assert cp.whoami().name == "alice"
    token["v"] = "tok-bob"
    assert cp.whoami().name == "bob"
    token["v"] = "tok-alice"
    assert cp.whoami().name == "alice"
    assert len(calls) == 3


def test_the_username_helper_follows_the_token_too():
    """`_username` is what names a Default Project and what `app_manage_url` builds a link out of.
    It reads `whoami`, so it inherits the fix — asserted because it is the caller that made the
    leak visible rather than merely present."""
    token = {"v": "tok-alice"}
    calls: list[str] = []
    cp = _cp(lambda: token["v"], calls)

    assert cp._username() == "alice"
    token["v"] = "tok-bob"
    assert cp._username() == "bob"
