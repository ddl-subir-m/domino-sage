"""GET/PUT /api/settings: the Connection section of the settings drawer (ONE-APP-PLAN.md §2.7).
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _client():
    import sage.orchestrator.app as appmod

    return TestClient(appmod.control_app), appmod


def test_get_settings_redacts_secrets_to_booleans():
    client, appmod = _client()
    original = appmod._SETTINGS
    try:
        import dataclasses

        appmod._SETTINGS = dataclasses.replace(
            original, domino_host="https://d.example", domino_token="a-real-secret")
        r = client.get("/api/settings")
        assert r.status_code == 200
        body = r.json()
        assert body["domino_host"] == "https://d.example"
        assert body["domino_token"] is True
        assert "a-real-secret" not in r.text
    finally:
        appmod._SETTINGS = original


def test_put_settings_persists_and_is_read_back(tmp_path):
    client, appmod = _client()
    original_settings, original_home = appmod._SETTINGS, appmod._SAGE_HOME
    try:
        appmod._SAGE_HOME = tmp_path
        r = client.put("/api/settings", json={"domino_host": "https://saved.example"})
        assert r.status_code == 200
        assert r.json()["domino_host"] == "https://saved.example"
        assert r.json()["restartRequired"] is True
        assert (tmp_path / "settings.json").is_file()

        r2 = client.get("/api/settings")
        assert r2.json()["domino_host"] == "https://saved.example"
    finally:
        appmod._SETTINGS = original_settings
        appmod._SAGE_HOME = original_home


def test_put_settings_refuses_an_unknown_field():
    client, appmod = _client()
    original = appmod._SETTINGS
    try:
        r = client.put("/api/settings", json={"not_a_real_field": "x"})
        assert r.status_code == 400
    finally:
        appmod._SETTINGS = original


def test_put_settings_refuses_a_non_object_body():
    client, _ = _client()
    r = client.put("/api/settings", json=["not", "an", "object"])
    assert r.status_code == 400


def test_test_settings_refuses_with_no_host_at_all():
    client, appmod = _client()
    original = appmod._SETTINGS
    try:
        import dataclasses

        appmod._SETTINGS = dataclasses.replace(original, domino_host="", domino_token="")
        r = client.post("/api/settings/test", json={})
        assert r.status_code == 400
        assert r.json()["ok"] is False
    finally:
        appmod._SETTINGS = original


def test_test_settings_reports_a_whoami_failure_without_raising(monkeypatch):
    client, appmod = _client()

    class _Boom:
        def whoami(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(appmod, "build_token_source", lambda host, token: _Boom())
    r = client.post("/api/settings/test", json={"domino_host": "https://d.example",
                                                 "domino_token": "bad"})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "error": "connection refused"}


def test_test_settings_reports_success(monkeypatch):
    client, appmod = _client()

    class _Ok:
        def whoami(self):
            from sage.provision.domino import UserRef
            return UserRef(id="u-1", name="alice")

    monkeypatch.setattr(appmod, "build_token_source", lambda host, token: _Ok())
    r = client.post("/api/settings/test", json={"domino_host": "https://d.example",
                                                 "domino_token": "good"})
    assert r.json() == {"ok": True, "id": "u-1", "name": "alice"}


def test_test_settings_falls_back_to_the_saved_token_when_only_the_host_is_given(monkeypatch):
    client, appmod = _client()
    original = appmod._SETTINGS
    seen = {}

    class _Ok:
        def whoami(self):
            from sage.provision.domino import UserRef
            return UserRef(id="u-1", name="alice")

    def fake_build(host, token):
        seen["host"], seen["token"] = host, token
        return _Ok()

    try:
        import dataclasses

        appmod._SETTINGS = dataclasses.replace(original, domino_host="", domino_token="saved-tok")
        monkeypatch.setattr(appmod, "build_token_source", fake_build)
        r = client.post("/api/settings/test", json={"domino_host": "https://typed.example"})
        assert r.json()["ok"] is True
        assert seen == {"host": "https://typed.example", "token": "saved-tok"}
    finally:
        appmod._SETTINGS = original
