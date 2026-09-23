"""Pure port-parsing tests for the preview supervisor (Step 3.4; uvicorn-only since #490)."""
from sage.preview.supervisor import parse_uvicorn_url


def test_parses_uvicorns_running_line():
    line = "INFO:     Uvicorn running on http://127.0.0.1:5173 (Press CTRL+C to quit)"
    assert parse_uvicorn_url(line) == "http://127.0.0.1:5173"
    assert parse_uvicorn_url("INFO:     Started reloader process [4242] using WatchFiles") is None
    assert parse_uvicorn_url("INFO:     Application startup complete.") is None


# --- Which port the app's own uvicorn is asked for ---------------------------------------------
#
# Two Sage instances on one machine reap each other's dev server, because `_clear_stale_port` kills
# whatever is LISTENING on the port before every spawn. The override is what lets a worktree under
# QA sit beside the checkout it is compared against.
import subprocess
import sys
import threading

from sage.preview.supervisor import UvicornSupervisor


def test_env_overrides_the_port(monkeypatch, tmp_path):
    monkeypatch.setenv("SAGE_PREVIEW_PORT", "5399")
    assert UvicornSupervisor(tmp_path)._env_port() == 5399


def test_blank_and_padded_values(monkeypatch, tmp_path):
    monkeypatch.setenv("SAGE_PREVIEW_PORT", "  5400  ")
    assert UvicornSupervisor(tmp_path)._env_port() == 5400


def test_a_typo_falls_back_to_a_free_port_rather_than_raising(monkeypatch, tmp_path):
    """A bad value must not be the reason a build session cannot open its preview."""
    monkeypatch.setenv("SAGE_PREVIEW_PORT", "off")
    port = UvicornSupervisor(tmp_path)._env_port()
    assert isinstance(port, int) and port > 0


def test_a_no_build_app_is_served_by_its_own_uvicorn(monkeypatch, tmp_path):
    """A fastapi-antd app's preview is the app's own server, run by the interpreter running Sage —
    the one here known to carry fastapi and uvicorn — with reload and `SAGE_PREVIEW=1`, which the
    page's shim stamps so the helpers reach the builder (#490). It serves at the root, so the proxy
    prepends nothing."""
    monkeypatch.setenv("SAGE_PREVIEW_PORT", "5402")
    seen = {}

    def fake_popen(argv, **kw):
        seen["argv"] = argv
        seen["env"] = kw["env"]
        seen["cwd"] = kw["cwd"]
        return type("P", (), {"stdout": None, "pid": 1, "wait": lambda self: 0})()

    monkeypatch.setattr(UvicornSupervisor, "_clear_stale_port", lambda self, port: seen.setdefault("reaped", port))
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())

    sup = UvicornSupervisor(tmp_path)
    assert sup.mount_base() == ""
    sup._spawn()

    assert seen["argv"][:4] == [sys.executable, "-m", "uvicorn", "app:app"]
    assert seen["argv"][seen["argv"].index("--port") + 1] == "5402"
    assert "--reload" in seen["argv"]
    assert seen["env"]["SAGE_PREVIEW"] == "1"
    assert seen["cwd"] == tmp_path
    assert seen["reaped"] == 5402
