"""One more try when a call never left the machine.

A dropped connection, a reset, or a 502/503/504 from a proxy is a blip: the next attempt a
moment later usually lands. A timeout already waited, a 4xx is the server's answer, and a store
that objected to the statement has nothing to gain from being asked again. Those are not retried.

Four attempts, then the original failure. The pauses are short on purpose — a hang that lasts
must still surface, and a retry of a timeout would multiply the wait.
"""
from __future__ import annotations

import logging
import re
import time

log = logging.getLogger("sage.transient")

ATTEMPTS = 4
BACKOFF_S = (0.4, 1.0, 2.0)
TRANSIENT_STATUS = frozenset({502, 503, 504})
# A rate limit clears in seconds, not in the blip's fraction of one. Longer pauses, and a budget
# for the whole wait, so a provider that keeps saying 429 still ends the turn instead of hanging it.
RATE_LIMIT_BACKOFF_S = (2.0, 5.0, 10.0)
RATE_LIMIT_BUDGET_S = 20.0

_DROP = re.compile(
    r"ConnectError|ReadError|WriteError|CloseError|RemoteProtocolError|"
    r"gateway returned 50[234]|closed the stream mid-response|"
    r"connection refused|connection reset|failed to connect",
    re.IGNORECASE,
)
# The sentence the shim writes into an already-open stream. It is the whole reply when nothing
# else arrived, and a suffix when the model had already said something.
_CLOSED = re.compile(
    r"⚠️?\s*The model gateway closed the stream mid-response \([^)]*\)\.\s*"
    r"This is usually an upstream idle or duration limit — please retry\.?",
)


def pause(attempt: int) -> None:
    time.sleep(BACKOFF_S[attempt])


def rate_limit_delay(attempt: int, waited: float, retry_after: str | None = None) -> float | None:
    """Seconds to wait before trying a rate-limited call again, or None to give up.

    `Retry-After` wins when it is a number of seconds. A wait that would carry the total past the
    budget is not started: the person is better told now than after a pause that ends in a 429.
    """
    if attempt >= len(RATE_LIMIT_BACKOFF_S):
        return None
    delay = RATE_LIMIT_BACKOFF_S[attempt]
    try:
        delay = max(0.0, float(retry_after))
    except (TypeError, ValueError):
        pass
    return delay if waited + delay <= RATE_LIMIT_BUDGET_S else None


def wait(seconds: float, cancel=None) -> bool:
    """Sleep, ending early when `cancel` (a `StreamCancellation`) fires. True when it did."""
    if cancel is None:
        time.sleep(seconds)
        return False
    return cancel.event.wait(seconds)


def transport_blip(exc: BaseException) -> bool:
    """A connection that failed before it could be a timeout.

    `TimeoutException` is a transport error too, and it is the one this refuses: the read budget
    is already 300s, and repeating it is how a quiet gateway becomes a twenty-minute turn.
    """
    import httpx

    return isinstance(exc, (httpx.NetworkError, httpx.RemoteProtocolError))


def call_http(do):
    """`do()` once, and again on a dropped connection or a 502/503/504.

    The response — including a 4xx, and a 5xx that survived every attempt — is returned. The
    caller still decides what a status means. The last transport error is re-raised.
    """
    last: BaseException | None = None
    for attempt in range(ATTEMPTS):
        try:
            response = do()
        except Exception as exc:
            if not transport_blip(exc) or attempt + 1 == ATTEMPTS:
                raise
            last = exc
            log.warning("request did not connect (%s); retrying", type(exc).__name__)
            pause(attempt)
            continue
        status = getattr(response, "status_code", None)
        if status in TRANSIENT_STATUS and attempt + 1 < ATTEMPTS:
            log.warning("request answered %s; retrying", status)
            pause(attempt)
            continue
        return response
    if last is not None:
        raise last
    raise RuntimeError("transient retry ended without a response")


def lost_on_a_short_drop(body: str, step_error: str, gateway_message: str) -> bool:
    """The turn has nothing a person could use, and the failure is a dropped connection.

    A paragraph that arrived before the drop is an answer and stays one. A guardrail refusal
    names a policy; sending the question again asks to be refused again.
    """
    raw = " ".join(part for part in (body, step_error, gateway_message) if part)
    if not raw or not _DROP.search(raw):
        return False
    if "guardrail" in raw.lower() or "blocked by" in raw.lower():
        return False
    useful = _CLOSED.sub("", body or "").strip()
    return not useful
