"""Pure port-parsing tests for the Vite supervisor (Step 3.4)."""
from sage.preview.supervisor import parse_vite_url


def test_parses_local_line():
    assert parse_vite_url("  ->  Local:   http://localhost:5173/") == "http://localhost:5173"
    assert parse_vite_url("  Local:   http://127.0.0.1:5199/") == "http://127.0.0.1:5199"


def test_ignores_network_and_noise():
    assert parse_vite_url("  Network: http://10.0.0.2:5173/") is None
    assert parse_vite_url("VITE v8 ready in 76 ms") is None


# --- Which port Vite is asked for -------------------------------------------------------------
#
# Two Sage instances on one machine reap each other's dev server, because `_clear_stale_port` kills
# whatever is LISTENING on the port before every spawn. The override is what lets a worktree under
# QA sit beside the checkout it is compared against.
import subprocess
import threading

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
