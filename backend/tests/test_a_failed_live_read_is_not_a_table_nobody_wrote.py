"""ADR-0041 — a read that broke must not end the turn as a table that was never written.

Live: `live_read_table` failed, the failure came back as the driver's own sentence about SQL, the
model did the sensible thing and queried the same table in Python — and then replied "here are the
first 5 rows" over a Thread with nothing in it. Every step was reasonable except the last, and
nothing anywhere had told it the screen was still empty.

So each failure says so, on the three surfaces a failure can arrive by: the read itself, the tool
file that carries it, and the pack that tells the agent what a finished turn looks like.
"""

from __future__ import annotations

from pathlib import Path

from sage.liveread import mcp

ROOT = Path(__file__).resolve().parents[2]


def test_the_failure_text_says_the_screen_is_still_empty_and_what_to_do():
    def explode(name, args):
        raise ValueError("SQL compilation error: syntax error line 1 at position 14")

    reply = mcp.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "live_read_table", "arguments": {}}},
        run=explode,
    )
    said = reply["result"]["content"][0]["text"]

    assert reply["result"]["isError"] is True
    # The driver's own words survive — they are how anyone works out what broke.
    assert "SQL compilation error" in said
    assert "Nothing was put on the person's screen" in said
    assert "write the table file yourself" in said
    assert "do not say anything is showing that you did not write" in said


def test_a_refusal_is_left_alone():
    # A refusal is a sentence the person is owed, not a failure to work around. Telling the model to
    # go get the data in Python would answer past the very thing being refused.
    reply = mcp.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "live_read_table", "arguments": {}}},
        run=lambda name, args: "Payroll is not in this conversation. Use it in the conversation first.",
    )
    assert "isError" not in reply["result"]
    assert "Python" not in reply["result"]["content"][0]["text"]


def test_the_tool_file_says_the_same_thing_on_every_way_it_can_fail():
    # The custom tool is what the model actually holds, and it has four failure returns of its own
    # that never reach the Python above: no route, a bad status, a body it cannot read, a body with
    # nothing in it. All four end the same way or the model is back to narrating an empty Thread.
    ts = (ROOT / "backend" / "sage" / "liveread" / "tools" / "live_read.ts").read_text()
    assert ts.count("FELL_THROUGH") == 5, "one definition and four uses"
    assert "Nothing was put on the person's screen" in ts
    assert "write the table file" in ts


def test_the_pack_never_lets_a_turn_claim_a_card_it_did_not_write():
    prompt = (ROOT / "template" / "chat" / "AGENTS.md").read_text()
    assert "Never say a table or a chart is on screen unless you wrote its file this turn" in prompt
    # And the two ways a turn arrives there with no file: the read failed, or it answered in Python.
    assert "A live read that\nfailed put nothing there" in prompt
    assert "a query you ran in Python" in prompt
