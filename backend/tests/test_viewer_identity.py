"""Extended identity: the viewer's JWT beats the sidecar, and survives OpenCode's /v1 hop."""
from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

from sage.gateway.client import (
    bind_viewer_token,
    jwt_identity,
    prefer_viewer,
    remembered_viewer_token,
    reset_viewer_token,
    viewer_token,
)


def _jwt(**claims: str) -> str:
    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig"


def test_prefer_viewer_beats_the_sidecar():
    reset_viewer_token()
    bind_viewer_token("viewer-jwt")
    assert prefer_viewer(lambda: "sidecar")() == "viewer-jwt"
    reset_viewer_token()
    assert prefer_viewer(lambda: "sidecar")() == "sidecar"


def test_remembered_viewer_survives_the_internal_v1_hop():
    """OpenCode dials /v1 over localhost with no Authorization header, so that hop has no viewer of
    its own and inherits the last browser call's. It can also outlive the request that started it
    (#77 — a build the person walked away from goes on running), which is why the remembered token
    is not cleared when that request ends.

    Asked through `remembered_viewer_token` rather than `viewer_token`: the inheriting is a thing
    the middleware does for one kind of path, not a thing every reader gets for free. That
    distinction is the fix in `test_one_viewers_jwt_is_not_handed_to_the_next_viewer` below."""
    reset_viewer_token()
    bind_viewer_token("viewer-jwt")
    bind_viewer_token(None, remember=False)          # the browser request ends
    assert remembered_viewer_token() == "viewer-jwt"  # the build outlives it
    bind_viewer_token(remembered_viewer_token(), remember=False)   # the /v1 hop, as the middleware binds it
    assert prefer_viewer(lambda: "sidecar")() == "viewer-jwt"
    reset_viewer_token()


def test_a_request_that_carries_no_viewer_reads_as_no_viewer():
    """`viewer_token` is this request's, or nothing. It used to fall through to the remembered one,
    which handed the fallback to every reader rather than to the one hop entitled to it."""
    reset_viewer_token()
    bind_viewer_token("viewer-jwt")
    bind_viewer_token(None, remember=False)
    assert viewer_token() is None
    reset_viewer_token()


def test_one_viewers_jwt_is_not_handed_to_the_next_viewer():
    """The leak. A published Workbench App is a door serving many viewers (ADR-0004), and the
    remembered token is process-wide. Viewer A arrives with a JWT; viewer B arrives without one and
    used to fall through to A's — `/api/me` answered A, and anything reading `viewer_token` acted
    as A.

    Driven through the real middleware, because the middleware is where the entitlement is decided
    and a hand-rolled bind would be asserting against my own reconstruction of it."""
    reset_viewer_token()
    import sage.orchestrator.app as appmod

    client = TestClient(appmod.control_app)
    alice = _jwt(sub="u1", preferred_username="alice")
    assert client.get("/api/me", headers={"Authorization": f"Bearer {alice}"}).json()["name"] == "alice"

    # Same process, no Authorization. Alice's JWT is still remembered for her build's /v1 hop.
    assert remembered_viewer_token() == alice
    assert client.get("/api/me").json()["name"] != "alice"
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
