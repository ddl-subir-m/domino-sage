"""/api/diag asks OPENCODE which skills it holds, once per directory that matters.

`_install_opencode_skills` logs what it copied into `~/.config/opencode/skills/`, and that line can
be true while no turn is ever offered the skill: the slot is shared by every checkout on the
machine, and skill discovery is per directory — `Skill.discovery` takes `(directory, worktree)` and
walks up from the directory. So the question is asked three times: of the server's own instance as
a control, of the `.sage/chat-work` a Chat turn runs in, and of the `apps/<appId>` a Build turn
runs in. This is the ADR-0041 lesson one slot over — every surface read healthy while the model's
list was empty, and only asking the thing itself could say otherwise.

Run against a real socket rather than a stubbed `httpx.get`, for the reason the MCP sibling gives:
the thing being checked IS the round trip, and a stub would pass a wrong URL.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from sage.orchestrator import app as app_module

CHAT_WORK = ".sage/chat-work"


def _opencode(by_directory: dict[str, object], status: int = 200):
    """A stand-in `opencode serve`, answering `GET /skill?directory=` the way 1.18.4 does.

    Keyed by directory on purpose: a server that answers the same list whatever it is asked is the
    one shape this page must not be built on.
    """
    class _H(BaseHTTPRequestHandler):
        def do_GET(self):                       # BaseHTTPRequestHandler's own spelling
            path, _, query = self.path.partition("?")
            asked = (urllib.parse.parse_qs(query).get("directory") or [""])[0]
            payload = by_directory.get(asked, by_directory.get("", []))
            if path != "/skill" or payload is None:
                self.send_response(404 if path != "/skill" else status)
                self.end_headers()
                return
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class _Handle:
    """The bit of OpenCodeServer this reads: the URL it reported when it came up."""

    def __init__(self, url: str | None) -> None:
        self._url = url

    def url(self) -> str:
        if self._url is None:
            raise RuntimeError("opencode server not ready")
        return self._url


def _skill(name: str, description: str | None = "Does a thing.") -> dict:
    out = {"name": name, "location": f"/root/skills/{name}/SKILL.md", "content": "# body"}
    if description is not None:
        out["description"] = description
    return out


@pytest.fixture
def workspace(tmp_path, monkeypatch) -> tuple[str, str]:
    """A Chat workdir and a built app, as the two helpers on the page find them."""
    work = tmp_path / "workspace" / CHAT_WORK
    work.mkdir(parents=True)
    built = tmp_path / "workspace" / "apps" / "app_1"
    built.mkdir(parents=True)
    monkeypatch.setattr(app_module.orchestrator, "_wm",
                        SimpleNamespace(_dir=str(tmp_path / "workspace")), raising=False)
    monkeypatch.setattr(app_module.orchestrator, "_project",
                        SimpleNamespace(app_for_turn=lambda: SimpleNamespace(path=built),
                                        model_calls=0, tool_call_responses=0,
                                        last_gateway_error=None, session_id=None),
                        raising=False)
    return str(work), str(built)


def _diag(tmp_path: Path, monkeypatch, oc_server: object) -> dict:
    cfg = tmp_path / ".config" / "opencode"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "opencode.json").write_text(json.dumps({"mcp": {}}))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(app_module.orchestrator, "_oc_server", oc_server, raising=False)
    r = TestClient(app_module.control_app).get("/api/diag")
    assert r.status_code == 200
    return r.json()["skills"]


def test_a_chat_turn_gets_the_seeded_skill(tmp_path, monkeypatch, workspace):
    """The healthy reading. It is worth pinning because the unhealthy ones below are only legible
    beside it: the same list, asked of a different directory, is what carries the finding."""
    work, built = workspace
    srv = _opencode({"": [_skill("investigate-weak-signals")],
                     work: [_skill("investigate-weak-signals")],
                     built: [_skill("investigate-weak-signals"), _skill("data-table")]})
    try:
        out = _diag(tmp_path, monkeypatch, _Handle(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert out["opencode_says_chat_work"]["directory"] == work
    assert out["opencode_says_chat_work"]["skills"] == ["investigate-weak-signals"]


def test_the_built_apps_directory_is_asked_the_same_question(tmp_path, monkeypatch, workspace):
    """Build turns run in `apps/<appId>`, not in the Chat workdir, and `data-table` reaching one
    says nothing about the other. Two directories, two answers, both on the page."""
    work, built = workspace
    srv = _opencode({"": [_skill("investigate-weak-signals")],
                     work: [_skill("investigate-weak-signals")],
                     built: [_skill("investigate-weak-signals"), _skill("data-table")]})
    try:
        out = _diag(tmp_path, monkeypatch, _Handle(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert out["opencode_says_built_app"]["directory"] == built
    assert "data-table" in out["opencode_says_built_app"]["skills"]


def test_a_directory_that_sees_nothing_is_reported_beside_a_control_that_does(tmp_path, monkeypatch,
                                                                             workspace):
    """The failure this page exists for, and the reason the control row is there. The files are on
    disk and the install logged success; the instance a Chat turn runs in is holding nothing. With
    only the bare call — the one no turn ever uses — this state reads exactly like a healthy one."""
    work, built = workspace
    srv = _opencode({"": [_skill("investigate-weak-signals")],
                     work: [],
                     built: [_skill("investigate-weak-signals")]})
    try:
        out = _diag(tmp_path, monkeypatch, _Handle(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert out["opencode_says"]["skills"] == ["investigate-weak-signals"]      # the control
    assert out["opencode_says_chat_work"]["skills"] == []                      # and the turn's
    assert out["opencode_says_built_app"]["skills"] == ["investigate-weak-signals"]


def test_a_skill_opencode_will_never_offer_is_named_rather_than_counted(tmp_path, monkeypatch,
                                                                        workspace):
    """1.18.4 loads a skill with no `description` and then filters it out of the list the model is
    shown. It is in this reply and in no prompt, so `skills` alone would report it healthy."""
    work, _built = workspace
    srv = _opencode({"": [_skill("investigate-weak-signals", description=None)],
                     work: [_skill("investigate-weak-signals", description=None)]})
    try:
        out = _diag(tmp_path, monkeypatch, _Handle(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert out["opencode_says_chat_work"]["skills"] == ["investigate-weak-signals"]
    assert out["opencode_says_chat_work"]["no_description"] == ["investigate-weak-signals"]


def test_a_directory_that_does_not_exist_yet_still_gets_a_row(tmp_path, monkeypatch):
    """No `workspace` fixture: nothing is bound, so there is no chat workdir and no built app. An
    omitted row would be indistinguishable from an ask that came back empty, and an empty chat-work
    row is the exact finding this block was added to carry."""
    monkeypatch.setattr(app_module.orchestrator, "_wm", None, raising=False)
    monkeypatch.setattr(app_module.orchestrator, "_project", None, raising=False)
    srv = _opencode({"": [_skill("investigate-weak-signals")]})
    try:
        out = _diag(tmp_path, monkeypatch, _Handle(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert out["opencode_says"]["skills"] == ["investigate-weak-signals"]
    assert out["opencode_says_chat_work"] == {"asked": False, "why": "there is no chat workdir yet"}
    assert out["opencode_says_built_app"]["asked"] is False


def test_no_opencode_server_says_so_rather_than_reading_as_a_verdict(tmp_path, monkeypatch,
                                                                     workspace):
    """`asked: False` and a reason. An empty list here would read as "OpenCode holds no skills",
    which is the wrong finding when the truth is that nobody was asked."""
    out = _diag(tmp_path, monkeypatch, None)

    assert out["opencode_says"]["asked"] is False
    assert out["opencode_says"]["why"]


def test_an_opencode_that_cannot_be_reached_does_not_break_the_page(tmp_path, monkeypatch,
                                                                    workspace):
    """A diagnostic must never be the thing that breaks the diagnostics page."""
    out = _diag(tmp_path, monkeypatch, _Handle("http://127.0.0.1:1"))

    assert out["opencode_says"]["asked"] is True
    assert out["opencode_says"]["ok"] is False
    assert out["opencode_says"]["error"]


def test_an_unreadable_reply_is_reported_as_one(tmp_path, monkeypatch, workspace):
    srv = _opencode({"": None}, status=500)
    try:
        out = _diag(tmp_path, monkeypatch, _Handle(f"http://127.0.0.1:{srv.server_port}"))
    finally:
        srv.shutdown()
        srv.server_close()

    assert out["opencode_says"]["ok"] is False
    assert out["opencode_says"]["status"] == 500
