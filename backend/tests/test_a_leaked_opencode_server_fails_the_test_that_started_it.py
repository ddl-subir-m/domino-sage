"""The check that stops a test from leaving `opencode serve` running for good (#536).

`OpenCodeServer.start` puts OpenCode in its own session (`start_new_session=True`), so `stop()` can
kill the whole group. The price is that the server does not die with its parent: only `stop()`
ends it. On 2026-09-22 a draft test built an `Orchestrator` with no fake OpenCode, its first turn
reached `_ensure_opencode`, and three real servers ran for two days under `launchd` until they were
found by hand.

`_opencode_server_is_stopped` in `conftest.py` stops such a server and names the test that started
it. This is the test of that check: an autouse fixture nobody ever sees fire is indistinguishable
from one that cannot.
"""
from __future__ import annotations


def test_the_fixture_stops_the_leaked_server_and_fails_only_its_test(pytester):
    """End to end, through a real pytest run, with a REAL process group standing in for OpenCode.

    `start()` runs its real code: only `Popen`'s command is swapped, for a shell that prints the
    line OpenCode prints and then waits, so the check sees a real `subprocess.Popen` in a real
    session of its own — the only kind that can outlive a test.
    """
    pytester.makeconftest(
        "from tests.conftest import _opencode_server_is_stopped  # noqa: F401")
    pytester.makepyfile("""
        import os
        import subprocess

        import pytest

        from sage.driver import server as drv

        _kept = {}
        _real_popen = subprocess.Popen

        @pytest.fixture
        def fake_opencode(monkeypatch):
            line = "opencode server listening on http://127.0.0.1:4096"
            monkeypatch.setattr(drv.subprocess, "Popen", lambda cmd, **kw: _real_popen(
                ["sh", "-c", f"echo '{line}'; exec sleep 60"], **kw))

        def test_leaks_its_server(tmp_path, fake_opencode):
            server = drv.OpenCodeServer(cwd=tmp_path)
            server.start(ready_timeout_s=10)
            _kept["pid"] = server._proc.pid

        def test_stops_its_own_server(tmp_path, fake_opencode):
            server = drv.OpenCodeServer(cwd=tmp_path)
            server.start(ready_timeout_s=10)
            server.stop()

        def test_the_leaked_server_is_gone():
            with pytest.raises(ProcessLookupError):
                os.kill(_kept["pid"], 0)
    """)
    result = pytester.runpytest_subprocess("-n0", "-p", "no:cacheprovider")
    # An error rather than a failure, because the check runs in teardown: the leaking test's own
    # body had nothing wrong with it — what it got wrong is what it left.
    result.assert_outcomes(passed=3, errors=1)
    result.stdout.fnmatch_lines(["*test_leaks_its_server*left 1 OpenCode server(s) running*"])
