"""_resolve_git_target: how the hub picks the git host to provision against (Phase 4 cleanup).

The point of the change: an explicit SAGE_GIT_HOST lets the hub run as a plain baked App with no
git-based project of its own; sniffing an origin remote is only the legacy fallback.
"""
from __future__ import annotations

import pytest

from sage.hub import app
from sage.provision import credentials


def test_explicit_host_wins_without_touching_any_checkout(monkeypatch):
    monkeypatch.setattr(app, "_GIT_HOST", "github.com")
    # Point the fallback at a bogus path to prove it's never consulted.
    monkeypatch.setattr(app, "_GIT_CWD", "/does/not/exist")
    monkeypatch.setattr(
        app.credentials, "remote_for", lambda cwd: pytest.fail("origin sniff must not run")
    )
    assert app._resolve_git_target() == ("github.com", "github")


def test_falls_back_to_origin_sniff_when_host_unset(monkeypatch):
    monkeypatch.setattr(app, "_GIT_HOST", None)
    monkeypatch.setattr(
        app.credentials,
        "remote_for",
        lambda cwd: credentials.GitRemote("github", "github.com", "owner", "https"),
    )
    assert app._resolve_git_target() == ("github.com", "github")


def test_errors_when_neither_host_nor_origin_available(monkeypatch):
    monkeypatch.setattr(app, "_GIT_HOST", None)
    monkeypatch.setattr(app.credentials, "remote_for", lambda cwd: None)
    with pytest.raises(RuntimeError, match="SAGE_GIT_HOST"):
        app._resolve_git_target()
