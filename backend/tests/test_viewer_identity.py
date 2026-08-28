"""Viewer identity: this request's JWT, for `/api/me`, and never the previous request's."""
from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from sage.gateway.client import (
    bind_viewer_token,
    jwt_identity,
    reset_viewer_token,
    viewer_token,
)


def _jwt(**claims: str) -> str:
    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig"


def test_a_request_that_carries_no_viewer_reads_as_no_viewer():
    """`viewer_token` is this request's, or nothing. It used to fall through to the remembered one,
    which handed the fallback to every reader rather than to the one hop entitled to it."""
    reset_viewer_token()
    bind_viewer_token("viewer-jwt")
    bind_viewer_token(None)
    assert viewer_token() is None
    reset_viewer_token()


def test_one_viewers_jwt_is_not_handed_to_the_next_viewer():
    """The leak. A published Workbench App is a door serving many viewers (ADR-0004), and the
    remembered token used to be process-wide. Viewer A arrives with a JWT; viewer B arrives without
    one and used to fall through to A's — `/api/me` answered A, and anything reading `viewer_token`
    acted as A. Nothing is remembered now (#91), so what this still guards is the clearing: the
    middleware drops the ContextVar when a request ends, on a worker the next viewer will reuse.

    Driven through the real middleware, because the middleware is where the entitlement is decided
    and a hand-rolled bind would be asserting against my own reconstruction of it."""
    reset_viewer_token()
    import sage.orchestrator.app as appmod

    client = TestClient(appmod.control_app)
    alice = _jwt(sub="u1", preferred_username="alice")
    assert client.get("/api/me", headers={"Authorization": f"Bearer {alice}"}).json()["name"] == "alice"

    # Same process, no Authorization. Nothing of Alice's is left for this request to find.
    assert client.get("/api/me").json()["name"] != "alice"
    reset_viewer_token()


def test_the_middleware_clears_the_viewer_even_when_the_request_blows_up():
    """The clearing, asserted where TestClient cannot see it.

    Each TestClient request runs in its own task with its own copy of the context, so whatever one
    request leaves in the ContextVar is invisible to the next one however the middleware behaves —
    the test above guards the entitlement, and after #91 it cannot also guard this. Driving the
    middleware coroutine by hand keeps it in THIS context, the way a pooled worker reuses one, so
    what it leaves behind is visible. The app raises because the guarantee is `finally`: a request
    that dies half-way must not leave its viewer sitting on the worker for the next one."""
    import sage.orchestrator.app as appmod

    async def _boom(scope, receive, send):
        assert viewer_token() == "alice-jwt"
        raise RuntimeError("request blew up")

    scope = {"type": "http", "path": "/api/me", "headers": [(b"authorization", b"Bearer alice-jwt")]}
    reset_viewer_token()
    with pytest.raises(RuntimeError):
        appmod._ViewerIdentityMiddleware(_boom)(scope, None, None).send(None)
    assert viewer_token() is None
    reset_viewer_token()


def test_jwt_identity_reads_preferred_username():
    tok = _jwt(sub="u1", preferred_username="alice")
    assert jwt_identity(tok) == {"id": "u1", "name": "alice"}
    assert jwt_identity(None) == {}
    assert jwt_identity("not-a-jwt") == {}


def test_me_uses_the_viewer_jwt():
    reset_viewer_token()
    import sage.orchestrator.app as appmod

    tok = _jwt(sub="u1", preferred_username="alice")
    r = TestClient(appmod.control_app).get("/api/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.json() == {"id": "u1", "name": "alice"}
    reset_viewer_token()
