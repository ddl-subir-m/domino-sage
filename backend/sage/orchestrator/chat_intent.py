"""Pre-turn Chat intent classifier for bounded data turns (#364)."""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass

from .. import degraded, timing
from ..gateway.client import CostLabels, GatewayClient
from ..router.models import ModelCatalog
from .scope import _extract, _model_for

log = logging.getLogger(__name__)

TIMEOUT_S = 5.0
MIN_CONFIDENCE = 0.65
MAX_PROMPT_CHARS = 1600
MAX_CONTEXT_CHARS = 1200
LABELS = frozenset({"plain_answer", "data_answer", "data_artifact", "build_app", "other_chat"})

_SYSTEM = """\
Classify one Chat user turn.

Return one JSON object only:
{"label":"plain_answer|data_answer|data_artifact|build_app|other_chat","confidence":0.0}

Labels:
- plain_answer: general explanation or Q&A, no data read needed.
- data_answer: answer from bound data without writing an artifact.
- data_artifact: answer from bound data and write one requested chart/table artifact.
- build_app: app, dashboard, report, page, or tool people can open, use, share, or keep.
- other_chat: anything else that should use normal Chat behavior.

Rules:
- Use data_artifact only when the user asks for a chart/table/file artifact and bound data context exists.
- If no bound data/file/table/dataset context is listed, do not use data_artifact.
- App, dashboard, report, page, or shareable tool requests are build_app, even if they involve data.
- Low certainty should use confidence below 0.65.
"""


# The fallbacks that are the classifier WORKING, so the report stays at INFO (#467). `""` is a
# clean answer; `low-confidence` and `no-bound-context` are it answering and then declining, which
# the turn is designed to proceed from. Every other fallback is the classifier failing to answer at
# all — `invalid-json` is a model whose JSON mode cannot hold, and a turn that silently lost intent
# detection is a turn nobody can explain afterwards. Named rather than derived from `Intent.valid`,
# which is False for `low-confidence` too and would put the ordinary outcome in the warning stream:
# a level split that warns on everything buries the one line worth reading.
_WORKING = ("", "low-confidence", "no-bound-context")


@dataclass(frozen=True)
class Intent:
    label: str = ""
    confidence: float = 0.0
    raw: str = ""
    fallback: str = ""

    @property
    def valid(self) -> bool:
        return bool(self.label) and not self.fallback

    @property
    def usable_label(self) -> bool:
        """True when the classifier returned a label it meant, sure of it or not (#401).

        `valid` answers "may this turn be BOUNDED by the label", and confidence belongs in that
        answer: arming a read-only lane off a guess is how a question ends up on a lane that cannot
        reach the warehouse. Widening off the same label does not spend anything — it asks the
        person — and the question the classifier is least sure about is the one most likely to need
        an investigation. One field was answering both, in opposite directions.

        THE ADMITTED SET IS NAMED, not inferred from `label` being non-empty: `_parse` keeps the
        label on three fallbacks, and a caller written as `intent.label in {...}` would admit all
        three. `low-confidence` is the one being admitted on purpose. `unknown-label` is harmless,
        since every caller tests membership anyway. The one that earns this line is
        **`invalid-confidence`** — it keeps `label="data_answer"` on a reply that answered `1.7` or
        `NaN`, so a membership check cannot see that the classifier never scored the turn at all.

        `no-bound-context` is excluded too, for symmetry rather than for effect, and it is worth
        saying which. It is stamped by `_call` below and not by `_parse`, only `if intent.valid`,
        and only when nothing is bound — so the population THIS ticket admits can never wear it,
        and the one caller refuses it on a later condition regardless. Do not reach for it as the
        justification for this line; reach for `invalid-confidence`.
        """
        return bool(self.label) and self.fallback in ("", "low-confidence")


def _parse(raw: str) -> Intent:
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return Intent(raw=raw, fallback="invalid-json")
    if not isinstance(body, dict):
        return Intent(raw=raw, fallback="invalid-json")
    label = str(body.get("label") or "").strip()
    confidence = body.get("confidence")
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        return Intent(label=label, raw=raw, fallback="invalid-confidence")
    if label not in LABELS:
        return Intent(label=label, confidence=confidence, raw=raw, fallback="unknown-label")
    if confidence < MIN_CONFIDENCE:
        return Intent(label=label, confidence=confidence, raw=raw, fallback="low-confidence")
    return Intent(label=label, confidence=confidence, raw=raw)


def _user_payload(prompt: str, context: str, has_bound_context: bool) -> str:
    parts = [
        f"Bound context present: {'yes' if has_bound_context else 'no'}",
        f"User: {(prompt or '').strip()}",
    ]
    if context.strip():
        parts.append(f"Context:\n{context.strip()[:MAX_CONTEXT_CHARS]}")
    return "\n\n".join(parts)


@dataclass
class Pending:
    done: threading.Event | bool
    box: dict | None = None
    deadline: float = 0.0
    timeout_s: float = TIMEOUT_S
    # The model the call was routed to, carried so the two warnings below can name it. A timeout and
    # a raised error are the same degradation as an unparseable body, and `/api/diag/log?warn=1`
    # cannot answer "which model" for them unless the answer travels here (#467).
    model: str = ""

    def result(self) -> Intent:
        if self.done is True:
            return self.box["intent"] if self.box else Intent()
        assert isinstance(self.done, threading.Event)
        assert self.box is not None
        remaining = max(0.0, self.deadline - time.monotonic())
        if not self.done.wait(remaining):
            # Counted here and possibly again on the worker (#463). The thread is abandoned rather
            # than cancelled — a blocked socket read cannot be interrupted — so a call that lands
            # after the wall reports for itself below, and the turn is credited with two lost
            # judgements for one classify. Left that way: the alternative is holding the count open
            # for a thread this class exists to stop waiting for, and the log ring shows both lines.
            degraded.judgement_lost()
            log.warning("chat intent: classify timed out after %.1fs - using current Chat behavior"
                        " model=%s", self.timeout_s, self.model or "-")
            return Intent(fallback="timeout")
        if err := self.box.get("error"):
            degraded.judgement_lost()
            log.warning("chat intent: classify failed (%s: %s) - using current Chat behavior"
                        " model=%s", type(err).__name__, err, self.model or "-")
            return Intent(fallback="error")
        intent = self.box.get("intent")
        return intent if isinstance(intent, Intent) else Intent(fallback="invalid-json")


def start(
    prompt: str,
    *,
    context: str,
    has_bound_context: bool,
    gateway: GatewayClient,
    catalog: ModelCatalog,
    session: str | None = None,
    version: str | None = None,
    timeout_s: float = TIMEOUT_S,
) -> Pending:
    text = (prompt or "").strip()
    if not text:
        return Pending(True, {"intent": Intent(fallback="empty")})
    if len(text) > MAX_PROMPT_CHARS:
        log.info("chat intent: fallback=prompt-too-long - using current Chat behavior")
        return Pending(True, {"intent": Intent(fallback="prompt-too-long")})

    # Bound once and threaded everywhere this call names a model: the request, the timing ledger and
    # the report below. It used to be derived twice, and the report is a third site that would have
    # to agree with both — the drift `scope._extract` exists to prevent (#467).
    model = _model_for(catalog)
    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _user_payload(text, context, has_bound_context)},
        ],
        "max_tokens": 160,
        "temperature": 0,
        "stream": True,
        "response_format": {"type": "json_object"},
    }
    labels = CostLabels(phase="ask", mode="auto", component="chat-intent",
                        session=session, version=version)

    def _call() -> Intent:
        call = timing.model_call(model, "chat-intent")
        chunks = []
        try:
            for chunk in gateway.route(request, labels):
                call.first_byte()
                call.chunk()
                chunks.append(chunk)
        except BaseException as e:
            call.done(ok=False, error=f"{type(e).__name__}: {e}")
            raise
        call.done()
        intent = _parse(_extract(b"".join(chunks)))
        if intent.valid and intent.label == "data_artifact" and not has_bound_context:
            intent = Intent(label=intent.label, confidence=intent.confidence,
                            raw=intent.raw, fallback="no-bound-context")
        label = intent.label or "-"
        suffix = f" fallback={intent.fallback}" if intent.fallback else ""
        degraded_here = intent.fallback not in _WORKING
        if degraded_here:
            # The same condition the level split turns on, asked once and used twice (#463). `_WORKING`
            # is the line between the classifier answering-and-declining and the classifier failing to
            # answer, and the count means what the warning stream means or it means nothing.
            degraded.judgement_lost()
        level = log.warning if degraded_here else log.info
        level("chat intent: label=%s confidence=%.2f context=%s%s model=%s",
              label, intent.confidence, "yes" if has_bound_context else "no", suffix, model)
        return intent

    done = threading.Event()
    box: dict = {}

    def _worker() -> None:
        try:
            box["intent"] = _call()
        except Exception as e:
            box["error"] = e
        finally:
            done.set()

    threading.Thread(target=_worker, name="sage-chat-intent", daemon=True).start()
    return Pending(done=done, box=box, deadline=time.monotonic() + timeout_s, timeout_s=timeout_s,
                   model=model)
