"""The check that fails a test for what it leaves open (#608).

`__nothing_is_left_open` in `conftest.py` bills the test that leaves a thread, a process or a file
descriptor behind, rather than whichever test lands next on that worker. This is the test of that
check: an autouse fixture nobody ever sees fire is indistinguishable from one that cannot.
"""
from __future__ import annotations


def test_each_kind_of_leak_fails_its_own_test_and_nothing_else(pytester):
    """End to end, through a real pytest run. One leaker per kind, each beside a twin that closes
    what it opened, and a last test that proves the leaked process was stopped."""
    pytester.makeconftest("from tests.conftest import __nothing_is_left_open  # noqa: F401")
    pytester.makepyfile("""
        import os
        import socket
        import subprocess
        import threading

        _kept = []
        _stop = threading.Event()

        def test_leaks_a_thread():
            threading.Thread(target=_stop.wait, name="left-behind", daemon=True).start()

        def test_joins_its_thread():
            t = threading.Thread(target=lambda: None)
            t.start()
            t.join()

        def test_leaks_a_file(tmp_path):
            _kept.append(open(tmp_path / "f", "w"))

        def test_closes_its_file(tmp_path):
            with open(tmp_path / "f", "w"):
                pass

        def test_leaks_a_socket():
            _kept.append(socket.socket())

        def test_leaks_a_process():
            _kept.append(subprocess.Popen(["sleep", "60"]))

        def test_waits_for_its_process():
            subprocess.run(["true"], check=True)

        def test_the_leaked_process_was_stopped():
            _stop.set()
            proc = next(p for p in _kept if isinstance(p, subprocess.Popen))
            assert proc.poll() is not None
    """)
    result = pytester.runpytest_subprocess("-n0", "-p", "no:cacheprovider", "-p", "no:randomly")
    # Errors rather than failures, because the check runs in teardown: each leaker's own body had
    # nothing wrong with it — what it got wrong is what it left.
    result.assert_outcomes(passed=8, errors=4)
    result.stdout.fnmatch_lines_random([
        "*test_leaks_a_thread*left open: thread 'left-behind'*",
        "*test_leaks_a_file*left open: fd * (file)*",
        "*test_leaks_a_socket*left open: fd * (socket)*",
        "*test_leaks_a_process*left open: process *sleep*60*",
    ])


def test_the_check_is_set_up_first_so_it_tears_down_last(request):
    """Its name is what orders it, so a rename is what would break it: pytest sets up autouse
    fixtures in name order, and a check that tears down before the save timer's cancel or the
    turn-lock grace bills the threads those were about to stop."""
    mine = [n for n in request.fixturenames if n.startswith("_")]
    assert mine[0] == "__nothing_is_left_open", mine
