"""Chat → Build detect-once classifier (docs/workbench/handoff.md).

After a sage-chat turn, decide whether this Thread has started asking for a lasting app.
One bounded gateway call, no tools. Biased to CHAT (no suggestion). Fail open on timeout
or error; fail safe (suggest) on an unreadable answer; breaker after three garbage replies.

Explicit "build me a dashboard" / "open this in the builder" skips the model and counts as a hit.
"""
from __future__ import annotations

import concurrent.futures
import logging
import re

from ..gateway.client import CostLabels, GatewayClient
from ..router.models import ModelCatalog
from .scope import _extract, _model_for

log = logging.getLogger(__name__)

TIMEOUT_S = 8.0
MAX_UNREADABLE = 3

# Only the last user + last assistant + title. Truncate rather than refuse.
_TITLE_CHARS = 200
_TURN_CHARS = 1500

_SYSTEM = """\
You decide whether a Chat Thread has started asking for a lasting UI that other people would open, \
rather than a one-off answer about data.

Answer with exactly one word, nothing else:

APP - the user now wants a lasting app: more than one view, something that should keep working \
tomorrow, a dashboard or tool colleagues would open.
CHAT - everything else: a question, a chart, a table, exploration, a follow-up about the same numbers.

Default to CHAT. Suggesting an app every few messages is worse than missing a real app request. \
Answer APP only if you would be uncomfortable treating this as a one-off analysis."""

# Explicit confirm-intent. Keep this tight: "dashboard" in an analysis question is the classifier's
# job, not this regex. The spec's examples are "build me a dashboard" and "open this in the builder".
_EXPLICIT_BUILD = re.compile(
    r"(?:"
    r"\bbuild me (?:an? )?(?:app|dashboard|ui|page)\b"
    r"|\bopen (?:this|it) in (?:the )?(?:builder|build)\b"
    r"|\bturn (?:this|it) into an app\b"
    r"|\bmake (?:this|it)(?: into)? an app\b"
    r")",
    re.IGNORECASE,
)


class _Health:
    """Consecutive unreadable answers, and the breaker they trip. Process-wide: the thing being
    tracked is the gateway route, not a Thread."""

    def __init__(self) -> None:
        self.unreadable = 0
        self.broken = False

    def reset(self) -> None:
        self.unreadable = 0
        self.broken = False

    def answered(self) -> None:
        self.unreadable = 0

    def unreadable_answer(self, answer: str) -> bool:
        """Record an answer in neither vocabulary; return what wants_an_app should return.

        True (suggest) until the breaker trips, then False (stay in Chat) forever after."""
        self.unreadable += 1
        if self.unreadable < MAX_UNREADABLE:
            log.warning("handoff: unrecognised verdict %r (%d in a row) — suggesting",
                        answer[:60], self.unreadable)
            return True
        if not self.broken:
            self.broken = True
            log.error("handoff: classifier BROKEN — %d unreadable verdicts in a row (last %r). "
                      "Not calling it again; Chat will not suggest Open in Build from the classifier. "
                      "Check the gateway route for the ask model.",
                      self.unreadable, answer[:60])
        return False


_health = _Health()


def looks_like_build_request(prompt: str) -> bool:
    """True when the user already asked to build — skip the classifier, go to the sheet."""
    return bool(_EXPLICIT_BUILD.search(prompt or ""))


def should_classify(handoff: dict | None) -> bool:
    """False once this Thread has been suggested or suppressed. Classifier runs at most once."""
    if not handoff:
        return True
    if handoff.get("suggestedAt") or handoff.get("suppressed"):
        return False
    return handoff.get("status") not in ("suggested", "suppressed", "planned", "bound")


def _payload(title: str, user: str, assistant: str) -> str:
    parts = [f"Thread title: {(title or '').strip()[:_TITLE_CHARS]}",
             f"User: {(user or '').strip()[:_TURN_CHARS]}"]
    text = (assistant or "").strip()
    if text:
        parts.append(f"Assistant: {text[:_TURN_CHARS]}")
    return "\n\n".join(parts)


def last_assistant_text(history: list[dict]) -> str:
    texts = [e.get("text") or "" for e in history
             if e.get("type") == "agent" and e.get("kind") == "text" and e.get("text")]
    return texts[-1] if texts else ""


def wants_an_app(
    *,
    title: str,
    user: str,
    assistant: str,
    gateway: GatewayClient,
    catalog: ModelCatalog,
    locked: bool,
    session: str | None = None,
    version: str | None = None,
    timeout_s: float = TIMEOUT_S,
) -> bool:
    """True when this Thread should be offered Open in Build.

    False on timeout or error (fail open). True on an unreadable answer until the breaker trips."""
    if _health.broken:
        return False
    text = _payload(title, user, assistant).strip()
    if not text:
        return False

    request = {
        "model": _model_for(catalog, locked),
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": text},
        ],
        "max_tokens": 256,
        "temperature": 0,
        "stream": True,
    }
    labels = CostLabels(phase="ask", mode="auto", component="handoff",
                        session=session, version=version)

    def _call() -> str:
        return _extract(b"".join(gateway.route(request, labels)))

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="sage-handoff")
    try:
        answer = pool.submit(_call).result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        log.warning("handoff: classify timed out after %.1fs — no suggestion", timeout_s)
        return False
    except Exception as e:
        log.warning("handoff: classify failed (%s: %s) — no suggestion", type(e).__name__, e)
        return False
    finally:
        pool.shutdown(wait=False)

    verdict = answer.strip().upper()
    if verdict.startswith("APP"):
        _health.answered()
        return True
    if verdict.startswith("CHAT"):
        _health.answered()
        return False
    return _health.unreadable_answer(answer)
