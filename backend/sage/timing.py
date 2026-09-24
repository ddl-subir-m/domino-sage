"""Where a turn's wall-clock actually goes.

Sage's latency only exists against a live gateway and a real workspace, so the loop for a slow
build cannot be a local test — it has to be a measurement the deployed builder takes of itself and
can be read back over HTTP (`/api/diag/timing`, the same shell-free surface `/api/diag/log` serves).
This is that recorder.

Three kinds of record, because the turn path has three shapes of cost and they need different
accounting:

  * SPANS — a named stretch of the turn thread: a pre-turn gate, one iteration of the build loop, a
    typecheck. Nested, so `gate.scope` inside `pre-turn` reads as a tree rather than a flat list.
  * MODEL CALLS — every inference that reaches the shim, with its own first-byte and total. These
    happen on OTHER threads (the /v1 handler serves OpenCode while the build thread sleeps in its
    poll loop), which is exactly why they are a separate list and not spans: they overlap the spans
    they belong to, and forcing them into one stack would misattribute the overlap.
  * COUNTERS and OBSERVATIONS — how many polls, how long they spent in `client.messages()`, and the
    lag between a tool part completing inside OpenCode and Sage noticing it on the next poll. That
    last one is the whole of the "does the sampling loop cost us anything" question: it is the only
    number that separates Sage's polling delay from the model's real latency.

One turn at a time, process-wide, because `_turn_lock` already guarantees that (ADR-0013). So the
current record is a module global rather than a contextvar — a contextvar would not reach the /v1
request that OpenCode makes mid-turn, which is the one place the model calls are visible.

Never raises. A recorder that can break a build is worse than no recorder: every entry point
swallows, and every call is a no-op when no turn is open.
"""
from __future__ import annotations

import collections
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from uuid import uuid4

from .tool_timing import ToolObserver, tool_readout

log = logging.getLogger(__name__)

# How many finished turns stay readable. A build is minutes, so this is the last session or two of
# work — enough to compare a slow turn against the fast one before it, which is the comparison that
# usually names the cause.
_HISTORY = 20


def _model_name(value: object) -> str | None:
    """Bound model evidence without applying the identifier rule that rejects spaces."""
    if not isinstance(value, str) or not 1 <= len(value) <= 160:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value


@dataclass
class Span:
    name: str
    depth: int
    t0: float
    t1: float | None = None
    fields: dict = field(default_factory=dict)

    @property
    def ms(self) -> float:
        return ((self.t1 if self.t1 is not None else time.monotonic()) - self.t0) * 1000


@dataclass
class ModelCall:
    n: int
    t0: float
    model: str = ""
    phase: str = ""
    # WHICH RULE put this call on that model (`Reason`'s own value, #316). Recorded beside the model
    # and not derived from it: four rules move a request off the picked model and three of them land
    # on gpt-5.4, so the alias alone cannot say whether the pick was honoured, overruled by the Ask
    # pin, or dropped by the signing veto — which is the question a waterfall gets read for when a
    # step ran somewhere nobody expected.
    reason: str = ""
    call_id: str = field(default_factory=lambda: uuid4().hex)
    turn_id: str = ""
    protocol: str | None = None
    requested_effort: str | None = None
    effort_status: str = "unknown"
    session_id: str | None = None
    root_session_id: str | None = None
    first_text: float | None = None
    first_tool_argument: float | None = None
    last_chunk: float | None = None
    max_chunk_gap: float = 0.0
    outcome: str = "running"
    forwarded_request_bytes: int | None = None
    requested_alias: str | None = None
    response_reported_model: str | None = None
    request_composition: dict | None = None
    build_intent: dict | None = None
    tool_invocations: list[dict] = field(default_factory=list)
    tools_truncated: bool = False
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    first_byte: float | None = None   # monotonic, not a duration — the waterfall needs the moment
    # The moment the shim finished rewriting this request and handed back the generator that will
    # make the call. `first_byte` is measured from `t0`, which starts BEFORE the shim runs, so a slow
    # first byte has two possible causes and this is the line that separates them: everything before
    # `prepared` is Sage's own work on the payload, everything after it is the gateway's. Measured
    # 2026-09-11 against a live Builder, one implement call reported ttfb=38.9s where its seventeen
    # neighbours reported ~2.4s, and nothing recorded could say which side of this line it sat on.
    prepared: float | None = None
    # What this step actually did. A call line could say it cost 6.4 seconds and never say what for,
    # so the only way to rank steps for removal was to read chunk counts and guess — and a guess
    # that looks well-founded is the expensive kind. Ordered and not deduped: two reads before a
    # write is a different step from one read, and a step that re-reads what it just wrote is the
    # one worth cutting.
    tools: list[str] = field(default_factory=list)
    t1: float | None = None
    chunks: int = 0
    ok: bool = True
    error: str = ""
    # How big the request was. This is the number that explains a slow first byte: measured against
    # the dogfood gateway on 2026-09-11, a step's time to first byte tracked its payload — 1.55s at
    # 14KB, 2.4s at 128KB, ~4s at 321KB, same model and same box. Seconds alone cannot tell a turn
    # that got slower from a conversation that got bigger, and every step re-sends the whole
    # conversation, so this is what says which one happened.
    #
    # Legacy reqBytes is the incoming OpenCode body. forwarded_request_bytes separately measures
    # the final rewritten native request; neither body is retained here.
    request_bytes: int = 0
    # What the provider said it read, when it says anything at all. Optional rather than
    # always-present on purpose: the gateway emits a `usage` frame for gpt-5.4 and NOT for sonnet,
    # with or without `stream_options.include_usage` (verified 2026-09-11). Chat runs on sonnet
    # today, so a token field alone would be empty exactly where the question is being asked, which
    # is why `request_bytes` above is the primary signal and this one is the bonus.
    input_tokens: int | None = None
    cached_tokens: int | None = None


@dataclass
class TurnRecord:
    kind: str
    started_at: float                 # wall clock, for reading a record back hours later
    t0: float                         # monotonic, what every offset is measured from
    prompt: str = ""
    turn_id: str = field(default_factory=lambda: uuid4().hex)
    app_id: str | None = None
    conversation_id: str | None = None
    spans: list[Span] = field(default_factory=list)
    calls: list[ModelCall] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    _tool_observer: ToolObserver | None = field(default=None, repr=False)
    tools_truncated: bool = False
    intervals: list[dict] = field(default_factory=list)
    intervals_truncated: bool = False
    repeat_brake: list[dict] = field(default_factory=list)
    repeat_brake_truncated: bool = False
    implementation_session: dict = field(default_factory=dict)
    pre_edit_guard: dict = field(default_factory=dict)
    context_rollover: dict = field(default_factory=dict)
    counters: dict[str, float] = field(default_factory=dict)
    observations: dict[str, list[float]] = field(default_factory=dict)
    t1: float | None = None
    decision: str = ""
    ok: bool | None = None

    @property
    def ms(self) -> float:
        return ((self.t1 if self.t1 is not None else time.monotonic()) - self.t0) * 1000


_lock = threading.Lock()
_current: TurnRecord | None = None
_history: collections.deque[TurnRecord] = collections.deque(maxlen=_HISTORY)
_CURRENT_TURN_RECORD = object()
# Span nesting is per-thread: the build thread's stack must not be deepened by a /v1 request that
# happens to open a span on another thread at the same moment.
_stack = threading.local()


def enabled() -> bool:
    """On unless switched off. The cost is a monotonic() and a list append per span, and the thing
    it measures only happens on a real deployment — an off-by-default recorder is one that is always
    off exactly when someone needs it."""
    return os.environ.get("SAGE_TIMING", "1").strip().lower() not in ("0", "false", "no")


def start_turn(kind: str, prompt: str = "", *, turn_id: str | None = None,
               app_id: str | None = None,
               conversation_id: str | None = None,
               started: tuple[float, float] | None = None) -> TurnRecord | None:
    """Open a record. Any turn still open is closed first — a turn that died without finishing is
    still the most interesting one in the ring, so it is kept rather than dropped."""
    global _current
    if not enabled():
        return
    try:
        with _lock:
            if _current is not None and _current.t1 is None:
                _current.t1 = time.monotonic()
                _current.decision = _current.decision or "abandoned"
                # Closed the same way finish_turn closes one, spans and calls included. An abandoned
                # turn can still have work running on another thread — a gate prefetch outliving the
                # generator a disconnected client walked away from — and leaving those open would let
                # them stamp themselves later than the record they belong to.
                for sp in _current.spans:
                    if sp.t1 is None:
                        sp.t1 = _current.t1
                for c in _current.calls:
                    if c.t1 is None:
                        c.t1 = _current.t1
                        c.outcome = "incomplete"
                        c.ok = False
                _history.append(_current)
            wall, monotonic = started or (time.time(), time.monotonic())
            _current = TurnRecord(kind=kind, started_at=wall, t0=monotonic,
                                  prompt=(prompt or "")[:200], turn_id=turn_id or uuid4().hex,
                                  app_id=app_id, conversation_id=conversation_id)
            record = _current
        _stack.depth = 0
        return record
    except Exception:
        log.debug("timing: start_turn failed", exc_info=True)
        return None


def record_span(record: TurnRecord | None, name: str, start: float, end: float) -> None:
    """Attach elapsed work measured before this record became the active turn."""
    if record is None or not enabled() or end < start:
        return
    try:
        with _lock:
            if record.t1 is None:
                record.spans.append(Span(name=name, depth=0, t0=start, t1=end))
    except Exception:
        log.debug("timing: prior span failed", exc_info=True)


def bind_context(turn_id: str, *, app_id: str | None = None,
                 conversation_id: str | None = None,
                 record: TurnRecord | None = None) -> None:
    """Bind identity after the existing context setup; never initialize an app for diagnostics."""
    with _lock:
        rec = record if record is not None else _current
        if rec is not None and rec.t1 is None and rec.turn_id == turn_id:
            rec.app_id = app_id
            rec.conversation_id = conversation_id


def finish_turn(ok: bool | None = None, decision: str = "", *,
                record: TurnRecord | None | object = _CURRENT_TURN_RECORD) -> TurnRecord | None:
    """Close the open turn and hand its record back.

    `None` means there was no turn to close, OR that closing it failed — `_current` is cleared on
    the first statement under the lock, so a record can be lost after that point and the caller
    cannot tell the two apart. Everything after the clear is list and deque work under the lock,
    which is why this is a sentence rather than a branch.

    The return value is the one read that cannot be wrong about WHICH turn it is. `recent()` and
    `last_finished()` both answer a question about the process; this answers a question about the
    caller, because the caller is the one that ended it (#339).
    """
    global _current
    try:
        with _lock:
            rec = _current if record is _CURRENT_TURN_RECORD else record
            if rec is None:
                return None
            if _current is rec:
                _current = None
            if rec.t1 is not None:
                return rec
            rec.t1 = time.monotonic()
            for sp in rec.spans:
                if sp.t1 is None:
                    sp.t1 = rec.t1
            for c in rec.calls:
                if c.t1 is None:
                    c.t1 = rec.t1
                    c.outcome = "incomplete"
                    c.ok = False
            rec.ok = ok if ok is not None else rec.ok
            rec.decision = decision or rec.decision
            _history.append(rec)
        return rec
    except Exception:
        log.debug("timing: finish_turn failed", exc_info=True)
        return None


def open_span(name: str, **fields) -> Span | None:
    """A span whose end is not a block. The build loop's iterations end at a `continue` and at seven
    different `return`s, so a `with` around one would mean restructuring the loop to measure it.
    `finish_turn` closes anything still open, so a forgotten close costs a "(open)" in the readout
    rather than a wrong number."""
    rec = _current
    if rec is None or not enabled():
        return None
    s = Span(name=name, depth=getattr(_stack, "depth", 0), t0=time.monotonic(),
             fields={k: v for k, v in fields.items()})
    try:
        with _lock:
            rec.spans.append(s)
    except Exception:
        log.debug("timing: open_span failed", exc_info=True)
    return s


def close_span(s: Span | None) -> None:
    if s is not None and s.t1 is None:
        s.t1 = time.monotonic()


def decide(ok: bool | None, decision: str) -> None:
    """The turn's outcome, recorded where it is known (the `done` event) rather than where the
    record closes (an outer `finally` that cannot see it)."""
    rec = _current
    if rec is not None:
        rec.ok = ok
        rec.decision = decision or rec.decision


def implementation_session(*, fresh: bool, reason: str, created: bool,
                           persisted: bool, dispatch_started: bool) -> None:
    """Record safe implementation-session state.

    ``reason`` classifies the path that selected the session. The standard direct path is
    ``reused`` even when validation replaces a stale ID; ``fresh``, ``created``, and ``persisted``
    record that lifecycle outcome.
    """
    if reason not in {
        "approved_plan", "phase", "broken_call_recovery", "pre_edit_recovery",
        "context_rollover", "reused",
    }:
        return
    rec = _current
    if rec is None:
        return
    try:
        with _lock:
            rec.implementation_session = {
                "fresh": bool(fresh),
                "reason": reason,
                "created": bool(created),
                "persisted": bool(persisted),
                "dispatchStarted": bool(dispatch_started),
            }
    except Exception:
        log.debug("timing: implementation session update failed", exc_info=True)


def pre_edit_guard(value: dict) -> None:
    """Record the guard's fixed, content-free state."""
    rec = _current
    if rec is None:
        return
    try:
        safe: dict[str, object] = {}
        integer_fields = {
            "policyVersion", "sessionGeneration", "modelCalls", "modelCallLimit",
            "originalUniqueToolResultBytes", "toolResultLimitBytes",
            "maxForwardedNonMediaRequestBytes", "requestLimitBytes",
        }
        enum_fields = {
            "attempt": {"initial", "recovery"},
            "state": {"armed", "recovering", "disarmed", "terminal"},
            "trigger": {
                "none", "model_calls", "request_bytes", "tool_result_bytes",
                "no_edit_completion", "request_measurement_unavailable",
                "tree_witness_unavailable", "session_abort_unconfirmed",
            },
            "action": {"route", "recover", "stop", "disarm", "fail"},
        }
        for key in integer_fields:
            item = value.get(key)
            if not isinstance(item, int) or isinstance(item, bool) or item < 0:
                return
            safe[key] = item
        if safe["policyVersion"] != 1:
            return
        first_edit = value.get("firstEditObserved")
        if not isinstance(first_edit, bool):
            return
        safe["firstEditObserved"] = first_edit
        for key, allowed in enum_fields.items():
            item = value.get(key)
            if item not in allowed:
                return
            safe[key] = item
        with _lock:
            rec.pre_edit_guard = safe
    except Exception:
        log.debug("timing: pre-edit guard update failed", exc_info=True)


def context_rollover(value: dict) -> None:
    """Record only the fixed numeric whole-request context state."""
    rec = _current
    if rec is None:
        return
    try:
        integers = (
            "policyVersion", "limitNonMediaBytes", "sessionGeneration", "rolloverCount",
            "totalWireBytes", "mediaBytes", "nonMediaContextBytes",
        )
        safe = {}
        for key in integers:
            item = value.get(key)
            if not isinstance(item, int) or isinstance(item, bool) or item < 0:
                return
            safe[key] = item
        if safe["policyVersion"] != 1:
            return
        if value.get("measurementStatus") not in {"complete", "unavailable"}:
            return
        if value.get("action") not in {"route", "rollover", "offer_continue", "fail"}:
            return
        offered = value.get("continuationOffered")
        if not isinstance(offered, bool):
            return
        safe.update({
            "measurementStatus": value["measurementStatus"],
            "action": value["action"],
            "continuationOffered": offered,
        })
        with _lock:
            rec.context_rollover = safe
    except Exception:
        log.debug("timing: context rollover update failed", exc_info=True)


@contextmanager
def span(name: str, **fields):
    """Time a named stretch of the current turn. A no-op with no turn open, so call sites that are
    shared between a turn and the background (`_check_remote`) need no branch of their own."""
    rec = _current
    if rec is None or not enabled():
        yield None
        return
    depth = getattr(_stack, "depth", 0)
    s = Span(name=name, depth=depth, t0=time.monotonic(), fields={k: v for k, v in fields.items()})
    try:
        with _lock:
            rec.spans.append(s)
        _stack.depth = depth + 1
        yield s
    finally:
        # Only if it is still open, matching close_span. A span on a background thread — a gate
        # prefetch outliving the turn that started it — has already been stamped by finish_turn at
        # the turn's own end, and writing over that would put a span in the record that ends after
        # the record does.
        if s.t1 is None:
            s.t1 = time.monotonic()
        _stack.depth = depth


def count(name: str, n: float = 1) -> None:
    rec = _current
    if rec is None:
        return
    try:
        with _lock:
            rec.counters[name] = rec.counters.get(name, 0) + n
    except Exception:
        log.debug("timing: count failed", exc_info=True)


def observe(name: str, value: float) -> None:
    """A sample in a distribution — kept whole rather than averaged, because the tail is the signal.
    A poll lag whose median is 400ms and whose max is 4s is a different bug from one that is
    uniformly 2s, and a mean hides which one you have."""
    rec = _current
    if rec is None:
        return
    try:
        with _lock:
            xs = rec.observations.setdefault(name, [])
            if len(xs) < 2000:   # a runaway loop must not become a memory leak
                xs.append(value)
    except Exception:
        log.debug("timing: observe failed", exc_info=True)


class _CallHandle:
    """What a /v1 request holds for the length of one inference. Safe to use with no turn open."""

    def __init__(self, call: ModelCall | None) -> None:
        self._call = call

    @contextmanager
    def _active(self):
        # The handle owns one call, never the process's next turn. Finish and updates use the
        # same lock so a late pump cannot change even the closed record's snapshot.
        with _lock:
            yield self._call if self._call is not None and self._call.t1 is None else None

    def first_byte(self) -> None:
        with self._active() as c:
            if c is not None and c.first_byte is None:
                c.first_byte = time.monotonic()

    def prepared(self, forwarded_bytes: int | None = None, *, requested_alias: str | None = None,
                 request_composition: dict | None = None) -> None:
        with self._active() as c:
            if c is not None and c.prepared is None:
                c.prepared = time.monotonic()
                c.forwarded_request_bytes = forwarded_bytes
                c.requested_alias = _model_name(requested_alias)
                c.request_composition = request_composition

    def intent(self, *, kind: str, status: str, carrier_count: int, carrier_bytes: int,
               source_request_count: int, plan_present: bool, failure_stage: str) -> None:
        """Keep only the bounded Build-intent result. No carrier content or identity enters timing."""
        if kind not in {"direct_build", "approved_plan", "phase"}:
            return
        if status not in {"ok", "missing", "changed", "duplicate", "unsupported"}:
            return
        if failure_stage not in {"none", "install", "prepare", "final_check"}:
            return
        with self._active() as c:
            if c is not None:
                c.build_intent = {
                    "kind": kind,
                    "status": status,
                    "carrierCount": max(0, int(carrier_count)),
                    "carrierBytes": max(0, int(carrier_bytes)),
                    "sourceRequestCount": max(0, int(source_request_count)),
                    "planPresent": bool(plan_present),
                    "failureStage": failure_stage,
                }

    def tool(self, names: list[str]) -> None:
        """Legacy non-native readers supply newly announced names, not cumulative sets."""
        with self._active() as c:
            if c is not None:
                room = 40 - len(c.tools)
                c.tools.extend(str(n)[:40] for n in names[:room])
                c.tools_truncated |= len(names) > room

    def stream_metadata(self, events) -> None:
        """Snapshot bounded metadata; retain no response bodies or argument fragments."""
        with self._active() as c:
            if c is None:
                return
            now = c.last_chunk if c.last_chunk is not None else time.monotonic()
            if events.saw_text and c.first_text is None:
                c.first_text = now
            if events.saw_tool_argument and c.first_tool_argument is None:
                c.first_tool_argument = now
            c.tool_invocations = [dict(t) for t in events.tool_invocations]
            c.tools = [t["name"] or "?" for t in c.tool_invocations]
            c.tools_truncated = events.tools_truncated
            for attr in ("input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens"):
                value = getattr(events, attr)
                if value is not None:
                    setattr(c, attr, value)
            reported = getattr(events, "reported_model", None)
            # This field came from the provider stream. Its shape cannot make it trusted: secrets
            # and result text can have a valid identifier shape too. Retain it only when it echoes
            # an identity already trusted from routing or the final outbound request.
            if isinstance(reported, str) and reported in {c.model, c.requested_alias}:
                c.response_reported_model = reported

    def chunk(self) -> None:
        with self._active() as c:
            if c is not None:
                now = time.monotonic()
                if c.last_chunk is not None:
                    c.max_chunk_gap = max(c.max_chunk_gap, now - c.last_chunk)
                c.last_chunk = now
                c.chunks += 1

    def model(self, name: str, phase: str = "", reason: str = "") -> None:
        with self._active() as c:
            if c is not None:
                c.model = _model_name(name) or c.model
                c.phase = phase or c.phase
                c.reason = reason or c.reason

    def route(self, protocol: str, effort: str | None) -> None:
        with self._active() as c:
            if c is not None:
                c.protocol = protocol
                c.requested_effort = effort
                c.effort_status = "provider_default" if effort is None else "explicit"

    def request(self, n_bytes: int) -> None:
        with self._active() as c:
            if c is not None:
                c.request_bytes = int(n_bytes)

    def usage(self, input_tokens: int | None, cached_tokens: int | None = None,
              output_tokens: int | None = None, reasoning_tokens: int | None = None) -> None:
        with self._active() as c:
            if c is not None:
                for attr, value in (("input_tokens", input_tokens), ("cached_tokens", cached_tokens),
                                    ("output_tokens", output_tokens), ("reasoning_tokens", reasoning_tokens)):
                    if value is not None:
                        setattr(c, attr, int(value))

    def done(self, ok: bool = True, error: str = "", *, outcome: str | None = None) -> None:
        with self._active() as c:
            if c is not None:
                c.t1 = time.monotonic()
                c.ok = ok
                c.error = error[:200]
                c.outcome = outcome or ("success" if ok else "error")


_CURRENT_RECORD = object()


def model_call(model: str = "", phase: str = "", *, record=_CURRENT_RECORD,
               session_id: str | None = None, root_session_id: str | None = None,
               app_id: str | None = None, conversation_id: str | None = None) -> _CallHandle:
    rec = _current if record is _CURRENT_RECORD else record
    if rec is None or not enabled():
        return _CallHandle(None)
    try:
        with _lock:
            if rec.t1 is not None:
                return _CallHandle(None)
            rec.app_id = rec.app_id or app_id
            rec.conversation_id = rec.conversation_id or conversation_id
            call = ModelCall(n=len(rec.calls) + 1, t0=time.monotonic(), model=model, phase=phase,
                             turn_id=rec.turn_id, session_id=session_id, root_session_id=root_session_id)
            rec.calls.append(call)
        return _CallHandle(call)
    except Exception:
        log.debug("timing: model_call failed", exc_info=True)
        return _CallHandle(None)


def tool_observer() -> ToolObserver:
    with _lock:
        rec = _current if enabled() else None
        if rec is None:
            return ToolObserver(None, _lock)
        if rec._tool_observer is None:
            rec._tool_observer = ToolObserver(rec, _lock)
        return rec._tool_observer


def current() -> TurnRecord | None:
    return _current


def recent(n: int = 5) -> list[TurnRecord]:
    """The readout's view of the process: newest first, with a turn still running at the front.

    It answers "what has this process been doing", and the running turn leads because the record
    someone most wants is the one they are waiting on. So nothing in the list is promised to be
    finished, to have recorded a single call yet, or to belong to the caller — a turn opened on
    another thread a moment ago is newest, and takes the front. The list is empty when nothing has
    been recorded, which includes every run with `SAGE_TIMING` off, and one longer than the
    finished turns it found while a turn is running — up to `n` of those, and the live one in
    front of them.

    A caller that means "the turn I just ran" is asking a different question and must not ask it
    here (#336, #339): `finish_turn` hands back the record it closed, and `last_finished()` names
    the newest one that is over.
    """
    with _lock:
        out = list(_history)[-max(1, n):]
        # Read under the same lock as the ring, and not after it. A `finish_turn` landing between
        # the two rings the turn and clears `_current`, so a snapshot taken in two steps can miss
        # it in both halves — the newest turn, the one this list leads with on purpose, absent
        # from the readout entirely. One acquisition makes the pair a fact about one moment.
        cur = _current
    out.reverse()
    if cur is not None:
        out.insert(0, cur)
    return out


def last_finished() -> TurnRecord | None:
    """The newest turn that is OVER, or `None` if no turn has ended in this process.

    Never the turn still running, so it cannot hand back an empty record that belongs to whoever
    started a turn since. An abandoned turn counts: `start_turn` closes the one it displaces and
    rings it, and a turn that died without finishing is still the most interesting one there.

    `None` is the answer before the first turn ends, and so through any run started with
    `SAGE_TIMING=0`: nothing opens a turn, so nothing ever rings. The flag is not consulted HERE,
    and deliberately not in `finish_turn` either — a turn already open has to be closed and
    cleared whatever the flag says now, or `_current` is stranded and every later turn is stamped
    onto a record nobody can end.

    It is the NEWEST finished turn and not the caller's own — a background turn that both began
    and ended inside the caller's window would still win. Only `finish_turn`'s return value is
    proof of identity; use this where the caller cannot hold that.
    """
    with _lock:
        return _history[-1] if _history else None


# ---- readout ------------------------------------------------------------------------------------
#
# Rendered here rather than at the endpoint so the browser view and the script that polls it can
# never disagree about what a number means.

def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(p * len(s)))]


def _offset(value: float | None, origin: float) -> int | None:
    return None if value is None else round((value - origin) * 1000)


def as_dict(rec: TurnRecord) -> dict:
    return {
        "kind": rec.kind,
        "turnId": rec.turn_id, "appId": rec.app_id, "conversationId": rec.conversation_id,
        "boundary": "Sage gateway observations; not provider compute or reasoning time",
        "startedAt": rec.started_at,
        "prompt": rec.prompt,
        "ms": round(rec.ms),
        "ok": rec.ok,
        "decision": rec.decision,
        "running": rec.t1 is None,
        "implementationSession": dict(rec.implementation_session),
        "preEditGuard": dict(rec.pre_edit_guard),
        "contextRollover": dict(rec.context_rollover),
        "spans": [{"name": s.name, "depth": s.depth, "atMs": round((s.t0 - rec.t0) * 1000),
                   "ms": round(s.ms), "open": s.t1 is None, **s.fields} for s in rec.spans],
        "calls": [{"n": c.n, "model": c.model, "phase": c.phase, "reason": c.reason,
                   "callId": c.call_id, "turnId": c.turn_id, "protocol": c.protocol,
                   "requestedEffort": c.requested_effort, "effortStatus": c.effort_status,
                   "sessionId": c.session_id, "rootSessionId": c.root_session_id,
                   "firstTextMs": _offset(c.first_text, c.t0),
                   "firstToolArgumentMs": _offset(c.first_tool_argument, c.t0),
                   "lastChunkMs": _offset(c.last_chunk, c.t0),
                   "maxChunkGapMs": round(c.max_chunk_gap * 1000),
                   "outcome": c.outcome, "forwardedReqBytes": c.forwarded_request_bytes,
                   "requestedAlias": c.requested_alias,
                   "responseReportedModel": c.response_reported_model,
                   "requestComposition": c.request_composition,
                   "buildIntent": c.build_intent,
                   "toolInvocations": [dict(t) for t in c.tool_invocations],
                   "toolsTruncated": c.tools_truncated,
                   "outTokens": c.output_tokens, "reasoningTokens": c.reasoning_tokens,
                   "atMs": round((c.t0 - rec.t0) * 1000),
                   "ttfbMs": None if c.first_byte is None else round((c.first_byte - c.t0) * 1000),
                   "prepMs": None if c.prepared is None else round((c.prepared - c.t0) * 1000),
                   "ms": None if c.t1 is None else round((c.t1 - c.t0) * 1000),
                   "chunks": c.chunks, "reqBytes": c.request_bytes, "tools": list(c.tools),
                   "inTokens": c.input_tokens, "cachedTokens": c.cached_tokens,
                   "ok": c.ok, "error": c.error} for c in rec.calls],
        "tools": [tool_readout(t, rec) for t in rec.tools],
        "toolsTruncated": rec.tools_truncated,
        "intervals": [dict(i) for i in rec.intervals],
        "intervalsTruncated": rec.intervals_truncated,
        "repeatBrake": [dict(b) for b in rec.repeat_brake],
        "repeatBrakeTruncated": rec.repeat_brake_truncated,
        "counters": {k: round(v, 1) for k, v in rec.counters.items()},
        "observations": {k: {"n": len(v), "p50": round(_pct(v, 0.5)), "p90": round(_pct(v, 0.9)),
                             "max": round(max(v)) if v else 0, "sum": round(sum(v))}
                         for k, v in rec.observations.items()},
    }


def render(rec: TurnRecord) -> str:
    """One turn as a waterfall: what ran, when it started, how long it took.

    Model calls are interleaved with the spans by start time rather than listed after them, because
    the question this answers is "what was the turn doing while nothing was on screen", and the
    answer is usually a call sitting inside a span that looks idle.
    """
    d = as_dict(rec)
    head = (f"{time.strftime('%H:%M:%S', time.localtime(rec.started_at))}  {d['kind']:<8} "
            f"{d['ms'] / 1000:7.1f}s  {'RUNNING' if d['running'] else d['decision'] or '-'}"
            f"   {d['prompt'][:60]!r}")
    rows: list[tuple[int, str]] = []
    for s in d["spans"]:
        pad = "  " * s["depth"]
        extra = " ".join(f"{k}={v}" for k, v in s.items()
                         if k not in ("name", "depth", "atMs", "ms", "open"))
        rows.append((s["atMs"],
                     (f"  {s['atMs'] / 1000:7.1f}  {s['ms'] / 1000:7.1f}s  {pad}{s['name']}"
                      f"{'  (open)' if s['open'] else ''}{'  ' + extra if extra else ''}")))
    for c in d["calls"]:
        ttfb = "-" if c["ttfbMs"] is None else f"{c['ttfbMs'] / 1000:.1f}s"
        # Beside ttfb and not instead of it: ttfb is what the step waited, `shim` is how much of that
        # wait Sage caused. Read together they answer "was this the gateway or was it us", which is
        # the first question a stalled call raises and the one the number alone could not answer.
        shim = "" if c["prepMs"] is None else f" shim={c['prepMs'] / 1000:.1f}s"
        total = "-" if c["ms"] is None else f"{c['ms'] / 1000:.1f}s"
        # Beside ttfb, because they are read together: a first byte that grew while the request
        # grew is a conversation getting heavier, and one that grew on a steady request is not.
        req = f" req={c['reqBytes'] / 1024:.0f}KB" if c["reqBytes"] else ""
        tok = f" in={c['inTokens']}tok" if c["inTokens"] is not None else ""
        # Last on the line and in call order, because this is what the line is read FOR once the
        # question is "which of these eighteen steps did not need to be its own round trip".
        tools = f" tools={','.join(c['tools'])}" if c.get("tools") else ""
        rows.append((c["atMs"],
                     (f"  {c['atMs'] / 1000:7.1f}  {(c['ms'] or 0) / 1000:7.1f}s      "
                      f"· call {c['n']} {c['model'] or '?'}/{c['phase'] or '?'}"
                      f"{' (' + c['reason'] + ')' if c.get('reason') else ''} "
                      f"ttfb={ttfb}{shim} total={total} chunks={c['chunks']}"
                      f"{req} {tok}{tools}"
                      f"{'' if c['ok'] else '  FAILED ' + c['error']}")))
    rows.sort(key=lambda r: r[0])
    lines = [head, "      at    dur", *[r[1] for r in rows]]
    if d["counters"]:
        lines.append("  counters: " + "  ".join(f"{k}={v}" for k, v in sorted(d["counters"].items())))
    for k, o in sorted(d["observations"].items()):
        lines.append(f"  {k}: n={o['n']} p50={o['p50']} p90={o['p90']} max={o['max']} sum={o['sum']}")
    return "\n".join(lines)


def render_all(n: int = 5) -> str:
    recs = recent(n)
    if not recs:
        return "(no turns recorded yet)"
    return "\n\n".join(render(r) for r in recs)


def as_json(n: int = 5) -> str:
    return json.dumps([as_dict(r) for r in recent(n)], indent=2)
