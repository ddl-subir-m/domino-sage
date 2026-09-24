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
    prepends nothing.

    No Domino credential rides in this env, deliberately: this process runs the agent's own
    generated code (`app:app`, `--reload`), and a real token here would be a token the agent's code
    can read directly (`os.environ`), which ONE-APP-PLAN.md §2.8 rules out ("the agent never holds a
    Domino token"). The preview's `/api/domino/*` and `/api/queries/*` are answered by Sage's own
    proxy IN THE ORCHESTRATOR PROCESS instead (`app.py`'s `_preview_platform`), never by this child.
    """
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
    assert "SAGE_DOMINO_TOKEN" not in seen["env"]
