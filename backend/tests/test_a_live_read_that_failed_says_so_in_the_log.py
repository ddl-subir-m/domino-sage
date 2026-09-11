"""ADR-0041 — a Live read that produced no card has to leave evidence.

A turn where the read broke and a turn where the assistant never called one looked identical from
outside: an answer with no table under it. `mcp.handle` turned the exception into plain text with
nothing written down, the assistant read that text as an answer, and went off to query the data in
Python instead — which is the thing ADR-0041 exists to stop, done silently.

So every outcome says which one it was. Never a row: the shape, the column count and the path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sage.liveread import mcp, run


@dataclass
class FakeRows:
    columns: list
    rows: list


def turn_for(tmp_path, **kw):
    seen = object()
    base = {
        "thread_id": "thr_a",
        "examples_dir": tmp_path / "examples" / "thr_a",
        "bound": {"datasource": ("DWH",)},
        "source_for": lambda n: seen if n == "DWH" else None,
        "sample_rows": lambda s, db, sc, t, lim: FakeRows(
            ["ID", "TITLE"], [[i, f"call {i}"] for i in range(lim)]),
        "binding_for": {("datasource", "DWH"): "bnd_1"},
    }
    base.update(kw)
    return run.Turn(**base)


def lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "sage.liveread"]


def test_a_read_that_wrote_a_card_says_so_with_its_shape_and_never_its_rows(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="sage.liveread")
    run.perform("live_read_table", {"source": "DWH", "table": "GONG__CALLS", "limit": 1},
                turn_for(tmp_path))

    said = lines(caplog)
    assert len(said) == 1
    assert "GONG__CALLS" in said[0] and "1 row" in said[0] and "2 columns" in said[0]
    assert "gong-calls.table.json" in said[0]
    assert "call 0" not in said[0]


def test_the_log_holds_no_value_even_where_the_creator_shared_the_table(tmp_path, caplog):
    # The assistant is handed the rows on this branch. The log is a diagnostic, not a transcript.
    caplog.set_level(logging.INFO, logger="sage.liveread")
    run.perform("live_read_table", {"source": "DWH", "table": "GONG__CALLS", "limit": 1},
                turn_for(tmp_path, shared=(("bnd_1", "GONG__CALLS"),)))
    assert "call 0" not in "\n".join(lines(caplog))


def test_a_refusal_is_a_read_with_no_card_and_reads_as_one(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="sage.liveread")
    run.perform("live_read_table", {"source": "Other-Warehouse", "table": "T"}, turn_for(tmp_path))

    said = lines(caplog)
    assert len(said) == 1
    assert said[0].startswith("live read: no card")
    assert "Other-Warehouse" in said[0]


def test_a_source_the_platform_cannot_open_says_no_card_too(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="sage.liveread")
    run.perform("live_read_table", {"source": "DWH", "table": "T"},
                turn_for(tmp_path, source_for=lambda n: None))
    assert lines(caplog) and lines(caplog)[0].startswith("live read: no card")


def test_a_read_that_raised_is_a_warning_and_still_answers_the_assistant(caplog):
    # The reply is unchanged: an exception here has always reached the assistant as text, because a
    # protocol error would leave the person with no sentence at all. What is new is the line.
    caplog.set_level(logging.INFO, logger="sage.liveread")

    def explode(name, args):
        raise ValueError("SQL compilation error: syntax error line 1 at position 14")

    reply = mcp.handle(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
         "params": {"name": "live_read_table", "arguments": {"source": "DWH", "table": "T"}}},
        run=explode,
    )

    assert reply["result"]["isError"] is True
    assert "SQL compilation error" in reply["result"]["content"][0]["text"]

    warned = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warned) == 1, "and a WARNING, so it outlives the turn in the diag warn ring"
    assert "live_read_table" in warned[0].getMessage()
    assert "ValueError" in warned[0].getMessage()
    assert "SQL compilation error" in warned[0].getMessage()


def test_a_read_that_worked_warns_about_nothing(caplog):
    caplog.set_level(logging.INFO, logger="sage.liveread")
    mcp.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "live_read_files", "arguments": {"dataset": "d"}}},
        run=lambda name, args: "Read the files in d: 2 rows.",
    )
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
