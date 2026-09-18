"""#428: an old Project's refusal reached the person through a model that rewrote it.

A person in an existing Project asked for a total over a bound table. The refusal was correct — the
capability is gated on Project age and there is no backfill — but what they saw was the model
asking them to run the query by hand.

The gate is not the defect and is not touched here. The defect is that the refusal is a STRING
RETURNED TO A MODEL, which then decides what to do with it. `_data_use_note` returns `""` in an old
Project, so the model was never told the operation existed; the tool schema offers `operation` in
its enum regardless; so the model tries it, gets back a bare fact about a capability it was never
briefed on, and improvises. *"Data calculation is available in new projects only."* is true and
says nothing about what to do, and what the model did with the gap was hand the work back.

WHY THESE ASSERT THE SENTENCE AND NOT A FLAG. There is no seam between this refusal and the person
— `_no_card` logs and returns the string unchanged, and nothing else in `run.py` puts a refusal on
screen. The sentence IS the whole mechanism, so it is the only thing there is to test. That also
makes this a weaker test than most here, and it is worth being honest about the bound: it proves
the model was TOLD not to hand the work back. It cannot prove the model obeys. Only a live turn
can, and that is recorded as unverified in the report rather than implied by a green test.
"""

from __future__ import annotations

import pytest

from sage.liveread import grant
from sage.liveread.calculate import calculate
from sage.liveread.text_analysis import analyze
from sage.orchestrator import brand


class _Turn:
    """Only the field the gate reads. Every gated path returns before touching anything else."""

    data_use_enabled = False
    analyze_text_batch = None
    upload_for = None


def _refusals() -> list[tuple[str, str]]:
    """Both gated operations, reached through their real entry points rather than through `grant`.

    Calling `data_use_says` directly would pass while either gate still returned its own hand-rolled
    sentence, which is exactly the drift the shared function exists to stop.
    """
    args = {"dataset": "upload", "path": "sales.csv", "group_by": "region",
            "sum_column": "amount", "operation": "sum"}
    return [
        ("live_read_files sum", calculate(args, _Turn())),
        ("live_read_files analyze_text", analyze({**args, "operation": "analyze_text",
                                                 "text_column": "body"}, _Turn())),
    ]


@pytest.mark.parametrize("where,said", _refusals())
def test_the_refusal_names_the_project_as_the_reason(where: str, said: str):
    """Not the mechanism. `Refusal`'s own docstring already forbids naming the mechanism, and
    "available in new projects only" named nothing else."""
    assert "created before" in said, f"{where}: no reason given — {said}"
    assert "cannot run here" in said, f"{where}: does not say it will not work — {said}"


@pytest.mark.parametrize("where,said", _refusals())
def test_the_refusal_says_the_dead_end_is_permanent(where: str, said: str):
    """An old Project can never gain this. A sentence that leaves it sounding like a setting sends
    the person looking for a switch that does not exist."""
    assert "no way to switch it on" in said, f"{where}: reads as a togglable setting — {said}"


@pytest.mark.parametrize("where,said", _refusals())
def test_the_refusal_offers_something_this_project_can_still_do(where: str, said: str):
    """A dead end is the one thing this must not be. Both gated operations have an ungated
    neighbour that still works in an old Project, and the sentence names it."""
    assert "can still" in said, f"{where}: dead end — {said}"


@pytest.mark.parametrize("where,said", _refusals())
def test_the_refusal_forbids_handing_the_work_back_to_the_person(where: str, said: str):
    """The reported symptom, and the load-bearing clause.

    A bare fact left the improvising open and the model filled it with "go write the SQL yourself".
    """
    assert "Do not ask the person to write SQL" in said, f"{where}: {said}"
    assert "work the numbers out by hand" in said, f"{where}: {said}"


def test_both_gates_say_the_same_thing_in_the_same_words():
    """One sentence, two callers. Two copies drift, and the drift would be invisible: both refuse
    the same thing for the same reason, and nothing reads them side by side except this."""
    (_, calc), (_, text) = _refusals()
    # Built through `brand.text` rather than typed out, because "Project" is a pack noun and a
    # partner's pack renames it. `test_the_paranoid_pack_finds_no_leak` caught exactly that in the
    # sentence itself; a test that hardcodes it is the same defect one file over.
    shared = brand.text("there is no way to switch it on for a {project} that already exists")
    assert shared in calc and shared in text
    assert calc != text, "each names its own operation and its own fallback"


def test_the_operation_and_the_fallback_are_the_only_things_that_differ():
    """The template is shared; only the two substitutions are per-caller. Pinned so that a caller
    which starts hand-rolling its own sentence again fails here rather than quietly diverging."""
    (_, calc), (_, text) = _refusals()
    assert "local data calculation" in calc and "sample row" in calc
    assert "text analysis" in text and "read the head of one" in text
    assert grant.data_use_says("X", "Y") != grant.data_use_says("X", "Z")
