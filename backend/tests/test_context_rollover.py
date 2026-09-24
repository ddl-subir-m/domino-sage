"""Issue #528: the 53 acceptance items grouped by their owning behavior seam."""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from sage import build_diagnostics, build_intent, timing
from sage.build_intent import BuildIntent
from sage.build_policy import BuildPolicy
from sage.context_rollover import (
    ContextAction,
    ContextContinuationRegistry,
    ContextRolloverState,
)
from sage.gateway.protocol import Protocol
from sage.orchestrator import app as appmod
from sage.orchestrator import native_routes
from sage.orchestrator.service import Orchestrator, TurnWedged
from sage.pre_edit_guard import PreEditAction, PreEditGuard
from sage.request_composition import measure, wire_bytes
from sage.router.models import Mode
from sage.tool_result_window import apply_tool_result_window

from .fake_opencode import FakeOpenCode, Turn
from .test_native_model_controls import active, dispatch, request_body
from .test_native_model_controls import running as _running
from .test_phased_build import (
    PHASED_PLAN,
    _plan_then_phases,
    _writes,
)
from .test_phased_build import _build as _phased_build
from .test_turn_path import _build

_PY_NODE = "tests/test_context_rollover.py::"
_UI_NODE = "tests/test_context_rollover_ui.py::"

ACCEPTANCE_MATRIX = {
    1: _PY_NODE + "test_non_media_boundary_is_inclusive_and_media_is_subtracted",
    2: _PY_NODE + "test_non_media_boundary_is_inclusive_and_media_is_subtracted",
    3: _PY_NODE + "test_non_media_boundary_is_inclusive_and_media_is_subtracted",
    4: _PY_NODE + "test_final_rendered_protocol_bytes_include_unknown_framing_as_non_media",
    5: _PY_NODE + "test_shared_media_classifier_counts_large_supported_media_outside_non_media_limit",
    6: _PY_NODE + "test_shared_media_classifier_counts_large_supported_media_outside_non_media_limit",
    7: _PY_NODE + "test_unavailable_limited_or_invalid_measurement_fails_closed",
    8: _PY_NODE + "test_measurement_is_after_the_tool_result_window",
    9: _PY_NODE + "test_final_rendered_protocol_bytes_include_unknown_framing_as_non_media",
    10: _PY_NODE + "test_final_rendered_protocol_bytes_include_unknown_framing_as_non_media",
    11: _PY_NODE + "test_final_rendered_protocol_bytes_include_unknown_framing_as_non_media",
    12: _PY_NODE + "test_native_first_breach_has_exact_protocol_body_and_never_reaches_gateway",
    13: _PY_NODE + "test_first_breach_is_atomic_and_publishes_one_decision",
    14: _PY_NODE + "test_first_breach_is_atomic_and_publishes_one_decision",
    15: _PY_NODE + "test_native_first_breach_has_exact_protocol_body_and_never_reaches_gateway",
    16: _PY_NODE + "test_native_retry_from_retired_session_stays_local",
    17: _PY_NODE + "test_stream_lifecycle_rolls_once_keeps_disk_and_offers_one_continue",
    18: _PY_NODE + "test_stream_lifecycle_rolls_once_keeps_disk_and_offers_one_continue",
    19: _PY_NODE + "test_phased_continue_resumes_interrupted_phase_and_finishes_later_phases",
    20: _PY_NODE + "test_stream_lifecycle_rolls_once_keeps_disk_and_offers_one_continue",
    21: _PY_NODE + "test_stream_lifecycle_rolls_once_keeps_disk_and_offers_one_continue",
    22: _PY_NODE + "test_automatic_rollover_request_has_canonical_intent_and_disk_packet_without_private_history",
    23: _PY_NODE + "test_private_prior_content_is_absent_from_model_diagnostics_logs_ui_and_continuation",
    24: _PY_NODE + "test_private_prior_content_is_absent_from_model_diagnostics_logs_ui_and_continuation",
    25: _PY_NODE + "test_private_prior_content_is_absent_from_model_diagnostics_logs_ui_and_continuation",
    26: _PY_NODE + "test_stream_lifecycle_rolls_once_keeps_disk_and_offers_one_continue",
    27: _PY_NODE + "test_stream_lifecycle_rolls_once_keeps_disk_and_offers_one_continue",
    28: _PY_NODE + "test_stream_lifecycle_rolls_once_keeps_disk_and_offers_one_continue",
    29: _PY_NODE + "test_stream_lifecycle_rolls_once_keeps_disk_and_offers_one_continue",
    30: _PY_NODE + "test_stream_lifecycle_rolls_once_keeps_disk_and_offers_one_continue",
    31: _PY_NODE + "test_stream_lifecycle_rolls_once_keeps_disk_and_offers_one_continue",
    32: _PY_NODE + "test_opaque_id_and_public_record_contain_no_prompt_path_or_private_content",
    33: _PY_NODE + "test_continue_first_claim_is_sse_and_starts_one_turn",
    34: _UI_NODE + "test_continue_click_sends_only_opaque_id_and_scope_and_cannot_double_start",
    35: _PY_NODE + "test_registry_is_one_slot_scoped_idempotent_and_keeps_the_canonical_intent_by_reference",
    36: _PY_NODE + "test_registry_is_one_slot_scoped_idempotent_and_keeps_the_canonical_intent_by_reference",
    37: _PY_NODE + "test_registry_is_one_slot_scoped_idempotent_and_keeps_the_canonical_intent_by_reference",
    38: _PY_NODE + "test_registry_is_one_slot_scoped_idempotent_and_keeps_the_canonical_intent_by_reference",
    39: _PY_NODE + "test_continue_restores_exact_direct_file_and_resource_references",
    40: _PY_NODE + "test_registry_is_one_slot_scoped_idempotent_and_keeps_the_canonical_intent_by_reference",
    41: _PY_NODE + "test_automatic_rollover_request_has_canonical_intent_and_disk_packet_without_private_history",
    42: _PY_NODE + "test_continue_turn_starts_with_a_fresh_rollover_allowance",
    43: _UI_NODE + "test_rollover_and_context_limit_render_once_and_replay_needs_server_confirmation",
    44: _UI_NODE + "test_context_limit_uses_a_dedicated_component_and_event_handlers_do_not_auto_continue",
    45: _PY_NODE + "test_user_stop_wins_over_a_pending_rollover_and_offers_no_continue",
    46: _PY_NODE + "test_specific_brake_can_finish_without_rollover_or_continue",
    47: _PY_NODE + "test_pre_edit_guard_wins_before_whole_context_limit",
    48: _PY_NODE + "test_context_replacement_does_not_reset_pre_edit_counters",
    49: _PY_NODE + "test_stream_lifecycle_rolls_once_keeps_disk_and_offers_one_continue",
    50: _PY_NODE + "test_broken_call_repair_objective_survives_rollover_and_continue_offer",
    51: _PY_NODE + "test_success_cancellation_exception_and_terminal_limit_clear_active_state",
    52: _PY_NODE + "test_later_independent_build_starts_at_generation_zero",
    53: _PY_NODE + "test_private_prior_content_is_absent_from_model_diagnostics_logs_ui_and_continuation",
}


def _state(*, limit=786_432, rollovers=1, baseline="base"):
    return ContextRolloverState(
        replace(
            BuildPolicy(),
            build_context_non_media_max_bytes=limit,
            build_context_automatic_rollover_limit=rollovers,
        ),
        baseline,
    )


def _cross_once(state: ContextRolloverState, *, total=786_433, media=0):
    decision = state.decide(
        total_wire_bytes=total, media_bytes=media, measurement_status="complete")
    assert decision.action is ContextAction.ROLLOVER
    assert state.consume_pending() == decision
    assert state.begin_rollover("old-session")
    assert state.activate_rollover()


def test_all_53_acceptance_items_have_an_owning_test_group():
    assert set(ACCEPTANCE_MATRIX) == set(range(1, 54))
    missing = set()
    root = Path(__file__).parent.parent
    for node in ACCEPTANCE_MATRIX.values():
        path, name = node.split("::", 1)
        source = (root / path).read_text()
        if f"def {name}(" not in source:
            missing.add(node)
    assert missing == set()


@pytest.mark.parametrize(
    ("total", "media", "action"),
    [
        (786_432, 0, ContextAction.ROUTE),
        (786_433, 0, ContextAction.ROLLOVER),
        (3_786_432, 3_000_000, ContextAction.ROUTE),
        (3_786_433, 3_000_000, ContextAction.ROLLOVER),
    ],
)
def test_non_media_boundary_is_inclusive_and_media_is_subtracted(total, media, action):
    assert _state().decide(
        total_wire_bytes=total, media_bytes=media, measurement_status="complete"
    ).action is action


@pytest.mark.parametrize(
    ("total", "media", "status"),
    [(-1, 0, "unavailable"), (10, -1, "complete"), (10, 11, "complete"),
     (10, 0, "limited")],
)
def test_unavailable_limited_or_invalid_measurement_fails_closed(total, media, status):
    state = _state()
    decision = state.decide(
        total_wire_bytes=total, media_bytes=media, measurement_status=status)
    assert decision.action is ContextAction.FAIL
    assert decision.reason == "context_measurement_unavailable"
    assert state.consume_pending() == decision
    assert state.diagnostic()["measurementStatus"] == "unavailable"


@pytest.mark.parametrize("protocol", list(Protocol))
def test_final_rendered_protocol_bytes_include_unknown_framing_as_non_media(protocol):
    if protocol is Protocol.CHAT:
        payload = {"messages": [{"role": "assistant", "content": "x"}], "future": "unknown"}
    elif protocol is Protocol.MESSAGES:
        payload = {"system": "rules", "messages": [
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "x"}]}
        ], "future": "unknown"}
    else:
        payload = {"instructions": "rules", "input": [
            {"type": "reasoning", "encrypted_content": "x"}
        ], "future": "unknown"}
    total = wire_bytes(payload)
    composition = measure(payload, total)
    assert composition["status"] == "complete"
    assert sum(composition["categories"].values()) == total
    assert composition["categories"]["unclassifiedBytes"] > 0


def test_shared_media_classifier_counts_large_supported_media_outside_non_media_limit():
    private_media = "data:image/png;base64," + "A" * 3_000_000
    payload = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "make this"},
        {"type": "image_url", "image_url": {"url": private_media}},
    ]}]}
    total = wire_bytes(payload)
    composition = measure(payload, total)
    media = composition["categories"]["mediaBytes"]
    assert media > 3_000_000
    assert _state().decide(
        total_wire_bytes=total, media_bytes=media,
        measurement_status=composition["status"],
    ).action is ContextAction.ROUTE
    assert private_media not in json.dumps(_state().diagnostic())


def test_measurement_is_after_the_tool_result_window():
    original = {"messages": [
        {"role": "tool", "tool_call_id": "one", "content": "x" * 300_000}
    ]}
    bounded, diagnostics = apply_tool_result_window(original, BuildPolicy())
    total = wire_bytes(bounded)
    composition = measure(bounded, total, {"toolResultWindow": diagnostics})
    assert total < wire_bytes(original)
    assert composition["toolResultWindow"]["forwardedModelFacingBytes"] < 300_000


def test_first_breach_is_atomic_and_publishes_one_decision():
    for _ in range(20):
        state = _state(limit=1)
        barrier = threading.Barrier(9)
        answers = []

        def decide(local_barrier=barrier, local_answers=answers, local_state=state):
            local_barrier.wait()
            local_answers.append(local_state.decide(
                total_wire_bytes=2, media_bytes=0, measurement_status="complete").action)

        threads = [threading.Thread(target=decide) for _ in range(8)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        assert set(answers) == {ContextAction.ROLLOVER}
        assert state.consume_pending().action is ContextAction.ROLLOVER
        assert state.consume_pending() is None


def test_retired_root_and_descendants_remain_local_after_fresh_session_starts():
    state = _state(limit=1)
    _cross_once(state, total=2)
    assert state.rejects_session("old-session")
    assert state.rejects_session(
        "child", lambda child, root: child == "child" and root == "old-session")
    assert not state.rejects_session("fresh-session")
    assert state.generation == 1


def test_second_breach_offers_continue_and_never_starts_a_second_rollover():
    state = _state(limit=1)
    _cross_once(state, total=2)
    second = state.decide(total_wire_bytes=2, media_bytes=0, measurement_status="complete")
    assert second.action is ContextAction.OFFER_CONTINUE
    assert state.consume_pending() == second
    assert state.consume_pending() is None
    assert state.decide(
        total_wire_bytes=2, media_bytes=0, measurement_status="complete"
    ).action is ContextAction.OFFER_CONTINUE
    assert state.diagnostic() | {} == {
        "policyVersion": 1, "limitNonMediaBytes": 1, "sessionGeneration": 1,
        "rolloverCount": 1, "totalWireBytes": 2, "mediaBytes": 0,
        "nonMediaContextBytes": 2, "measurementStatus": "complete",
        "action": "offer_continue", "continuationOffered": True,
    }


def test_central_policy_override_allows_two_automatic_generations():
    state = _state(limit=1, rollovers=2)
    _cross_once(state, total=2)
    assert state.decide(
        total_wire_bytes=2, media_bytes=0, measurement_status="complete"
    ).action is ContextAction.ROLLOVER
    state.consume_pending()
    assert state.begin_rollover("generation-one") and state.activate_rollover()
    assert state.generation == 2
    assert state.decide(
        total_wire_bytes=2, media_bytes=0, measurement_status="complete"
    ).action is ContextAction.OFFER_CONTINUE


def test_stop_clears_a_pending_rollover_and_creates_no_offer():
    state = _state(limit=1)
    state.decide(total_wire_bytes=2, media_bytes=0, measurement_status="complete")
    state.claim_cancellation()
    assert state.consume_pending() is None
    assert state.diagnostic()["continuationOffered"] is False


def test_specific_brake_can_finish_without_rollover_or_continue():
    state = _state(limit=1)
    state.finish()
    assert state.consume_pending() is None
    assert state.diagnostic()["rolloverCount"] == 0
    assert state.diagnostic()["continuationOffered"] is False


def test_context_replacement_does_not_reset_pre_edit_counters():
    guard = PreEditGuard(
        replace(BuildPolicy(), pre_edit_model_call_limit=3), "base", lambda: "base")
    assert guard.decide_request((), 1).action is PreEditAction.ROUTE
    before = guard.diagnostic()
    state = _state(limit=1)
    _cross_once(state, total=2)
    guard.note_session_replacement()
    after = guard.diagnostic()
    assert after["modelCalls"] == before["modelCalls"] == 1
    assert after["attempt"] == before["attempt"] == "initial"


def _built_orchestrator(tmp_path, turns):
    orch, oc, _gateway = _build(tmp_path, turns)
    project = orch.project(start_preview=False)
    project.workspace.mark_built()
    project.control.set_mode(Mode.IMPLEMENT)
    return orch, oc, project


def test_stream_lifecycle_rolls_once_keeps_disk_and_offers_one_continue(tmp_path):
    private = "PRIVATE_PRIOR_ASSISTANT_PROSE"
    orch, oc, project = _built_orchestrator(tmp_path, [
        Turn(text=private, writes={"src/App.tsx": "// work survived\n"}),
        Turn(text="second generation"),
    ])
    original_send = oc.send_prompt

    def oversized(session_id, text, *args, **kwargs):
        original_send(session_id, text, *args, **kwargs)
        project.context_rollover.decide(
            total_wire_bytes=786_433, media_bytes=0, measurement_status="complete")

    oc.send_prompt = oversized
    events = list(orch.build_stream("Build the dashboard.", conversation="conv-a"))
    rollover = [event for event in events if event["type"] == "build-rollover"]
    limits = [event for event in events if event["type"] == "build-context-limit"]
    done = [event for event in events if event["type"] == "done"]
    assert len(rollover) == len(limits) == len(done) == 1
    assert rollover[0]["generation"] == 1
    assert limits[0]["kept"] is True
    assert done[0]["decision"] == "context_limit"
    assert oc.interrupted == 2
    assert len(oc.sessions) == 2
    assert {row["directory"] for row in oc.sessions} == {str(project.workspace.path)}
    assert (project.workspace.path / "src" / "App.tsx").read_text() == "// work survived\n"
    assert private not in oc.prompts[1]["text"]
    assert "src/App.tsx" in oc.prompts[1]["text"]
    continuation = project.context_continuations.latest()
    assert continuation is not None and continuation.current_digest != continuation.baseline_digest
    assert continuation.intent.source_requests == ("Build the dashboard.",)
    assert project.context_rollover is None


def test_synchronous_build_uses_the_native_route_and_terminal_continue_lifecycle(
        native_env, monkeypatch):
    client, orch, gateway = native_env
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "implement"})
    project = orch._project
    project.workspace.mark_built()
    oc = FakeOpenCode(project.workspace.path)
    orch._oc_client = oc
    orch._build_policy = replace(
        BuildPolicy(), build_context_non_media_max_bytes=1,
        pre_edit_model_call_limit=100, pre_edit_request_non_media_max_bytes=1_000_000)
    statuses = []

    def routed_send(session_id, _text, *args, **kwargs):
        response = dispatch(
            client, {"X-Session-Id": session_id}, Protocol.CHAT, "GLM 5.3 OR")
        statuses.append((response.status_code, response.json()))

    monkeypatch.setattr(oc, "send_prompt", routed_send)
    result = orch.build("Build synchronously.", conversation="conv-sync")
    assert result["decision"] == "context_limit"
    assert result["continuationId"]
    assert [status for status, _body in statuses] == [400, 400]
    assert all("sage_context_" in json.dumps(body) for _status, body in statuses)
    assert gateway.seen == []
    assert len(oc.sessions) == 2 and oc.interrupted == 2
    assert project.context_continuations.latest().intent.source_requests == (
        "Build synchronously.",)
    assert project.context_rollover is None


def test_synchronous_build_pre_edit_guard_wins_at_the_real_native_route(
        native_env, monkeypatch):
    client, orch, gateway = native_env
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "implement"})
    project = orch._project
    project.workspace.mark_built()
    oc = FakeOpenCode(project.workspace.path)
    orch._oc_client = oc
    orch._build_policy = replace(
        BuildPolicy(), pre_edit_model_call_limit=0,
        build_context_non_media_max_bytes=1)
    statuses = []

    def routed_send(session_id, _text, *args, **kwargs):
        response = dispatch(
            client, {"X-Session-Id": session_id}, Protocol.CHAT, "GLM 5.3 OR")
        statuses.append(response.status_code)

    monkeypatch.setattr(oc, "send_prompt", routed_send)
    result = orch.build("Build synchronously.", conversation="conv-sync-guard")
    assert result["decision"] == "pre_edit_limit"
    assert statuses == [409, 409]
    assert gateway.seen == []
    assert oc.interrupted == 1
    assert project.context_continuations.latest() is None
    assert project.context_rollover is None


def test_synchronous_rollover_refused_stop_preserves_lock_and_safety_state(
        tmp_path, monkeypatch):
    orch, oc, project = _built_orchestrator(tmp_path, [Turn()])
    original_send = oc.send_prompt

    def oversized(session_id, text, *args, **kwargs):
        original_send(session_id, text, *args, **kwargs)
        project.context_rollover.decide(
            total_wire_bytes=786_433, media_bytes=0, measurement_status="complete")

    monkeypatch.setattr(oc, "send_prompt", oversized)
    monkeypatch.setattr(orch, "_stop_wedged_session", lambda *_args, **_kwargs: False)
    result = orch.build("Build synchronously.", conversation="conv-sync-wedged")
    assert result["decision"] == "wedged"
    assert orch._turn_wedged is True and orch._turn_lock.locked()
    assert project.context_rollover is not None
    assert project.pre_edit_guard is not None
    assert project.active_session_id is not None


def test_broken_call_repair_objective_survives_rollover_and_continue_offer(tmp_path):
    orch, oc, project = _built_orchestrator(tmp_path, [
        Turn(broken_write=True), Turn(text="retrying broken call"), Turn(text="clean rollover"),
    ])
    original_send = oc.send_prompt

    def cross_on_recovery(session_id, text, *args, **kwargs):
        original_send(session_id, text, *args, **kwargs)
        if len(oc.prompts) >= 2:
            project.context_rollover.decide(
                total_wire_bytes=786_433, media_bytes=0, measurement_status="complete")

    oc.send_prompt = cross_on_recovery
    events = list(orch.build_stream("Build it.", conversation="conv-broken"))
    assert next(event for event in events if event["type"] == "done")["decision"] == "context_limit"
    assert "Active repair objective: broken_call_recovery" in oc.prompts[2]["text"]
    assert project.context_continuations.latest().repair_objective == "broken_call_recovery"


def test_phased_build_shares_one_rollover_allowance_and_stops_the_active_phase(
        tmp_path, monkeypatch):
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *_args, **_kwargs: None)
    orch, oc, project, _plan_events = _plan_then_phases(tmp_path)
    original_send = oc.send_prompt
    phase_start = len(oc.prompts)

    def oversized_phase(session_id, text, *args, **kwargs):
        original_send(session_id, text, *args, **kwargs)
        if len(oc.prompts) > phase_start:
            project.context_rollover.decide(
                total_wire_bytes=786_433, media_bytes=0, measurement_status="complete")

    oc.send_prompt = oversized_phase
    events = list(orch.approve_stream())
    assert [event["type"] for event in events].count("build-rollover") == 1
    assert [event["type"] for event in events].count("build-context-limit") == 1
    assert [event["type"] for event in events].count("done") == 1
    assert next(event for event in events if event["type"] == "done")["decision"] == "context_limit"
    assert next(event for event in events if event["type"] == "step-done")["decision"] == "context_limit"
    continuation = project.context_continuations.latest()
    assert continuation is not None and continuation.phase_id.startswith("1. Data module")
    assert continuation.approved_plan_record_id
    assert project.context_rollover is None


def _phase_two_context_offer(
        tmp_path, monkeypatch, *, plan=PHASED_PLAN,
        phase_two_marker="2. Trades table (this step)",
        phase_one_label="Data module"):
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *_args, **_kwargs: None)
    phase_one = _writes("src/data.ts")
    phase_one.text = "The data module is complete."
    turns = [
        Turn(text=plan),
        phase_one,
        Turn(text="first local context refusal"),
        Turn(text="second local context refusal"),
        _writes("src/Table.tsx"),
        _writes("src/Filter.tsx"),
    ]
    orch, oc, project = _phased_build(tmp_path, turns)
    list(orch.build_stream("build me a trades dashboard", conversation="conv"))
    original_send = oc.send_prompt

    def cross_only_phase_two(session_id, text, *args, **kwargs):
        intent = project.active_build_intent
        original_send(session_id, text, *args, **kwargs)
        if intent is not None and phase_two_marker in intent.phase_index:
            project.context_rollover.decide(
                total_wire_bytes=786_433, media_bytes=0,
                measurement_status="complete")

    oc.send_prompt = cross_only_phase_two
    events = list(orch.approve_stream(conversation="conv"))
    oc.send_prompt = original_send
    continuation = project.context_continuations.latest()
    assert continuation is not None
    assert [event["type"] for event in events].count("build-rollover") == 1
    assert [event["type"] for event in events].count("build-context-limit") == 1
    assert phase_two_marker in continuation.phase_id
    assert continuation.intent.prior_phase_notes == (
        f"1. {phase_one_label} — The data module is complete.",)
    assert continuation.approved_plan_version > 0
    assert len(continuation.approved_plan_digest) == 64
    assert project.workspace.read_plan_retry_step() == 2
    return orch, oc, project, continuation


def test_phased_continue_resumes_interrupted_phase_and_finishes_later_phases(
        tmp_path, monkeypatch):
    orch, oc, project, continuation = _phase_two_context_offer(tmp_path, monkeypatch)
    original_send = oc.send_prompt
    continued_generations = []
    continued_intents = []

    def inspect_continued_phase_two(session_id, text, *args, **kwargs):
        intent = project.active_build_intent
        original_send(session_id, text, *args, **kwargs)
        if intent is not None and "2. Trades table (this step)" in intent.phase_index:
            continued_generations.append(
                project.context_rollover.diagnostic()["sessionGeneration"])
            continued_intents.append(intent)

    oc.send_prompt = inspect_continued_phase_two
    save_calls = []
    mark_calls = []
    workspace_type = type(project.workspace)
    original_mark = workspace_type.mark_built

    def save_once(_project, prompt):
        save_calls.append(prompt)

    def mark_once(workspace):
        mark_calls.append(True)
        return original_mark(workspace)

    monkeypatch.setattr(orch, "_save_to_git", save_once)
    monkeypatch.setattr(workspace_type, "mark_built", mark_once)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    response = TestClient(appmod.control_app).post("/api/project/build/continue", json={
        "continuationId": continuation.continuation_id,
        "conversation": continuation.conversation,
        "appId": continuation.app_id,
    })
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: {")
    ]
    assert response.status_code == 200, response.text
    assert [event["n"] for event in events if event["type"] == "step-start"] == [2, 3]
    assert [event["n"] for event in events if event["type"] == "step-done"] == [1, 2, 3]
    assert len([event for event in events if event["type"] == "done"]) == 1
    assert next(event for event in events if event["type"] == "done")["ok"] is True
    assert continued_generations[0] == 0
    assert continued_intents[0] is continuation.intent
    assert continued_intents[0].intent_id == continuation.intent_id
    assert continued_intents[0].prior_phase_notes == continuation.intent.prior_phase_notes
    assert (project.workspace.path / "src/Table.tsx").exists()
    assert (project.workspace.path / "src/Filter.tsx").exists()
    assert project.workspace.read_plan_retry_step() == 0
    assert project.workspace.has_built()
    assert mark_calls == [True]
    assert save_calls == ["build plan (3 phases)"]
    assert project.workspace.read_plan() is None
    assert project.workspace.read_archived_plan_doc_id() == continuation.approved_plan_record_id


DUPLICATE_PHASE_PLAN = PHASED_PLAN.replace(
    "### 1. Data module\n"
    "- Files — src/data.ts\n"
    "- Do — Export two hundred sample trade rows.\n"
    "- Done when — src/data.ts exports rows and the app compiles.\n\n"
    "### 2. Trades table\n"
    "- Files — src/Table.tsx\n"
    "- Do — Render the rows in a sortable table.\n"
    "- Done when — The preview shows a sortable table.\n"
    "- Don't touch — src/data.ts",
    "### 1. Repeated phase A\n"
    "- Files — src/Repeated.tsx\n"
    "- Do — Render the repeated content.\n"
    "- Done when — The repeated content is visible.\n\n"
    "### 2. Repeated phase B\n"
    "- Files — src/Repeated.tsx\n"
    "- Do — Render the repeated content.\n"
    "- Done when — The repeated content is visible.",
)


def test_phased_continue_resolves_duplicate_phase_text_by_recorded_index(
        tmp_path, monkeypatch):
    orch, _oc, _project, continuation = _phase_two_context_offer(
        tmp_path, monkeypatch, plan=DUPLICATE_PHASE_PLAN,
        phase_two_marker="2. Repeated phase B (this step)",
        phase_one_label="Repeated phase A")
    assert continuation.intent.phase_brief.startswith("### 2. Repeated phase B")
    monkeypatch.setattr(appmod, "orchestrator", orch)
    response = TestClient(appmod.control_app).post("/api/project/build/continue", json={
        "continuationId": continuation.continuation_id,
        "conversation": continuation.conversation,
        "appId": continuation.app_id,
    })
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: {")
    ]
    assert response.status_code == 200, response.text
    assert [event["n"] for event in events if event["type"] == "step-start"] == [2, 3]
    assert [event["n"] for event in events if event["type"] == "step-done"] == [1, 2, 3]
    assert next(event for event in events if event["type"] == "done")["ok"] is True


def test_phased_continue_rechecks_plan_changed_during_execution_before_completion(
        tmp_path, monkeypatch):
    orch, oc, project, continuation = _phase_two_context_offer(tmp_path, monkeypatch)
    original_send = oc.send_prompt
    changed_markdown = []

    def mutate_phase_three_after_send(session_id, text, *args, **kwargs):
        intent = project.active_build_intent
        original_send(session_id, text, *args, **kwargs)
        if intent is None or "3. Currency filter (this step)" not in intent.phase_index:
            return
        approved = project.record.read_plan_doc(continuation.approved_plan_record_id)
        changed = approved["markdown"].replace(
            "Add a currency dropdown above the table.",
            "Add an unapproved region dropdown above the table.",
        )
        assert changed != approved["markdown"]
        project.record.write_plan_doc_version(
            continuation.approved_plan_record_id, changed)
        project.workspace.write_plan(changed, continuation.approved_plan_record_id)
        changed_markdown.append(changed)

    oc.send_prompt = mutate_phase_three_after_send
    save_calls = []
    mark_calls = []
    monkeypatch.setattr(orch, "_save_to_git", lambda *_args: save_calls.append(True))
    monkeypatch.setattr(
        type(project.workspace), "mark_built",
        lambda *_args: mark_calls.append(True))
    monkeypatch.setattr(appmod, "orchestrator", orch)
    response = TestClient(appmod.control_app).post("/api/project/build/continue", json={
        "continuationId": continuation.continuation_id,
        "conversation": continuation.conversation,
        "appId": continuation.app_id,
    })
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: {")
    ]
    assert response.status_code == 200, response.text
    assert changed_markdown
    assert [event["type"] for event in events].count("done") == 1
    assert next(event for event in events if event["type"] == "done") == {
        "type": "done", "ok": False, "decision": "invalid phased continuation"}
    assert mark_calls == []
    assert save_calls == []
    assert project.workspace.read_plan() == changed_markdown[0]
    assert project.workspace.read_archived_plan_doc_id() != continuation.approved_plan_record_id
    # Installing the changed live plan clears the old plan version's retry marker.
    assert project.workspace.read_plan_retry_step() == 0
    assert project.context_continuations.claim(
        continuation.continuation_id, continuation.conversation,
        continuation.app_id)[0] == "invalid"


def test_stopping_phased_continue_retires_the_current_approved_plan(
        tmp_path, monkeypatch):
    orch, _oc, project, continuation = _phase_two_context_offer(tmp_path, monkeypatch)
    status, claimed, claim_token = project.context_continuations.claim(
        continuation.continuation_id, continuation.conversation, continuation.app_id)
    assert status == "claimed"
    ticket, _ = orch.prepare_stream_turn(
        "continued-stop", kind="build", conversation=continuation.conversation, app=True)
    events = []
    for event in orch.continue_build_stream(claimed, claim_token, turn_ticket=ticket):
        events.append(event)
        if event.get("type") == "step-start" and event.get("n") == 2:
            project.stop_requested = True
    assert [event["type"] for event in events].count("stopped") == 1
    assert not [event for event in events if event["type"] == "done"]
    assert project.workspace.read_plan_retry_step() == 0
    assert project.workspace.read_plan() is None
    assert project.workspace.read_archived_plan_doc_id() == continuation.approved_plan_record_id


@pytest.mark.parametrize("mutation", ["edit", "archive", "app_id"])
def test_live_plan_edit_or_archive_removes_available_continue_from_turn_state(
        tmp_path, monkeypatch, mutation):
    orch, oc, project, continuation = _phase_two_context_offer(tmp_path, monkeypatch)
    if mutation == "edit":
        approved = project.record.read_plan_doc(continuation.approved_plan_record_id)
        sections = dict(approved["sections"])
        sections["plan"] = sections["plan"].replace(
            "Add a currency dropdown above the table.",
            "Add an unapproved region dropdown above the table.",
        )
        orch.patch_plan_doc(continuation.approved_plan_record_id, {"sections": sections})
    elif mutation == "app_id":
        orch.patch_plan_doc(
            continuation.approved_plan_record_id, {"appId": "app_moved"})
    else:
        orch.archive_plan_doc(continuation.approved_plan_record_id, True)
    assert orch.turn_state()["context_continuation"] is None
    prompts_before = len(oc.prompts)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    response = TestClient(appmod.control_app).post("/api/project/build/continue", json={
        "continuationId": continuation.continuation_id,
        "conversation": continuation.conversation,
        "appId": continuation.app_id,
    })
    assert response.status_code == 409
    assert response.json() == {"error": "continuation unavailable"}
    assert len(oc.prompts) == prompts_before


def test_live_plan_app_change_is_refused_while_phased_continue_holds_turn_lock(
        tmp_path, monkeypatch):
    orch, oc, project, continuation = _phase_two_context_offer(tmp_path, monkeypatch)
    original_send = oc.send_prompt
    phase_started = threading.Event()
    release_phase = threading.Event()

    def block_resumed_phase(session_id, text, *args, **kwargs):
        intent = project.active_build_intent
        original_send(session_id, text, *args, **kwargs)
        if intent is not None and "2. Trades table (this step)" in intent.phase_index:
            phase_started.set()
            assert release_phase.wait(10)

    oc.send_prompt = block_resumed_phase
    monkeypatch.setattr(appmod, "orchestrator", orch)
    result = {}

    def run_continue():
        result["response"] = TestClient(appmod.control_app).post(
            "/api/project/build/continue", json={
                "continuationId": continuation.continuation_id,
                "conversation": continuation.conversation,
                "appId": continuation.app_id,
            })

    thread = threading.Thread(target=run_continue)
    thread.start()
    try:
        assert phase_started.wait(10)
        response = TestClient(appmod.control_app).patch(
            f"/api/plans/{continuation.approved_plan_record_id}",
            json={"appId": "app_moved"},
        )
        assert response.status_code == 409
        assert response.json() == {"error": "busy"}
        current = project.record.read_plan_doc(continuation.approved_plan_record_id)
        assert current["appId"] == continuation.app_id
        assert current["version"] == continuation.approved_plan_version
    finally:
        release_phase.set()
        thread.join(10)
    assert not thread.is_alive()
    assert result["response"].status_code == 200
    continued_events = [
        json.loads(line.removeprefix("data: "))
        for line in result["response"].text.splitlines()
        if line.startswith("data: {")
    ]
    assert next(event for event in continued_events if event["type"] == "done")["ok"] is True


@pytest.mark.parametrize("mutation", ["edit_phase_three", "archive"])
def test_phased_continue_rejects_a_changed_or_retired_approved_plan(
        tmp_path, monkeypatch, mutation):
    orch, oc, project, continuation = _phase_two_context_offer(tmp_path, monkeypatch)
    if mutation == "edit_phase_three":
        approved = project.record.read_plan_doc(continuation.approved_plan_record_id)
        changed = approved["markdown"].replace(
            "Add a currency dropdown above the table.",
            "Add an unapproved region dropdown above the table.",
        )
        assert changed != approved["markdown"]
        project.record.write_plan_doc_version(
            continuation.approved_plan_record_id, changed)
        project.workspace.write_plan(changed, continuation.approved_plan_record_id)
    else:
        project.workspace.archive_plan(cancelled=True)
    prompts_before = len(oc.prompts)
    archives_before = len(list((project.workspace.path / ".sage" / "plans").glob("*.md")))
    monkeypatch.setattr(appmod, "orchestrator", orch)
    response = TestClient(appmod.control_app).post("/api/project/build/continue", json={
        "continuationId": continuation.continuation_id,
        "conversation": continuation.conversation,
        "appId": continuation.app_id,
    })
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: {")
    ]
    assert response.status_code == 200, response.text
    assert [event["type"] for event in events] == ["error", "done"]
    assert events[-1] == {
        "type": "done", "ok": False, "decision": "invalid phased continuation"}
    assert len(oc.prompts) == prompts_before
    assert not (project.workspace.path / "src/Table.tsx").exists()
    assert not (project.workspace.path / "src/Filter.tsx").exists()
    assert len(list((project.workspace.path / ".sage" / "plans").glob("*.md"))) == archives_before
    assert project.context_continuations.claim(
        continuation.continuation_id, continuation.conversation,
        continuation.app_id)[0] == "invalid"
    if mutation == "edit_phase_three":
        assert project.workspace.read_plan() is not None


def test_continue_turn_starts_with_a_fresh_rollover_allowance(tmp_path):
    orch, oc, project = _built_orchestrator(tmp_path, [Turn(), Turn(
        writes={"src/App.tsx": "// continued\n"})])
    intent = BuildIntent.for_direct("Continue canonical work")
    available = project.context_continuations.offer(
        conversation="conv-continue", app_id=project.workspace.app_id, intent=intent,
        baseline_digest=project.snapshot.working_tree_hash(),
        repair_objective="broken_call_recovery")
    (project.workspace.path / "src" / "App.tsx").write_text("// current disk before Continue\n")
    status, record, token = project.context_continuations.claim(
        available.continuation_id, "conv-continue", project.workspace.app_id)
    assert status == "claimed"
    ticket, _ = orch.prepare_stream_turn(
        "turn-continue", kind="build", conversation="conv-continue", app=True)
    original_send = oc.send_prompt
    seen_states = []

    def first_request_crosses(session_id, text, *args, **kwargs):
        original_send(session_id, text, *args, **kwargs)
        seen_states.append(project.context_rollover.diagnostic())
        if len(seen_states) == 1:
            project.context_rollover.decide(
                total_wire_bytes=786_433, media_bytes=0, measurement_status="complete")

    oc.send_prompt = first_request_crosses
    events = list(orch.continue_build_stream(record, token, turn_ticket=ticket))
    assert [event["type"] for event in events].count("build-rollover") == 1
    assert next(event for event in events if event["type"] == "done")["ok"] is True
    assert seen_states[0]["sessionGeneration"] == seen_states[0]["rolloverCount"] == 0
    assert "src/App.tsx" in oc.prompts[0]["text"]
    assert "Active repair objective: broken_call_recovery" in oc.prompts[1]["text"]
    assert "Continue canonical work" not in oc.prompts[0]["text"]
    assert project.context_rollover is None


def test_later_independent_build_starts_at_generation_zero(tmp_path):
    orch, oc, project = _built_orchestrator(tmp_path, [
        Turn(writes={"src/App.tsx": "// first\n"}),
        Turn(writes={"src/App.tsx": "// second\n"}),
    ])
    original_send = oc.send_prompt
    starts = []

    def observe_generation(session_id, text, *args, **kwargs):
        starts.append(project.context_rollover.diagnostic()["sessionGeneration"])
        original_send(session_id, text, *args, **kwargs)

    oc.send_prompt = observe_generation
    first = list(orch.build_stream("First independent Build.", conversation="conv-first"))
    second = list(orch.build_stream("Second independent Build.", conversation="conv-second"))
    assert next(event for event in first if event["type"] == "done")["ok"] is True
    assert next(event for event in second if event["type"] == "done")["ok"] is True
    assert starts == [0, 0]


def test_normal_exception_and_wedged_cleanup_own_context_state_correctly(tmp_path, monkeypatch):
    orch, _oc, project = _built_orchestrator(tmp_path, [])
    ordinary_state = _state()

    def ordinary_failure(*_args, **_kwargs):
        project.context_rollover = ordinary_state
        if False:
            yield None
        raise RuntimeError("synthetic")

    monkeypatch.setattr(orch, "_build_stream", ordinary_failure)
    with pytest.raises(RuntimeError, match="synthetic"):
        list(orch.build_stream("Build.", conversation="conv-cleanup"))
    assert project.context_rollover is None and not orch.turn_busy()

    wedged_state = _state()

    def wedged_failure(*_args, **_kwargs):
        project.context_rollover = wedged_state
        orch._turn_wedged = True
        if False:
            yield None
        raise TurnWedged()

    monkeypatch.setattr(orch, "_build_stream", wedged_failure)
    list(orch.build_stream("Build.", conversation="conv-wedged"))
    assert project.context_rollover is wedged_state and orch._turn_lock.locked()


def test_success_cancellation_exception_and_terminal_limit_clear_active_state(
        tmp_path, monkeypatch):
    orch, _oc, project = _built_orchestrator(tmp_path, [])

    def run_case(outcome):
        state = _state()

        def stream(*_args, **_kwargs):
            project.context_rollover = state
            if outcome == "cancelled":
                state.claim_cancellation()
            elif outcome == "context_limit":
                state.decide(total_wire_bytes=2_000_000, media_bytes=0,
                             measurement_status="complete")
            if outcome == "exception":
                if False:
                    yield None
                raise RuntimeError("synthetic")
            yield {"type": "done", "ok": outcome == "success", "decision": outcome}

        monkeypatch.setattr(orch, "_build_stream", stream)
        if outcome == "exception":
            with pytest.raises(RuntimeError, match="synthetic"):
                list(orch.build_stream("Build.", conversation=f"conv-{outcome}"))
        else:
            list(orch.build_stream("Build.", conversation=f"conv-{outcome}"))
        assert project.context_rollover is None
        assert not orch.turn_busy()

    for outcome in ("success", "cancelled", "exception", "context_limit"):
        run_case(outcome)


def test_user_stop_wins_over_a_pending_rollover_and_offers_no_continue(tmp_path):
    orch, oc, project = _built_orchestrator(tmp_path, [Turn()])
    original_send = oc.send_prompt

    def stop_with_pending_rollover(session_id, text, *args, **kwargs):
        original_send(session_id, text, *args, **kwargs)
        project.context_rollover.decide(
            total_wire_bytes=786_433, media_bytes=0, measurement_status="complete")
        project.stop_requested = True

    oc.send_prompt = stop_with_pending_rollover
    events = list(orch.build_stream("Build it.", conversation="conv-stop"))
    assert [event["type"] for event in events].count("stopped") == 1
    assert all(event["type"] not in {"build-rollover", "build-context-limit"}
               for event in events)
    assert project.context_continuations.latest() is None
    assert project.context_rollover is None


def test_continue_restores_exact_direct_file_and_resource_references(
        tmp_path, monkeypatch):
    orch, _oc, project = _built_orchestrator(tmp_path, [])
    typed = {
        "source": "public/data/design.md", "handler": "markdown", "selector": "",
        "sha256": "a" * 64, "status": "prepared",
    }
    resource = {"kind": "data_source", "id": "warehouse-1", "name": "Warehouse",
                "table": "orders", "private": "PRIVATE_UNUSED_REQUEST_FIELD" * 10_000}
    monkeypatch.setattr(
        orch, "_context_rollover_packet",
        lambda *_args: "CURRENT SOURCE MAP WITH APPROVED ACCESS METHODS")
    captured = {}

    def capture_stream(prompt, mentions=None, resources=None, **kwargs):
        captured.update(prompt=prompt, mentions=mentions, resources=resources, **kwargs)
        yield {"type": "done", "ok": True, "decision": "typecheck clean"}

    monkeypatch.setattr(orch, "_build_stream", capture_stream)
    available = project.context_continuations.offer(
        conversation="conv-ref", app_id=project.workspace.app_id,
        intent=BuildIntent.for_direct("request"), baseline_digest="base",
        file_references=[typed], resource_references=[resource])
    status, record, token = project.context_continuations.claim(
        available.continuation_id, "conv-ref", project.workspace.app_id)
    assert status == "claimed"
    ticket, _ = orch.prepare_stream_turn(
        "turn-ref", kind="build", conversation="conv-ref", app=True)
    events = list(orch.continue_build_stream(record, token, turn_ticket=ticket))
    assert events[-1]["ok"] is True
    assert captured["mentions"] == ["public/data/design.md"]
    assert captured["explicit_references"] == [typed]
    assert captured["resources"] == [
        {"kind": "data_source", "id": "warehouse-1", "table": "orders"}]
    assert "PRIVATE_UNUSED_REQUEST_FIELD" not in repr(record)
    assert captured["continuation_note"] == "CURRENT SOURCE MAP WITH APPROVED ACCESS METHODS"


def test_continuation_reference_carriers_are_immutable_copies():
    registry = ContextContinuationRegistry(replace(
        BuildPolicy(), build_context_continuation_reference_max_count=2))
    file_record = {"source": "public/data/design.md", "handler": "markdown",
                   "selector": "", "sha256": "a" * 64, "status": "prepared"}
    private = "PRIVATE_UNUSED_REQUEST_FIELD" * 10_000
    resource = {"kind": "data_source", "id": "warehouse-1", "name": "Warehouse",
                "unused": private}
    continuation = registry.offer(
        conversation="conv", app_id="app", intent=BuildIntent.for_direct("build"),
        file_references=[file_record], resource_references=[resource])
    file_record["source"] = "changed"
    resource["id"] = "changed"
    assert continuation.file_references()[0]["source"] == "public/data/design.md"
    assert continuation.resource_references()[0]["id"] == "warehouse-1"
    assert set(continuation.resource_references()[0]) == {"kind", "id"}
    assert private not in repr(continuation)
    bounded = registry.offer(
        conversation="conv", app_id="app", intent=BuildIntent.for_direct("build"),
        resource_references=[
            {"kind": "llm_alias", "id": str(index)} for index in range(10)])
    assert len(bounded.resource_references()) == 2
    combined = registry.offer(
        conversation="conv", app_id="app", intent=BuildIntent.for_direct("build"),
        file_references=[
            {**file_record, "source": f"public/data/{index}.md"} for index in range(2)],
        resource_references=[{"kind": "llm_alias", "id": "must-not-fit"}])
    assert len(combined.file_references()) == 2
    assert combined.resource_references() == []


def test_only_bound_resource_handles_enter_a_continuation_record(tmp_path):
    orch, _oc, project = _built_orchestrator(tmp_path, [])
    project.workspace.update_bindings(lambda _rows: [{
        "kind": "llm_alias", "id": "alias-1", "name": "Model",
        "display_name": "Model",
    }])
    private = "PRIVATE_UNUSED_REQUEST_FIELD" * 10_000
    accepted = orch._continuation_resource_references(project, [
        {"kind": "llm_alias", "id": "alias-1", "name": "Model", "unused": private},
        {"kind": "llm_alias", "id": "not-bound", "unused": private},
    ])
    assert accepted == [{"kind": "llm_alias", "id": "alias-1"}]
    continuation = project.context_continuations.offer(
        conversation="conv", app_id=project.workspace.app_id,
        intent=BuildIntent.for_direct("build"), resource_references=accepted)
    assert continuation.resource_references() == accepted
    assert private not in repr(continuation)


def test_registry_is_one_slot_scoped_idempotent_and_keeps_the_canonical_intent_by_reference():
    registry = ContextContinuationRegistry()
    intent = BuildIntent.for_approved(("PRIVATE_REQUEST",), "PRIVATE_PLAN", "answer", "handoff")
    first = registry.offer(
        conversation="conv-a", app_id="app-a", intent=intent,
        approved_plan_record_id="plan-1", phase_id="2 of 4",
        repair_objective="runtime_repair", source_map_digest="source",
        baseline_digest="base", current_digest="current", parent_turn_id="turn-a")
    assert first.intent is intent
    assert registry.claim(first.continuation_id, "conv-b", "app-a")[0] == "invalid"
    assert registry.claim(first.continuation_id, "conv-a", "app-b")[0] == "invalid"
    status, claimed, token = registry.claim(first.continuation_id, "conv-a", "app-a")
    assert status == "claimed" and claimed.intent is intent and token
    assert registry.claim(first.continuation_id, "conv-a", "app-a")[0] == "already_claimed"
    assert registry.release_refused(first.continuation_id, "wrong") is False
    assert registry.release_refused(first.continuation_id, token) is True
    assert registry.latest().continuation_id == first.continuation_id

    second_intent = BuildIntent.for_direct("newer")
    second = registry.offer(conversation="conv-a", app_id="app-a", intent=second_intent)
    assert registry.latest().continuation_id == second.continuation_id
    assert registry.claim(first.continuation_id, "conv-a", "app-a")[0] == "invalid"
    registry.invalidate_available()
    assert registry.latest() is None
    assert ContextContinuationRegistry().latest() is None  # restart replay is display-only


def test_invalidating_available_does_not_steal_a_claimed_continuation_token():
    registry = ContextContinuationRegistry()
    offered = registry.offer(
        conversation="conv", app_id="app", intent=BuildIntent.for_direct("build"))
    status, _claimed, token = registry.claim(offered.continuation_id, "conv", "app")
    assert status == "claimed" and token
    registry.invalidate_available()
    assert registry.release_refused(offered.continuation_id, token) is True
    assert registry.latest().continuation_id == offered.continuation_id


def test_opaque_id_and_public_record_contain_no_prompt_path_or_private_content():
    registry = ContextContinuationRegistry()
    private = "PRIVATE_SENTINEL_/a/path_and_prompt"
    record = registry.offer(
        conversation="conv", app_id="app", intent=BuildIntent.for_direct(private),
        source_map_digest="digest", baseline_digest="base", current_digest="current")
    public = record.public()
    assert set(public) == {"continuationId", "conversation", "appId", "state"}
    assert private not in json.dumps(public)
    assert "/" not in record.continuation_id


class _Snapshot:
    def working_tree_hash(self):
        return "current"

    def changed_paths(self, before, after):
        assert before == "base" and after == "current"
        return ["src/App.tsx"]


class _App:
    def __init__(self, root: Path):
        self.path = root


class _Project:
    def __init__(self, root: Path):
        self.snapshot = _Snapshot()
        self._app = _App(root)

    def app_for_turn(self):
        return self._app


def test_clean_packet_uses_disk_orientation_fixed_repair_enum_and_no_old_history(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("export function App() {}")
    private = "PRIVATE_RAW_TOOL_RESULT_AND_ASSISTANT_PROSE"
    intent = BuildIntent.for_phase((private,), "Build the chart", "2 of 4", "", ())
    packet = Orchestrator._context_rollover_packet(
        _Project(tmp_path), intent, "base", "runtime_repair")
    assert "clean context" in packet
    assert "src/App.tsx" in packet
    assert "Build the chart" in packet
    assert "runtime_repair" in packet
    assert private not in packet


def test_automatic_rollover_request_has_canonical_intent_and_disk_packet_without_private_history(
        tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("export const current = true")
    intent = BuildIntent.for_approved(
        ("Build the approved dashboard.",), "APPROVED PLAN BODY", "ANSWERED QUESTIONS", "")
    private = "PRIVATE_TOOL_OUTPUT_ASSISTANT_PROSE_REJECTED_REQUEST"
    packet = Orchestrator._context_rollover_packet(
        _Project(tmp_path), intent, "base", "typecheck_repair")
    outgoing = build_intent.install(
        {"model": "test", "stream": True,
         "messages": [{"role": "user", "content": packet}]},
        Protocol.CHAT, intent)
    rendered = json.dumps(outgoing)
    assert "APPROVED PLAN BODY" in rendered
    assert "ANSWERED QUESTIONS" in rendered
    assert "Build the approved dashboard." in rendered
    assert "Existing source paths" in packet
    assert "src/App.tsx" in packet
    assert "Current implementation objective" in packet
    assert "typecheck_repair" in packet
    assert private not in rendered
    assert build_intent.inspect(outgoing, Protocol.CHAT, intent).status == "ok"


def test_private_prior_content_is_absent_from_model_diagnostics_logs_ui_and_continuation(
        native_env, caplog):
    assistant_private = "PRIVATE_PRIOR_ASSISTANT_TEXT"
    tool_private = "PRIVATE_RAW_TOOL_RESULT"
    rejected_private = "PRIVATE_REJECTED_REQUEST_BODY"
    client, orch, gateway = native_env
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "implement"})
    project = orch._project
    oc = FakeOpenCode(project.workspace.path)
    orch._oc_client = oc
    intent = BuildIntent.for_direct("Build the dashboard.")
    project.active_build_intent = intent
    project.build_conversation = "conv-private"
    state = _state(limit=4096, baseline=project.snapshot.working_tree_hash())
    project.context_rollover = state
    rejected = request_body(Protocol.CHAT, "GLM 5.3 OR", messages=[
        {"role": "assistant", "content": assistant_private},
        {"role": "tool", "tool_call_id": "prior-read", "content": tool_private},
        {"role": "user", "content": rejected_private + "x" * 20_000},
    ])
    with caplog.at_level(logging.INFO, logger="sage.context_rollover"):
        with active(orch) as headers:
            first = client.post(
                "/v1/sage/chat/completions", headers=headers, json=rejected)
            assert first.status_code == 400
            assert gateway.seen == []
            decision = state.consume_pending()
            assert decision is not None and decision.action is ContextAction.ROLLOVER
            assert state.begin_rollover(headers["X-Session-Id"])
            new_session = orch._replace_build_session(
                project, oc, project.build_conversation,
                reason="context_rollover", persist=False)
            assert state.activate_rollover()
            packet = Orchestrator._context_rollover_packet(
                project, intent, state.baseline, "implementation")
            clean = client.post(
                "/v1/sage/chat/completions",
                headers={"X-Session-Id": new_session},
                json=request_body(
                    Protocol.CHAT, "GLM 5.3 OR",
                    messages=[{"role": "user", "content": packet}]),
            )
            assert clean.status_code == 200
            second = client.post(
                "/v1/sage/chat/completions",
                headers={"X-Session-Id": new_session}, json=rejected)
            assert second.status_code == 400
            assert state.consume_pending().action is ContextAction.OFFER_CONTINUE
    assert len(gateway.seen) == 1
    outgoing = gateway.seen[0][0]
    continuation = project.context_continuations.offer(
        conversation="conv-private", app_id=project.workspace.app_id, intent=intent,
        source_map_digest="digest", baseline_digest=state.baseline,
        current_digest=project.snapshot.working_tree_hash())
    ui_event = {
        "type": "build-context-limit",
        "message": ("The build reached its context limit twice. Current app changes are saved. "
                    "Continue in a new clean session."),
        "kept": True,
        "continuationId": continuation.continuation_id,
    }
    surfaces = [
        json.dumps(outgoing), json.dumps(state.diagnostic()),
        "\n".join(record.getMessage() for record in caplog.records),
        json.dumps(ui_event), repr(continuation), first.text, second.text,
    ]
    for private in (assistant_private, tool_private, rejected_private):
        assert all(private not in surface for surface in surfaces)


@pytest.fixture
def native_env(tmp_path, monkeypatch):
    return _running.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("model,protocol", [
    ("GLM 5.3 OR", Protocol.CHAT),
    ("Opus-4.8", Protocol.MESSAGES),
    ("gpt-5.4", Protocol.RESPONSES),
])
def test_native_first_breach_has_exact_protocol_body_and_never_reaches_gateway(
        native_env, model, protocol):
    client, orch, gateway = native_env
    client.post("/api/project/model", json={"pick": model, "mode": "implement"})
    project = orch._project
    project.context_rollover = _state(limit=1)
    project.active_build_intent = BuildIntent.for_direct("build")
    with active(orch) as headers:
        response = dispatch(client, headers, protocol, model)
        retry = dispatch(client, headers, protocol, model)
    assert response.status_code == retry.status_code == 400
    assert gateway.seen == []
    error = {
        "type": "invalid_request_error",
        "message": native_routes._CONTEXT_ROLLOVER_REQUIRED,
    }
    expected = (
        {"type": "error", "error": error}
        if protocol is Protocol.MESSAGES else
        {"error": {**error, "code": "sage_context_rollover_required", "param": None}}
    )
    assert response.json() == retry.json() == expected
    assert project.context_rollover.consume_pending().action is ContextAction.ROLLOVER
    assert project.context_rollover.consume_pending() is None


def test_native_retry_from_retired_session_stays_local(native_env):
    client, orch, gateway = native_env
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "implement"})
    project = orch._project
    state = _state(limit=1)
    _cross_once(state, total=2)
    project.context_rollover = state
    project.active_build_intent = BuildIntent.for_direct("build")
    project.active_session_id = "fresh-session"
    orch._turn_lock.acquire()
    timing.start_turn("build", turn_id="retired-retry", conversation_id="conversation")
    try:
        response = dispatch(
            client, {"X-Session-Id": "old-session"}, Protocol.CHAT, "GLM 5.3 OR")
        record = timing.finish_turn(decision="context_rollover_required")
    finally:
        orch._turn_lock.release()
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "sage_context_rollover_required"
    assert gateway.seen == []
    calls = timing.as_dict(record)["calls"]
    assert len(calls) == 1
    assert calls[0]["outcome"] == "context_rollover_required"
    assert calls[0]["sessionId"] == "old-session"
    assert calls[0]["rootSessionId"] == "fresh-session"


@pytest.mark.parametrize("protocol", [
    Protocol.CHAT, Protocol.MESSAGES, Protocol.RESPONSES,
])
def test_retired_native_session_rejects_invalid_body_before_decoding(native_env, protocol):
    client, orch, gateway = native_env
    project = orch._project
    state = _state(limit=1)
    _cross_once(state, total=2)
    project.context_rollover = state
    project.active_build_intent = BuildIntent.for_direct("build")
    project.active_session_id = "fresh-session"
    orch._turn_lock.acquire()
    timing.start_turn("build", turn_id="retired-invalid-body")
    path = {
        Protocol.CHAT: "chat/completions",
        Protocol.MESSAGES: "anthropic/messages",
        Protocol.RESPONSES: "responses",
    }[protocol]
    try:
        response = client.post(
            f"/v1/sage/{path}", headers={"X-Session-Id": "old-session"}, content=b"{")
        record = timing.finish_turn(decision="context_rollover_required")
    finally:
        orch._turn_lock.release()
    error = {
        "type": "invalid_request_error",
        "message": native_routes._CONTEXT_ROLLOVER_REQUIRED,
    }
    expected = (
        {"type": "error", "error": error}
        if protocol is Protocol.MESSAGES else
        {"error": {**error, "code": "sage_context_rollover_required", "param": None}}
    )
    assert response.status_code == 400
    assert response.json() == expected
    assert gateway.seen == []
    calls = timing.as_dict(record)["calls"]
    assert len(calls) == 1
    assert calls[0]["outcome"] == "context_rollover_required"


def test_pre_edit_guard_wins_before_whole_context_limit(native_env):
    client, orch, gateway = native_env
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "implement"})
    project = orch._project
    project.pre_edit_guard = PreEditGuard(
        replace(BuildPolicy(), pre_edit_model_call_limit=0), "base", lambda: "base")
    project.context_rollover = _state(limit=1)
    project.active_build_intent = BuildIntent.for_direct("build")
    with active(orch) as headers:
        assert dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR").status_code == 409
    assert gateway.seen == []
    assert project.pre_edit_guard.consume_pending().action is PreEditAction.RECOVER
    assert project.context_rollover.consume_pending() is None


def test_native_measurement_failure_does_not_route_or_spend_rollover(native_env, monkeypatch):
    client, orch, gateway = native_env
    client.post("/api/project/model", json={"pick": "GLM 5.3 OR", "mode": "implement"})
    project = orch._project
    project.context_rollover = _state(limit=999_999)
    project.active_build_intent = BuildIntent.for_direct("build")
    monkeypatch.setattr(native_routes, "measure", lambda *_args, **_kwargs: None)
    with active(orch) as headers:
        response = dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR")
    assert response.status_code == 400
    assert "sage_context_measurement_unavailable" in response.text
    assert gateway.seen == []
    assert project.context_rollover.diagnostic()["rolloverCount"] == 0


def test_context_diagnostics_are_exact_content_free_and_log_transitions_do_not_name_id(caplog):
    private = "PRIVATE_SENTINEL_/tool/path/prompt"
    timing.start_turn("build", turn_id="turn", app_id="app", conversation_id="conv")
    with caplog.at_level(logging.INFO, logger="sage.context_rollover"):
        state = _state(limit=1)
        state.decide(total_wire_bytes=2, media_bytes=0, measurement_status="complete")
        state.consume_pending()
        assert state.begin_rollover("old") and state.activate_rollover()
        state.finish()
    rec = timing.finish_turn(decision="context_limit")
    direct = timing.as_dict(rec)["contextRollover"]
    exported = build_diagnostics.snapshot(
        rec, {"turnId": "turn", "appId": "app", "conversationId": "conv", "kind": "build"},
        outcome="context_limit", terminal=True)["contextRollover"]
    assert direct == exported
    assert set(direct) == {
        "policyVersion", "limitNonMediaBytes", "sessionGeneration", "rolloverCount",
        "totalWireBytes", "mediaBytes", "nonMediaContextBytes", "measurementStatus",
        "action", "continuationOffered",
    }
    assert private not in json.dumps(direct)
    logs = "\n".join(row.getMessage() for row in caplog.records)
    assert private not in logs
    assert "active->rollover_starting" in logs
    assert "rollover_starting->active" in logs
    assert "active->terminal" in logs


@pytest.mark.parametrize(
    ("decision", "expected"),
    [("context_limit", "context_limit"),
     ("context_measurement_unavailable", "context_measurement_unavailable")],
)
def test_context_terminal_decisions_have_explicit_diagnostic_outcomes(
        tmp_path, decision, expected):
    timing.start_turn("build", turn_id=f"turn-{decision}")
    build_diagnostics.begin(
        tmp_path, turn_id=f"turn-{decision}", app_id="app", conversation_id="conv",
        kind="build")
    build_diagnostics.observe({"type": "done", "ok": False, "decision": decision})
    capture = build_diagnostics._current.get()
    assert capture is not None and capture.outcome == expected
    build_diagnostics.finish(timing.finish_turn(decision=decision))


class _ContinueStub:
    def __init__(self, status="already_claimed"):
        self.status = status
        self.claims = []
        self.started = 0

    def claim_context_continuation(self, continuation_id, conversation, app_id):
        self.claims.append((continuation_id, conversation, app_id))
        if self.status == "already_claimed":
            return self.status, None, None
        if self.status == "invalid":
            return self.status, None, None
        intent = BuildIntent.for_direct("private")
        registry = ContextContinuationRegistry()
        record = registry.offer(conversation=conversation, app_id=app_id, intent=intent)
        return "claimed", replace(record, continuation_id=continuation_id), "token"

    def prepare_stream_turn(self, turn_id, **_kwargs):
        return SimpleNamespace(id=turn_id, sequence=1, epoch="epoch", granted=True), "running"

    def continue_build_stream(self, _record, _token, *, turn_ticket):
        self.started += 1
        yield {"type": "done", "ok": True, "decision": "typecheck clean"}

    def release_stream_turn(self, _ticket):
        return None

    def release_context_continuation(self, *_args):
        return True


def test_continue_endpoint_requires_exact_content_free_body_and_duplicate_is_json(monkeypatch):
    stub = _ContinueStub()
    monkeypatch.setattr(appmod, "orchestrator", stub)
    client = TestClient(appmod.control_app)
    bad = client.post("/api/project/build/continue", json={
        "continuationId": "opaque", "conversation": "conv", "appId": "app", "prompt": "PRIVATE"})
    assert bad.status_code == 400
    duplicate = client.post("/api/project/build/continue", json={
        "continuationId": "opaque", "conversation": "conv", "appId": "app"})
    assert duplicate.status_code == 200
    assert duplicate.json() == {"status": "already_claimed"}
    assert stub.started == 0


def test_continue_first_claim_is_sse_and_starts_one_turn(monkeypatch):
    stub = _ContinueStub("claimed")
    monkeypatch.setattr(appmod, "orchestrator", stub)
    response = TestClient(appmod.control_app).post("/api/project/build/continue", json={
        "continuationId": "opaque", "conversation": "conv", "appId": "app"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.count('"type": "done"') == 1
    assert stub.started == 1
