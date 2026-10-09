"""OpenCode lets go of a directory Sage has stopped using (#742).

OpenCode builds an instance for each directory it is asked about (each Built App, the Chat work
dir) and keeps it until it is disposed. Measured 2026-10-09 against the pinned 1.18.4: about 80 MB
per directory, and nothing released it, so a workspace that touched many apps grew until it ran out
of memory. Three rules now hold:

- A directory Sage has not used for `_OPENCODE_IDLE_S` is disposed, and at most `_OPENCODE_CAP`
  stay live; the least recently used go first.
- Never while a turn runs: the sweep only takes the turn lock without waiting, so a turn running
  anywhere skips it. Never while OpenCode says a session there is still running, either.
- A request to a directory being disposed waits for the dispose, then reaches a fresh instance.
"""
from __future__ import annotations

import threading
import time

import pytest

from sage.driver.opencode import OpenCodeClient
from sage.orchestrator import service as svc

from .fake_opencode import FakeOpenCode, Turn, execution_plan
from .test_an_approved_plan_runs_as_implement import _build

IDLE = svc._OPENCODE_IDLE_S
CAP = svc._OPENCODE_CAP
PLAN = Turn(text=execution_plan())


def _turn_in_selected_app(orch, oc) -> str:
    list(orch.build_stream("build me a dashboard"))
    return oc.sessions[-1]["directory"]


# --- the orchestrator's sweep --------------------------------------------------------------------

def test_a_directory_left_idle_is_released_and_the_one_used_after_it_is_not(tmp_path):
    orch, oc = _build(tmp_path, [PLAN, PLAN])
    a = _turn_in_selected_app(orch, oc)
    orch.create_app()
    b = _turn_in_selected_app(orch, oc)
    assert a != b and set(oc.instances()) == {a, b}

    released = orch._reap_opencode(now=oc.instances()[a] + IDLE)

    assert released == [a] and oc.disposed == [a]
    assert set(oc.instances()) == {b}


def test_a_directory_inside_its_idle_window_is_kept(tmp_path):
    orch, oc = _build(tmp_path, [PLAN])
    a = _turn_in_selected_app(orch, oc)

    assert orch._reap_opencode(now=oc.instances()[a] + IDLE - 1) == []
    assert oc.disposed == []


class _SweepsMidTurn(FakeOpenCode):
    """Runs the sweep from inside the turn, at the moment the prompt is sent."""

    orch = None
    swept: list | None = None

    def send_prompt(self, session_id, text, *args, **kwargs):
        self.swept = self.orch._reap_opencode(now=time.monotonic() + 10 * IDLE)
        return super().send_prompt(session_id, text, *args, **kwargs)


def test_a_turn_running_in_the_directory_is_never_released(tmp_path):
    orch, _ = _build(tmp_path, [])
    oc = _SweepsMidTurn(orch._oc_client.workspace, [PLAN])
    oc.orch = orch
    orch._oc_client = oc

    list(orch.build_stream("build me a dashboard"))

    assert oc.swept == [] and oc.disposed == []
    assert oc.sessions[-1]["directory"] in oc.instances()


def test_beyond_the_cap_the_least_recently_used_go_first(tmp_path):
    orch, oc = _build(tmp_path, [])
    dirs = [str(tmp_path / f"d{i}") for i in range(CAP + 2)]
    for d in dirs:
        oc.create_session(d)

    released = orch._reap_opencode(now=oc.instances()[dirs[-1]])

    assert released == dirs[:2]
    assert set(oc.instances()) == set(dirs[2:])


def test_the_sweep_releases_an_idle_directory_with_no_request_arriving(tmp_path, monkeypatch):
    orch, oc = _build(tmp_path, [])
    oc.create_session(str(tmp_path / "a"))
    monkeypatch.setattr(svc, "_OPENCODE_IDLE_S", 0)

    orch.start_preview_reaper(interval_s=0.01)
    try:
        deadline = time.monotonic() + 5
        while not oc.disposed and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        orch.stop_preview_reaper()
    assert oc.disposed == [str(tmp_path / "a")]


# --- the client: OpenCode's own word, and the gate -----------------------------------------------

class _Resp:
    def __init__(self, body=None):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


@pytest.fixture
def wire(monkeypatch):
    """The real client against a scripted wire: `calls` records each request in order."""
    calls: list[tuple[str, str]] = []
    state = {"status": {}, "dispose_entered": threading.Event(), "dispose_go": None}

    def post(url, json=None, params=None, timeout=None, **_):
        if url.endswith("/instance/dispose"):
            state["dispose_entered"].set()
            if state["dispose_go"] is not None:
                assert state["dispose_go"].wait(5)
            calls.append(("dispose", params["directory"]))
            return _Resp(True)
        calls.append(("create", json["location"]["directory"]))
        return _Resp({"id": f"s{len(calls)}"})

    def get(url, params=None, timeout=None, **_):
        calls.append(("status", (params or {}).get("directory")))
        return _Resp(state["status"])

    monkeypatch.setattr("sage.driver.opencode.httpx.post", post)
    monkeypatch.setattr("sage.driver.opencode.httpx.get", get)
    monkeypatch.setattr("sage.driver.opencode.httpx.patch", lambda *a, **k: _Resp({}))
    return calls, state


def test_every_directory_a_request_names_is_recorded_with_its_last_use(wire):
    client = OpenCodeClient("http://x")
    sid = client.create_session("/w/a")
    first = client.instances()["/w/a"]

    client.is_running(sid)

    assert set(client.instances()) == {"/w/a"} and client.instances()["/w/a"] > first


def test_a_session_opencode_says_is_still_running_keeps_its_directory(wire):
    calls, state = wire
    client = OpenCodeClient("http://x")
    client.create_session("/w/a")
    state["status"] = {"s1": {"type": "busy"}}

    assert client.release_instance("/w/a") is False
    assert ("dispose", "/w/a") not in calls and "/w/a" in client.instances()

    state["status"] = {"s1": {"type": "idle"}}
    assert client.release_instance("/w/a") is True
    assert calls[-2:] == [("status", "/w/a"), ("dispose", "/w/a")]
    assert client.instances() == {}


def test_a_request_to_a_directory_being_released_waits_for_the_dispose(wire):
    calls, state = wire
    client = OpenCodeClient("http://x")
    client.create_session("/w/a")
    state["dispose_go"] = threading.Event()
    results: dict = {}

    releaser = threading.Thread(target=lambda: results.update(r=client.release_instance("/w/a")))
    releaser.start()
    creator = None
    try:
        assert state["dispose_entered"].wait(5)
        creator = threading.Thread(target=lambda: results.update(s=client.create_session("/w/a")))
        creator.start()
        creator.join(0.3)
        assert creator.is_alive(), "a request reached the directory while it was being disposed"
    finally:
        state["dispose_go"].set()
        releaser.join(5)
        if creator is not None:
            creator.join(5)

    assert results["r"] is True and "s" in results
    assert calls[-2:] == [("dispose", "/w/a"), ("create", "/w/a")]
    assert "/w/a" in client.instances()


@pytest.mark.parametrize("probe", ["opencode_mcp_status", "opencode_skill_status"])
def test_a_diagnostic_probe_that_names_a_directory_records_it(tmp_path, monkeypatch, probe):
    """The probes ask OpenCode over their own HTTP call, and that loads the instance just the same."""
    orch, oc = _build(tmp_path, [])
    class _Server:
        def url(self):
            return "http://oc"

    class _Ok(_Resp):
        status_code = 200

    orch._oc_server = _Server()
    monkeypatch.setattr(svc.httpx, "get", lambda *a, **k: _Ok({}))

    getattr(orch, probe)(str(tmp_path / "chat-work"))

    assert str(tmp_path / "chat-work") in oc.instances()
