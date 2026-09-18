"""#426: the card asked which table after the person had already named one, and cost a turn.

Measured live on 2026-09-18: `@Snowflake-Data-Warehouse Show me a few sample rows from
DWH.MARTS.MIXPANEL__EVENT.` returned *"Pick a Table to start from"* and nothing else —
`done: ok=false decision="table candidates"` — with `DWH.MARTS.MIXPANEL__EVENT` sitting in the
card's own list. The whole first data turn bought a click.

`named_source` tests the BINDING's recorded table (`not b.table`), and its docstring says that is
the whole test. Prose is matched only to pick WHICH Data Source is meant, never to answer the
question the card then asks, so a fully-qualified name left the Binding exactly as unscoped as no
name at all.

TWO GATES, ONE FIX. Chat and Build both call `named_source` and both draw the same card, so fixing
one would leave the other asking for a table the person had just named. The Chat half writes the
Thread's context row and the Build half writes the Binding, which is why each has its own test here
rather than one test over a shared helper.

WHAT IS DELIBERATELY NOT DONE. A bare or half-qualified name still raises the card: the position is
exactly what those cannot supply, and `Candidate`'s own docstring gives the reason —
`DWH.MARTS.GONG__CALLS` and `SANDBOX.PUBLIC.GONG__CALLS` are two different tables. Widening to a
unique bare name is reasonable and is a separate decision.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from sage.resources.table_search import Candidate, named_candidate
from sage.workspace.threads import ThreadStore

from .test_a_chat_build_request_is_asked_which_table import (
    _gong_warehouse,
    _orch,
    _thread_with_source,
)

# Both carry the @mention, exactly as the live prompt did. Without it `named_source` matches no
# store — "dwh" is not a handle for `Snowflake-Data-Warehouse` — the gate declines before any of
# this is reached, and a test asserting "no card" would pass because the gate never ran. That is
# the same test passing for the opposite reason, so `test_a_bare_table_name_still_gets_the_card`
# below is the positive control: it shares this shape and must still draw one.
NAMED = "@Snowflake-Data-Warehouse show me a few sample rows from DWH.MARTS.GONG__CALLS."
BARE = "@Snowflake-Data-Warehouse show me a few sample rows from GONG__CALLS"


def _events(orch, tid, prompt):
    return [e for e in orch.chat_stream(tid, prompt) if isinstance(e, dict)]


def _cards(events):
    return [e for e in events if e.get("type") == "table-candidates"]


def _row(orch, tid) -> dict:
    """The store's row on the Thread — what a handoff reads through `binding_from_context`.

    `columns` sits BESIDE `scope` rather than inside it, which is the shape
    `confirm_thread_table_candidate` writes and the shape the prompt renders from.
    """
    items = ThreadStore(orch._chat_project().record.path).read_context(tid).get("items") or []
    rows = [i for i in items if str(i.get("kind") or "") in ("data_source", "datasource", "table")]
    return rows[0] if rows else {}


def _scope(orch, tid) -> dict:
    return _row(orch, tid).get("scope") or {}


# --------------------------------------------------------------------------------------
# The matcher itself. Pure, so these are the cheap half and they carry the boundary cases.
# --------------------------------------------------------------------------------------

CANDIDATES = (
    Candidate("DWH", "MARTS", "GONG__CALLS"),
    Candidate("DWH", "MARTS", "GONG__CALL_PARTICIPANTS"),
    Candidate("SANDBOX", "PUBLIC", "GONG__CALLS"),
    Candidate("DWH", "STAGING", "STG_GONG__CALLS"),
)


@pytest.mark.parametrize("prompt,want", [
    # The live sentence. The trailing full stop is the case that matters: it ends the sentence and
    # is not part of the name, and a boundary rule that treated every `.` alike would miss exactly
    # the prompt this ticket was filed about.
    ("rows from DWH.MARTS.GONG__CALLS.", "GONG__CALLS"),
    ("rows from DWH.MARTS.GONG__CALLS", "GONG__CALLS"),
    ("rows from dwh.marts.gong__calls please", "GONG__CALLS"),
    ("from DWH.MARTS.GONG__CALLS, grouped by day", "GONG__CALLS"),
    ("compare SANDBOX.PUBLIC.GONG__CALLS over time", "GONG__CALLS"),
])
def test_a_fully_qualified_name_resolves_to_its_candidate(prompt: str, want: str):
    found = named_candidate(prompt, CANDIDATES)
    assert found is not None and found.table == want, prompt


@pytest.mark.parametrize("prompt,why", [
    ("show me GONG__CALLS", "bare: two databases hold one, which is what the position is for"),
    ("show me MARTS.GONG__CALLS", "half-qualified: no database"),
    ("join DWH.MARTS.GONG__CALLS and SANDBOX.PUBLIC.GONG__CALLS", "two named is a question"),
    ("rows from DWH.MARTS.NOT_A_TABLE", "names nothing the store holds"),
    ("rows from RAW.DWH.MARTS.GONG__CALLS", "a longer path that merely ends with one"),
    ("DWH.MARTS.GONG__CALLS.DURATION", "a column reference, not the table alone"),
    ("", "no sentence at all"),
])
def test_anything_less_than_one_exact_name_keeps_the_card(prompt: str, why: str):
    assert named_candidate(prompt, CANDIDATES) is None, why


def test_the_match_is_against_the_candidates_and_never_parsed_out_of_the_sentence():
    """A name that resolves to nothing must raise the card, not scope the Binding to a table the
    store does not hold. Parsing dotted names out of prose would do the second."""
    assert named_candidate("rows from MADE.UP.NAME", CANDIDATES) is None
    assert named_candidate("rows from DWH.MARTS.GONG__CALLS", ()) is None


# --------------------------------------------------------------------------------------
# Chat: the measured symptom, end to end through the real gate.
# --------------------------------------------------------------------------------------

def test_the_chat_turn_is_not_spent_asking_for_a_table_the_person_named(tmp_path: Path):
    """The whole ticket. No card, and the turn keeps going."""
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)

    events = _events(orch, tid, NAMED)

    assert _cards(events) == [], "the question was already answered"
    done = [e for e in events if e.get("type") == "done"]
    assert done and done[-1].get("decision") != "table candidates", done


def test_the_record_it_writes_is_the_one_the_click_would_have_written(tmp_path: Path):
    """Through `confirm_thread_table_candidate`, not around it — one writer, one record (ADR-0038).

    The columns matter as much as the position: the row is what the turn prompt renders from, and a
    table with no columns beside it sends the agent to ask the store what it just chose.
    """
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)

    _events(orch, tid, NAMED)

    scope = _scope(orch, tid)
    assert scope.get("database") == "DWH"
    assert scope.get("schema") == "MARTS"
    assert scope.get("table") == "GONG__CALLS"
    assert _row(orch, tid).get("columns"), "the click reads columns; so must this"


def test_a_bare_table_name_still_gets_the_card(tmp_path: Path):
    """The position is exactly what a bare name cannot supply, so this is not a near-miss to be
    widened away: it is the case the card exists for."""
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)

    assert len(_cards(_events(orch, tid, BARE))) == 1


def test_nothing_is_recorded_on_the_turn_that_draws_the_card(tmp_path: Path):
    """The negative control for the whole change: looking is not choosing (ADR-0038). A gate that
    recorded on the way past would pass every test above while quietly writing records nobody
    clicked."""
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)

    _events(orch, tid, BARE)

    assert not _scope(orch, tid).get("table"), "the card was drawn; nothing should be recorded"


def test_a_record_that_cannot_be_written_asks_rather_than_ending_the_turn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
):
    """Fail toward the card. A refused write that also swallowed the card would leave the person
    with no way on at all, which is worse than the question this removes."""
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)

    def _refuse(*_a, **_k):
        raise RuntimeError("the store would not answer")

    orch.confirm_thread_table_candidate = _refuse  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR):
        events = _events(orch, tid, NAMED)

    assert len(_cards(events)) == 1, "the person still has a way on"
    assert "could not be recorded" in caplog.text


# --------------------------------------------------------------------------------------
# Build: the same fix on the other gate, and the one place it must NOT apply.
# --------------------------------------------------------------------------------------

BUILD_NAMED = "build me a dashboard from DWH.MARTS.GONG__CALLS."


def test_the_build_gate_does_not_ask_either(tmp_path: Path, monkeypatch):
    """The second gate. Fixing only Chat would leave Build asking for a table just named, and both
    call `named_source` for the same reason."""
    from .test_a_mentioned_store_and_its_table_are_one_card import (
        MENTION,
        _build,
        _client,
        _kinds,
        _sources,
    )
    from .test_a_mentioned_store_and_its_table_are_one_card import (
        _orch as _borch,
    )

    orch = _borch(tmp_path)
    orch.bind_data_source("ds-dwh")  # already a dependency; only the table is unanswered
    client = _client(orch, monkeypatch)

    frames = _build(client, prompt=BUILD_NAMED, resources=MENTION)

    assert "table-candidates" not in _kinds(frames), frames
    recorded = _sources(client)
    assert len(recorded) == 1
    assert (recorded[0]["schema"], recorded[0]["table"]) == ("MARTS", "GONG__CALLS")


def test_the_merged_card_still_asks_because_its_click_declares_a_binding(
        tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture):
    """THE BOUNDARY, and the reason this is not just "apply it on both gates".

    Where nothing is bound yet, the card is the merged one (#206) and its click declares a Binding
    AND a Scope in one act. That act is legitimate because a PERSON chose the store and the table
    together. A sentence can answer "which table"; it cannot answer "depend on this store", and
    creating a dependency because a prompt happened to name a table is the side-effect bind that
    `scope_data_source`'s refusal exists to prevent (ADR-0021).

    So a fully-qualified name here changes nothing: the card is drawn and nothing is recorded.

    THE LOG ASSERTION IS THE POINT OF THIS TEST, not the card. `scope_data_source` would refuse the
    write anyway, so removing the guard leaves the outcome identical and the first two assertions
    green — a plant proved exactly that. What changes is HOW: without the guard every merged-card
    turn that names a table attempts a write, has it refused, and logs an exception for a case that
    is ordinary and expected. Not asking is a decision; being refused is an accident that happens
    to land well. This pins the decision.
    """
    from .test_a_mentioned_store_and_its_table_are_one_card import (
        MENTION,
        _build,
        _client,
        _kinds,
        _sources,
    )
    from .test_a_mentioned_store_and_its_table_are_one_card import (
        _orch as _borch,
    )

    client = _client(_borch(tmp_path), monkeypatch)

    with caplog.at_level(logging.ERROR):
        frames = _build(client, prompt=BUILD_NAMED, resources=MENTION)

    assert "table-candidates" in _kinds(frames), frames
    assert _sources(client) == [], "naming a table must not bind a store nobody declared"
    assert "could not be recorded" not in caplog.text, (
        "the write was attempted and refused; it should never have been attempted")
