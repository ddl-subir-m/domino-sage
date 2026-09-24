"""Thread-safe whole-request context limits for one implementation Build."""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum

from . import timing
from .build_intent import BuildIntent
from .build_policy import BuildPolicy

log = logging.getLogger("sage.context_rollover")


class ContextState(str, Enum):
    ACTIVE = "active"
    ROLLOVER_STARTING = "rollover_starting"
    CONTINUE_OFFERED = "continue_offered"
    TERMINAL = "terminal"


class ContextAction(str, Enum):
    ROUTE = "route"
    ROLLOVER = "rollover"
    OFFER_CONTINUE = "offer_continue"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class ContextDecision:
    action: ContextAction
    reason: str = ""


class ContextRolloverState:
    """Own the automatic-rollover allowance for one top-level Build."""

    def __init__(self, policy: BuildPolicy, baseline: str = "") -> None:
        self._policy = policy
        self._baseline = baseline
        self._lock = threading.RLock()
        self._state = ContextState.ACTIVE
        self._generation = 0
        self._rollovers = 0
        self._total = 0
        self._media = 0
        self._non_media = 0
        self._status = "complete"
        self._action = ContextAction.ROUTE
        self._pending: ContextDecision | None = None
        self._continuation_offered = False
        self._retired_sessions: set[str] = set()
        self._publish()

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def baseline(self) -> str:
        return self._baseline

    def _diagnostic_locked(self) -> dict:
        return {
            "policyVersion": 1,
            "limitNonMediaBytes": self._policy.build_context_non_media_max_bytes,
            "sessionGeneration": self._generation,
            "rolloverCount": self._rollovers,
            "totalWireBytes": self._total,
            "mediaBytes": self._media,
            "nonMediaContextBytes": self._non_media,
            "measurementStatus": self._status,
            "action": self._action.value,
            "continuationOffered": self._continuation_offered,
        }

    def diagnostic(self) -> dict:
        with self._lock:
            return self._diagnostic_locked()

    def _publish(self) -> None:
        timing.context_rollover(self._diagnostic_locked())

    def _transition(self, state: ContextState, decision: ContextDecision, *, pending: bool) -> None:
        previous = self._state
        self._state = state
        self._action = decision.action
        if pending and self._pending is None:
            self._pending = decision
        log.info(
            "context rollover transition: state=%s->%s generation=%d rollovers=%d "
            "measurement=%s action=%s",
            previous.value, state.value, self._generation, self._rollovers,
            self._status, decision.action.value,
        )
        self._publish()

    def decide(self, *, total_wire_bytes: int, media_bytes: int,
               measurement_status: str) -> ContextDecision:
        """Decide one final request. The caller must invoke the pre-edit guard first."""
        with self._lock:
            if self._state is ContextState.ROLLOVER_STARTING:
                return ContextDecision(ContextAction.ROLLOVER, "context_limit")
            if self._state is ContextState.CONTINUE_OFFERED:
                return ContextDecision(ContextAction.OFFER_CONTINUE, "context_limit")
            if self._state is ContextState.TERMINAL:
                return ContextDecision(ContextAction.FAIL, "context_measurement_unavailable")

            valid = (
                measurement_status == "complete"
                and isinstance(total_wire_bytes, int) and not isinstance(total_wire_bytes, bool)
                and isinstance(media_bytes, int) and not isinstance(media_bytes, bool)
                and total_wire_bytes >= 0 and 0 <= media_bytes <= total_wire_bytes
            )
            if not valid:
                self._status = "unavailable"
                self._total = 0
                self._media = 0
                self._non_media = 0
                decision = ContextDecision(ContextAction.FAIL, "context_measurement_unavailable")
                self._transition(ContextState.TERMINAL, decision, pending=True)
                return decision

            self._status = "complete"
            self._total = total_wire_bytes
            self._media = media_bytes
            self._non_media = total_wire_bytes - media_bytes
            if self._non_media <= self._policy.build_context_non_media_max_bytes:
                self._action = ContextAction.ROUTE
                self._publish()
                return ContextDecision(ContextAction.ROUTE)

            if self._rollovers < self._policy.build_context_automatic_rollover_limit:
                decision = ContextDecision(ContextAction.ROLLOVER, "context_limit")
                self._transition(ContextState.ROLLOVER_STARTING, decision, pending=True)
                return decision

            self._continuation_offered = True
            decision = ContextDecision(ContextAction.OFFER_CONTINUE, "context_limit")
            self._transition(ContextState.CONTINUE_OFFERED, decision, pending=True)
            return decision

    def consume_pending(self) -> ContextDecision | None:
        with self._lock:
            decision, self._pending = self._pending, None
            return decision

    def begin_rollover(self, old_session: str) -> bool:
        """Claim the pending rollover immediately before replacement session creation."""
        with self._lock:
            if self._state is not ContextState.ROLLOVER_STARTING:
                return False
            if old_session:
                self._retired_sessions.add(old_session)
            return True

    def activate_rollover(self) -> bool:
        with self._lock:
            if self._state is not ContextState.ROLLOVER_STARTING:
                return False
            self._rollovers += 1
            self._generation += 1
            self._transition(
                ContextState.ACTIVE, ContextDecision(ContextAction.ROUTE), pending=False)
            return True

    def rejects_session(self, session_id: str,
                        belongs_to: Callable[[str, str], bool] | None = None) -> bool:
        """Reject the retired root and descendants without calling the client under our lock."""
        with self._lock:
            retired = tuple(self._retired_sessions)
        if session_id in retired:
            return True
        return bool(belongs_to and any(belongs_to(session_id, root) for root in retired))

    def claim_cancellation(self) -> None:
        with self._lock:
            self._pending = None
            self._continuation_offered = False
            self._transition(ContextState.TERMINAL, ContextDecision(ContextAction.FAIL, "cancelled"),
                             pending=False)

    def finish(self) -> None:
        """Close a normal turn while preserving its final public action and measurements."""
        with self._lock:
            self._pending = None
            if self._state is ContextState.TERMINAL:
                return
            self._transition(
                ContextState.TERMINAL, ContextDecision(self._action), pending=False)


@dataclass(frozen=True, slots=True)
class ContextContinuation:
    continuation_id: str
    conversation: str
    app_id: str
    intent: BuildIntent
    intent_id: str
    approved_plan_record_id: str
    phase_id: str
    repair_objective: str
    source_map_digest: str
    baseline_digest: str
    current_digest: str
    created_at: float
    parent_turn_id: str
    file_reference_records: tuple[str, ...] = ()
    resource_reference_records: tuple[str, ...] = ()
    state: str = "available"

    def public(self) -> dict:
        return {
            "continuationId": self.continuation_id,
            "conversation": self.conversation,
            "appId": self.app_id,
            "state": self.state,
        }

    def file_references(self) -> list[dict]:
        """Return fresh typed file handles from the immutable content-free carrier."""
        return [json.loads(item) for item in self.file_reference_records]

    def resource_references(self) -> list[dict]:
        """Return fresh Resource handles from the immutable content-free carrier."""
        return [json.loads(item) for item in self.resource_reference_records]


class ContextContinuationRegistry:
    """A bounded one-slot, process-local continuation registry for one Project."""

    def __init__(self, policy: BuildPolicy | None = None) -> None:
        self._policy = policy or BuildPolicy()
        self._lock = threading.Lock()
        self._record: ContextContinuation | None = None
        self._claim_token: str | None = None

    @staticmethod
    def _file_reference(record: object) -> dict | None:
        if not isinstance(record, dict):
            return None
        source = str(record.get("source") or "")
        handler = str(record.get("handler") or "")
        if not source or not handler:
            return None
        result = {
            "source": source,
            "handler": handler,
            "selector": str(record.get("selector") or ""),
            "sha256": str(record.get("sha256") or ""),
            "status": str(record.get("status") or ""),
        }
        if isinstance(record.get("pages"), list):
            result["pages"] = list(record["pages"])
        return result

    @staticmethod
    def _resource_reference(record: object) -> dict | None:
        if not isinstance(record, dict):
            return None
        kind = str(record.get("kind") or "")
        resource_id = str(record.get("id") or "")
        if not kind or not resource_id:
            return None
        result = {"kind": kind, "id": resource_id}
        table = str(record.get("table") or "")
        if table:
            result["table"] = table
        return result

    @staticmethod
    def _reference_carrier(records, canonicalize, limit: int) -> tuple[str, ...]:
        """Freeze bounded content-free typed handles without caller-owned mutable data."""
        if limit <= 0:
            return ()
        out = []
        for raw in records or ():
            record = canonicalize(raw)
            if record is None:
                continue
            out.append(json.dumps(
                record, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            if len(out) >= limit:
                break
        return tuple(out)

    def offer(self, *, conversation: str, app_id: str, intent: BuildIntent,
              approved_plan_record_id: str = "", phase_id: str = "",
              repair_objective: str = "implementation", source_map_digest: str = "",
              baseline_digest: str = "", current_digest: str = "",
              parent_turn_id: str = "", file_references=(),
              resource_references=()) -> ContextContinuation:
        max_references = self._policy.build_context_continuation_reference_max_count
        file_carrier = self._reference_carrier(
            file_references, self._file_reference, max_references)
        resource_carrier = self._reference_carrier(
            resource_references, self._resource_reference,
            max(0, max_references - len(file_carrier)))
        record = ContextContinuation(
            continuation_id=secrets.token_urlsafe(24),
            conversation=conversation,
            app_id=app_id,
            intent=intent,
            intent_id=intent.intent_id,
            approved_plan_record_id=approved_plan_record_id,
            phase_id=phase_id,
            repair_objective=repair_objective,
            source_map_digest=source_map_digest,
            baseline_digest=baseline_digest,
            current_digest=current_digest,
            created_at=time.time(),
            parent_turn_id=parent_turn_id,
            file_reference_records=file_carrier,
            resource_reference_records=resource_carrier,
        )
        with self._lock:
            self._record = record
            self._claim_token = None
        return record

    def latest(self) -> ContextContinuation | None:
        with self._lock:
            return self._record if self._record and self._record.state == "available" else None

    def invalidate_available(self) -> None:
        with self._lock:
            if self._record is not None and self._record.state == "available":
                self._record = replace(self._record, state="invalidated")
                self._claim_token = None

    def claim(self, continuation_id: str, conversation: str,
              app_id: str) -> tuple[str, ContextContinuation | None, str | None]:
        """Return claimed, already_claimed, or invalid without exposing record content."""
        with self._lock:
            record = self._record
            if record is None or record.continuation_id != continuation_id:
                return "invalid", None, None
            if record.conversation != conversation or record.app_id != app_id:
                return "invalid", None, None
            if record.state == "claimed":
                return "already_claimed", None, None
            if record.state != "available":
                return "invalid", None, None
            token = secrets.token_urlsafe(18)
            claimed = replace(record, state="claimed")
            self._record = claimed
            self._claim_token = token
            return "claimed", claimed, token

    def release_refused(self, continuation_id: str, token: str) -> bool:
        with self._lock:
            record = self._record
            if (record is None or record.continuation_id != continuation_id
                    or record.state != "claimed" or token != self._claim_token):
                return False
            self._record = replace(record, state="available")
            self._claim_token = None
            return True
