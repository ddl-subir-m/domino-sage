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


# The same turn as it arrives frame by frame (#186): the search says it is reading, says what it
# has after each database lands, and then settles. Only the last of these is ever written to
# `.sage/history.jsonl` — these three reach the transcript over SSE, through the same derivation.
STREAMED = [
    HISTORY[0],
    {"type": "table-search", "sourceId": "ds-dwh", "sourceName": "Snowflake-Data-Warehouse",
     "message": "Reading Snowflake-Data-Warehouse…", "groups": [], "total": 0},
    {"type": "table-search", "sourceId": "ds-dwh", "sourceName": "Snowflake-Data-Warehouse",
     "message": "Reading Snowflake-Data-Warehouse…",
     "groups": [{"database": "DWH", "schema": "MARTS", "tables": ["GONG__CALLS"]}], "total": 2},
]


def _run(answered: dict | None = None, history: list[dict] | None = None) -> dict:
    out = subprocess.run(["node", str(_HARNESS)],
                         input=json.dumps({"history": history or HISTORY, "prompt": PROMPT,
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
def test_the_streaming_frames_fill_one_card_in_rather_than_drawing_a_card_each():
    """The transcript is derived from the whole event list every time a frame lands, so a search
    reporting four databases pushes four frames through the same branch chain (#186). Pushed rather
    than replaced, they would draw four cards under one sentence asking the person to pick a table
    out of one warehouse — and the last of them would be the only one they could answer.

    What survives is the settled card: it carries the prompt, which is what makes it answerable.
    """
    cards = _run(history=STREAMED + HISTORY[1:])["cards"]

    assert len(cards) == 1
    assert cards[0]["searching"] is False
    assert cards[0]["prompt"] == PROMPT
    assert cards[0]["total"] == 3


@needs_node
def test_a_turn_that_ended_leaves_no_card_still_saying_it_is_reading():
    """The backstop for the ends the search does not reach itself.

    It takes its own card back when it gives up, but a stream cut mid-walk — a dropped connection,
    a worker restart, an exception past the first frame — reaches none of that code. What is left
    is a spinner and "…is reading what X holds…" sitting over a finished turn, and it is the one
    state on this card nobody can answer their way out of: the searching card has no buttons, so
    there is nothing to click and nothing to wait for. A turn being over says enough.
    """
    assert _run(history=STREAMED + [
        {"type": "done", "ok": True, "decision": "built"},
    ])["cards"] == []


@needs_node
def test_a_card_taken_back_leaves_no_blank_turn_where_it_stood():
    """A Stop during the walk is the case with nothing after it (#186).

    The searching card is the assistant message's only block, because the gate runs before any
    build output, and a Stop carries no sentence to put in its place — the person who pressed it
    knows why the card went. So retiring it empties the message, and a transcript that kept the
    empty one renders an assistant turn that said nothing: a blank row under the request, which
    reads as an answer that failed to load rather than a search somebody called off.
    """
    out = _run(history=STREAMED + [{"type": "table-search-ended", "sourceId": "ds-dwh"}])

    assert out["cards"] == []
    assert out["emptyMessages"] == 0


@needs_node
def test_a_search_that_gave_up_takes_its_card_off_the_screen():
    """The store stopped answering, or held nothing to offer, and the turn went on to the ordinary
    build. A card left standing would say "reading" for the rest of the session over a turn that
    had already moved on — and it is the one card nobody can answer, so it would never go."""
    cards = _run(history=STREAMED + [
        {"type": "table-search-ended", "sourceId": "ds-dwh"},
        {"type": "agent", "kind": "text", "text": "Which table should this read?"},
        {"type": "done", "ok": True, "decision": "built"},
    ])["cards"]

    assert cards == []


@needs_node
def test_a_streaming_frame_offers_no_way_to_pick_a_table_from_a_half_read_catalog():
    """Readable, not answerable. A click sends the request again, and a request sent while the walk
    is still running queues behind the turn doing the walking — which then ends by drawing its
    settled card onto a transcript that had already answered one.

    The prompt is the seam. The card's buttons are drawn from it, so a searching frame that carried
    one would be clickable however the component were written.
    """
    cards = _run(history=STREAMED)["cards"]

    assert len(cards) == 1
    assert cards[0]["searching"] is True
    assert cards[0]["prompt"] == ""
    assert cards[0]["total"] == 2
    # DRAWN, not merely held. The card shows the names it has found — that is the whole of the fix,
    # and a card holding them and rendering none of them passes every assertion above it — and not
    # one of them is a button.
    assert cards[0]["drawn"]["names"] == ["GONG__CALLS"]
    assert cards[0]["drawn"]["pickable"] == []


@needs_node
def test_a_settled_card_opens_collapsed_even_though_the_searching_one_had_nothing_to_collapse():
    """The cap on how many buttons mount at once has to survive the card filling in (#186).

    A real warehouse holds 602 tables and a prompt that matched none of their names opens on the
    whole list, because five arbitrary names laid out like answers read as answers. "The whole
    list" is only a list up to a point, past which it is a paint on every re-render of the
    transcript — so past it the card says how many there are and one click opens them.

    The trap is that the searching card mounts first, carrying nothing, and the component is not
    remounted when the settled one replaces it in place. Anything deciding this at mount time reads
    "nothing matched, and there are zero tables" and then holds that answer over 602.

    WHAT THIS TEST CANNOT SEE is that trap. The harness mocks `useState` as a function that runs
    its initializer on every call, so a component that seeded the answer at mount would pass here
    and fail in a browser. What it pins is the behaviour: eighty tables, none of them matched, five
    names drawn and the rest one click behind. Reproducing mount identity would take a real React,
    and the derivation this rests on — `expanded || (nothing matched and the list is short)` — is
    written so there is no mount to depend on.
    """
    big = [f"T_{n:03d}" for n in range(80)]
    cards = _run(history=STREAMED + [
        {"type": "table-candidates", "prompt": PROMPT, "message": "No Table name matches.",
         "sourceId": "ds-dwh", "sourceName": "Snowflake-Data-Warehouse", "answered": {},
         "groups": [{"database": "DWH", "schema": "MARTS", "tables": big[:5]}],
         "allGroups": [{"database": "DWH", "schema": "MARTS", "tables": big}],
         "total": 80, "matched": 0, "live": True},
        {"type": "done", "ok": False, "decision": "table candidates"},
    ])["cards"]

    assert len(cards) == 1
    assert cards[0]["drawn"]["names"] == big[:5]
    assert cards[0]["drawn"]["more"] is True


@needs_node
def test_the_replay_carries_the_gates_this_turn_had_already_answered():
    """"Start over and build a gong dashboard from Snowflake" answers the reset offer, then reaches
    this card. The reset gate is a prompt match with nothing remembered, so a replay that dropped
    `skipResetGate` would offer to throw the app away a second time — for a request the person
    already answered, with the destructive button back under it."""
    out = _run({"skipResetGate": True, "skipIncomingGate": False})

    assert out["replay"]["skipResetGate"] is True
    assert out["replay"]["skipTableGate"] is True


def test_the_merged_cards_click_carries_the_flag_that_binds_the_store_too():
    """The merged card's claim is followed the whole way from the row to the request (#206).

    An `@mention`ed store draws one card instead of two, and its click has to record the Binding as
    well as the Scope — the server's ordinary door refuses a Scope with no Binding under it, on
    purpose. Three hops carry that: the history row says so, the store copies it onto the block, and
    the click sends it. Only the middle hop is invisible from either end, which is why this drives
    the real store rather than calling the API directly.
    """
    merged = [dict(row, bindFirst=True) if row["type"] == "table-candidates" else row
              for row in HISTORY]

    out = _run(history=merged)

    assert out["click"]["bindFirst"] is True
    assert out["click"]["table"] == "GONG__CALLS"


def test_an_ordinary_cards_click_asks_for_no_binding_at_all():
    """Absent, not `false`. A click that is not claiming to declare a Binding sends a request
    shaped exactly as it was before the merged card existed, so the server has nothing to read
    where there is nothing to claim."""
    assert "bindFirst" not in _run()["click"]
