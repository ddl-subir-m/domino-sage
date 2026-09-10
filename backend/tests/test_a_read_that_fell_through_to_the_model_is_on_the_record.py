"""ADR-0041 — a read that ended with the model querying the table itself says so.

The ADR permits the answer: "a read-only turn may read the world", and Chat queries Data Sources
every day. But it is a different door from the one the person bound the table for. Rows go into the
model's context down that one, and the gateway routes to Vendor-backed Aliases as well as
Domino-hosted ones — so on some turns they leave Domino.

Answering is still right. A turn that silently CHANGED which door it used, leaving no way to know
afterwards, was not. So `/api/diag` names those turns: the tool, the Conversation and what broke.
Never a row, never the token.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .test_a_live_read_reaches_the_person_end_to_end import Warehouse, _call, _orch, _token


class Broken(Warehouse):
    def sample_rows(self, source, database, schema, table, limit=5):
        raise ValueError("SQL compilation error: syntax error line 1 at position 14")


def ready(tmp_path: Path, resources):
    orch, oc = _orch(tmp_path, resources)
    tid = orch.create_thread()["id"]
    orch.add_thread_context(
        tid, {"kind": "data_source", "id": "ds1", "name": "Snowflake-Data-Warehouse"})
    list(orch.chat_stream(tid, "show me 1 sample conversation"))
    return orch, tid, _token(oc)


def test_a_read_that_broke_names_the_turn_that_answered_another_way(tmp_path: Path):
    orch, tid, token = ready(tmp_path, Broken())
    _call(orch, "live_read_table", {
        "token": token, "source": "Snowflake-Data-Warehouse", "table": "GONG__CALLS"})

    reach = orch.live_read_reach()
    assert reach["fell_through_since_boot"] == 1
    row = reach["fell_through"][0]
    assert row["tool"] == "live_read_table"
    assert row["thread"] == tid
    assert "SQL compilation error" in row["why"]
    assert isinstance(row["at"], float), "seconds after boot, like everything else on this block"
    assert token not in json.dumps(reach), "the token is a turn's authority and is never written down"


def test_a_read_that_worked_is_not_on_the_list(tmp_path: Path):
    orch, _, token = ready(tmp_path, Warehouse())
    _call(orch, "live_read_table", {
        "token": token, "source": "Snowflake-Data-Warehouse", "table": "GONG__CALLS", "limit": 1})
    assert orch.live_read_reach()["fell_through"] == []


def test_a_refusal_is_not_a_fall_through(tmp_path: Path):
    # A refusal hands the assistant a sentence to relay, not a reason to go and query the table.
    # Counting it here would report a door as opened that was in fact held shut.
    orch, _, token = ready(tmp_path, Warehouse())
    _call(orch, "live_read_table", {"token": token, "source": "Payroll-Warehouse", "table": "PAY"})
    assert orch.live_read_reach()["fell_through"] == []
    assert orch.live_read_reach()["fell_through_since_boot"] == 0


def test_the_warning_says_what_the_failure_cost_and_not_only_what_broke(tmp_path: Path, caplog):
    orch, _, token = ready(tmp_path, Broken())
    with caplog.at_level(logging.WARNING, logger="sage.liveread"):
        _call(orch, "live_read_table", {
            "token": token, "source": "Snowflake-Data-Warehouse", "table": "GONG__CALLS"})

    said = "\n".join(r.getMessage() for r in caplog.records if r.name == "sage.liveread")
    assert "SQL compilation error" in said, "what broke"
    assert "the rows reach the model rather than the card (ADR-0041)" in said, "what it cost"
