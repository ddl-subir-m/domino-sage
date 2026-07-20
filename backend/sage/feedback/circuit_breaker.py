"""Circuit breaker (SPEC C10, PLAN 5.2, R1).

Bounds the agent's self-correction loop so a weak model can't oscillate forever burning
wall-clock and cost. Stops on: too many iterations, a time budget, or no progress (the same
error signature repeated N turns). Pure logic with an injectable clock for testing.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    action: str  # "continue" | "stop"
    reason: str


class CircuitBreaker:
    def __init__(
        self,
        max_iterations: int = 15,
        max_seconds: float = 600.0,
        no_progress_limit: int = 3,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._max_iterations = max_iterations
        self._max_seconds = max_seconds
        self._no_progress_limit = no_progress_limit
        import time

        self._clock = clock or time.monotonic
        self._start: float | None = None
        self._iterations = 0
        self._last_signature: str | None = None
        self._repeat_count = 0

    def record(self, report_signature: str, resolved: bool) -> Decision:
        """Call once per agent turn with the current typecheck signature.

        resolved=True (typecheck clean) always stops the loop as success.
        """
        if self._start is None:
            self._start = self._clock()
        self._iterations += 1

        if resolved:
            return Decision("stop", "typecheck clean")

        # No-progress: identical error set repeated across turns.
        if report_signature and report_signature == self._last_signature:
            self._repeat_count += 1
        else:
            self._repeat_count = 0
        self._last_signature = report_signature

        if self._repeat_count + 1 >= self._no_progress_limit:
            return Decision("stop", f"no progress: same errors {self._repeat_count + 1} turns in a row")
        if self._iterations >= self._max_iterations:
            return Decision("stop", f"hit max iterations ({self._max_iterations})")
        if self._clock() - self._start >= self._max_seconds:
            return Decision("stop", f"hit time budget ({self._max_seconds}s)")
        return Decision("continue", f"iteration {self._iterations}, errors remain")
