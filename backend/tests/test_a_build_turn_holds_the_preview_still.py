"""A Build turn holds the preview still, and its check sees the code it restarted (#752).

Live, Signal Room 24 on `e10172cb` (#714): the fastapi-antd preview runs `uvicorn --reload`, so every
`.py` write a Build made restarted the server under the person watching, and a build writes
`app.py` many times. The same build then ended "Code checks passed. The app wasn't run — the code
changed while the page was being checked."

Two mechanisms, one per symptom:

  * the reloader restarted on each intermediate write. A Build turn now holds it: a change seen while
    held restarts nothing and is remembered, so the server restarts once when the turn lets go —
    unless the end-of-turn check restarted it deliberately in the meantime;
  * the check pinned the code tree, THEN restarted the preview, and that restart writes into the app
    (`sage_keys.json`, the names a viewer may set, rewritten whenever the app's `secret()` calls
    changed — which a build wiring an MCP server does). The tree then differed from its own pin.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

from sage.preview import reload_gate
from sage.preview.supervisor import UvicornSupervisor

from . import test_changed_page_validation as page_tests
from .test_preview_reload_status import _APP, _fixture, _wait

build = page_tests.build
run = page_tests.run


def _acknowledge(orch):
    return lambda event: orch.record_preview_ack(event["validationId"])


# ---- the check sees the tree its own restart left -----------------------------------------------

class WritingPreview(page_tests.Preview):
    """A restart that writes a Sage-owned file into the app, as `UvicornSupervisor._spawn` does."""

    def __init__(self, app, path: Path):
        super().__init__(app)
        self.path = path

    def retry_start(self, *, explicit=False):
        (self.path / "sage_keys.json").write_text(
            json.dumps([{"name": "TAVILY_API_KEY", "note": ""}]) + "\n")
        return super().retry_start(explicit=explicit)


def test_a_file_the_checks_own_restart_writes_is_not_a_code_change(build):
    orch, project, _ = build
    project.supervisor = WritingPreview(project.workspace.app_id, project.workspace.path)
    _, done = run(orch, report=_acknowledge(orch))
    assert done["verification"].get("reason") != "the code changed while the page was being checked"
    assert done["verification"]["stages"]["runtime"] == "passed", done["verification"]


# ---- a Build turn holds the preview's own restarts ----------------------------------------------

class HeldPreview(page_tests.Preview):
    def __init__(self, app):
        super().__init__(app)
        self.held = False
        self.calls: list[str] = []

    def hold_reloads(self):
        self.held = True
        self.calls.append("hold")

    def release_reloads(self):
        self.held = False
        self.calls.append("release")

    def retry_start(self, *, explicit=False):
        self.calls.append("restart while held" if self.held else "restart")
        return super().retry_start(explicit=explicit)


def test_a_build_turn_holds_reloads_while_it_writes_and_lets_go_after_its_check(build):
    orch, project, oc = build
    preview = project.supervisor = HeldPreview(project.workspace.app_id)
    held_at_send = []
    send_prompt = oc.send_prompt

    def sending(*args, **kwargs):
        held_at_send.append(preview.held)
        return send_prompt(*args, **kwargs)

    oc.send_prompt = sending
    _, done = run(orch, report=_acknowledge(orch))
    assert done["verification"]["stages"]["runtime"] == "passed"
    assert held_at_send and all(held_at_send), "the agent wrote while the preview could restart"
    assert preview.calls == ["hold", "restart while held", "release"]
    assert orch._held_preview is None


# ---- the gate: what the reloader does while held -----------------------------------------------

def test_the_gate_keeps_changes_back_while_held_and_restarts_once_after():
    held = [True]
    seen = iter([["app.py"], None, ["app.py"], None, None, None])
    gated = reload_gate.gate(lambda reloader: next(seen), lambda: held[0])
    assert [gated(None), gated(None), gated(None)] == [None, None, None]
    held[0] = False
    assert gated(None) == ["app.py"], "the change made while held restarts once on release"
    assert gated(None) is None, "and only once"


def test_a_change_after_release_restarts_as_it_always_did():
    gated = reload_gate.gate(lambda reloader: ["app.py"], lambda: False)
    assert gated(None) == ["app.py"]


def test_the_preview_runs_uvicorn_through_the_gate_with_its_hold(monkeypatch, tmp_path):
    import subprocess
    import threading

    monkeypatch.setenv("SAGE_PREVIEW_PORT", "5402")
    seen = {}

    def fake_popen(argv, **kw):
        seen["argv"], seen["env"] = argv, kw["env"]
        return type("P", (), {"stdout": None, "pid": 1, "wait": lambda self: 0})()

    monkeypatch.setattr(UvicornSupervisor, "_clear_stale_port", lambda self, port: None)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())
    _fixture(tmp_path)
    sup = UvicornSupervisor(tmp_path, pinned_port=True)
    sup._spawn()

    argv = seen["argv"]
    assert argv[:3] == [sys.executable, "-c", Path(reload_gate.__file__).read_text(encoding="utf-8")]
    assert argv[3] == "app:app" and "--reload" in argv
    hold = Path(seen["env"][reload_gate.HOLD_ENV])
    assert not hold.exists()
    try:
        sup.hold_reloads()
        assert hold.exists()
    finally:
        sup.release_reloads()
    assert not hold.exists()


# ---- the real reloader: restarts per Build turn ------------------------------------------------

def _reloads(sup) -> int:
    return sum("Reloading..." in line for line in sup.recent_output(40))


def test_a_held_uvicorn_restarts_once_after_many_writes(tmp_path, monkeypatch):
    """The measured symptom. Three `app.py` writes inside a hold restart nothing; letting go
    restarts once. Opens one uvicorn process group, closed in `finally` (stopped, then waited)."""
    monkeypatch.delenv("SAGE_PREVIEW_PORT", raising=False)
    _fixture(tmp_path)
    sup = UvicornSupervisor(tmp_path)
    proc = None
    try:
        url = sup.start(ready_timeout_s=12)
        proc = sup._proc
        generation = sup.status()["generation"]
        time.sleep(0.6)  # StatReload takes its baseline on its first poll after the child starts
        sup.hold_reloads()
        for n in range(3):
            (tmp_path / "app.py").write_text(f"# edit {n}\n" + _APP)
            os.utime(tmp_path / "app.py", (time.time() + n + 1,) * 2)
            time.sleep(0.6)
        assert _reloads(sup) == 0, sup.recent_output()
        assert sup.status()["generation"] == generation
        assert httpx.get(url, timeout=1).status_code == 200, "the held server kept serving"
        sup.release_reloads()
        _wait(lambda: _reloads(sup) == 1 and sup.status()["state"] == "ready")
        time.sleep(0.6)
        assert _reloads(sup) == 1, sup.recent_output()
    finally:
        sup.release_reloads()
        sup.stop()
        if proc is not None:
            proc.wait(timeout=10)
            _wait(lambda: proc.stdout.closed, timeout=5)
