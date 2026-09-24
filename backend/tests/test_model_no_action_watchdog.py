from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from sage import build_diagnostics, timing
from sage.build_policy import BuildPolicy
from sage.gateway.events import StreamEvents
from sage.gateway.protocol import Protocol
from sage.orchestrator.service import (
    Orchestrator,
    PlanRecoveryBudget,
    Project,
    _model_active_status,
)
from sage.router.model_control import ModelControl

from .test_native_model_controls import active, dispatch, running  # noqa: F401


def _sse(event: dict) -> bytes:
    return b"data: " + json.dumps(event).encode() + b"\n\n"


@pytest.mark.parametrize(
    ("protocol", "event"),
    [
        (Protocol.CHAT,
         {"choices": [{"delta": {"reasoning_content": "PRIVATE_REASONING"}}]}),
        (Protocol.MESSAGES,
         {"type": "content_block_delta",
          "delta": {"type": "thinking_delta", "thinking": "PRIVATE_REASONING"}}),
        (Protocol.RESPONSES,
         {"type": "response.reasoning_text.delta", "delta": "PRIVATE_REASONING"}),
    ],
)
def test_reasoning_frames_are_counted_without_content_or_action(protocol, event):
    events = StreamEvents(protocol)

    events.feed(_sse(event))

    assert events.reasoning_only_chunks == 1
    assert events.first_action_kind is None
    assert "PRIVATE_REASONING" not in repr(events)


@pytest.mark.parametrize(
    ("protocol", "event", "kind"),
    [
        (Protocol.CHAT, {"choices": [{"delta": {"content": "hello"}}]}, "text"),
        (Protocol.MESSAGES,
         {"type": "content_block_start", "index": 0,
          "content_block": {"type": "tool_use", "id": "call", "name": "read"}}, "tool"),
        (Protocol.RESPONSES,
         {"type": "response.output_item.added", "item_id": "item",
          "item": {"type": "function_call", "call_id": "call", "name": "read"}}, "tool"),
    ],
)
def test_first_text_or_tool_announcement_is_action(protocol, event, kind):
    events = StreamEvents(protocol)

    events.feed(_sse(event))

    assert events.first_action_kind == kind


def test_empty_tool_shape_is_not_action_and_output_limit_remains_visible():
    empty = StreamEvents(Protocol.CHAT)
    empty.feed(_sse({"choices": [{"delta": {"tool_calls": [{"index": 0,
                                                               "function": {}}]}}]}))
    assert empty.first_action_kind is None

    terminal = StreamEvents(Protocol.CHAT)
    terminal.feed(_sse({"choices": [{"delta": {"content": "at the boundary"},
                                      "finish_reason": "length"}]}))
    assert terminal.error == "length"
    assert terminal.first_action_kind == "text"


def _project() -> Project:
    return Project(id="project", workspace=object(), record=object(), supervisor=object(),
                   queries=object(), control=object(), shim=object())


def test_active_call_is_token_owned_bounded_and_reconstructable():
    project = _project()
    project.begin_active_model_call("first", "turn", 0.0)
    snapshot = project.observe_active_model_call(
        "first", 31.0, first_action_kind=None, reasoning_only_chunks=4)

    assert snapshot == {
        "callId": "first", "turnId": "turn", "elapsedSeconds": 31.0,
        "chunkCount": 1, "lastChunkAt": 31.0, "firstActionKind": None,
        "firstActionAt": None, "reasoningOnlyChunks": 4,
        "noticeSent": False, "timeoutSent": False,
    }
    assert project.mark_active_model_notice("first") is True
    project.begin_active_model_call("second", "turn", 32.0)
    assert project.clear_active_model_call("first") is False
    assert project.active_model_snapshot(40.0)["callId"] == "second"
    assert project.clear_active_model_call("second") is True


def test_action_at_timeout_boundary_wins_and_no_chunk_has_no_watchdog_decision():
    project = _project()
    policy = BuildPolicy(model_no_action_notice_seconds=30,
                         model_no_action_timeout_seconds=120)
    project.begin_active_model_call("call", "turn", 0.0)
    assert project.active_model_snapshot(130)["chunkCount"] == 0

    snapshot = project.observe_active_model_call(
        "call", 120.0, first_action_kind="tool", reasoning_only_chunks=9)

    assert snapshot["firstActionKind"] == "tool"
    assert snapshot["elapsedSeconds"] >= policy.model_no_action_timeout_seconds


def test_planning_recovery_is_exactly_one_clean_retry():
    recovery = PlanRecoveryBudget(1)

    assert recovery.choose() == ("initial", "recover")
    assert recovery.choose() == ("recovery", "stop")
    assert recovery.choose() == ("recovery", "stop")


def test_run_sage_plan_retries_the_immutable_request_in_the_same_directory():
    project = _project()

    class Control:
        def arm_read_only(self, reason):
            assert reason == "plan"
            return object()

        def disarm_read_only(self, token):
            pass

        def snapshot(self):
            return ModelControl().snapshot()

    class Client:
        def __init__(self):
            self._dirs = {"old": "/same/app"}
            self.sent = []
            self.created = []

        def messages(self, sid):
            if self.sent and self.sent[-1][0] == sid and sid == "fresh":
                return [{"type": "assistant", "id": "answer", "content": [
                    {"type": "text", "text": "# App\n\nA complete plan"}]}]
            return []

        def send_prompt(self, sid, prompt, agent, model=None):
            self.sent.append((sid, prompt, agent))
            if sid == "old":
                project.last_gateway_error = {
                    "code": "model_no_action_timeout", "message": "safe", "call_id": "call-1",
                    "turn_id": "turn", "elapsed_ms": 120_000, "chunk_count": 8,
                }

        def wait_for_idle(self, sid):
            pass

        def create_session(self, directory):
            self.created.append(directory)
            self._dirs["fresh"] = directory
            return "fresh"

    client = Client()
    project.control = Control()
    orchestrator = object.__new__(Orchestrator)
    orchestrator._oc_client = client
    orchestrator._build_policy = BuildPolicy()
    orchestrator._stop_wedged_session = lambda *args, **kwargs: True

    plan, sid = orchestrator._run_sage_plan(project, "IMMUTABLE REQUEST", "old")

    assert plan.startswith("# App") and sid == "fresh"
    assert client.created == ["/same/app"]
    assert client.sent == [
        ("old", "IMMUTABLE REQUEST", "sage-plan"),
        ("fresh", "IMMUTABLE REQUEST", "sage-plan"),
    ]


def test_no_action_timing_and_export_are_bounded(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(timing, "enabled", lambda: True)
    monkeypatch.setattr(
        timing, "time", SimpleNamespace(monotonic=lambda: now[0], time=lambda: 1.0))
    timing.start_turn("build", turn_id="turn", app_id="app", conversation_id="conversation")
    record = timing.current()
    handle = timing.model_call()
    events = StreamEvents(Protocol.CHAT)
    now[0] = 31.0
    handle.chunk()
    events.feed(_sse({"choices": [{"delta": {"reasoning_content": "PRIVATE"}}]}))
    handle.stream_metadata(events)
    handle.no_action_notice()
    now[0] = 120.0
    handle.no_action_timeout()
    handle.done(ok=False, outcome="no_action_timeout")
    timing.model_no_action_recovery(handle.call_id, "initial", "recover", record=record)
    timing.finish_turn()

    call = build_diagnostics.snapshot(
        record, {"turnId": "turn", "appId": "app", "conversationId": "conversation",
                 "kind": "build"}, terminal=True)["timing"]["calls"][0]

    assert call["firstActionMs"] is None
    assert call["noActionNoticeMs"] == 31_000
    assert call["noActionTimeoutMs"] == 120_000
    assert call["reasoningOnlyChunks"] == 1
    assert call["outcome"] == "no_action_timeout"
    assert call["noActionRecoveryAttempt"] == "initial"
    assert call["noActionRecoveryAction"] == "recover"
    assert "PRIVATE" not in json.dumps(call)


_HARNESS = Path(__file__).parent / "js" / "build_stream_harness.mjs"


def _node(payload: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable")
    result = subprocess.run(
        [node, str(_HARNESS)], input=json.dumps(payload), text=True,
        capture_output=True, check=True)
    return json.loads(result.stdout)


def test_model_active_is_live_status_only_and_clears_on_action():
    status = "The model is working but has not returned text or a tool yet — 30 s"
    result = _node({"events": [
        {"type": "model-active", "active": True,
         "message": status},
        {"type": "agent", "kind": "text", "text": "Ready"},
        {"type": "done", "ok": True, "decision": "clean"},
    ]})

    assert status in result["typings"]
    assert result["typings"][-1] is None
    assert status not in result["values"]


def test_refresh_reconstructs_model_active_for_the_same_running_build():
    status = "The model is working but has not returned text or a tool yet — 30 s"
    result = _node({
        "turnState": {
            "running": True,
            "running_turn": {"kind": "build", "conversation": "conv_1", "app": "app_a",
                             "turnId": "turn_1", "sequence": 1, "epoch": "epoch_1"},
            "model_active": {"callId": "call", "turnId": "turn_1", "message": status},
        },
        "events": [{"type": "done", "ok": True, "decision": "clean"}],
    })

    assert status in result["typings"]
    assert result["typings"][-1] is None


def test_model_active_status_uses_one_clamped_thirty_second_bucket():
    assert _model_active_status(30) == (
        30, "The model is working but has not returned text or a tool yet — 30 s")
    assert _model_active_status(59.999) == _model_active_status(30)
    assert _model_active_status(60) == (
        60, "The model is working but has not returned text or a tool yet — 60 s")


def test_native_reasoning_stream_times_out_with_safe_protocol_error(
        running, monkeypatch, caplog):  # noqa: F811
    from sage.gateway.client import FakeGatewayClient
    from sage.orchestrator import native_routes

    client, orch, _ = running
    assert client.post(
        "/api/project/model", json={"mode": "plan", "pick": "GLM 5.3 OR"}).status_code == 200
    orch._build_policy = replace(
        orch._build_policy, model_no_action_notice_seconds=30,
        model_no_action_timeout_seconds=120)
    ticks = iter((0.0, 1.0, 31.0, 31.0, 121.0, 121.0, 122.0, 123.0, 124.0))
    monkeypatch.setattr(
        native_routes, "time", SimpleNamespace(monotonic=lambda: next(ticks, 124.0)))

    class ReasoningGateway(FakeGatewayClient):
        def route(self, request, labels, *, protocol, cancel):
            for text in ("PRIVATE_ONE", "PRIVATE_TWO"):
                yield _sse({"choices": [{"delta": {"reasoning_content": text}}]})

    orch._project.shim._gateway = ReasoningGateway()
    timing.start_turn("build", turn_id="turn")
    try:
        with caplog.at_level(logging.WARNING, logger="sage.orchestrator"), active(orch) as headers:
            response = dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR")
        record = timing.finish_turn()
    finally:
        if timing.current() is not None:
            timing.finish_turn()

    assert response.status_code == 200
    assert "sage_gateway_error" in response.text
    assert orch._project.last_gateway_error["code"] == "model_no_action_timeout"
    assert orch._project.active_model_snapshot() is None
    assert timing.as_dict(record)["calls"][0]["outcome"] == "no_action_timeout"
    notices = [item.getMessage() for item in caplog.records
               if item.getMessage().startswith("model no-action notice:")]
    assert len(notices) == 1 and "PRIVATE" not in notices[0]


def test_provider_output_limit_at_timeout_threshold_keeps_precedence(
        running, monkeypatch):  # noqa: F811
    from sage.gateway.client import FakeGatewayClient
    from sage.orchestrator import native_routes

    client, orch, _ = running
    assert client.post(
        "/api/project/model", json={"mode": "plan", "pick": "GLM 5.3 OR"}).status_code == 200
    ticks = iter((0.0, 1.0, 121.0, 121.0, 122.0, 123.0))
    monkeypatch.setattr(
        native_routes, "time", SimpleNamespace(monotonic=lambda: next(ticks, 123.0)))

    class LimitedGateway(FakeGatewayClient):
        def route(self, request, labels, *, protocol, cancel):
            yield _sse({"choices": [{"delta": {}, "finish_reason": "length"}]})

    orch._project.shim._gateway = LimitedGateway()
    token = orch._project.control.arm_read_only("plan")
    try:
        with active(orch) as headers:
            dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR")
    finally:
        orch._project.control.disarm_read_only(token)

    assert orch._project.last_gateway_error["code"] == "model_output_limit"
