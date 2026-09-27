"""An erased poll costs its deadline one second and the test no real wait."""
import time

import pytest

from sage.orchestrator import service


@pytest.mark.parametrize("method", ["wait", "wait_any"])
def test_a_noop_sleep_pays_the_poll_in_scripted_time(monkeypatch, method):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    tap = service._EventTap(object(), "scripted-session")
    wall_start = time.perf_counter()
    clock_start = time.monotonic()

    assert getattr(tap, method)(1.0) is False

    assert time.perf_counter() - wall_start < 0.5
    assert 1.0 <= time.monotonic() - clock_start < 1.5


def test_an_erased_sleep_outside_the_poll_does_not_advance_the_clock(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    wall_start = time.perf_counter()
    clock_start = time.monotonic()

    time.sleep(30.0)

    wall_elapsed = time.perf_counter() - wall_start
    clock_elapsed = time.monotonic() - clock_start
    assert abs(clock_elapsed - wall_elapsed) < 0.1


def test_restored_poll_callbacks_cannot_advance_a_later_tests_clock(monkeypatch):
    from .scripted_clock import script_poll_clock

    waited = []
    with monkeypatch.context() as patch:
        patch.setattr(service, "_WALL_SLEEP", waited.append)
        restore = script_poll_clock()
        old_clock, old_wait = time.monotonic, service._WALL_SLEEP
        patch.setattr(time, "sleep", lambda *_: None)
        try:
            before = time.monotonic()
            old_wait(2.0)
            assert time.monotonic() - before >= 2.0
        finally:
            restore()

        old_wait(3.0)
        assert waited == [3.0]
        assert abs(old_clock() - time.monotonic()) < 0.1


def test_a_failed_test_restores_the_clock_before_the_next_test_and_lock_grace(pytester):
    pytester.makeconftest("""
        from tests.conftest import (
            _the_wall_clock_starts_each_test,
            _turn_lock_is_handed_back,
        )
    """)
    pytester.makepyfile("""
        import threading
        import time
        import pytest
        from sage.orchestrator import service
        from tests.test_chat_turn import _orch

        wall_clock = time.monotonic
        wall_sleep = time.sleep
        erased_sleeps = []

        def test_failure(monkeypatch):
            monkeypatch.setattr(time, 'sleep', lambda *_: None)
            service._EventTap(object(), 's').wait(30.0)
            assert time.monotonic() - wall_clock() == pytest.approx(30.0, abs=0.1)
            # Undo will reinstate these callbacks after the clock fixture restores.
            monkeypatch.setattr(time, 'monotonic', time.monotonic)
            monkeypatch.setattr(service, '_WALL_SLEEP', service._WALL_SLEEP)
            pytest.fail('intentional failure after an erased poll')

        def test_next_clock_is_clean():
            assert time.sleep is wall_sleep
            assert abs(time.monotonic() - wall_clock()) < 0.1

        def test_lock_grace_uses_real_time(tmp_path, monkeypatch):
            orch, _ = _orch(tmp_path)
            orch._turn_lock.acquire()
            monkeypatch.setattr(time, 'sleep', erased_sleeps.append)
            service._EventTap(object(), 's').wait(30.0)
            threading.Timer(0.1, orch._release_turn).start()

        def test_lock_grace_did_not_use_the_erased_sleep():
            assert erased_sleeps == [30.0]
    """)
    result = pytester.runpytest_subprocess("-n0", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=3, failed=1)
    result.stdout.fnmatch_lines(["*intentional failure after an erased poll*"])
