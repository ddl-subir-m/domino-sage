"""How many judgements a turn asked for and did not get (#463).

Four read-only classifiers run on the ask slot — `chat_intent`, `scope`, `handoff` and
`table_rank` — and every one of them falls back silently when it cannot get an answer. Each
fallback is correct on its own: `scope` fails OPEN because an unreachable classifier must not
block builds, `table_rank` fails CLOSED because the layer heuristic always has candidates, and
Chat answers either way. The COMPOSITION is what fails. Three correct silent fallbacks add up to
a turn that looks healthy while three judgements were replaced by defaults, and the one control
a person has over models cannot reach the slot that broke.

This is the count that makes "did this turn lose its judgements?" one number on `/api/diag`
instead of three grep strings over the log ring.

**Not `timing.count()`**, and the reason is the one `app._resolved` already gives for the
Project's copy of the resolved model: the timing ledger no-ops when `SAGE_TIMING` is off, so a
count kept there would read zero on every deployment that has not turned tracing on. Whether a
turn lost its judgements must not be switchable by a performance flag.

What counts is a judgement the turn asked for and did not get, which is three shapes and not one:
the call never landed (timeout, error, no budget left), an answer landed that could not be used
(unparseable JSON, an empty body, a verdict in neither vocabulary), or no call was made because
a breaker had already tripped. The third earns its place rather than riding on the log: each
`_Health` breaker announces itself ONCE at ERROR and is then silent for the life of the process,
so every later turn loses that judgement with nothing at all to say so — and a count that read
zero on the most degraded turns there are would be worse than no count.

ONE exclusion, named rather than left to be discovered as an omission. `handoff` loses its judgement
when the sensitivity gate cannot be read, and that is not counted here: it is a different subsystem
failing in front of the classifier rather than the classifier degrading, the sibling `except` in
`_preview_approve_model` answers the same condition the opposite way by design (ADR-0057), and it
reports itself with a full traceback rather than silently. If that ever becomes the interesting
failure, it wants its own count and not a rank in this one.

What does NOT count is a classifier answering and then declining. `low-confidence`,
`no-bound-context`, an empty prompt and a prompt over the length guard are ordinary outcomes the
turn is designed to proceed from, and #467 already drew that line for the log level. Same line
here, so the count and the warning stream agree about what a degradation is.

Process-wide rather than per-project, for the reason the three `_Health` breakers beside it are:
a Sage builder serves one project, and the thing being counted is a gateway route and a model.

Reset when a turn is GRANTED (`service._begin_model_record`) and never when one ends — the same
boundary and the same argument as the resolved-model record it is reset beside. A classifier
abandoned by a timeout is not cancelled, so a late worker can land its report inside the NEXT
turn and be counted there. That is a property of not interrupting a blocked socket read, named
here rather than closed: the alternative is holding the turn open for a thread it deliberately
stopped waiting for.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_count = 0


def reset() -> None:
    """Start a turn's count at zero."""
    global _count
    with _lock:
        _count = 0


def judgement_lost() -> None:
    """One classifier asked for a judgement this turn and did not get a usable one.

    Called beside the report rather than instead of it: the log says WHICH classifier and which
    model, and this says how many, and neither is derivable from the other on a surface that is
    always on.
    """
    global _count
    with _lock:
        _count += 1


def count() -> int:
    """How many judgements this turn has lost so far."""
    with _lock:
        return _count
