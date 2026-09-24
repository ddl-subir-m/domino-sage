"""`sage_domino.py`'s `token()`, out of the seeded template (ONE-APP-PLAN.md §2.4).

A laptop preview has no sidecar at `localhost:8899` at all — `UvicornSupervisor` sets
`SAGE_DOMINO_TOKEN` in the child's environment from its own `TokenSource` before spawning it
(`test_supervisor_parse.py` covers that half). This is the other half: the app-side reader has to
actually prefer it, and must not reach for the sidecar at all when it is set, since there may be
none reachable to reach.

Loaded by path out of the real template, the way `test_a_no_build_app_serves_from_static_files.py`
already loads `sage_serve.py` — so this is the file that actually ships, not a description of it.
"""
from __future__ import annotations

import importlib.util
import sys
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO / "template" / "fastapi-antd"


@pytest.fixture
def sage_domino(tmp_path: Path, monkeypatch):
    for stale in ("sage_queries", "sage_domino"):
        sys.modules.pop(stale, None)
    # sage_domino.py imports sage_queries.py by relative sys.path insertion off its own file's
    # parent — the real template directory, not tmp_path, since this loads the shipped file in place.
    spec = importlib.util.spec_from_file_location("sage_domino", TEMPLATE_DIR / "sage_domino.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.delenv("SAGE_DOMINO_TOKEN", raising=False)
    yield mod
    for stale in ("sage_queries", "sage_domino"):
        sys.modules.pop(stale, None)


def test_the_override_is_used_and_the_sidecar_is_never_asked(sage_domino, monkeypatch):
    monkeypatch.setenv("SAGE_DOMINO_TOKEN", "laptop-pat-1")

    def _no_sidecar(*a, **kw):
        raise AssertionError("the sidecar must not be reached when SAGE_DOMINO_TOKEN is set")

    monkeypatch.setattr(urllib.request, "urlopen", _no_sidecar)

    assert sage_domino.token() == "laptop-pat-1"


def test_a_bearer_prefixed_override_is_stripped_the_same_as_the_sidecars_own_answer(sage_domino, monkeypatch):
    monkeypatch.setenv("SAGE_DOMINO_TOKEN", "Bearer laptop-pat-1")
    assert sage_domino.token() == "laptop-pat-1"


def test_blank_falls_back_to_the_sidecar(sage_domino, monkeypatch):
    monkeypatch.setenv("SAGE_DOMINO_TOKEN", "   ")
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"sidecar-jwt-1"

    def fake_urlopen(url, timeout=5):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert sage_domino.token() == "sidecar-jwt-1"
    assert seen["url"] == sage_domino.sq.sidecar_url()


def test_with_no_override_at_all_the_sidecar_is_asked(sage_domino, monkeypatch):
    monkeypatch.delenv("SAGE_DOMINO_TOKEN", raising=False)
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"Bearer sidecar-jwt-2"

    def fake_urlopen(url, timeout=5):
        seen["called"] = True
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert sage_domino.token() == "sidecar-jwt-2"
    assert seen.get("called") is True
