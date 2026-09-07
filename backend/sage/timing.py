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

log = logging.getLogger(__name__)

# How many finished turns stay readable. A build is minutes, so this is the last session or two of
# work — enough to compare a slow turn against the fast one before it, which is the comparison that
# usually names the cause.
_HISTORY = 20


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
    first_byte: float | None = None   # monotonic, not a duration — the waterfall needs the moment
    t1: float | None = None
    chunks: int = 0
    ok: bool = True
    error: str = ""


@dataclass
class TurnRecord:
    kind: str
    started_at: float                 # wall clock, for reading a record back hours later
    t0: float                         # monotonic, what every offset is measured from
    prompt: str = ""
    spans: list[Span] = field(default_factory=list)
    calls: list[ModelCall] = field(default_factory=list)
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
# Span nesting is per-thread: the build thread's stack must not be deepened by a /v1 request that
# happens to open a span on another thread at the same moment.
_stack = threading.local()


def enabled() -> bool:
    """On unless switched off. The cost is a monotonic() and a list append per span, and the thing
    it measures only happens on a real deployment — an off-by-default recorder is one that is always
    off exactly when someone needs it."""
    return os.environ.get("SAGE_TIMING", "1").strip().lower() not in ("0", "false", "no")


def start_turn(kind: str, prompt: str = "") -> None:
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
                _history.append(_current)
            _current = TurnRecord(kind=kind, started_at=time.time(), t0=time.monotonic(),
                                  prompt=(prompt or "")[:200])
        _stack.depth = 0
    except Exception:
        log.debug("timing: start_turn failed", exc_info=True)


def finish_turn(ok: bool | None = None, decision: str = "") -> None:
    global _current
    try:
        with _lock:
            rec, _current = _current, None
            if rec is None:
                return
            rec.t1 = time.monotonic()
            for sp in rec.spans:
                if sp.t1 is None:
                    sp.t1 = rec.t1
            for c in rec.calls:
                if c.t1 is None:
                    c.t1 = rec.t1
            rec.ok = ok if ok is not None else rec.ok
            rec.decision = decision or rec.decision
            _history.append(rec)
    except Exception:
        log.debug("timing: finish_turn failed", exc_info=True)


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

    def first_byte(self) -> None:
        if self._call is not None and self._call.first_byte is None:
            self._call.first_byte = time.monotonic()

    def chunk(self) -> None:
        if self._call is not None:
            self._call.chunks += 1

    def model(self, name: str, phase: str = "") -> None:
        if self._call is not None:
            self._call.model = name or self._call.model
            self._call.phase = phase or self._call.phase

    def done(self, ok: bool = True, error: str = "") -> None:
        if self._call is not None and self._call.t1 is None:
            self._call.t1 = time.monotonic()
            self._call.ok = ok
            self._call.error = error[:200]


def model_call(model: str = "", phase: str = "") -> _CallHandle:
    rec = _current
    if rec is None or not enabled():
        return _CallHandle(None)
    try:
        with _lock:
            call = ModelCall(n=len(rec.calls) + 1, t0=time.monotonic(), model=model, phase=phase)
            rec.calls.append(call)
        return _CallHandle(call)
    except Exception:
        log.debug("timing: model_call failed", exc_info=True)
        return _CallHandle(None)


def current() -> TurnRecord | None:
    return _current


def recent(n: int = 5) -> list[TurnRecord]:
    """Newest first, with a turn still running at the front."""
    with _lock:
        out = list(_history)[-max(1, n):]
    out.reverse()
    if _current is not None:
        out.insert(0, _current)
    return out


# ---- readout ------------------------------------------------------------------------------------
#
# Rendered here rather than at the endpoint so the browser view and the script that polls it can
# never disagree about what a number means.

def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(p * len(s)))]


def as_dict(rec: TurnRecord) -> dict:
    return {
        "kind": rec.kind,
        "startedAt": rec.started_at,
        "prompt": rec.prompt,
        "ms": round(rec.ms),
        "ok": rec.ok,
        "decision": rec.decision,
        "running": rec.t1 is None,
        "spans": [{"name": s.name, "depth": s.depth, "atMs": round((s.t0 - rec.t0) * 1000),
                   "ms": round(s.ms), "open": s.t1 is None, **s.fields} for s in rec.spans],
        "calls": [{"n": c.n, "model": c.model, "phase": c.phase,
                   "atMs": round((c.t0 - rec.t0) * 1000),
                   "ttfbMs": None if c.first_byte is None else round((c.first_byte - c.t0) * 1000),
                   "ms": None if c.t1 is None else round((c.t1 - c.t0) * 1000),
                   "chunks": c.chunks, "ok": c.ok, "error": c.error} for c in rec.calls],
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
        total = "-" if c["ms"] is None else f"{c['ms'] / 1000:.1f}s"
        rows.append((c["atMs"],
                     (f"  {c['atMs'] / 1000:7.1f}  {(c['ms'] or 0) / 1000:7.1f}s      "
                      f"· call {c['n']} {c['model'] or '?'}/{c['phase'] or '?'} "
                      f"ttfb={ttfb} total={total} chunks={c['chunks']}"
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
