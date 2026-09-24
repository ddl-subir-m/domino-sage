"""Thread-safe limits for implementation work before the first app edit."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum

from . import timing
from .build_policy import BuildPolicy
from .tool_result_window import CompletedToolResult

log = logging.getLogger("sage.pre_edit_guard")


class PreEditState(str, Enum):
    DISABLED = "disabled"
    INITIAL_ARMED = "initial_armed"
    RECOVERY_STARTING = "recovery_starting"
    RECOVERY_ARMED = "recovery_armed"
    DISARMED = "disarmed"
    TERMINAL = "terminal"


class PreEditTrigger(str, Enum):
    NONE = "none"
    MODEL_CALLS = "model_calls"
    REQUEST_BYTES = "request_bytes"
    TOOL_RESULT_BYTES = "tool_result_bytes"
    NO_EDIT_COMPLETION = "no_edit_completion"
    MODEL_OUTPUT_LIMIT = "model_output_limit"
    MODEL_NO_ACTION = "model_no_action"
    REQUEST_MEASUREMENT_UNAVAILABLE = "request_measurement_unavailable"
    TREE_WITNESS_UNAVAILABLE = "tree_witness_unavailable"
    SESSION_ABORT_UNCONFIRMED = "session_abort_unconfirmed"


class PreEditAction(str, Enum):
    ROUTE = "route"
    RECOVER = "recover"
    STOP = "stop"
    DISARM = "disarm"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class PreEditDecision:
    action: PreEditAction
    trigger: PreEditTrigger = PreEditTrigger.NONE


class PreEditGuard:
    """Own one implementation Build's pre-edit state and counters.

    The callback returns the current authoritative working-tree identity. Result content and
    request content never enter this object.
    """

    def __init__(self, policy: BuildPolicy, baseline: str,
                 current_tree: Callable[[], str]) -> None:
        self._policy = policy
        self._baseline = baseline
        self._current_tree = current_tree
        self._lock = threading.RLock()
        self._state = PreEditState.INITIAL_ARMED
        self._attempt = "initial"
        self._session_generation = 0
        self._recoveries = 0
        self._model_calls = 0
        self._tool_result_bytes = 0
        self._max_request_bytes = 0
        self._seen_results: set[str] = set()
        self._first_edit_observed = False
        self._trigger = PreEditTrigger.NONE
        self._action = PreEditAction.ROUTE
        self._pending: PreEditDecision | None = None
        self._specific_terminal = False
        self._publish()

    @property
    def baseline(self) -> str:
        with self._lock:
            return self._baseline

    @property
    def state(self) -> PreEditState:
        with self._lock:
            return self._state

    @property
    def is_armed(self) -> bool:
        with self._lock:
            return self._state in {
                PreEditState.INITIAL_ARMED, PreEditState.RECOVERY_ARMED,
            }

    @property
    def specific_terminal(self) -> bool:
        with self._lock:
            return self._specific_terminal

    def _diagnostic_locked(self) -> dict:
        if self._state in {PreEditState.INITIAL_ARMED, PreEditState.RECOVERY_ARMED}:
            state = "armed"
        elif self._state is PreEditState.RECOVERY_STARTING:
            state = "recovering"
        elif self._state is PreEditState.DISARMED:
            state = "disarmed"
        else:
            state = "terminal"
        return {
            "policyVersion": 1,
            "attempt": self._attempt,
            "sessionGeneration": self._session_generation,
            "state": state,
            "modelCalls": self._model_calls,
            "modelCallLimit": self._policy.pre_edit_model_call_limit,
            "originalUniqueToolResultBytes": self._tool_result_bytes,
            "toolResultLimitBytes": self._policy.pre_edit_original_tool_result_max_bytes,
            "maxForwardedNonMediaRequestBytes": self._max_request_bytes,
            "requestLimitBytes": self._policy.pre_edit_request_non_media_max_bytes,
            "firstEditObserved": self._first_edit_observed,
            "trigger": self._trigger.value,
            "action": self._action.value,
        }

    def diagnostic(self) -> dict:
        with self._lock:
            return self._diagnostic_locked()

    def _publish(self) -> None:
        timing.pre_edit_guard(self._diagnostic_locked())

    def _transition(self, state: PreEditState, decision: PreEditDecision, *, pending: bool) -> None:
        old = self._state
        self._state = state
        self._trigger = decision.trigger
        self._action = decision.action
        if pending and self._pending is None:
            self._pending = decision
        log.info(
            "pre-edit guard transition: state=%s->%s attempt=%s generation=%d trigger=%s "
            "action=%s model_calls=%d tool_result_bytes=%d max_request_bytes=%d",
            old.value, state.value, self._attempt, self._session_generation,
            decision.trigger.value, decision.action.value, self._model_calls,
            self._tool_result_bytes, self._max_request_bytes,
        )
        self._publish()

    def _witness_locked(self, *, allow_recovery_starting: bool = False) -> PreEditDecision | None:
        eligible = {PreEditState.INITIAL_ARMED, PreEditState.RECOVERY_ARMED}
        if allow_recovery_starting:
            eligible.add(PreEditState.RECOVERY_STARTING)
        if self._state not in eligible:
            return (PreEditDecision(PreEditAction.DISARM)
                    if self._state is PreEditState.DISARMED else None)
        try:
            current = self._current_tree()
        except Exception:
            current = ""
        if not current or not self._baseline:
            decision = PreEditDecision(
                PreEditAction.FAIL, PreEditTrigger.TREE_WITNESS_UNAVAILABLE)
            self._transition(PreEditState.TERMINAL, decision, pending=True)
            return decision
        if current and self._baseline and current != self._baseline:
            self._first_edit_observed = True
            decision = PreEditDecision(PreEditAction.DISARM)
            self._pending = None
            self._transition(PreEditState.DISARMED, decision, pending=False)
            return decision
        return None

    def check_edit(self) -> bool:
        """Run the authoritative edit comparison and disarm if it changed."""
        with self._lock:
            decision = self._witness_locked()
            return decision is not None and decision.action is PreEditAction.DISARM

    def rebaseline(self, baseline: str) -> None:
        """Move the witness over a user-side attachment or approved-source change."""
        if not baseline:
            return
        with self._lock:
            if self._state in {
                PreEditState.INITIAL_ARMED,
                PreEditState.RECOVERY_STARTING,
                PreEditState.RECOVERY_ARMED,
            }:
                self._baseline = baseline
                self._publish()

    def _limit_decision_locked(self, trigger: PreEditTrigger) -> PreEditDecision:
        if (self._state is PreEditState.INITIAL_ARMED
                and self._recoveries < self._policy.pre_edit_clean_recovery_limit):
            decision = PreEditDecision(PreEditAction.RECOVER, trigger)
            self._transition(PreEditState.RECOVERY_STARTING, decision, pending=True)
            return decision
        decision = PreEditDecision(PreEditAction.STOP, trigger)
        self._transition(PreEditState.TERMINAL, decision, pending=True)
        return decision

    def decide_request(self, results: Iterable[CompletedToolResult],
                       non_media_request_bytes: int) -> PreEditDecision:
        """Decide one final native request before it can reach the gateway."""
        with self._lock:
            if self._state is PreEditState.DISARMED:
                return PreEditDecision(PreEditAction.ROUTE)
            if self._state is PreEditState.RECOVERY_STARTING:
                return PreEditDecision(PreEditAction.RECOVER, self._trigger)
            if self._state is PreEditState.TERMINAL:
                return PreEditDecision(PreEditAction.STOP, self._trigger)
            witness = self._witness_locked()
            if witness is not None:
                if witness.action is not PreEditAction.DISARM:
                    return witness
                self._model_calls += 1
                self._max_request_bytes = max(self._max_request_bytes, non_media_request_bytes)
                self._publish()
                return witness

            for result in results:
                if result.identity in self._seen_results:
                    continue
                self._seen_results.add(result.identity)
                self._tool_result_bytes += result.non_media_bytes

            # Priority is contractual when several candidates cross together.
            if self._model_calls + 1 > self._policy.pre_edit_model_call_limit:
                return self._limit_decision_locked(PreEditTrigger.MODEL_CALLS)
            if non_media_request_bytes > self._policy.pre_edit_request_non_media_max_bytes:
                return self._limit_decision_locked(PreEditTrigger.REQUEST_BYTES)
            if self._tool_result_bytes > self._policy.pre_edit_original_tool_result_max_bytes:
                return self._limit_decision_locked(PreEditTrigger.TOOL_RESULT_BYTES)

            self._model_calls += 1
            self._max_request_bytes = max(self._max_request_bytes, non_media_request_bytes)
            self._trigger = PreEditTrigger.NONE
            self._action = PreEditAction.ROUTE
            self._publish()
            return PreEditDecision(PreEditAction.ROUTE)

    def no_edit_completion(self) -> PreEditDecision:
        """Handle an OpenCode completion that left the authoritative tree unchanged."""
        return self._completion_without_edit(PreEditTrigger.NO_EDIT_COMPLETION)

    def model_output_limit(self) -> PreEditDecision:
        """Handle a provider output cap before the authoritative first app edit."""
        return self._completion_without_edit(PreEditTrigger.MODEL_OUTPUT_LIMIT)

    def model_no_action(self) -> PreEditDecision:
        """Handle a bounded model call that produced no text or tool announcement."""
        return self._completion_without_edit(PreEditTrigger.MODEL_NO_ACTION)

    def _completion_without_edit(self, trigger: PreEditTrigger) -> PreEditDecision:
        with self._lock:
            if self._state is PreEditState.DISARMED:
                return PreEditDecision(PreEditAction.DISARM)
            if self._state is PreEditState.TERMINAL:
                return PreEditDecision(PreEditAction.STOP, self._trigger)
            if self._state is PreEditState.RECOVERY_STARTING:
                return PreEditDecision(PreEditAction.RECOVER, self._trigger)
            witness = self._witness_locked()
            if witness is not None:
                return witness
            return self._limit_decision_locked(trigger)

    def begin_recovery(self) -> PreEditDecision:
        """Recheck the tree and grant recovery ownership immediately before session creation."""
        with self._lock:
            if self._state is PreEditState.DISARMED:
                return PreEditDecision(PreEditAction.DISARM)
            if self._state is PreEditState.TERMINAL:
                return PreEditDecision(PreEditAction.STOP, self._trigger)
            if self._state is not PreEditState.RECOVERY_STARTING:
                return PreEditDecision(PreEditAction.FAIL, self._trigger)
            witness = self._witness_locked(allow_recovery_starting=True)
            return witness or PreEditDecision(PreEditAction.RECOVER, self._trigger)

    def fail_request_measurement(self) -> PreEditDecision:
        """Fail closed when the final forwarded request cannot be measured."""
        with self._lock:
            if self._state is PreEditState.DISARMED:
                return PreEditDecision(PreEditAction.ROUTE)
            if self._state is PreEditState.RECOVERY_STARTING:
                return PreEditDecision(PreEditAction.RECOVER, self._trigger)
            if self._state is PreEditState.TERMINAL:
                return PreEditDecision(self._action, self._trigger)
            witness = self._witness_locked()
            if witness is not None:
                return witness
            decision = PreEditDecision(
                PreEditAction.FAIL, PreEditTrigger.REQUEST_MEASUREMENT_UNAVAILABLE)
            self._transition(PreEditState.TERMINAL, decision, pending=True)
            return decision

    def fail_session_abort(self) -> PreEditDecision:
        """Record that recovery could not take exclusive ownership of the app tree."""
        with self._lock:
            if self._state is PreEditState.DISARMED:
                return PreEditDecision(PreEditAction.DISARM)
            if self._state is PreEditState.TERMINAL:
                return PreEditDecision(self._action, self._trigger)
            decision = PreEditDecision(
                PreEditAction.FAIL, PreEditTrigger.SESSION_ABORT_UNCONFIRMED)
            self._pending = None
            self._transition(PreEditState.TERMINAL, decision, pending=False)
            return decision

    def consume_pending(self) -> PreEditDecision | None:
        with self._lock:
            decision, self._pending = self._pending, None
            return decision

    def start_recovery(self) -> bool:
        """Reset per-attempt counters after the fresh recovery session is ready."""
        with self._lock:
            if self._state is not PreEditState.RECOVERY_STARTING:
                return False
            self._recoveries += 1
            self._session_generation += 1
            self._attempt = "recovery"
            self._model_calls = 0
            self._tool_result_bytes = 0
            self._max_request_bytes = 0
            self._seen_results.clear()
            self._trigger = PreEditTrigger.NONE
            self._transition(
                PreEditState.RECOVERY_ARMED,
                PreEditDecision(PreEditAction.ROUTE),
                pending=False,
            )
            return True

    def fail_recovery_start(self) -> None:
        with self._lock:
            if self._state is PreEditState.RECOVERY_STARTING:
                self._transition(
                    PreEditState.TERMINAL,
                    PreEditDecision(PreEditAction.STOP, self._trigger),
                    pending=False,
                )

    def note_session_replacement(self) -> None:
        """Record a transport/session replacement without resetting either attempt budget."""
        with self._lock:
            if self._state in {
                PreEditState.INITIAL_ARMED,
                PreEditState.RECOVERY_STARTING,
                PreEditState.RECOVERY_ARMED,
            }:
                self._session_generation += 1
                self._publish()

    def claim_existing_terminal(self) -> bool:
        """Return whether a specific existing brake owns the terminal decision.

        A disarmed guard no longer participates, so the existing brake proceeds. A recovery or
        guard failure already in flight owns the choice and the existing brake must stand down.
        """
        with self._lock:
            if self._state is PreEditState.DISARMED:
                return True
            if self._state not in {
                PreEditState.INITIAL_ARMED,
                PreEditState.RECOVERY_ARMED,
            }:
                return False
            self._specific_terminal = True
            self._transition(
                PreEditState.TERMINAL,
                PreEditDecision(PreEditAction.STOP),
                pending=False,
            )
            return True

    def claim_cancellation(self) -> bool:
        """Let user cancellation win, including while a recovery session is being created."""
        with self._lock:
            if self._state in {PreEditState.DISARMED, PreEditState.TERMINAL}:
                return False
            self._pending = None
            self._specific_terminal = True
            self._transition(
                PreEditState.TERMINAL,
                PreEditDecision(PreEditAction.STOP),
                pending=False,
            )
            return True
