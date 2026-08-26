"""Extended identity: the viewer's JWT beats the sidecar, and survives OpenCode's /v1 hop."""
from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

from sage.gateway.client import (
    bind_viewer_token,
    jwt_identity,
    prefer_viewer,
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
    # Browser request remembers; OpenCode's later /v1 has no Authorization.
    reset_viewer_token()
    bind_viewer_token("viewer-jwt")
    bind_viewer_token(None, remember=False)
    assert viewer_token() == "viewer-jwt"
    bind_viewer_token(viewer_token(), remember=False)
    assert prefer_viewer(lambda: "sidecar")() == "viewer-jwt"
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
