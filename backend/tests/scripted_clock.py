"""Move `time.sleep` and `time.monotonic` together.

A poll with no event stream sleeps through `time.sleep`. Erasing sleep and leaving the clock
alone makes that sleep a busy spin; moving both makes the same poll cost the turn a second
and the suite nothing.

The caller puts the old clock back. `monkeypatch` would, but it is torn down after the
turn-lock grace, and that grace reads this same clock — a scripted one spends its five
seconds in a handful of calls and reports a lock that a real five seconds would have seen
released.
"""


def script_the_clock():
    import time

    saved_sleep = time.sleep
    saved_monotonic = time.monotonic
    offset = {"s": 0.0}

    def sleep(seconds: float = 0.0, *_args, **_kwargs) -> None:
        offset["s"] += seconds or 0.0

    time.sleep = sleep
    time.monotonic = lambda: saved_monotonic() + offset["s"]

    def restore() -> None:
        time.sleep = saved_sleep
        time.monotonic = saved_monotonic

    return restore
