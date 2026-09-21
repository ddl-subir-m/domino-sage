"""#436: the Data Source row named a route the turn does not have, and denied the one it does.

TWO DEFECTS, AND THEY ARE NOT EQUALLY EVIDENCED. Say which is which before reading the asserts.

1. THE PYTHON RECIPE — production, observed. For a chip that resolves normally, the row said
   "list its tables before you answer" through `DataSourceClient`. That is an instruction to run
   Python, in the most specific sentence in the prompt about that store, handed to lanes that have
   no shell: `READ_ONLY_DENIED` strips shell from a `data_answer` turn and the `data_artifact`
   allowlist (`shim/enforcement.py:334`) keeps every `live_read_*` tool while dropping it. All
   three well-formed chips in the workspace that filed this ticket carried that row.

2. THE "CANNOT QUERY IT LIVE" SENTENCE — production, reasoned from the code, NOT from the
   refusal in the ticket body. The turn quoted there ran on a chip a sibling session posted by
   hand during the #408 verification, with the raw Domino id in `id` and no `resourceId`. The
   Workbench door never posts that shape — `api.js:531` sends `resourceId` and no `id` at all — so
   that refusal is a harness artifact and cannot carry a claim about production.

   What DOES reach this branch in production is a transient one. `add_thread_context` stamps
   `sourceName` only when `_context_source` resolves, and that helper swallows
   `ResourceUnavailable` — raised when Domino did not answer, or answered and refused. A chip the
   Workbench posted correctly, attached in the seconds Domino was unreachable, is written without
   a `sourceName` and nothing ever retries it. Every later turn in that Thread is then told the
   store cannot be queried, about a store that is reachable again and whose name is in the chip.
   `test_a_well_formed_chip_attached_while_domino_is_down_is_not_shut_for_good` is that path,
   built through the real producer rather than by hand.

WHAT THESE TESTS CAN AND CANNOT SAY. They assert what the model is HANDED. Whether it then calls
the tool is a live measurement nobody can make from a worktree, and this file does not pretend
otherwise — which is the distinction #428 shipped without.
"""

from __future__ import annotations

from pathlib import Path

from sage.orchestrator.service import _chat_context_line

from .fake_opencode import Turn
from .test_chat_turn import _orch

TID = "thr_1a0b70c9e4e50f4fc4de0"

# A chip with no `sourceName`, in the shape the live API returned for the thread in the ticket
# body. That particular one was hand-posted by a sibling session, so it is used here as a SHAPE and
# not as evidence — the production route to this shape is the outage test at the bottom of the file.
LIVE_CHIP = {
    "id": "69d5685b8115e1aaaaaaaaaa",
    "addedBy": "user",
    "addedAt": "2026-09-19T00:24:12Z",
    "kind": "data_source",
    "name": "Snowflake-Data-Warehouse",
}

# The same store once `add_thread_context` resolved it — what the Workbench door actually produces,
# and the chip all three of the user's own attachments carried. Defect 1 lives on this one.
RESOLVED_CHIP = {**LIVE_CHIP, "id": "ctx_1a0b6b25ff",
                 "resourceId": "data_source:69d5685b8115e1aaaaaaaaaa",
                 "sourceName": "Snowflake-Data-Warehouse"}

SCOPED_CHIP = {**RESOLVED_CHIP, "name": "MIXPANEL__EVENT",
               "scope": {"database": "DWH", "schema": "MARTS", "table": "MIXPANEL__EVENT"}}

SHUT = "cannot query it live"


def test_the_chip_that_shipped_the_bug_is_no_longer_told_the_store_is_shut():
    """The whole ticket, at the byte the model reads."""
    line = _chat_context_line(LIVE_CHIP, thread_id=TID)

    assert SHUT not in line, "the row still tells the model the store cannot be reached"
    assert "Say that you cannot open it" not in line
    # Named, with the argument the tool actually takes. Asserting the bare tool name would pass on
    # a row that mentioned it without saying what to pass — and a store's name is the one thing
    # `live_read_query` needs that only this row knows.
    assert "live_read_query" in line
    assert "'Snowflake-Data-Warehouse'" in line


def test_the_resolved_chip_is_not_sent_at_python_it_does_not_have():
    """The other half of the same defect, and the one a `sourceName` does not save you from.

    A `data_answer` turn is read-only and a `data_artifact` turn runs an allowlist; both drop the
    shell and both keep every `live_read_*` tool. So the old row's `DataSourceClient` recipe — the
    most specific instruction in the prompt about this store — named the one route the turn cannot
    take, on every bounded lane.
    """
    line = _chat_context_line(RESOLVED_CHIP, thread_id=TID)

    assert "live_read_query" in line
    assert line.index("live_read_query") < line.index("DataSourceClient"), \
        "Python is offered before the tool the bounded lanes actually hold"
    assert "list its tables before you answer" not in line, "that instruction needs a shell"


def test_python_survives_as_the_stated_fallback():
    """Deleting it would be #370 again: a row that names a store and leaves the route unsaid.

    An unbounded Chat turn keeps the shell and can hold no live-read tool at all — the MCP
    handshake has been seen landing after the turn's tool list is fixed.
    """
    for chip in (LIVE_CHIP, RESOLVED_CHIP, SCOPED_CHIP):
        line = _chat_context_line(chip, thread_id=TID)
        assert "DataSourceClient" in line, chip
        assert "not in your tool list this turn" in line, chip


def test_a_scoped_table_passes_the_store_as_source_and_the_table_in_the_sql():
    """`live_read_query` takes the STORE as `source`. The table belongs in the statement.

    On a scoped chip `name` is the table, so a row that reached for `name` here would tell the
    agent to pass `MIXPANEL__EVENT` as the Data Source — the lookup that comes back "no Data Source
    registered under that name" and reads like the person attached the wrong thing.
    """
    line = _chat_context_line(SCOPED_CHIP, thread_id=TID)

    assert "source 'Snowflake-Data-Warehouse'" in line
    assert "'MIXPANEL__EVENT'" not in line, "the table is being offered as the Data Source"
    assert "DWH.MARTS.MIXPANEL__EVENT" in line


def test_a_scoped_table_is_the_only_table_the_row_permits():
    """The row hands out a shell recipe for the store, and the store holds other tables. #488: a
    fusion question on a Thread scoped to `MIXPANEL__EVENT` read the recipe as a door to the whole
    warehouse and went looking for Gong and SFDC through it. The row now says, in the same breath
    as the recipe, that the one table is the one table — and what to do when that is not enough."""
    line = _chat_context_line(SCOPED_CHIP, thread_id=TID)

    assert "DWH.MARTS.MIXPANEL__EVENT is the one table in this conversation" in line
    assert "do not query another table in Snowflake-Data-Warehouse" in line
    assert "say which and stop" in line
    # After the recipe, not before it: the clause qualifies the recipe, so it reads as part of it.
    assert line.index("DataSourceClient") < line.index("is the one table in this conversation")


def test_a_scoped_table_with_no_source_name_still_says_it_cannot_be_reached():
    """The one shape where nothing here knows what to pass, and the sentence is honest.

    Guards the narrowing from going too far. `name` is the table and no source resolved, so there
    is no store name in the row — inventing one from the table is the failure the old comment was
    written against, and it must not come back as the price of this fix.
    """
    chip = {**SCOPED_CHIP}
    chip.pop("sourceName")
    chip.pop("resourceId")

    line = _chat_context_line(chip, thread_id=TID)

    assert SHUT in line
    assert "live_read_query" not in line


def test_the_composed_prompt_carries_it_and_still_teaches_the_tool_by_name(tmp_path: Path):
    """Composed, not the helper alone — and both halves of the owner's criterion in one prompt.

    Every existing test that renders this prompt passes `{"items": []}`, so the Data Source row has
    never been in one. That is how a green suite sat on top of this.
    """
    orch, _ = _orch(tmp_path)

    prompt = orch._chat_prompt(TID, "How many distinct users are in DWH.MARTS.MIXPANEL__EVENT?",
                               {"items": [LIVE_CHIP]},
                               workspace=orch._chat_project().record.path)

    # Criterion 2: not naming the tool. The row for the store the person asked about names it.
    assert SHUT not in prompt
    assert "source 'Snowflake-Data-Warehouse'" in prompt
    # Criterion 1: naming the tool must go on working. These two are what carry that, and neither
    # is touched by this change — asserted here so a later edit to the row cannot quietly cost it.
    assert "Pass it as `token` on every" in prompt
    assert "one SELECT statement as sql" in prompt
    # #411's lane, which this must not swallow: a question SQL cannot express still has somewhere
    # to go that is not the tool.
    assert "If the question cannot be put in one SELECT, say so" in prompt


def test_a_chip_with_no_name_is_not_handed_a_store_name_to_pass():
    """`name` carries display fallbacks — the row's own id, then the literal "unnamed" — and
    neither is a store `live_read_query` can look up.

    The fix turned a row that named no route into a row that names one, and this is the edge where
    that could go wrong in the other direction: a chip with nothing to pass would be told to pass
    the word "unnamed".
    """
    line = _chat_context_line({"kind": "data_source", "id": "ds-77"}, thread_id=TID)

    assert SHUT in line
    assert "live_read_query" not in line
    assert "source '" not in line, "the row is telling the agent to pass a display fallback"


def test_a_well_formed_chip_attached_while_domino_is_down_is_not_shut_for_good(tmp_path: Path,
                                                                              monkeypatch):
    """The production path to a chip with no `sourceName`, and the reason the narrowing stays.

    It does NOT need a malformed chip. `add_thread_context` stamps `sourceName` only when
    `_context_source` resolves, and that helper swallows `ResourceUnavailable` — which is raised
    when Domino did not answer at all, or answered and refused (`resources/provider.py:83`). So a
    chip the Workbench posted correctly, attached in the seconds Domino was unreachable, is written
    to `context.json` without a `sourceName` and stays that way.

    Nothing retries it. Every later turn in that Thread then rendered "This workspace cannot query
    it live. Say that you cannot open it." about a store that was reachable again by then, and whose
    name was sitting in the chip the whole time.
    """
    from sage.resources.provider import ResourceUnavailable

    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]

    def _down(*_a, **_k):
        raise ResourceUnavailable("Domino did not answer.")

    monkeypatch.setattr(type(orch), "_data_source", _down)

    # Byte for byte the payload the composer posts for a bare Data Source chip — `resourceId` and
    # all. The only thing wrong with this turn is the weather.
    row = orch.add_thread_context(tid, {
        "kind": "data_source",
        "name": "Snowflake-Data-Warehouse",
        "resourceId": "data_source:ds-dwh",
    })
    assert "sourceName" not in row, "the premise of this test is gone; re-derive it"

    monkeypatch.undo()          # Domino is back. The chip on disk is not.
    list(orch.chat_stream(tid, "How many distinct users are in DWH.MARTS.MIXPANEL__EVENT?"))
    prompt = oc.prompts[0]["text"]

    assert SHUT not in prompt, "a transient outage at attach time shut the store permanently"
    assert "source 'Snowflake-Data-Warehouse'" in prompt
