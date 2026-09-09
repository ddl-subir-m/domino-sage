"""A Chat turn dials its own directory's MCP servers, because nothing else does.

Measured on the pinned OpenCode 1.18.4 in a live workspace, 2026-09-09. An MCP client belongs to an
instance and an instance is a directory. The instance for a Chat turn is created WITH the session:

    18:37:59  "creating instance" directory=/mnt/code/.sage/chat-work
    18:37:59  bootstrapping ... LSPs ... formatters ... init ... event connected
    18:37:59  chat tools: live read NOT OFFERED — all 10: apply_patch, bash, edit, ...

No handshake anywhere in that bootstrap, and none on the turn path either. OpenCode connects an
instance's MCP clients only when something asks `GET /mcp`. Meanwhile `opencode mcp list` and a bare
`GET /mcp` both reported the server connected — truthfully, about the OpenCode server's OWN
instance, which no turn ever uses. Every surface agreed and every turn went out empty.

Connecting BEFORE the session does nothing: the instance is not there yet. That is why reading the
diagnostics page a minute before a turn did not help, and why this call sits where it does.
"""
from __future__ import annotations

import json

import httpx
import pytest

from sage.driver.opencode import OpenCodeClient
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class _Server:
    """Stands in for `opencode serve`, recording what was asked of `/mcp`."""

    def __init__(self, payload=None, status=200, boom=False):
        self.payload = {"sage-live-read": {"status": "connected"}} if payload is None else payload
        self.status = status
        self.boom = boom
        self.asked: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.boom:
            raise httpx.ConnectError("refused", request=request)
        self.asked.append(dict(request.url.params))
        return httpx.Response(self.status, json=self.payload)


def _client(server: _Server, monkeypatch) -> OpenCodeClient:
    transport = httpx.MockTransport(server.handler)
    real_get = httpx.get

    def _get(url, **kw):
        kw.pop("timeout", None)
        with httpx.Client(transport=transport) as c:
            return c.get(url, **kw)

    monkeypatch.setattr(httpx, "get", _get)
    assert real_get is not httpx.get
    return OpenCodeClient(base_url="http://127.0.0.1:4096")


def test_it_asks_about_the_directory_the_turn_runs_in(monkeypatch):
    """The whole point. A bare call connects the server's own instance, which no turn uses."""
    server = _Server()

    names = _client(server, monkeypatch).connect_mcp("/mnt/code/.sage/chat-work")

    assert server.asked == [{"directory": "/mnt/code/.sage/chat-work"}]
    assert names == ["sage-live-read"]


def test_a_server_that_did_not_connect_is_not_reported_as_one(monkeypatch):
    server = _Server({"sage-live-read": {"status": "failed"}})

    assert _client(server, monkeypatch).connect_mcp("/w") == []


def test_an_unreachable_opencode_does_not_break_the_turn(monkeypatch):
    """A turn that would have answered without Live read must still answer."""
    server = _Server(boom=True)

    assert _client(server, monkeypatch).connect_mcp("/w") == []


def test_a_non_200_does_not_break_the_turn(monkeypatch):
    assert _client(_Server(status=503), monkeypatch).connect_mcp("/w") == []


@pytest.mark.parametrize("payload", [None, [], "nonsense", {"x": "not-a-dict"}])
def test_a_reply_in_any_other_shape_is_survived(monkeypatch, payload):
    server = _Server(payload if payload is not None else {})

    assert _client(server, monkeypatch).connect_mcp("/w") == []


# --- and the turn actually does it ---------------------------------------------------------------


class _OkFeedback:
    def check(self, path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class _Gateway:
    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def test_a_chat_turn_connects_for_the_directory_it_runs_in(tmp_path, _no_waiting):
    """End to end through the turn, not just the client method.

    The directory matters and is asserted: connecting the OpenCode server's own instance is what
    every surface was already doing, and it is exactly what did not help.
    """
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp_path / "mnt" / "code"
    oc = FakeOpenCode(ws, [Turn(text="Here it is.")])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=_Gateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=_OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False)
    tid = orch.create_thread()["id"]

    list(orch.chat_stream(tid, "what does the table look like"))

    assert oc.mcp_connected, "the turn never dialled its own directory's MCP servers"
    assert all(d.endswith("chat-work") for d in oc.mcp_connected), oc.mcp_connected
