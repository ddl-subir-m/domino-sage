"""The Workbench half of the table search (#183, ADR-0038).

Two things break here without throwing anything, which is why this runs the real `store.js` rather
than reading it.

`buildHistoryToMessages` is a chain of `ev.type === ...` branches, and a turn event with no branch
reaches the transcript and vanishes — a defect this repo has already shipped once. A candidate card
that vanishes is a turn that asked nothing and answered nothing.

And the click is TWO acts, deliberately: it writes the record, and then it sends the request the
person already made against it. Drop the second and the dashboard they asked for is never built.
Drop `skipTableGate` from the second and the build hands back the same card it was answering,
forever. Neither shows up in Python, because neither crosses it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "table_candidate_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

PROMPT = "build me a dashboard of daily gong calls from Snowflake"

# One turn, exactly as the orchestrator wrote it to `.sage/history.jsonl`: the person's sentence, the
# card, and the `done` that says the turn stopped here without building.
HISTORY = [
    {"type": "user", "text": PROMPT},
    {"type": "table-candidates", "prompt": PROMPT,
     "message": "Sage read what **Snowflake-Data-Warehouse** holds.",
     "sourceId": "ds-dwh", "sourceName": "Snowflake-Data-Warehouse",
     "groups": [{"database": "DWH", "schema": "MARTS", "tables": ["GONG__CALLS"]},
                {"database": "DWH", "schema": "STAGING", "tables": ["STG_GONG__CALLS"]}],
     "allGroups": [{"database": "DWH", "schema": "MARTS",
                    "tables": ["GONG__CALLS", "FCT_USAGE_DAILY"]},
                   {"database": "DWH", "schema": "STAGING", "tables": ["STG_GONG__CALLS"]}],
     "total": 3, "matched": 2},
    {"type": "done", "ok": False, "decision": "table candidates"},
]


def _run(answered: dict | None = None) -> dict:
    out = subprocess.run(["node", str(_HARNESS)],
                         input=json.dumps({"history": HISTORY, "prompt": PROMPT,
                                           "answered": answered or {}}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_card_is_drawn_from_the_turn_row_with_its_tables_intact():
    """The whole card survives the transcript, grouping included. The schema is the difference the
    person is being asked to see — `MARTS.GONG__CALLS` against `STAGING.STG_GONG__CALLS` — so a
    render that kept the names and lost the groups would ask them to pick blind."""
    card = _run()["cards"]

    assert len(card) == 1
    assert card[0]["total"] == 3
    assert card[0]["sourceId"] == "ds-dwh"
    assert card[0]["prompt"] == PROMPT
    assert card[0]["groups"] == [
        {"database": "DWH", "schema": "MARTS", "tables": ["GONG__CALLS"]},
        {"database": "DWH", "schema": "STAGING", "tables": ["STG_GONG__CALLS"]},
    ]


@needs_node
def test_a_card_read_back_off_the_server_carries_no_buttons():
    """`live` is set only on a frame that arrived over SSE this session. A card replayed on a page
    load is a record of a decision somebody already made, and its buttons would write a Binding
    record and start a build out of a message they are only scrolling back through.

    The buttons GO rather than grey out, which is what the three offers beside this one do and what
    matters most here: a warehouse's worth of dead buttons under a sentence still asking somebody to
    pick one reads as an app that has broken, not as a question already answered.
    """
    assert _run()["cards"][0]["live"] is False
    ui = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js" / "components"
          / "message-blocks.js").read_text()
    card = ui[ui.index("function TableCandidates("):ui.index("// A change that happened")]
    assert "block.live && block.prompt" in card
    assert "!block.live" not in card, "a replayed card renders its buttons disabled, not gone"


@needs_node
def test_the_click_writes_the_record_first_and_then_replays_the_request():
    """Two calls, in that order. The record stands whether or not the build after it succeeds, and
    the build is an ordinary turn taking the turn lock like any other — which is what makes this two
    requests rather than one route doing both.

    `skipTableGate` on the replay is the card being answered rather than skipped. Without it the
    same request meets the same gate and gets the same card back.
    """
    out = _run()

    # The record first, then the reload that retires the card, then the build. What follows them is
    # the ordinary end of any build turn — the preview probe and the Bindings re-read — and pinning
    # those here would make this test fail the next time a turn learns to refresh something else.
    assert out["routes"][0] == "api/bindings/data_source/ds-dwh/candidate"
    assert "api/project/build/stream" in out["routes"]
    assert out["routes"].index("api/project/build/stream") > out["routes"].index("api/bindings")
    assert out["click"] == {"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"}
    assert out["replay"]["prompt"] == PROMPT
    assert out["replay"]["skipTableGate"] is True


@needs_node
def test_answering_the_card_retires_it_rather_than_leaving_it_clickable():
    """Reloading the transcript is what retires an offer here, as it is for the three beside this
    one: the server's copy carries no `live`, so the buttons go with the reload.

    Without it the card stays answerable after its build finishes, and a second click months into a
    conversation would overwrite the recorded table and start another build — off a catalog the
    server may already have dropped as stale.
    """
    out = _run()

    assert "api/project/history" in " ".join(out["routes"])
    assert out["routes"].index("api/project/build/stream") > next(
        i for i, r in enumerate(out["routes"]) if r.startswith("api/project/history"))
    assert all(card["live"] is False for card in out["cardsAfter"])


@needs_node
def test_the_replay_carries_the_gates_this_turn_had_already_answered():
    """"Start over and build a gong dashboard from Snowflake" answers the reset offer, then reaches
    this card. The reset gate is a prompt match with nothing remembered, so a replay that dropped
    `skipResetGate` would offer to throw the app away a second time — for a request the person
    already answered, with the destructive button back under it."""
    out = _run({"skipResetGate": True, "skipIncomingGate": False})

    assert out["replay"]["skipResetGate"] is True
    assert out["replay"]["skipTableGate"] is True
