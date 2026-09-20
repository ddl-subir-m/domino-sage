"""#440: a bare Data Source chip carries the columns of a table the sentence named.

A bare Data Source chip — the store attached with no table pinned — rendered no columns, so the
agent composed its first SELECT against columns it had never seen, the statement failed
`000904 invalid identifier`, and the turn spent a `live_read_table` probe and a retry recovering.
Measured live four times over two revs (#440).

The machinery to supply those columns already existed and already ran on that turn. When the
sentence names a fully-qualified table, the Chat table gate matches it (`named_candidate`) and
records it through `confirm_thread_table_candidate`, which reads the columns and writes them onto
the Thread's context row (#426). So the question was never where the columns come from. It was
WHEN: did that recording reach THIS turn's prompt, or only the next one?

MEASURED FIRST, by the first test below, before the fix was written. It reached only the next one,
and the reason is narrow: the gate records in time — it runs well above the prompt assembly — but
`read_context` returns a fresh object off disk on every call and the recording is a
read-modify-write of its own, so the `ctx` snapshot taken at the top of the turn was stale by
exactly the field the agent needed. The fix is a re-read of the Thread's own file after the gate,
and nothing else.

One test per acceptance criterion, in the ticket's order. Two habits run through all of them:

- A POSITIVE CONTROL wherever an absence is asserted. A gate that declined and a gate that never
  ran are indistinguishable from the events (#445), and a prompt naming no store makes this gate
  a no-op — so `NAMED` carries the @mention the live sentence carried, and the declines below are
  asserted by their REASON rather than by the word.
- The last test is green with the fix and green without it, which the ticket predicted. It is here
  to catch a fix that buys this turn's columns by taking them off the next turn's, and it is
  recorded as insufficient on its own rather than left to look like coverage.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.workspace.threads import ThreadStore

from .test_a_chat_build_request_is_asked_which_table import (
    _gong_warehouse,
    _orch,
    _second_source,
    _thread_with_source,
)

# The live sentence from #426, which is the same shape #440 was measured on: the store by
# @mention, the table fully qualified in prose, and no table pinned on the Thread.
NAMED = "@Snowflake-Data-Warehouse show me a few sample rows from DWH.MARTS.GONG__CALLS."


def _row(orch, tid: str) -> dict:
    items = ThreadStore(orch._chat_project().record.path).read_context(tid).get("items") or []
    rows = [i for i in items if str(i.get("kind") or "") in ("data_source", "datasource", "table")]
    return rows[0] if rows else {}


def test_the_named_tables_columns_reach_this_turns_prompt(
        tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """CRITERION 1, and the measurement the fix was chosen from (#440).

    This is the test that was written first and read red: the gate had recorded, the row on disk
    carried the columns, and the prompt still went without them. The two assertions above the
    prompt one are what made that readable — without them a gate that never ran reds here
    identically and points at the wrong half of the turn.
    """
    orch, oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)

    with caplog.at_level(logging.INFO):
        list(orch.chat_stream(tid, NAMED))

    # The positive control. Without it a decline reads as a pass.
    assert "named outright" in caplog.text, caplog.text
    assert _row(orch, tid).get("columns"), "the gate recorded the columns on the row"

    assert oc.prompts, "the turn reached the agent"
    said = oc.prompts[-1]["text"]
    assert "CALL_ID" in said, said


def test_a_turn_that_names_no_table_still_gets_the_bare_route_and_no_columns(
        tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """The bare-Data-Source branch keeps its job (#436). Nothing here invented a table.

    Two unscoped stores and a sentence naming neither, which is the one shape that declines
    without reaching a warehouse — so the turn runs on rather than ending at a card. The decline
    is asserted from the log, because a gate that never ran and a gate that ruled the store out
    are indistinguishable from the events (#445).
    """
    orch, oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)
    _second_source(orch, tid)

    with caplog.at_level(logging.INFO):
        list(orch.chat_stream(tid, "how many were there last week?"))

    # The REASON, not just the word: the gate declines four ways and only this one leaves the
    # turn running without having reached a warehouse. A decline that drifted to "holds nothing
    # to offer" would still pass a looser assertion while testing a different fixture.
    assert "declined — no unscoped Data Source named" in caplog.text, caplog.text
    assert oc.prompts, "the turn reached the agent"
    said = oc.prompts[-1]["text"]
    assert "Columns:" not in said, said
    assert "No Table is chosen on it" in said, said
    assert "live_read_query" in said, "the store is still named and still reachable"


def test_a_pinned_table_chip_is_unchanged(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """One code path renders columns, not two.

    A chip attached WITH a table already carries the columns `add_thread_context` read at attach
    time, and its Binding is scoped — so this gate declines and the scoped row at
    `_chat_context_line` renders exactly what it rendered before this ticket.
    """
    orch, oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch, scope={"database": "DWH", "schema": "MARTS",
                                           "table": "GONG__CALLS"})

    with caplog.at_level(logging.INFO):
        list(orch.chat_stream(tid, "how many calls were there last week?"))

    # A scoped Binding is not an unscoped one, so the gate has nothing to ask about — the same
    # reason and the same line as the test above, reached from the opposite fixture.
    assert "declined — no unscoped Data Source named" in caplog.text, caplog.text
    assert oc.prompts, "the turn reached the agent"
    said = oc.prompts[-1]["text"]
    assert "table DWH.MARTS.GONG__CALLS." in said, said
    assert "Columns: CALL_ID TEXT, STARTED_AT TIMESTAMP." in said, said


def test_rendering_the_prompt_reads_no_store(tmp_path: Path, monkeypatch):
    """THE GUARD `_chat_context_line`'s refusal asks for (#400, #417): no lookup while rendering.

    The count is taken across `_chat_prompt` itself rather than over the turn, because the turn
    DOES read columns — the gate's recording is where that read belongs and it is the whole reason
    no second one is needed. `reads` being non-empty is what proves the counter is wired to the
    call it claims to count; without it an unwired spy reports zero for the wrong reason.

    BOTH doors, not just the columns one. `_chat_context_line`'s refusal names re-resolving the
    store's own name as the lookup it will not make (`"Do not 'fix' this into a lookup"`), and
    that route is `_data_source` rather than `list_columns` — so a guard counting only columns
    would pass a fix that put a Domino round trip in the prompt to recover a `sourceName`, which
    is the exact thing the comment forbids.
    """
    orch, oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)

    reads: list[tuple[str, ...]] = []
    inner_columns = orch._resources.list_columns
    inner_source = orch._data_source

    def counted(source, database, schema, table=""):
        reads.append(("list_columns", database, schema, table))
        return inner_columns(source, database, schema, table)

    def counted_source(source_id):
        reads.append(("_data_source", str(source_id)))
        return inner_source(source_id)

    # Through `monkeypatch` rather than by assignment. Both objects are per-`Orchestrator` and
    # `_orch` builds a fresh one per test, so nothing leaks today — but an unrestored spy is one
    # refactor away from a cross-test leak, and `-n auto` hands out individual TESTS, which makes
    # that the hardest kind of red in this repo to attribute back to the test that caused it.
    monkeypatch.setattr(orch._resources, "list_columns", counted)
    monkeypatch.setattr(orch, "_data_source", counted_source)

    during: list[int] = []
    render = Orchestrator._chat_prompt

    def wrapped(self, *a, **k):
        before = len(reads)
        try:
            return render(self, *a, **k)
        finally:
            during.append(len(reads) - before)

    monkeypatch.setattr(Orchestrator, "_chat_prompt", wrapped)

    list(orch.chat_stream(tid, NAMED))

    assert reads, "the spy is wired: the turn read the store somewhere"
    assert during, "the prompt was rendered"
    assert during == [0], (during, reads)
    assert oc.prompts, "the turn reached the agent"
    assert "CALL_ID" in oc.prompts[-1]["text"], "and it rendered them anyway"


def test_a_later_turn_in_the_same_thread_still_renders_the_columns(tmp_path: Path):
    """Green whichever fix is chosen, and so never the only test (#440).

    By the second turn the record is on disk before the turn starts, so this asks nothing about
    ordering. It is here to catch a fix that moves the columns onto this turn by taking them off
    the next one.
    """
    orch, oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)

    list(orch.chat_stream(tid, NAMED))
    list(orch.chat_stream(tid, "and how many of them are there?"))

    assert len(oc.prompts) == 2, oc.prompts
    assert "CALL_ID" in oc.prompts[-1]["text"], oc.prompts[-1]["text"]
