"""#436: the Session-context row told the model the store was shut, and the model believed it.

MEASURED, not reasoned. On cloud-dogfood at `78e9223`, thread `thr_1a0b70c9e4e50f4fc4de0`, the
chip the person attached held exactly `{id, addedBy, addedAt, kind, name}` — no `resourceId`, so
`_context_source_id` resolved no source and `add_thread_context` stamped no `sourceName`. That chip
rendered as:

    - Data Source Snowflake-Data-Warehouse. This workspace cannot query it live. Do not invent
      rows. Say that you cannot open it.

and the model replied "the Data Source ... cannot be queried live from this conversation", which is
that sentence handed back. `live_read_query` was armed in the same turn (`chat tools: ... all 8`),
and the one argument it takes for a store is its NAME (`liveread/mcp.py`: "The Data Source name"),
which the row held the whole time.

WHY THE CHIP AND NOT THE PHRASING. Three threads in that workspace whose chips DID carry
`sourceName` were asked the same plain question and none of them refused — they reached the data
path (one landed on #435). The split tracks the chip.

WHAT THESE TESTS CAN AND CANNOT SAY. They assert what the model is HANDED. Whether it then calls
the tool is a live measurement nobody can make from a worktree, and this file does not pretend
otherwise — which is the distinction #428 shipped without.
"""

from __future__ import annotations

from pathlib import Path

from sage.orchestrator.service import _chat_context_line

from .test_chat_turn import _orch

TID = "thr_1a0b70c9e4e50f4fc4de0"

# The failing chip, field for field, as the live API returned it. Not a fixture built to suit the
# assertion: `id` is the raw Data Source id rather than a `ctx_` one, and there is no `resourceId`,
# which together are why no `sourceName` was ever stamped.
LIVE_CHIP = {
    "id": "69d5685b8115e1aaaaaaaaaa",
    "addedBy": "user",
    "addedAt": "2026-09-19T00:24:12Z",
    "kind": "data_source",
    "name": "Snowflake-Data-Warehouse",
}

# The same store once `add_thread_context` managed to resolve it. Both shapes are in that one
# workspace, so both are production, and the fix has to hold for each.
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
