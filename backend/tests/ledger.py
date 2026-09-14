"""Reading the turn ledger from a test.

`timing.recent()` is the diagnostics readout's view of the whole process, and a test that just ran
a turn is asking something narrower. Two ways it answers the wrong question: a turn opened on
another thread since takes the front of the list, so `recent(1)[0]` comes back as a stranger's
record with no calls on it (#339); and with `SAGE_TIMING=0` nothing is recorded at all, so the list
is empty and the index raises before any assertion runs (#336).

So a test asks `last_turn()` for the turn that is OVER, and carries `needs_ledger` to declare the
dependency it has on the flag. The flag is a supported configuration and nobody runs in it, which
is how both tests reached `main` having never been exercised under it.
"""

from __future__ import annotations

import pytest

from sage import timing

# Two marks, because two different things depend on the ledger. A test asserting on what a turn
# RECORDED has nothing to say with the recorder off and skips; a test that merely cross-checks
# against the ledger still has a subject either way, and skipping it would delete product coverage
# over a diagnostics flag. Both get a ledger of their own — see `ledger` in conftest.
own_ledger = pytest.mark.usefixtures("ledger")
needs_ledger = pytest.mark.usefixtures("ledger_required")


def last_turn() -> timing.TurnRecord:
    """The record of the turn that finished most recently.

    Not `recent(1)[0]`: that one leads with a turn still running, whoever started it. This is the
    right read wherever the test cannot hold its own record — the turn was closed inside
    `build_stream` or `chat_stream` and the return value never reached the caller. Where the test
    DOES call `finish_turn` itself, use what that returns instead and the question of which turn
    you got does not arise.
    """
    rec = timing.last_finished()
    assert rec is not None, "no turn reached the ledger — did the turn under test run at all?"
    return rec
