"""Issue #527: the 41 acceptance items grouped by their owning seam.

The matrix is executable documentation. Each acceptance number names the focused test group that
proves it, so a later contract change cannot silently drop an item from the test plan.
"""
from __future__ import annotations

import json
import threading
from dataclasses import replace

import pytest

from sage import build_diagnostics, timing
from sage.build_policy import BuildPolicy
from sage.gateway.protocol import Protocol
from sage.pre_edit_guard import (
    PreEditAction,
    PreEditDecision,
    PreEditGuard,
    PreEditState,
    PreEditTrigger,
)
from sage.tool_result_window import (
    CompletedToolResult,
    apply_tool_result_window,
    completed_tool_results,
)

ACCEPTANCE_MATRIX = {
    **{n: "numeric_boundaries" for n in range(1, 13)},
    **{n: "original_result_identity" for n in range(13, 17)},
    17: "no_edit_completion", 18: "no_edit_completion",
    **{n: "clean_recovery_packet" for n in range(19, 23)},
    **{n: "authoritative_edit" for n in range(23, 29)},
    **{n: "brake_precedence" for n in range(29, 32)},
    32: "transport_replacement", 33: "transport_replacement",
    34: "concurrency", 35: "concurrency",
    36: "lifecycle_scope", 37: "lifecycle_scope",
    **{n: "ui_replay" for n in range(38, 41)},
    41: "redacted_diagnostics",
}


def _guard(*, calls=12, request=524_288, results=196_608, tree=None):
    current = tree if tree is not None else ["base"]
    policy = replace(
        BuildPolicy(),
        pre_edit_model_call_limit=calls,
        pre_edit_request_non_media_max_bytes=request,
        pre_edit_original_tool_result_max_bytes=results,
    )
    return PreEditGuard(policy, "base", lambda: current[0]), current


def _recover(guard):
    assert guard.consume_pending().action is PreEditAction.RECOVER
    guard.start_recovery()
    assert guard.state is PreEditState.RECOVERY_ARMED


def test_all_41_acceptance_items_have_an_owning_test_group():
    assert set(ACCEPTANCE_MATRIX) == set(range(1, 42))


@pytest.mark.parametrize("attempt", ["initial", "recovery"])
def test_numeric_model_call_boundary_routes_12_and_blocks_13(attempt):
    guard, _ = _guard()
    if attempt == "recovery":
        guard.no_edit_completion()
        _recover(guard)
    assert [guard.decide_request((), 1).action for _ in range(12)] == [PreEditAction.ROUTE] * 12
    decision = guard.decide_request((), 1)
    assert decision.action is (PreEditAction.RECOVER if attempt == "initial" else PreEditAction.STOP)
    assert decision.trigger is PreEditTrigger.MODEL_CALLS


@pytest.mark.parametrize(
    ("attempt", "size", "action"),
    [
        ("initial", 524_288, PreEditAction.ROUTE),
        ("initial", 524_289, PreEditAction.RECOVER),
        ("recovery", 524_288, PreEditAction.ROUTE),
        ("recovery", 524_289, PreEditAction.STOP),
    ],
)
def test_forwarded_non_media_request_boundary_is_inclusive(attempt, size, action):
    guard, _ = _guard(calls=99)
    if attempt == "recovery":
        guard.no_edit_completion()
        _recover(guard)
    decision = guard.decide_request((), size)
    assert decision.action is action
    assert decision.trigger is (PreEditTrigger.NONE if action is PreEditAction.ROUTE
                                else PreEditTrigger.REQUEST_BYTES)


@pytest.mark.parametrize("attempt", ["initial", "recovery"])
def test_original_unique_result_boundary_is_inclusive_and_pre_window(attempt):
    guard, _ = _guard(calls=99, request=999_999)
    if attempt == "recovery":
        guard.no_edit_completion()
        _recover(guard)
    result = CompletedToolResult("native-a", 196_608)
    assert guard.decide_request((result,), 1).action is PreEditAction.ROUTE
    # A cumulative replay of one native ID counts once; equal bytes under another ID count again.
    assert guard.decide_request((result,), 1).action is PreEditAction.ROUTE
    decision = guard.decide_request((result, CompletedToolResult("native-b", 1)), 1)
    assert decision.action is (PreEditAction.RECOVER if attempt == "initial" else PreEditAction.STOP)
    assert decision.trigger is PreEditTrigger.TOOL_RESULT_BYTES


def test_trigger_priority_is_calls_then_request_then_results():
    guard, _ = _guard(calls=0, request=0, results=0)
    decision = guard.decide_request((CompletedToolResult("a", 1),), 1)
    assert decision.trigger is PreEditTrigger.MODEL_CALLS


def test_completed_results_keep_native_identity_and_original_pre_window_bytes():
    request = {"messages": [{"role": "tool", "tool_call_id": " native-id ", "content": "x" * 20_000}]}
    original = completed_tool_results(request, Protocol.CHAT)
    bounded, _ = apply_tool_result_window(request, BuildPolicy())
    assert original == (CompletedToolResult(" native-id ", 20_000),)
    assert len(bounded["messages"][0]["content"].encode()) <= BuildPolicy().tool_result_max_bytes


@pytest.mark.parametrize(
    ("protocol", "payload", "identity"),
    [
        (Protocol.CHAT, {"messages": [{"role": "tool", "tool_call_id": "c", "content": "x"}]}, "c"),
        (Protocol.MESSAGES, {"messages": [{"content": [{"type": "tool_result", "tool_use_id": "m",
                                                          "content": [{"type": "text", "text": "x"},
                                                                      {"type": "image", "data": "PRIVATE"}]}]}]}, "m"),
        (Protocol.RESPONSES, {"input": [{"type": "function_call_output", "call_id": "r",
                                           "output": "x"}]}, "r"),
    ],
)
def test_completed_result_protocols_use_native_ids_and_exclude_media(protocol, payload, identity):
    result = completed_tool_results(payload, protocol)[0]
    assert result.identity == identity
    assert 0 < result.non_media_bytes < 100
    assert "PRIVATE" not in repr(result)


def test_missing_completed_result_identity_is_a_fixed_local_error():
    with pytest.raises(ValueError, match="missing its native result identity"):
        completed_tool_results({"messages": [{"role": "tool", "content": "private"}]}, Protocol.CHAT)


def test_no_edit_completion_gets_one_clean_recovery_then_terminal_stop():
    guard, _ = _guard(calls=99)
    assert guard.no_edit_completion().action is PreEditAction.RECOVER
    _recover(guard)
    decision = guard.no_edit_completion()
    assert decision == guard.consume_pending()
    assert decision.action is PreEditAction.STOP
    assert decision.trigger is PreEditTrigger.NO_EDIT_COMPLETION


def test_authoritative_tree_edit_disarms_forever_and_allows_later_calls():
    guard, tree = _guard(calls=12)
    for _ in range(11):
        guard.decide_request((), 1)
    tree[0] = "edited-by-shell"
    assert guard.decide_request((), 1).action is PreEditAction.DISARM
    assert guard.state is PreEditState.DISARMED
    assert guard.decide_request((), 999_999).action is PreEditAction.ROUTE
    guard.rebaseline("later")
    assert guard.state is PreEditState.DISARMED and guard.baseline == "base"


def test_tool_success_is_only_a_hint_and_attachment_rebaseline_is_not_an_edit():
    guard, tree = _guard(calls=99)
    # No tool receipt enters this API. An unchanged tree stays armed.
    assert guard.decide_request((), 1).action is PreEditAction.ROUTE
    tree[0] = "attachment-change"
    guard.rebaseline(tree[0])
    assert guard.check_edit() is False
    tree[0] = "agent-edit"
    assert guard.check_edit() is True


def test_transport_replacement_changes_only_generation_and_keeps_recovery_allowance():
    guard, _ = _guard(calls=2)
    guard.decide_request((), 1)
    guard.note_session_replacement()
    diagnostic = guard.diagnostic()
    assert diagnostic["sessionGeneration"] == 1 and diagnostic["modelCalls"] == 1
    assert guard.decide_request((), 1).action is PreEditAction.ROUTE
    assert guard.decide_request((), 1).action is PreEditAction.RECOVER


def test_specific_brake_and_cancellation_win_without_recovery():
    guard, _ = _guard()
    assert guard.claim_existing_terminal() is True
    assert guard.state is PreEditState.TERMINAL and guard.specific_terminal
    assert guard.consume_pending() is None


def test_guard_and_existing_brake_have_one_atomic_winner():
    """Reviewer case 2: a brake cannot continue after recovery already owns the decision."""
    for _ in range(50):
        guard, _tree = _guard(calls=0)
        barrier = threading.Barrier(3)
        results = {}

        def threshold(current_guard=guard, current_barrier=barrier, current_results=results):
            current_barrier.wait()
            current_results["guard"] = current_guard.decide_request((), 1).action

        def brake(current_guard=guard, current_barrier=barrier, current_results=results):
            current_barrier.wait()
            current_results["brake"] = current_guard.claim_existing_terminal()

        threads = [threading.Thread(target=threshold), threading.Thread(target=brake)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        assert (results["guard"], results["brake"]) in {
            (PreEditAction.RECOVER, False),
            (PreEditAction.STOP, True),
        }

    guard, _ = _guard(calls=0)
    assert guard.decide_request((), 1).action is PreEditAction.RECOVER
    assert guard.claim_existing_terminal() is False
    assert guard.claim_cancellation() is True
    assert guard.consume_pending() is None


@pytest.mark.parametrize("recovery", [False, True])
def test_simultaneous_thresholds_have_one_atomic_pending_transition(recovery):
    guard, _ = _guard(calls=0)
    if recovery:
        guard.no_edit_completion()
        _recover(guard)
    barrier = threading.Barrier(8)
    decisions = []

    def cross():
        barrier.wait()
        decisions.append(guard.decide_request((), 1).action)

    threads = [threading.Thread(target=cross) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    expected = PreEditAction.STOP if recovery else PreEditAction.RECOVER
    assert set(decisions) == {expected}
    assert guard.consume_pending().action is expected
    assert guard.consume_pending() is None


def test_begin_recovery_rechecks_the_tree_and_disarms_instead_of_creating_a_session():
    """Reviewer case 3: an edit in the threshold-to-recovery window cancels recovery."""
    guard, tree = _guard(calls=0)
    assert guard.decide_request((), 1).action is PreEditAction.RECOVER
    assert guard.consume_pending().action is PreEditAction.RECOVER
    tree[0] = "late-agent-edit"

    decision = guard.begin_recovery()

    assert decision.action is PreEditAction.DISARM
    assert guard.state is PreEditState.DISARMED


def test_request_measurement_failure_first_checks_for_an_existing_edit():
    """A serializer failure cannot override an edit that already disarmed the guard."""
    guard, tree = _guard()
    tree[0] = "edited-before-measurement-failure"

    decision = guard.fail_request_measurement()

    assert decision.action is PreEditAction.DISARM
    assert guard.state is PreEditState.DISARMED


def test_abort_failure_does_not_override_a_terminal_owner():
    """A late abort failure keeps the cancellation decision that already won."""
    guard, _tree = _guard(calls=0)
    assert guard.decide_request((), 1).action is PreEditAction.RECOVER
    assert guard.claim_cancellation() is True
    terminal = guard.diagnostic()

    decision = guard.fail_session_abort()

    assert decision.action is PreEditAction.STOP
    assert decision.trigger is PreEditTrigger.NONE
    assert guard.diagnostic() == terminal


@pytest.mark.parametrize("site", ["request", "completion", "begin_recovery"])
def test_tree_witness_failure_is_a_fixed_failure_and_never_a_false_no_edit(site):
    """Reviewer case 7: an unreadable tree cannot start recovery or claim a no-edit stop."""
    def unreadable_tree():
        raise OSError("PRIVATE_TREE_PATH")

    guard = PreEditGuard(BuildPolicy(pre_edit_model_call_limit=0), "base", unreadable_tree)
    if site == "request":
        decision = guard.decide_request((), 1)
    elif site == "completion":
        decision = guard.no_edit_completion()
    else:
        guard._current_tree = lambda: "base"  # establish only the threshold transition
        assert guard.decide_request((), 1).action is PreEditAction.RECOVER
        guard.consume_pending()
        guard._current_tree = unreadable_tree
        decision = guard.begin_recovery()

    assert decision == PreEditDecision(
        PreEditAction.FAIL, PreEditTrigger.TREE_WITNESS_UNAVAILABLE)
    assert guard.state is PreEditState.TERMINAL
    assert guard.diagnostic()["action"] == "fail"
    assert "PRIVATE_TREE_PATH" not in json.dumps(guard.diagnostic())


def test_pre_edit_diagnostics_are_exact_and_content_free():
    private = "PRIVATE_SENTINEL_/app/path_result-id_prompt"
    timing.start_turn("build", turn_id="turn", app_id="app", conversation_id="conversation")
    timing.pre_edit_guard({
        "policyVersion": 1, "attempt": "initial", "sessionGeneration": 0,
        "state": "armed", "modelCalls": 1, "modelCallLimit": 12,
        "originalUniqueToolResultBytes": 4, "toolResultLimitBytes": 196_608,
        "maxForwardedNonMediaRequestBytes": 8, "requestLimitBytes": 524_288,
        "firstEditObserved": False, "trigger": "none", "action": "route",
        "prompt": private, private: private,
    })
    rec = timing.finish_turn()
    direct = timing.as_dict(rec)["preEditGuard"]
    exported = build_diagnostics.snapshot(
        rec, {"turnId": "turn", "appId": "app", "conversationId": "conversation",
              "kind": "build"}, terminal=True,
    )["preEditGuard"]
    assert direct == exported
    assert set(direct) == {
        "policyVersion", "attempt", "sessionGeneration", "state", "modelCalls",
        "modelCallLimit", "originalUniqueToolResultBytes", "toolResultLimitBytes",
        "maxForwardedNonMediaRequestBytes", "requestLimitBytes", "firstEditObserved",
        "trigger", "action",
    }
    assert private not in json.dumps(direct)
