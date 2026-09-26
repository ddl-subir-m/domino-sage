"""Pure port-parsing tests for the preview supervisors (Step 3.4; uvicorn since #490)."""
from sage.preview.supervisor import parse_uvicorn_url, parse_vite_url


def test_parses_local_line():
    assert parse_vite_url("  ->  Local:   http://localhost:5173/") == "http://localhost:5173"
    assert parse_vite_url("  Local:   http://127.0.0.1:5199/") == "http://127.0.0.1:5199"


def test_ignores_network_and_noise():
    assert parse_vite_url("  Network: http://10.0.0.2:5173/") is None
    assert parse_vite_url("VITE v8 ready in 76 ms") is None


def test_parses_uvicorns_running_line():
    line = "INFO:     Uvicorn running on http://127.0.0.1:5173 (Press CTRL+C to quit)"
    assert parse_uvicorn_url(line) == "http://127.0.0.1:5173"
    assert parse_uvicorn_url("INFO:     Started reloader process [4242] using WatchFiles") is None
    assert parse_uvicorn_url("INFO:     Application startup complete.") is None
    # The two parsers do not read each other's line: a supervisor keyed on the wrong one never
    # becomes ready, and start() reports a timeout instead of a preview.
    assert parse_vite_url(line) is None


# --- Which port Vite is asked for -------------------------------------------------------------
#
# Two Sage instances on one machine reap each other's dev server, because `_clear_stale_port` kills
# whatever is LISTENING on the port before every spawn. The override is what lets a worktree under
# QA sit beside the checkout it is compared against.
import subprocess
import threading

import pytest

from sage.preview.supervisor import ViteSupervisor, preview_port


def test_defaults_to_vites_own_port(monkeypatch):
    monkeypatch.delenv("SAGE_PREVIEW_PORT", raising=False)
    assert preview_port() == 5173


def test_env_overrides_the_port(monkeypatch):
    monkeypatch.setenv("SAGE_PREVIEW_PORT", "5399")
    assert preview_port() == 5399


def test_blank_and_padded_values(monkeypatch):
    monkeypatch.setenv("SAGE_PREVIEW_PORT", "  5400  ")
    assert preview_port() == 5400
    monkeypatch.setenv("SAGE_PREVIEW_PORT", "   ")
    assert preview_port() == 5173


def test_a_typo_falls_back_rather_than_raising(monkeypatch):
    """A bad value must not be the reason a build session cannot open its preview."""
    monkeypatch.setenv("SAGE_PREVIEW_PORT", "off")
    assert preview_port() == 5173


def test_the_port_reaches_vite_on_the_command_line(monkeypatch, tmp_path):
    """The override is worthless if Vite is never told: npm forwards it after `--`."""
    monkeypatch.setenv("SAGE_PREVIEW_PORT", "5401")
    seen = {}
    (tmp_path / "node_modules" / ".bin").mkdir(parents=True)
    (tmp_path / "node_modules" / ".bin" / "vite").write_text("#!/bin/sh")

    def fake_popen(argv, **kw):
        seen["argv"] = argv
        return type("P", (), {"stdout": None, "pid": 1, "wait": lambda self: 0})()

    monkeypatch.setattr(ViteSupervisor, "_clear_stale_port", lambda self, port: seen.setdefault("reaped", port))
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())

    ViteSupervisor(tmp_path)._spawn()

    assert seen["argv"] == ["npm", "run", "dev", "--", "--port", "5401"]
    # and the reaping is aimed at the port we actually asked for, not at 5173
    assert seen["reaped"] == 5401


def test_vite_supervisor_does_not_spawn_without_the_vite_binary(monkeypatch, tmp_path):
    """`npm run dev` with no `.bin/vite` exits 127; name the missing binary instead of spawning."""
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("// app")
    spawned: list[object] = []

    def fake_popen(*_a, **_kw):
        spawned.append(1)
        return type("P", (), {
            "stdout": None, "pid": 1,
            "poll": lambda self: 0, "wait": lambda self: 0,
        })()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ViteSupervisor, "_clear_stale_port", lambda self, port: None)

    with pytest.raises(RuntimeError) as e:
        ViteSupervisor(tmp_path).start(ready_timeout_s=0.1)

    assert spawned == []
    assert "node_modules/.bin/vite" in str(e.value)


def test_a_no_build_app_is_served_by_its_own_uvicorn(monkeypatch, tmp_path):
    """A fastapi-antd app's preview is the app's own server, run by the interpreter running Sage —
    the one here known to carry fastapi and uvicorn — with reload and `SAGE_PREVIEW=1`, which the
    page's shim stamps so the helpers reach the builder (#490). It serves at the root, so the proxy
    prepends nothing."""
    import sys

    from sage.preview.supervisor import UvicornSupervisor, make_supervisor

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

    (tmp_path / ".sage").mkdir()
    (tmp_path / ".sage" / "settings.json").write_text('{"stack": "fastapi-antd"}')
    sup = make_supervisor(tmp_path, "/u/o/p/notebookSession/r")
    assert isinstance(sup, UvicornSupervisor)
    assert sup.mount_base() == ""
    sup._spawn()

    assert seen["argv"][:4] == [sys.executable, "-m", "uvicorn", "app:app"]
    assert seen["argv"][seen["argv"].index("--port") + 1] == "5402"
    assert "--reload" in seen["argv"]
    assert seen["env"]["SAGE_PREVIEW"] == "1"
    assert "SAGE_BASE_PREFIX" not in seen["env"], "nothing on that stack reads it"
    assert seen["cwd"] == tmp_path
    assert seen["reaped"] == 5402


def test_an_app_with_no_record_keeps_its_vite_preview(tmp_path):
    from sage.preview.supervisor import UvicornSupervisor, make_supervisor

    sup = make_supervisor(tmp_path, "/p")
    assert isinstance(sup, ViteSupervisor) and not isinstance(sup, UvicornSupervisor)
    assert sup.mount_base() == "/p/preview"
