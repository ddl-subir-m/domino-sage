"""Open in Build writes its plan through the native harness, which requires a named turn.

Measured live 2026-09-28 on a Chat click: the planner resolved `mimo-v2.6-pro` (`auto-plan`) and
the ring never logged `model call -> streaming`. The click was recorded as

    Sage couldn't write a plan — model call failed: Sage stopped this request because its
    Build turn ended before it was ready.

That sentence is `sage_turn_scope_changed`. The door takes the turn lock without queueing, and
the harness refuses a model call whose holder has no ticket — the same refusal
`test_raw_lock_without_a_turn_ticket_cannot_route_native_calls` pins for a lock that must not
call a model. This door does call one. The call is made from inside `send_prompt`, which is
where OpenCode's provider posts `/v1/sage/resolve` and then the inference route.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sage.gateway.protocol import Protocol

from .fake_opencode import FakeOpenCode, Turn, execution_plan
from .test_model_diagnostics_follow_native_streams import scripted
from .test_native_model_controls import request_body
from .test_native_model_controls import running as native_running

running = native_running

pytestmark = pytest.mark.usefixtures("ledger")


def test_open_in_build_can_call_the_model_while_it_writes_the_plan(running, monkeypatch):
    client, orch, gateway = running
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "plan"})
    monkeypatch.setattr(gateway, "route", scripted(gateway, Protocol.CHAT))
    thread_id = orch.create_thread()["id"]
    oc = FakeOpenCode(Path(orch.project(start_preview=False).record.path),
                      [Turn(text=execution_plan())])
    original = oc.send_prompt
    seen: dict = {}

    def call_the_model(session_id, text, model=None, agent=None, **kwargs):
        from sage.orchestrator import app as appmod

        endpoint = next(
            route.endpoint for route in appmod.control_app.routes
            if route.path == "/v1/sage/chat/completions")

        async def body():
            return json.dumps(request_body(Protocol.CHAT, "GLM 5.3 OR")).encode()

        request = SimpleNamespace(
            headers={"x-session-id": session_id}, body=body,
            url=SimpleNamespace(path="/v1/sage/chat/completions"))

        async def consume():
            response = await endpoint(request)
            if hasattr(response, "body_iterator"):
                async for _ in response.body_iterator:
                    pass
            return response

        response = asyncio.run(consume())
        seen["status"] = response.status_code
        error = {}
        if response.status_code != 200 and getattr(response, "body", None):
            error = json.loads(response.body).get("error") or {}
        seen["code"] = error.get("code")
        seen["running"] = orch.turn_state()["running_turn"]
        original(session_id, text, model=model, agent=agent, **kwargs)

    oc.send_prompt = call_the_model
    orch._oc_client = oc

    drafted = orch.draft_handoff_plan(thread_id)

    assert seen["status"] == 200, seen
    assert seen["code"] != "sage_turn_scope_changed"
    assert gateway.seen, "the plan call never reached the gateway"
    assert seen["running"]["kind"] == "chat"
    assert seen["running"]["conversation"] == thread_id
    assert drafted["ok"] is True and drafted["plan"]
    assert orch.turn_state()["running_turn"] is None
    assert orch.project(start_preview=False).stop_requested is False
