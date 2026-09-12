"""The check that stops one held turn lock from being billed to somebody else's diff (#265).

`-n auto` is xdist's `--dist load`, which distributes individual TESTS. Adding a test file re-deals
the whole suite across workers, so a lock left held reddens whoever shares the worker afterwards
rather than the test that took it — and which file that is changes with every diff. It was found
the expensive way: nine new tests in one file produced six failures in a file the diff never
opened, all `TurnBusy: A build is already running`, and it took three checks (the file alone, the
two files under `-n0`, the suite with the new file deselected) to prove none of it was the diff.

`_turn_lock_is_handed_back` in `conftest.py` turns that into a named failure on the test that did
it. This is the test of that check: an autouse fixture nobody ever sees fire is indistinguishable
from one that cannot.
"""
from __future__ import annotations

from pathlib import Path

from .conftest import held_turn_locks
from .test_chat_turn import _orch


def test_a_held_turn_lock_is_named(tmp_path: Path):
    orch, _oc = _orch(tmp_path)
    assert orch not in held_turn_locks()

    orch._turn_lock.acquire()
    try:
        assert orch in held_turn_locks()
    finally:
        orch._turn_lock.release()
    assert orch not in held_turn_locks()


def test_a_wedged_workspace_is_not_a_leak(tmp_path: Path):
    """A wedge holds the lock for the life of the process by design, and says so (#39). Counting
    it would make the tests that assert on the wedge sentence fail for asserting on it."""
    orch, _oc = _orch(tmp_path)
    orch._turn_lock.acquire()
    orch._turn_wedged = True
    assert orch not in held_turn_locks()


def test_the_fixture_fails_the_leaker_and_bills_nobody_else(pytester):
    """End to end, through a real pytest run, because the halves only mean anything together.

    The leaking test is the one that goes red. The test after it inherits the held lock and stays
    green — the fixture asks what a test CHANGED, not what is held while it runs, so one leak is
    one red rather than a cascade attributed to whoever came next. The third test is the grace: a
    lock handed back a moment after the test ends is a background commit (`_after_chat_turn`),
    which is how 102 tests in this suite legitimately finish, not a leak.
    """
    # Named, not `import *`: the fixture is underscore-prefixed, and a star import skips it —
    # which silently produced the very bug this file is about, the leaking test green and the
    # next one red.
    pytester.makeconftest(
        "from tests.conftest import _turn_lock_is_handed_back  # noqa: F401")
    pytester.makepyfile("""
        import threading

        from tests.test_chat_turn import _orch

        _kept = {}

        def test_leaks_the_lock(tmp_path):
            orch, _oc = _orch(tmp_path)
            _kept["leaked"] = orch        # outlive the test, the way a real leak does
            orch._turn_lock.acquire()

        def test_inherits_it_and_is_not_billed(tmp_path):
            assert _kept["leaked"]._turn_lock.locked() is True

        def test_a_save_still_running_at_the_end_is_waited_out(tmp_path):
            orch, _oc = _orch(tmp_path)
            orch._turn_lock.acquire()
            threading.Timer(0.5, orch._release_turn).start()
    """)
    result = pytester.runpytest_subprocess("-n0", "-p", "no:cacheprovider")
    # An error rather than a failure, because the check runs in teardown: the leaking test's own
    # body had nothing wrong with it, which is the point — what it got wrong is what it left.
    result.assert_outcomes(passed=3, errors=1)
    result.stdout.fnmatch_lines(["*test_leaks_the_lock*left 1 turn lock(s) held*"])
