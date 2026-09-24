"""Pure port-parsing tests for the preview supervisor (Step 3.4; uvicorn-only since #490)."""
from sage.preview.supervisor import parse_uvicorn_url


def test_parses_uvicorns_running_line():
    line = "INFO:     Uvicorn running on http://127.0.0.1:5173 (Press CTRL+C to quit)"
    assert parse_uvicorn_url(line) == "http://127.0.0.1:5173"
    assert parse_uvicorn_url("INFO:     Started reloader process [4242] using WatchFiles") is None
    assert parse_uvicorn_url("INFO:     Application startup complete.") is None


# --- Which port the app's own uvicorn is asked for ----------------------------------------------
#
# One ephemeral, OS-assigned port per spawn (ONE-APP-PLAN.md §2.4: many projects, many previews, in
# one process) — no fixed `SAGE_PREVIEW_PORT` override and no reaper for whatever used to squat on
# a known port, both retired with the single-preview-per-process world they belonged to.
import subprocess
import sys
import threading

from sage.preview.supervisor import UvicornSupervisor


def test_a_no_build_app_is_served_by_its_own_uvicorn(monkeypatch, tmp_path):
    """A fastapi-antd app's preview is the app's own server, run by the interpreter running Sage —
    the one here known to carry fastapi and uvicorn — with reload and `SAGE_PREVIEW=1`, which the
    page's shim stamps so the helpers reach the builder (#490). It serves at the root, so the proxy
    prepends nothing."""
    seen = {}

    def fake_popen(argv, **kw):
        seen["argv"] = argv
        seen["env"] = kw["env"]
        seen["cwd"] = kw["cwd"]
        return type("P", (), {"stdout": None, "pid": 1, "wait": lambda self: 0})()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())

    sup = UvicornSupervisor(tmp_path)
    assert sup.mount_base() == ""
    sup._spawn()

    assert seen["argv"][:4] == [sys.executable, "-m", "uvicorn", "app:app"]
    port = seen["argv"][seen["argv"].index("--port") + 1]
    assert port.isdigit() and int(port) > 0
    assert "--reload" in seen["argv"]
    assert seen["env"]["SAGE_PREVIEW"] == "1"
    assert seen["cwd"] == tmp_path
    # No token source given: nothing platform-shaped is added on top of the inherited environment.
    assert sup._platform_env() == {}


class _FakeTokenSource:
    def __init__(self, kind: str, host: str = "https://dogfood.domino.tech", tok: str = "tok-123"):
        self.kind = kind
        self.api_host = host
        self._tok = tok

    def bearer(self) -> str:
        return self._tok


def test_a_static_token_source_hands_the_child_a_token_and_host(monkeypatch, tmp_path):
    """A laptop preview has no sidecar at all — `sage_domino.py`'s `token()` reads `SAGE_DOMINO_TOKEN`
    instead, and needs `DOMINO_API_HOST` set explicitly since a laptop shell never has it."""
    seen = {}
    monkeypatch.setattr(subprocess, "Popen",
                         lambda argv, **kw: (seen.update(env=kw["env"]) or
                                             type("P", (), {"stdout": None, "pid": 1,
                                                            "wait": lambda self: 0})()))
    monkeypatch.setattr(threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())

    sup = UvicornSupervisor(tmp_path, token_source=_FakeTokenSource("static"))
    sup._spawn()

    assert seen["env"]["DOMINO_API_HOST"] == "https://dogfood.domino.tech"
    assert seen["env"]["SAGE_DOMINO_TOKEN"] == "tok-123"


def test_a_sidecar_token_source_hands_the_child_the_host_but_not_a_token(monkeypatch, tmp_path):
    """A real workspace/App already has a sidecar reachable at localhost:8899 inside the same
    container — nothing needs a token minted into its environment, only the host."""
    seen = {}
    monkeypatch.setattr(subprocess, "Popen",
                         lambda argv, **kw: (seen.update(env=kw["env"]) or
                                             type("P", (), {"stdout": None, "pid": 1,
                                                            "wait": lambda self: 0})()))
    monkeypatch.setattr(threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())

    sup = UvicornSupervisor(tmp_path, token_source=_FakeTokenSource("sidecar"))
    sup._spawn()

    assert seen["env"]["DOMINO_API_HOST"] == "https://dogfood.domino.tech"
    assert "SAGE_DOMINO_TOKEN" not in seen["env"]
