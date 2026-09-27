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
    state = {"on": True, "s": 0.0}

    def sleep(seconds: float = 0.0, *args, **kwargs) -> None:
        if not state["on"]:
            return saved_sleep(seconds, *args, **kwargs)
        state["s"] += seconds or 0.0

    def monotonic() -> float:
        if not state["on"]:
            return saved_monotonic()
        return saved_monotonic() + state["s"]

    time.sleep = sleep
    time.monotonic = monotonic

    def restore() -> None:
        # Off before the names go back. A later undo can put these two functions back in place,
        # and a wrapper that kept counting would move the next test's clock.
        state["on"] = False
        time.sleep = saved_sleep
        time.monotonic = saved_monotonic

    return restore


def script_poll_clock():
    """Credit a streamless poll's erased sleep without changing other sleeps.

    Tests that set their own monotonic clock keep control of it. Disable retained
    callbacks on restore so a later monkeypatch undo cannot revive this clock.
    """
    import time

    from sage.orchestrator import service

    saved_sleep = time.sleep
    saved_monotonic = time.monotonic
    saved_wall_sleep = service._WALL_SLEEP
    state = {"active": True, "offset": 0.0}

    def monotonic() -> float:
        return saved_monotonic() + (state["offset"] if state["active"] else 0.0)

    def wall_sleep(seconds: float) -> None:
        if (state["active"] and time.monotonic is monotonic
                and time.sleep is not saved_sleep):
            state["offset"] += seconds
        else:
            saved_wall_sleep(seconds)

    time.monotonic = monotonic
    service._WALL_SLEEP = wall_sleep

    def restore() -> None:
        state["active"] = False
        time.monotonic = saved_monotonic
        service._WALL_SLEEP = saved_wall_sleep

    return restore
