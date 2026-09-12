"""The Workbench half of the table search in Chat (#188, ADR-0038).

The same two silent failures the Build harness beside this one exists for, over a second set of
code. `historyToMessages` is its own chain of `ev.type === ...` branches — not the one
`buildHistoryToMessages` is, so the Build tests prove nothing about it — and a turn event with no
branch reaches the Thread and vanishes. A candidate card that vanishes is a question that was
answered with nothing.

The click is TWO acts, deliberately: it writes the table onto the Thread, then asks the question the
person already asked. Drop the second and the answer never comes. Drop `skipTableGate` from it and
the turn hands back the same card it was answering, forever. Drop `echo: false` and their question
is drawn twice, once from the Thread and once optimistically under the card that already carries it.

None of those throws, and none of them crosses into Python — which is why this runs the real
`store.js` instead of reading it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "chat_table_candidate_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

PROMPT = "chart me the daily gong calls from Snowflake"

# One Chat turn, exactly as the orchestrator wrote it to the Thread: the person's sentence, the
# card, and the `done` that says the turn stopped here without answering.
HISTORY = [
    {"type": "user", "text": PROMPT},
    {"type": "table-candidates", "prompt": PROMPT,
     "message": "Sage read what **Snowflake-Data-Warehouse** holds.",
     "sourceId": "ds-dwh", "sourceName": "Snowflake-Data-Warehouse", "threadId": "thr_1",
     "groups": [{"database": "DWH", "schema": "MARTS", "tables": ["GONG__CALLS"]},
                {"database": "DWH", "schema": "STAGING", "tables": ["STG_GONG__CALLS"]}],
     "allGroups": [{"database": "DWH", "schema": "MARTS",
                    "tables": ["GONG__CALLS", "FCT_USAGE_DAILY"]},
                   {"database": "DWH", "schema": "STAGING", "tables": ["STG_GONG__CALLS"]}],
     "total": 3, "matched": 2},
    {"type": "done", "ok": False, "decision": "table candidates"},
]


def _run() -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"history": HISTORY, "prompt": PROMPT}),
        check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_card_is_drawn_from_the_thread_with_its_tables_intact():
    """The whole card survives the Chat transcript, grouping included — and it carries the Thread,
    which is what tells the click to write the record on the conversation rather than on a Built App
    this conversation may not even have."""
    cards = _run()["cards"]
    assert len(cards) == 1
    assert cards[0]["total"] == 3
    assert cards[0]["sourceId"] == "ds-dwh"
    assert cards[0]["threadId"] == "thr_1"
    assert cards[0]["prompt"] == PROMPT
    assert cards[0]["groups"] == [
        {"database": "DWH", "schema": "MARTS", "tables": ["GONG__CALLS"]},
        {"database": "DWH", "schema": "STAGING", "tables": ["STG_GONG__CALLS"]},
    ]


@needs_node
def test_a_card_read_back_off_the_thread_carries_no_buttons():
    """`live` is set only on a frame that arrived over SSE this session. A card replayed on a page
    load is the record of a decision somebody already made, and its buttons would write a table and
    run a turn out of a message nobody connected them to."""
    assert _run()["cards"][0]["live"] is False


@needs_node
def test_the_click_writes_the_record_first_and_then_asks_the_question_again():
    """Two calls, in order, and the second one carries `skipTableGate`.

    The record stands whether or not the answer after it succeeds, and the answer is an ordinary
    Chat turn taking the turn lock like any other — which is what makes this two requests rather
    than one route doing both. Without `skipTableGate` the replayed question meets the same gate and
    gets the same card back.
    """
    out = _run()

    assert out["routes"][0] == "api/threads/thr_1/context/data_source/ds-dwh/candidate"
    assert out["click"] == {"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"}
    assert "api/threads/thr_1/chat/stream" in out["routes"]
    assert out["routes"].index("api/threads/thr_1/chat/stream") > 0
    # The Dataset card's two fields ride along at their defaults, because a Chat turn carries every
    # gate flag the route reads (#196) and this click answers only the table one.
    assert out["replay"] == {"prompt": PROMPT, "skipTableGate": True,
                             "skipDatasetGate": False, "datasetDismissed": ""}


@needs_node
def test_the_answered_card_is_reloaded_away_and_the_question_is_not_asked_twice():
    """Reloading the Thread is what retires the card: the server's copy carries no `live`, so an
    answered card cannot be answered a second time — a second click months later would move the
    recorded table off a catalog the server may have dropped as stale.

    And the person's question stays a single bubble. It comes back with the reload, above the card
    that quotes it, so the replay draws none of its own.
    """
    out = _run()

    assert out["cardsAfter"] and all(card["live"] is False for card in out["cardsAfter"])
    assert out["asked"] == [PROMPT]
