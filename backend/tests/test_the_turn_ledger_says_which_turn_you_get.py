"""What `recent(n)` promises, and what it does not.

Two defects came out of the same missing sentence. `recent()` splices the module-global `_current`
onto the front of history, so `recent(1)[0]` is whichever turn is newest in the PROCESS — not the
turn the caller ran. A turn begun on another thread since displaces it, calls and all (#339), and
under `SAGE_TIMING=0` nothing is recorded at all so the list is empty and the index raises (#336).

The split here is the answer to both. `recent()` keeps the contract the diagnostics readout needs —
the record someone most wants is the one they are waiting on, so a running turn sits at the front.
`last_finished()` is the other question: the turn that is OVER. A caller who means "the turn I just
ran" asks that one, and gets `None` rather than someone else's work when there is no answer.
"""

from __future__ import annotations

import pytest

from sage import timing


@pytest.fixture(autouse=True)
def _on_and_alone(ledger, monkeypatch):
    """`ledger` is the same isolation every other ledger test gets — a record this file writes must
    not travel, and a case here asserting `None` must not read the turn some earlier test left.

    The flag is forced ON rather than skipped on, because this is the file the others skip INTO:
    one case here turns it off on purpose, and the rest have to be running with it on for that to
    be a contrast rather than a coincidence.
    """
    monkeypatch.setenv("SAGE_TIMING", "1")


def _a_turn_with_one_call(kind: str, prompt: str) -> None:
    timing.start_turn(kind, prompt)
    timing.model_call("a", "handoff").done()
    timing.finish_turn(ok=True, decision="answered")


def test_a_turn_begun_since_does_not_displace_the_one_that_finished():
    """#339, at the size it actually happens: a turn opens on another thread after mine closed, and
    my read comes back with an empty record that was never mine."""
    _a_turn_with_one_call("chat", "which desk lost the most?")
    timing.start_turn("build", "somebody else's turn, still running")

    intruder = timing.recent(1)[0]
    assert intruder is timing.current(), "recent() is the readout's view: the running turn leads"
    assert intruder.calls == [], "and it has recorded nothing yet, which is how the read went wrong"

    rec = timing.last_finished()
    assert rec is not None
    assert rec.prompt == "which desk lost the most?"
    assert [c.phase for c in rec.calls] == ["handoff"]


def test_the_running_turn_is_never_the_finished_one():
    """The narrower promise, stated on its own: whatever `last_finished()` returns is closed."""
    _a_turn_with_one_call("chat", "first")
    timing.start_turn("build", "second")

    rec = timing.last_finished()
    assert rec is not None and rec.t1 is not None
    assert rec is not timing.current()


def test_nothing_finished_yet_is_an_answer_rather_than_an_index_error():
    """#336's failure mode with the flag still ON: an empty ledger is not only what `SAGE_TIMING=0`
    leaves behind, it is also where every process starts. `recent(1)[0]` raises there and tells the
    caller nothing; `None` is a value a caller can branch on.

    Then the other half, in the same case because it is the same read: once a turn opens, `recent`
    stops being empty and starts being WRONG for this question — it has something to index, and the
    something is the running turn.
    """
    assert timing.recent(1) == [], "nothing recorded yet"
    assert timing.last_finished() is None

    timing.start_turn("build", "running, and the only turn there has ever been")

    assert timing.recent(1) != [], "the running turn is there to be indexed into"
    assert timing.last_finished() is None, "a turn that has not ended has not finished"


def test_finishing_a_turn_hands_back_the_record_it_closed():
    """The only read that cannot be wrong: the caller who ended the turn holds it by identity."""
    timing.start_turn("build", "mine")
    timing.model_call("i", "implement").done()
    rec = timing.finish_turn(ok=True, decision="typecheck clean")

    assert rec is not None
    assert rec.prompt == "mine" and rec.decision == "typecheck clean"
    assert rec is timing.last_finished()
    assert timing.finish_turn() is None, "there is no second record to close"


def test_an_abandoned_turn_counts_as_finished():
    """`start_turn` closes any turn still open rather than dropping it — a turn that died without
    finishing is the most interesting one in the ring. It is closed, so it is readable."""
    timing.start_turn("build", "died on its feet")
    timing.start_turn("chat", "the one that displaced it")

    rec = timing.last_finished()
    assert rec is not None
    assert rec.prompt == "died on its feet"
    assert rec.decision == "abandoned" and rec.t1 is not None


def test_a_turn_that_ends_mid_read_is_in_the_answer_once_rather_than_not_at_all(monkeypatch):
    """`recent` answers with two things — the ring and the live record — and they have to be one
    moment. Read in two steps, a `finish_turn` landing between them rings the turn and clears
    `_current`, so the snapshot taken a moment earlier does not have it and the second read no
    longer does either: the newest turn, which this list leads with on purpose, is missing.

    Driven off the lock's own release rather than off a second thread, because the window is
    microseconds wide and a racing test that reproduces it one run in a thousand is a test that
    reports green on a broken build. This fires on the release every time.
    """
    real = timing._lock

    class _EndsTheTurnOnRelease:
        def __enter__(self):
            return real.__enter__()

        def __exit__(self, *exc):
            out = real.__exit__(*exc)
            if timing.current() is not None:
                timing.finish_turn(ok=True, decision="ended mid-read")
            return out

    timing.start_turn("build", "ends while the readout is mid-sentence")
    monkeypatch.setattr(timing, "_lock", _EndsTheTurnOnRelease())
    got = timing.recent(5)

    prompts = [r.prompt for r in got]
    assert prompts.count("ends while the readout is mid-sentence") == 1, prompts


def test_the_ledger_switched_off_records_nothing_and_says_so(monkeypatch):
    """`SAGE_TIMING=0` is a supported configuration and nobody runs in it, so this is the one place
    the no-op is the behaviour under test. Every other ledger test skips under the flag instead:
    they assert on content, and there is no content to assert on."""
    monkeypatch.setenv("SAGE_TIMING", "0")
    assert timing.enabled() is False

    timing.start_turn("chat", "goes nowhere")
    timing.model_call("a", "handoff").done()
    with timing.span("setup.prompt"):
        pass
    assert timing.finish_turn(ok=True, decision="answered") is None

    assert timing.current() is None
    assert timing.recent(1) == []
    assert timing.last_finished() is None
    assert timing.render_all(1) == "(no turns recorded yet)"
